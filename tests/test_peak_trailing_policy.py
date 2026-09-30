import asyncio
import copy
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pandas as pd
import pytest

from core.engine import TradingEngine
from core.services.exits.peak_trailing_exit import (
    STATE_KEY, RETIRED_KEYS, PEAK_REASON, HARD_REASON,
    evaluate_peak_trailing, estimated_net_pnl, migrate_peak_state,
)
from core.services.exits.realtime_profit_exit import migrate_account_peak_exits
from core.services.exits.entry_atr_protection import enforce_atr_protection
from core.services.symbol_runner import process_single_symbol_runner


def position(side='LONG', atr=1., margin=100.):
    return dict(side=side,entry_price=100.,qty=1.,open_timestamp=60.,entry_atr=atr,
                margin=margin,entry_mode='CHANNEL_SWING',leverage=1.)


def observe(p, gain, stamp=61000, **kwargs):
    sign = 1 if p['side']=='LONG' else -1
    return evaluate_peak_trailing(p,100+sign*gain,stamp,fee=0.,slippage=0.,**kwargs)


@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_atr_arming_is_inclusive_and_latched(side):
    p=position(side)
    assert observe(p,1.499) is None
    assert not p[STATE_KEY]['armed']
    assert observe(p,1.,62000) is None
    assert observe(p,1.5,63000) is None
    assert p[STATE_KEY]['armed']
    decision=observe(p,1.19,64000)
    assert decision['type']==PEAK_REASON
    assert decision['trigger']=='NET_PEAK_DRAWDOWN_20PCT'
    assert p[STATE_KEY]['armed']


@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_strict_point_four_atr_and_no_wick_peak(side):
    p=position(side)
    assert observe(p,10.) is None
    assert observe(p,9.6,62000,atr=100.) is None
    result=observe(p,9.599,62001,atr=100.)
    assert result['trigger']=='PRICE_RETRACE_GT_04ATR'
    assert p[STATE_KEY]['atr']==1.
    assert p['peak_price']==100+(10 if side=='LONG' else -10)


def price_for_net(p, net, fee=.001, slip=.002):
    sign=1 if p['side']=='LONG' else -1
    execution=(net/p['qty']+p['entry_price']*(sign+fee))/(sign-fee)
    return execution/(1-sign*slip)


@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_net_five_percent_and_exact_twenty_percent_boundary(side):
    p=position(side,atr=100.,margin=40.)
    for stamp,net in [(61000,1.999),(62000,2.)]:
        assert evaluate_peak_trailing(p,price_for_net(p,net),stamp,fee=.001,slippage=.002) is None
        assert p[STATE_KEY]['armed'] is (net==2.)
    assert evaluate_peak_trailing(p,price_for_net(p,1.601),63000,fee=.001,slippage=.002) is None
    result=evaluate_peak_trailing(p,price_for_net(p,1.6),64000,fee=.001,slippage=.002)
    assert result['trigger']=='NET_PEAK_DRAWDOWN_20PCT'
    assert p['peak_net_pnl_usd']==pytest.approx(2.)


@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_gross_five_percent_is_not_net_five_percent(side):
    p=position(side,atr=100.,margin=40.)
    sign=1 if side=='LONG' else -1
    assert evaluate_peak_trailing(p,100+sign*2.,61000,fee=.001,slippage=.002) is None
    assert not p[STATE_KEY]['armed']
    assert p['peak_net_pnl_usd']<2.


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
    p=position(side)
    observe(p,10.)
    assert observe(p,9.,62000)
    p['open_timestamp']=63.
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
        assert not await enforce_atr_protection(a,'X',100+sign*2.)
        assert 'closed_exit_state' not in a.position_meta['X']
        assert await enforce_atr_protection(a,'X',100+sign*1.)
        assert PEAK_REASON in a.close_position.await_args.args[2]
        assert await enforce_atr_protection(a,'X',100+sign*3.)
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
    assert other['closed_exit_state']['pending']


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
        assert not await e._channel_quote_exit('X',100+sign*2.,now*1000)
        f=pd.DataFrame([dict(timestamp=int(now//60)*60000,is_closed=False,close=100.)])
        await process_single_symbol_runner(e,'X',now,None,False,exit_frame=f,exit_quote=100+sign*1.)
        assert a.close_position.await_args.args[2]==PEAK_REASON
        assert any('REALTIME_EXIT' in str(call) and PEAK_REASON in str(call)
                   and 'trigger=' in str(call) for call in a.log.call_args_list)
        e.fetch_klines.assert_not_called();lock.release()
    asyncio.run(run())


@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_latest_rule_has_no_tp1_partial_or_independent_ma_middle_exit(side):
    from core.services.strategies.pure_trend_v2 import PureTrendStrategyV2
    p=position(side)
    sign=1 if side=='LONG' else -1
    snap=dict(quote_ms=61000,kc_middle=150. if side=='LONG' else 50.,
              live_ma3=150. if side=='LONG' else 50.,open=101.,is_closed=True)
    result=PureTrendStrategyV2().evaluate_anti_whipsaw_profit_lock(p,100+sign*2.,snap,1.)
    assert result is None  # At the peak: no fixed TP1 or MA/KC close.
    assert p[STATE_KEY]['armed']
    assert PureTrendStrategyV2().evaluate_bar_closed_exit(p,pd.DataFrame([snap])) is None
