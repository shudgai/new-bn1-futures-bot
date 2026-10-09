import asyncio
import copy
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pandas as pd
import pytest

from core.engine import TradingEngine
from core.services.exits.peak_trailing_exit import (
    STATE_KEY, RETIRED_KEYS, PEAK_REASON, HARD_REASON, ABNORMAL_REASON,
    evaluate_peak_trailing, estimated_net_pnl, migrate_peak_state,
)
from core.services.exits.realtime_profit_exit import migrate_account_peak_exits
from core.services.exits.entry_atr_protection import enforce_atr_protection
from core.services.symbol_runner import process_single_symbol_runner

def position(side='LONG', atr=1., margin=100., qty=1.):
    return dict(side=side,entry_price=100.,qty=qty,open_timestamp=60.,entry_atr=atr,
                margin=margin,entry_mode='CHANNEL_SWING',leverage=1.)

def observe(p, gain, stamp=61000, **kwargs):
    sign = 1 if p['side']=='LONG' else -1
    return evaluate_peak_trailing(p,100+sign*gain,stamp,fee=0.,slippage=0.,**kwargs)

def price_for_net(p, net, fee=.001, slip=.002):
    sign = 1 if p['side']=='LONG' else -1
    execution = (net/p['qty']+p['entry_price']*(sign+fee))/(sign-fee)
    return execution/(1-sign*slip)

@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_dynamic_peak_pullback_threshold(monkeypatch, side):
    monkeypatch.setattr('core.services.exits.trend_hold_evaluator.evaluate_trend_hold', lambda *a, **k: ('RELEASED', 'TEST'))
    p = position(side)
    observe(p, 4.0, 61000)

    assert observe(p, 3.66, 62000) is None
    assert observe(p, 3.64, 63000) is None

@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_waterfall_priority_over_soft_exits(side):
    # Dynamic pullback candidate + Waterfall on the same tick => Waterfall wins.
    p = position(side, qty=1., margin=40.)
    sign = 1 if side == 'LONG' else -1
    observe(p, 4.0, 61000) # peak_net_pnl = 4.0
    
    # Waterfall needs a snapshot where price drops rapidly
    snap = dict(quote_ms=62000, live_open=100+sign*4.0, atr=1.0, 
                live_bar_ms=60000, closed_bar_ms=0)
    
    # Price drops to 100+sign*2.0 (2U locked). Waterfall body = 2.0 >= 1.5 ATR threshold.
    decision = evaluate_peak_trailing(p, 100+sign*2.0, snap, 1.0, fee=0., slippage=0.)
    assert decision['type'] == ABNORMAL_REASON
    assert decision['trigger'] == 'WATERFALL_DROP'

@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_doji_priority_over_soft_exits(side):
    # Waterfall overrides Doji; otherwise valid Doji evidence overrides pullback.
    p = position(side, qty=1., margin=40.)
    sign = 1 if side == 'LONG' else -1
    observe(p, 4.0, 61000) # peak 4.0
    
    snap = dict(quote_ms=62000, live_open=100+sign*4.0, atr=1.0,
                live_bar_ms=60000, closed_bar_ms=0,
                last_open=100+sign*3.8, 
                last_high=100+4.0 if side=='LONG' else 100-3.8, 
                last_low=100+3.8 if side=='LONG' else 100-4.0, 
                last_close=100+sign*3.8,
                live_high=100+4.0 if side=='LONG' else 100-3.8,
                live_low=100+3.8 if side=='LONG' else 100-4.0,
                ma5=10.0, last_ma5=10.0, 
                ma15=20.0 if side=='LONG' else 0.0, 
                last_ma15=20.0 if side=='LONG' else 0.0, kc_middle=15.0) # doji shape + RELEASED trend
                
    # This adverse body qualifies as a Waterfall.
    decision = evaluate_peak_trailing(p, 100+sign*2.0, snap, 1.0, fee=0., slippage=0.)
    # Waterfall also hits here because body=2.0 > 1.5 ATR. Waterfall wins!
    assert decision['trigger'] == 'WATERFALL_DROP'
    
    # Raising the ATR suppresses Waterfall while preserving the Doji evidence.
    p[STATE_KEY].pop('pending', None)
    p[STATE_KEY].pop('trigger', None)
    snap['atr'] = 2.0
    decision2 = evaluate_peak_trailing(p, 100+sign*2.0, snap, 2.0, fee=0., slippage=0.)
    assert decision2['trigger'] == 'DOJI_REVERSAL_EXIT'

@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_waterfall_remains_authorized_without_strategy_atr_stop(side):
    p = position(side, atr=1.0, margin=40.)
    sign = 1 if side == 'LONG' else -1
    
    snap = dict(quote_ms=62000, live_open=100+sign*1.0, atr=1.0, 
                live_bar_ms=60000, closed_bar_ms=0)
    
    # Drop below initial stop (100 - sign*1.5)
    decision = evaluate_peak_trailing(p, 100-sign*2.0, snap, 1.0, fee=0., slippage=0.)
    assert decision['type'] == ABNORMAL_REASON
    assert decision['trigger'] == 'WATERFALL_DROP'

@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_channel_swing_ignores_pullback_and_retains_waterfall(side):
    p = position(side, qty=1., margin=40.)
    sign = 1 if side == 'LONG' else -1
    observe(p, 6.0, 61000)
    
    snap = dict(quote_ms=62000, live_open=100+sign*6.0, atr=10.0, 
                live_bar_ms=60000, closed_bar_ms=0,
                ma5=100+sign*10.0, ma15=100-sign*10.0, kc_middle=100-sign*10.0)

    decision = evaluate_peak_trailing(p, 100+sign*3.0, snap, 10.0, fee=0., slippage=0.)
    assert decision is None
    assert not p[STATE_KEY].get('pending')

    snap.update(live_open=100+sign*3.0, atr=1.0)
    decision = evaluate_peak_trailing(p, 100., snap, 1.0, fee=0., slippage=0.)
    assert decision['trigger'] == 'WATERFALL_DROP'

@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_legacy_flags_purged_but_verified_peak_retained(side):
    p=position(side,atr=100.)
    ident=[side,60.,100.,1.]
    meta={key:{'pending':True} for key in RETIRED_KEYS}
    p.update(copy.deepcopy(meta))
    p['instant_exit_state']=dict(identity=ident,peak=2.,pending='EXIT_OUTER_MA3_REVERSAL')
    meta['instant_exit_state']=copy.deepcopy(p['instant_exit_state'])
    state=migrate_peak_state(p,meta)
    assert state['peak_price']==(102. if side=='LONG' else 98.)
    assert not state.get('pending')
    assert all(key not in source for source in (p,meta) for key in RETIRED_KEYS)
    assert observe(p,1.9) is None

@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_retired_channel_atr_pending_is_revoked(side):
    p=position(side)
    p['instant_exit_state']=dict(identity=[side,60.,100.,1.],peak=0.)
    p['closed_exit_state']=dict(pending=True,reason=HARD_REASON)
    assert observe(p,0.) is None
    assert not p[STATE_KEY].get('pending')

@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_new_position_cannot_inherit_previous_peak_or_pending(side):
    p=position(side, qty=1.)
    observe(p,10.)
    assert p[STATE_KEY]['peak_price'] == 100 + (10 if side == 'LONG' else -10)
    p['open_timestamp']=63.
    # New timestamp -> new position identity.
    # Because we don't have snapshot, trend is UNKNOWN.
    # A price drop to 0 gain (price=100) does not trigger any exit since initial_sl is not set.
    assert observe(p,0.,64000) is None
    assert not p[STATE_KEY].get('pending')
    assert p['peak_price']==100.

@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_stale_invalid_and_pre_entry_ticks_cannot_mutate(side):
    p=position(side)
    observe(p,10.,65000)
    saved=copy.deepcopy(p)
    for price,stamp in [(100.,64000),(float('nan'),66000),(100.,59000)]:
        assert evaluate_peak_trailing(p,price,stamp) is None
        assert p==saved

@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_account_adapter_does_not_retry_disabled_pullback_without_frame(side):
    async def run():
        p=position(side);p['open_timestamp']=time.time()-120
        sign=1 if side=='LONG' else -1
        a=SimpleNamespace(positions={'X':p},position_meta={'X':{'closed_exit_state':{'pending':True}}},
                          save_state=Mock(),close_position=AsyncMock(return_value=False))
        # Call enforce_atr_protection -> should clean closed_exit_state
        assert not await enforce_atr_protection(a,'X',100+sign*2.)
        assert 'closed_exit_state' not in a.position_meta['X']
        assert not await enforce_atr_protection(a,'X',100+sign*1.49)
        assert not await enforce_atr_protection(a,'X',100+sign*1.4)
        a.close_position.assert_not_awaited()
    asyncio.run(run())

def test_startup_cleanup_is_channel_only():
    p=position();p['closed_exit_state']={'pending':True}
    other=dict(p,entry_mode='OTHER')
    a=SimpleNamespace(positions={'X':p,'Y':other},position_meta={'X':{'doji_reversal_state':{}},'Y':{}},
                      save_state=Mock(),log=Mock())
    migrate_account_peak_exits(a)
    assert 'closed_exit_state' not in p
    assert 'doji_reversal_state' not in a.position_meta['X']
    assert a.position_meta['X'][STATE_KEY]['identity']==['LONG',60.,100.,1.]

@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_runner_and_ticker_hold_through_pullback_without_closed_candles_or_rest(side):
    async def run():
        now=time.time();p=position(side);p['open_timestamp']=now-120
        a=SimpleNamespace(positions={'X':p},position_meta={},save_state=Mock(),log=Mock(),
                          close_position=AsyncMock(return_value=False))
        e=object.__new__(TradingEngine);e.account=a;e.is_running=True
        e._take_over_manual_position=Mock()
        e.fetch_klines=AsyncMock(side_effect=AssertionError('No REST'))
        lock=asyncio.Lock();await lock.acquire();e._channel_symbol_locks={'X':lock}
        sign=1 if side=='LONG' else -1
        assert not await e._channel_quote_exit('X',100+sign*6.,now*1000) # drive to 6U peak
        f=pd.DataFrame([dict(timestamp=int(now//60)*60000,is_closed=False,close=100.)])
        
        await process_single_symbol_runner(e,'X',now,None,False,exit_frame=f,exit_quote=100+sign*1.)

        assert a.close_position.await_count == 0
        e.fetch_klines.assert_not_called();lock.release()
    asyncio.run(run())


@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_channel_swing_peak_reversal_uses_fixed_half_atr(side, monkeypatch):
    monkeypatch.setattr('core.services.exits.trend_hold_evaluator.evaluate_trend_hold', lambda *a, **k: ('RELEASED', 'TEST'))
    p = position(side)
    observe(p, 2.0, 61000)
    assert observe(p, 1.51, 62000) is None
    result = observe(p, 1.50, 63000)
    assert result is None

@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_channel_swing_pullback_does_not_exit_with_fees_and_slippage(side, monkeypatch):
    monkeypatch.setattr('core.services.exits.trend_hold_evaluator.evaluate_trend_hold', lambda *a, **k: ('RELEASED', 'TEST'))
    p = position(side)
    sign = 1 if side == 'LONG' else -1
    assert evaluate_peak_trailing(p, 100 + sign * 2.0, 61000, fee=.001, slippage=.002) is None
    assert evaluate_peak_trailing(p, 100 + sign * 1.61, 62000, fee=.001, slippage=.002) is None
    result = evaluate_peak_trailing(p, 100 + sign * 1.599, 63000, fee=.001, slippage=.002)
    assert result is None

@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_gross_five_percent_is_not_net_five_percent(side):
    # Migration: gross profit != net profit.
    p = position(side, qty=1., margin=40.)
    sign = 1 if side == 'LONG' else -1
    # fee is non-zero so a price gain of 4.0 is a net pnl < 4.0.
    assert evaluate_peak_trailing(p, 100+sign*4.0, 61000, fee=0.001, slippage=0.002) is None
    assert p[STATE_KEY]['peak_net_pnl'] < 4.0

@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_latest_rule_has_no_tp1_partial_or_independent_ma_middle_exit(side):
    from core.services.strategies.pure_trend_v2 import PureTrendStrategyV2
    p = position(side, qty=1.)
    sign = 1 if side == 'LONG' else -1
    snap = dict(quote_ms=61000, kc_middle=150. if side == 'LONG' else 50.,
                live_ma3=150. if side == 'LONG' else 50., open=101., is_closed=True)
    # Drive net to 5.0 to arm the ladder (compensating for fee)
    result = PureTrendStrategyV2().evaluate_anti_whipsaw_profit_lock(p, 100+sign*5.0, snap, 1.)
    assert result is None
    # Verify the peak_net_pnl was set (ladder armed)
    assert p[STATE_KEY]['peak_net_pnl'] >= 4.0
    assert PureTrendStrategyV2().evaluate_bar_closed_exit(p, pd.DataFrame([snap])) is None
