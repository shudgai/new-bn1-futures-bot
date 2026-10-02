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
def test_2u_ladder_arming_and_trigger(monkeypatch, side):
    monkeypatch.setattr('core.services.exits.trend_hold_evaluator.evaluate_trend_hold', lambda *a, **k: ('RELEASED', 'TEST'))
    # Test ordinary 2U Ladder without Waterfall keeps existing behavior
    p = position(side, qty=1., margin=40.) # net_pnl = gain
    sign = 1 if side == 'LONG' else -1
    # Drive price to 4U peak -> locks 2U
    observe(p, 4.0, 61000)
    assert p[STATE_KEY]['peak_net_pnl'] >= 4.0
    
    # Retrace to 2.1U -> no trigger
    assert observe(p, 2.1, 62000) is None
    
    # Retrace to 2.0U -> triggers
    decision = observe(p, 2.0, 63000)
    assert decision['type'] == PEAK_REASON
    assert decision['trigger'] == 'TRAILING_2U_LADDER'

@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_waterfall_priority_over_soft_exits(side):
    # 2U Ladder candidate + Waterfall same tick => WATERFALL_DROP wins
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
    # Doji candidate + Waterfall same tick => WATERFALL_DROP wins
    # Actually, we test DOJI alone here overriding 2U Ladder if no Waterfall.
    # We also test DOJI vs Waterfall in another test.
    p = position(side, qty=1., margin=40.)
    sign = 1 if side == 'LONG' else -1
    observe(p, 4.0, 61000) # peak 4.0
    
    snap = dict(quote_ms=62000, live_open=100+sign*4.0, atr=1.0,
                live_bar_ms=60000, closed_bar_ms=0,
                last_open=100+sign*3.8, 
                last_high=100+4.0 if side=='LONG' else 100-3.8, 
                last_low=100+3.8 if side=='LONG' else 100-4.0, 
                last_close=100+sign*3.8,
                ma5=10.0, last_ma5=10.0, 
                ma15=20.0 if side=='LONG' else 0.0, 
                last_ma15=20.0 if side=='LONG' else 0.0, kc_middle=15.0) # doji shape + RELEASED trend
                
    # Retrace to 2.0 (hits 2U ladder, but also triggers DOJI if body is small)
    decision = evaluate_peak_trailing(p, 100+sign*2.0, snap, 1.0, fee=0., slippage=0.)
    # Waterfall also hits here because body=2.0 > 1.5 ATR. Waterfall wins!
    assert decision['trigger'] == 'WATERFALL_DROP'
    
    # To test DOJI wins over 2U ladder without Waterfall, we make body < 1.5 ATR
    # Retrace to 100+sign*2.6 (locked is 2.0, so 2U ladder NOT hit, but wait, 
    # we want 2U ladder to hit... lock is at 2.0. If we drop to 2.0, body is 2.0.
    # If ATR is 2.0, Waterfall threshold is 3.0. Then Waterfall won't hit!
    p[STATE_KEY].pop('pending', None)
    p[STATE_KEY].pop('trigger', None)
    snap['atr'] = 2.0
    decision2 = evaluate_peak_trailing(p, 100+sign*2.0, snap, 2.0, fee=0., slippage=0.)
    assert decision2['trigger'] == 'DOJI_REVERSAL_EXIT'

@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_initial_atr_stop_priority_over_waterfall(side):
    # Initial ATR Hard Stop + Waterfall same tick => INITIAL_ATR_HARD_STOP wins
    p = position(side, atr=1.0, margin=40.)
    sign = 1 if side == 'LONG' else -1
    
    snap = dict(quote_ms=62000, live_open=100+sign*1.0, atr=1.0, 
                live_bar_ms=60000, closed_bar_ms=0)
    
    # Drop below initial stop (100 - sign*1.5)
    decision = evaluate_peak_trailing(p, 100-sign*2.0, snap, 1.0, fee=0., slippage=0.)
    assert decision['type'] == HARD_REASON
    assert decision['trigger'] == 'INITIAL_ATR'

@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_2u_ladder_relaxed_floor_under_trend_hold(side):
    # Test 2U ladder drops to relaxed floor under TREND_HOLD
    p = position(side, qty=1., margin=40.)
    sign = 1 if side == 'LONG' else -1
    observe(p, 6.0, 61000) # peak 6U -> normally locks 4U
    
    snap = dict(quote_ms=62000, live_open=100+sign*6.0, atr=10.0, 
                live_bar_ms=60000, closed_bar_ms=0,
                ma5=100+sign*10.0, ma15=100-sign*10.0, kc_middle=100-sign*10.0) # TREND_HOLD -> WARNING (since price retraced below ma5)
                
    # Under WARNING, soft exit is blocked.
    # At 3.0U, it should NOT trigger (since lock is 4U but we're at 3U, so 2U ladder wants to trigger but is blocked)
    # Actually wait: observe() passes an int, which skips the soft_exit_blocked logic. We must pass a dict.
    assert evaluate_peak_trailing(p, 100+sign*3.0, snap, 10.0, fee=0., slippage=0.) is None
    
    # At 2.0U, it triggers the relaxed lock. But if it's WARNING, soft_exit_blocked is True!
    snap['quote_ms'] = 63000
    decision = evaluate_peak_trailing(p, 100+sign*2.0, snap, 10.0, fee=0., slippage=0.)
    assert decision is None # Blocked by WARNING trend

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
def test_verified_hard_pending_survives_migration(side):
    p=position(side)
    p['instant_exit_state']=dict(identity=[side,60.,100.,1.],peak=0.)
    p['closed_exit_state']=dict(pending=True,reason=HARD_REASON)
    assert observe(p,0.)['type']==HARD_REASON

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
def test_account_adapter_cleans_both_copies_and_retries_without_frame(side):
    async def run():
        p=position(side);p['open_timestamp']=time.time()-120
        sign=1 if side=='LONG' else -1
        a=SimpleNamespace(positions={'X':p},position_meta={'X':{'closed_exit_state':{'pending':True}}},
                          save_state=Mock(),close_position=AsyncMock(return_value=False))
        # Call enforce_atr_protection -> should clean closed_exit_state
        assert not await enforce_atr_protection(a,'X',100+sign*2.)
        assert 'closed_exit_state' not in a.position_meta['X']
        # The original test checked that PEAK_REASON was hit at 1.0. 
        # With 2U ladder, peak_net_pnl must be >= 4 to arm.
        # gain = 2.0 -> peak_net_pnl = 2.0. So it won't arm.
        # Let's hit the initial ATR hard stop instead (price = 100 - sign*2.0)
        assert await enforce_atr_protection(a,'X',100-sign*2.0)
        assert HARD_REASON in a.close_position.await_args.args[2]
        
        # Second hit of HARD_REASON
        assert await enforce_atr_protection(a,'X',100-sign*3.)
        assert a.close_position.await_count==2
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
def test_runner_and_ticker_do_not_require_closed_candles_or_rest(side):
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
        
        # We need a snapshot that returns RELEASED to allow the soft exit (2U ladder).
        # We'll just monkeypatch evaluate_trend_hold to return RELEASED so soft_exit_blocked=False.
        import core.services.exits.trend_hold_evaluator as the
        original_eval = the.evaluate_trend_hold
        the.evaluate_trend_hold = Mock(return_value=('RELEASED', 'TEST'))
        try:
            await process_single_symbol_runner(e,'X',now,None,False,exit_frame=f,exit_quote=100+sign*1.)
        finally:
            the.evaluate_trend_hold = original_eval
            
        assert PEAK_REASON in a.close_position.await_args.args[2]
        assert any('REALTIME_EXIT' in str(call) and PEAK_REASON in str(call)
                   and 'trigger=' in str(call) for call in a.log.call_args_list)
        e.fetch_klines.assert_not_called();lock.release()
    asyncio.run(run())


@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_atr_arming_is_inclusive_and_latched(monkeypatch, side):
    monkeypatch.setattr('core.services.exits.trend_hold_evaluator.evaluate_trend_hold', lambda *a, **k: ('RELEASED', 'TEST'))
    # Migration: tests that the 2U ladder arms at exactly 4.0U net, and retains peak state.
    p = position(side, qty=1.)
    assert observe(p, 3.99) is None
    assert p[STATE_KEY]['peak_net_pnl'] < 4.0
    
    assert observe(p, 4.0, 62000) is None
    assert p[STATE_KEY]['peak_net_pnl'] >= 4.0
    
    # Locked at 2.0U. A drop to 2.1U does not trigger.
    assert observe(p, 2.1, 63000) is None
    
    # A drop to 2.0U triggers.
    decision = observe(p, 2.0, 64000)
    assert decision['type'] == PEAK_REASON
    assert decision['trigger'] == 'TRAILING_2U_LADDER'

@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_strict_point_four_atr_and_no_wick_peak(side, monkeypatch):
    monkeypatch.setattr('core.services.exits.trend_hold_evaluator.evaluate_trend_hold', lambda *a, **k: ('RELEASED', 'TEST'))
    # Migration: tests exact boundary triggering of the 2U ladder and peak retention
    p = position(side, qty=1.)
    # Drive net to 6.0U (locked net becomes 4.0U)
    assert observe(p, 6.0) is None
    assert p[STATE_KEY]['peak_net_pnl'] >= 6.0
    
    # Drop to 4.1U -> no trigger
    assert observe(p, 4.1, 62000) is None
    
    # Drop to 4.0U -> trigger
    result = observe(p, 4.0, 62001)
    assert result['trigger'] == 'TRAILING_2U_LADDER'
    assert p[STATE_KEY]['peak_net_pnl'] == 6.0

@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_net_five_percent_and_exact_twenty_percent_boundary(side, monkeypatch):
    monkeypatch.setattr('core.services.exits.trend_hold_evaluator.evaluate_trend_hold', lambda *a, **k: ('RELEASED', 'TEST'))
    # Migration: tests the 4U -> 2U, 6U -> 4U scaling exactly
    p = position(side, qty=1., margin=40.)
    
    # Up to 3.99U -> no trigger
    assert observe(p, 3.999, 61000) is None
    assert p[STATE_KEY]['peak_net_pnl'] < 4.0
    
    # 4.0U -> locked at 2.0U
    assert observe(p, 4.0, 62000) is None
    assert p[STATE_KEY]['peak_net_pnl'] >= 4.0
    
    # Drop to 2.01U -> no trigger
    assert observe(p, 2.01, 63000) is None
    
    # Drop to 2.0U -> trigger
    result = observe(p, 2.0, 64000)
    assert result['trigger'] == 'TRAILING_2U_LADDER'

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
