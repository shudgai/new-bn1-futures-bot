"""Continuation uses real quote order after closed breakout confirmation."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pandas as pd
import pytest

from test_closed_breakout_entry import candles
from core.engine import TradingEngine
from core.services.closed_breakout_entry import evaluate_breakout_pullback
from core.services.outer_turn_entry import observation_store
from core.services.strategies.unified_entry_strategy import UnifiedEntryStrategy
from core.services.symbol_runner import process_single_symbol_runner


def continuation_frame(side):
    frame = candles(side)
    frame.loc[3, 'is_closed'] = True
    live = frame.iloc[-1].copy()
    live['timestamp'] = 300000
    live['is_closed'] = False
    frame = pd.concat([frame, live.to_frame().T], ignore_index=True)
    frame.attrs['timeframe_ms'] = 60000
    return frame


def prices(side):
    return [115., 114.] if side == 'LONG' else [85.,86.]


@pytest.mark.parametrize('side', ['LONG','SHORT'])
def test_pullback_opens_without_turn_or_new_breakout(side):
    f=continuation_frame(side);state={};p1,p2=prices(side)
    assert not evaluate_breakout_pullback(f,p1,side,state,'X',0)[0]
    result=evaluate_breakout_pullback(f,p2,side,state,'X',1)
    assert result[0] and result[1]==f'KC_BREAKOUT_PULLBACK_{side}'
    assert evaluate_breakout_pullback(f,p2,side,state,'X',2)[0]
    # A favorable quote revokes the pullback, rather than latching indefinitely.
    assert not evaluate_breakout_pullback(f,p1,side,state,'X',3)[0]
    assert evaluate_breakout_pullback(f,p2,side,state,'X',4)[0]


@pytest.mark.parametrize('side',['LONG','SHORT'])
@pytest.mark.parametrize('fault',['no_breakout','historical_inside','touch','gap','new_bar','close','restart','direction'])
def test_continuation_invalidates_or_restarts_observation(side,fault):
    f=continuation_frame(side);state={};p1,p2=prices(side)
    assert not evaluate_breakout_pullback(f,p1,side,state,'X',0)[0]
    assert evaluate_breakout_pullback(f,p2,side,state,'X',1)[0]
    now=2;closed_at=None
    if fault=='no_breakout': f.loc[1,'open']=111 if side=='LONG' else 89
    if fault=='historical_inside': f.loc[3,'close']=100
    if fault=='touch': p2=110 if side=='LONG' else 90
    if fault=='gap': now=7
    if fault=='new_bar': f.loc[4,'timestamp']=360000
    if fault=='close': closed_at='new close'
    if fault=='restart': state={}
    if fault=='direction': f.loc[3,'kc_middle']=100
    assert not evaluate_breakout_pullback(f,p2,side,state,'X',now,closed_at)[0]
    if fault=='touch':
        # Returning outside needs a newly observed pullback.
        assert not evaluate_breakout_pullback(f,p1,side,state,'X',3)[0]
        assert evaluate_breakout_pullback(f,prices(side)[1],side,state,'X',4)[0]


@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_actual_runner_and_quote_gate_share_pullback_and_close_identity(side):
    f=continuation_frame(side);p1,p2=prices(side)
    account=SimpleNamespace(positions={},position_meta={},log=Mock(),trades=[],last_closed_at={})
    engine=SimpleNamespace(account=account,_channel_exit_frames={},_last_exit_bar_id={},
                           get_velocity_drop_ratio=Mock(return_value=0),
                           _execute_confirmed_channel_break=AsyncMock(return_value=False))
    for price in (p1,p2):
        asyncio.run(process_single_symbol_runner(engine,'X',0,None,False,exit_frame=f,exit_quote=price))
    engine._execute_confirmed_channel_break.assert_awaited_once()
    assert engine._execute_confirmed_channel_break.await_args.kwargs['v8_reason']==f'KC_BREAKOUT_PULLBACK_{side}'
    assert TradingEngine._channel_intrabar_ready(engine,'X',f,p2,side)
    # An externally observed close between scans discards the prior quote sequence.
    account.last_closed_at['X']=1234
    assert not TradingEngine._channel_intrabar_ready(engine,'X',f,p2,side)
    assert not UnifiedEntryStrategy().evaluate_entry(f,p2,side,engine=engine,symbol='X',existing_pos={'side':side})[0]
    assert ('breakout_pullback','X',side) not in observation_store(engine)


@pytest.mark.parametrize('side',['LONG','SHORT'])
@pytest.mark.parametrize('mode',['outer_cycle','next_breakout'])
def test_normal_reentry_requires_matched_fill_and_new_pullback(side,mode):
    frame=continuation_frame(side);p1,p2=prices(side)
    reason='Channel Swing CK_FADING_MA3_TURN_EXIT' if mode=='next_breakout' else 'Channel Swing PROFIT_PROTECTION t'
    ticket=dict(side=side,mode=mode,phase='closed',requires_pullback=False,token='t',
                exit_bar_id=240000,close_requested_at_ms=240001,close_reason=reason)
    account=SimpleNamespace(positions={},log=Mock(),save_state=Mock(),trades=[],last_closed_at={})
    engine=SimpleNamespace(account=account)
    assert not TradingEngine._profit_reentry_ready(engine,'X',ticket,frame,p1)
    account.trades=[dict(symbol='X',action='CLOSE_'+side,reason=reason,id=240002)]
    assert not TradingEngine._profit_reentry_ready(engine,'X',ticket,frame,p1)
    assert TradingEngine._profit_reentry_ready(engine,'X',ticket,frame,p2)
    # The exit candle itself remains forbidden.
    ticket['exit_bar_id']=300000
    account.trades[0]['id']=300002
    assert not TradingEngine._profit_reentry_ready(engine,'X',ticket,frame,p2)


@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_abnormal_reentry_still_calls_special_pullback_gate(side,monkeypatch):
    import core.engine as module
    frame=continuation_frame(side);p1,p2=prices(side)
    reason='Channel Swing EMERGENCY_EXIT_LIVE_ADVERSE_WATERFALL'
    ticket=dict(side=side,mode='outer_cycle',phase='closed',requires_pullback=True,token='t',
                exit_bar_id=240000,close_requested_at_ms=240001,close_reason=reason)
    account=SimpleNamespace(positions={},log=Mock(),save_state=Mock(),
        trades=[dict(symbol='X',action='CLOSE_'+side,reason=reason,id=240002)],last_closed_at={})
    engine=SimpleNamespace(account=account)
    guard=Mock(return_value=False)
    monkeypatch.setattr(module,'abnormal_pullback_ready',guard)
    assert not TradingEngine._profit_reentry_ready(engine,'X',ticket,frame,p1)
    assert not TradingEngine._profit_reentry_ready(engine,'X',ticket,frame,p2)
    assert guard.call_count==2
    assert ticket['requires_pullback'] is True


@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_reentry_dispatch_and_fresh_snapshot_use_continuation_code(side):
    frame=continuation_frame(side);p1,p2=prices(side)
    ticket=dict(side=side,mode='outer_cycle',phase='closed',requires_pullback=False,token='t',
                exit_bar_id=240000,close_requested_at_ms=240001,
                close_reason='Channel Swing PROFIT_PROTECTION t')
    account=SimpleNamespace(positions={},channel_profit_reentries={'X':ticket},log=Mock(),save_state=Mock(),
        trades=[dict(symbol='X',action='CLOSE_'+side,reason=ticket['close_reason'],id=240002)],last_closed_at={})
    engine=SimpleNamespace(account=account,_place_structured_entry=AsyncMock(return_value=False),
                           fetch_klines=AsyncMock(return_value=frame),tickers={'X':p2},
                           strategy=SimpleNamespace(compute_indicators=lambda f:f),
                           _channel_terminal_market=lambda f:False,
                           _release_resolved_abnormal_exit=Mock(return_value=False),
                           _ck_reverse_order_authorized=lambda *a:False)
    engine._profit_reentry_ready=lambda *a:TradingEngine._profit_reentry_ready(engine,*a)
    engine._channel_intrabar_ready=lambda *a,**k:TradingEngine._channel_intrabar_ready(engine,*a,**k)
    for price in (p1,p2):
        asyncio.run(TradingEngine._try_profit_reentry_locked(engine,'X',frame,price,False))
    engine._place_structured_entry.assert_awaited_once()
    signal=engine._place_structured_entry.await_args.args[1]
    assert signal['signal_code']==f'KC_BREAKOUT_PULLBACK_{side}'
    snapshot=asyncio.run(TradingEngine._fresh_channel_entry_snapshot(engine,'X',side,
                         'profit:t',profit_reentry_token='t'))
    assert snapshot['signal_code']==signal['signal_code']
    engine._place_structured_entry.reset_mock()
    asyncio.run(TradingEngine._try_profit_reentry_locked(engine,'X',frame,p2,True))
    engine._place_structured_entry.assert_not_awaited()
    engine._place_structured_entry.return_value=True
    asyncio.run(TradingEngine._try_profit_reentry_locked(engine,'X',frame,p2,False))
    assert 'X' not in account.channel_profit_reentries


def test_delayed_close_fill_cannot_reopen_in_actual_fill_bar():
    frame=continuation_frame('LONG')
    ticket=dict(side='LONG',mode='outer_cycle',phase='closed',requires_pullback=False,token='t',
                exit_bar_id=180000,close_requested_at_ms=180001,
                close_reason='Channel Swing PROFIT_PROTECTION t')
    account=SimpleNamespace(positions={},log=Mock(),save_state=Mock(),
        trades=[dict(symbol='X',action='CLOSE_LONG',reason=ticket['close_reason'],id=300002)],last_closed_at={})
    engine=SimpleNamespace(account=account)
    assert not TradingEngine._profit_reentry_ready(engine,'X',ticket,frame,115)
    frame.loc[4,'timestamp']=360000
    assert not TradingEngine._profit_reentry_ready(engine,'X',ticket,frame,115)
    assert TradingEngine._profit_reentry_ready(engine,'X',ticket,frame,114)
