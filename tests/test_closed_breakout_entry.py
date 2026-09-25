"""Closed breakout contract and actual scan/revalidation wiring, without orders."""
import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pandas as pd
import pytest

from core.engine import TradingEngine
from core.services.closed_breakout_entry import evaluate_closed_breakout
from core.services.entry_service import channel_closed_body_break_entry_action, supported_entry_reason
from core.services.strategies.unified_entry_strategy import UnifiedEntryStrategy
from core.services.symbol_runner import process_single_symbol_runner


def candles(side):
    rows = [
        (99, 100, 101, 98),
        (109, 112, 113, 108),
        (112, 114, 115, 111),
        (114, 113, 115, 112),
    ]
    frame = pd.DataFrame([dict(timestamp=(i+1)*60000, open=float(o), close=float(c), high=float(h), low=float(l),
                              kc_upper=110., kc_lower=90., kc_middle=100.+i,
                              ma3=105., ma15=100., atr=2., is_closed=i<3)
                          for i, (o,c,h,l) in enumerate(rows)])
    if side == 'SHORT':
        for key in ('open', 'close', 'kc_middle', 'ma3', 'ma15'):
            frame[key] = 200-frame[key]
        old_high = frame['high'].copy()
        frame['high'] = 200-frame['low']
        frame['low'] = 200-old_high
    frame.attrs['timeframe_ms'] = 60000
    return frame


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
@pytest.mark.parametrize('scale', [1., .000001])
def test_two_closed_bodies_accept_live_opposite_color_and_low_prices(side, scale):
    frame = candles(side)
    for col in ('open','close','high','low','kc_upper','kc_lower','kc_middle','ma3','ma15','atr'):
        frame[col] *= scale
    price = (113 if side == 'LONG' else 87)*scale
    allowed, reason, decision = UnifiedEntryStrategy().evaluate_entry(frame,price,side)
    assert allowed and decision['is_breakout']
    assert reason == f'KC_TWO_BAR_BREAKOUT_{side}'
    assert supported_entry_reason(reason,side)
    assert not supported_entry_reason(reason,'SHORT' if side=='LONG' else 'LONG')
    assert channel_closed_body_break_entry_action(frame,price,side)['action'] == 'ENTER'


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
@pytest.mark.parametrize('fault', ['unclosed','wrong_color','doji','small_body','wick_only',
                                  'gap_open','first_touch','second_touch','quote_touch',
                                  'nan','bad_ohlc','bar_gap','bad_ma','bad_ck'])
def test_invalid_breakout_cannot_enter_or_pass_quote_gate(side,fault):
    frame = candles('LONG');price=113.
    if fault=='unclosed': frame.loc[2,'is_closed']=False
    if fault=='wrong_color': frame.loc[2,['open','close']]=[114,112]
    if fault=='doji': frame.loc[2,'close']=112
    if fault=='small_body': frame.loc[2,'close']=112.1
    if fault=='wick_only': frame.loc[1,['open','close','low']]=[108,109,107]
    if fault=='gap_open': frame.loc[1,'open']=111
    if fault=='first_touch': frame.loc[1,'close']=110
    if fault=='second_touch': frame.loc[2,['open','close','low']]=[109,110,108]
    if fault=='quote_touch': price=110
    if fault=='nan': frame.loc[1,'open']=float('nan')
    if fault=='bad_ohlc': frame.loc[2,'high']=113
    if fault=='bar_gap': frame.loc[2,'timestamp']+=1
    if fault=='bad_ma': frame.loc[2,'ma3']=99
    if fault=='bad_ck': frame.loc[2,'kc_middle']=100
    if side=='SHORT':
        for col in ('open','close','kc_middle','ma3','ma15'):
            frame[col]=200-frame[col]
        hi=frame['high'].copy(); frame['high']=200-frame['low'];frame['low']=200-hi
        price=200-price
    assert not evaluate_closed_breakout(frame,price,side)[0]
    engine=SimpleNamespace(account=SimpleNamespace(positions={},log=Mock()))
    assert not TradingEngine._channel_intrabar_ready(engine,'X',frame,price,side)


@pytest.mark.parametrize('side', ['LONG','SHORT'])
def test_real_scan_dispatch_and_fresh_snapshot_revalidation(side):
    frame=candles(side);price=113 if side=='LONG' else 87
    account=SimpleNamespace(positions={},position_meta={},log=Mock(),channel_profit_reentries={})
    engine=SimpleNamespace(account=account,_channel_exit_frames={},_last_exit_bar_id={},
                           get_velocity_drop_ratio=Mock(return_value=0),
                           _execute_confirmed_channel_break=AsyncMock(return_value=False))
    asyncio.run(process_single_symbol_runner(engine,'X',0,None,False,exit_frame=frame,exit_quote=price))
    engine._execute_confirmed_channel_break.assert_awaited_once()
    assert engine._execute_confirmed_channel_break.await_args.kwargs['v8_reason']==f'KC_TWO_BAR_BREAKOUT_{side}'
    engine._release_resolved_abnormal_exit=Mock(return_value=False)
    engine.fetch_klines=AsyncMock(return_value=frame)
    engine.strategy=SimpleNamespace(compute_indicators=lambda f:f)
    engine.tickers={'X':price}
    engine._channel_intrabar_ready=lambda *a,**k:TradingEngine._channel_intrabar_ready(engine,*a,**k)
    engine._channel_terminal_market=lambda f:False
    engine._channel_candidate_bar_id=TradingEngine._channel_candidate_bar_id
    snapshot=asyncio.run(TradingEngine._fresh_channel_entry_snapshot(engine,'X',side,240000))
    assert snapshot['signal_code']==f'KC_TWO_BAR_BREAKOUT_{side}'
    frame.loc[2,'is_closed']=False
    assert asyncio.run(TradingEngine._fresh_channel_entry_snapshot(engine,'X',side,240000)) is None
    frame.loc[2,'is_closed']=True
    engine.tickers['X']=110 if side=='LONG' else 90
    assert asyncio.run(TradingEngine._fresh_channel_entry_snapshot(engine,'X',side,240000)) is None
    engine.tickers['X']=price
    engine._channel_entry_quote_times={'X':time.time()-10}
    assert asyncio.run(TradingEngine._fresh_channel_entry_snapshot(engine,'X',side,240000)) is None
