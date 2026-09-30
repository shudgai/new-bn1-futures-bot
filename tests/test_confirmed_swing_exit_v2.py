import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pandas as pd
import pytest

from core.services.strategies.pure_trend_v2 import PureTrendStrategyV2
from core.services.exits.dual_track_exit_service import DualTrackExitStrategy, POLICY


def frame(side='LONG', close=97.9):
    rows=[dict(timestamp=60000*(i+1),open=101.,close=101.,low=v,high=104.,is_closed=True,
               ma3=101.,ma15=102.,atr=10.,kc_upper=103.,kc_middle=101.,kc_lower=99.)
          for i,v in enumerate([100.,98.,100.,99.])]
    rows[-1].update(close=close,low=min(97.,close),high=105.)
    f=pd.DataFrame(rows)
    if side=='SHORT':
        orig=f.copy()
        for a,b in [('open','open'),('close','close'),('low','high'),('high','low'),('kc_upper','kc_lower'),('kc_lower','kc_upper'),('ma3','ma3'),('ma15','ma15'),('kc_middle','kc_middle')]:f[a]=202.-orig[b]
    return f


def position(side):
    return dict(side=side,entry_price=101.,entry_atr=10.,sl=80. if side=='LONG' else 122.,open_timestamp=1.,entry_mode='CHANNEL_SWING',qty=1.,margin=101.,leverage=1.)


@pytest.mark.parametrize('side',['LONG','SHORT'])
@pytest.mark.parametrize('close,exit_expected',[(97.9,True),(98.,False),(98.1,False),(101.,False)])
def test_retired_closed_break_has_no_exit_authority(side,close,exit_expected):
    reason=PureTrendStrategyV2().evaluate_bar_closed_exit(position(side),frame(side,close))
    assert reason is None


@pytest.mark.parametrize('side',['LONG','SHORT'])
@pytest.mark.parametrize('fault',['no_pivot','equal_pivot','live_break','before_entry','nan','gap'])
def test_missing_or_invalid_structure_holds(side,fault):
    f=frame(side);p=position(side)
    key='low' if side=='LONG' else 'high'
    if fault=='no_pivot':f[key]=[100,99,98,97] if side=='LONG' else [102,103,104,105]
    if fault=='equal_pivot':f.loc[0,key]=f.loc[1,key]
    if fault=='live_break':f.loc[3,'is_closed']=False
    if fault=='before_entry':p['open_timestamp']=250.
    if fault=='nan':f.loc[1,key]=float('nan')
    if fault=='gap':f.loc[3,'timestamp']+=60000
    assert PureTrendStrategyV2().evaluate_bar_closed_exit(p,f) is None


@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_old_drawdown_pending_cleared_and_new_pending_retried(side):
    p=position(side)
    p.update(peak_pnl_usdt=100.,peak_profit_diff=20.,unrealized_pnl=1.,
             closed_exit_state=dict(policy='closed_1m_ma15_structure_v1',pending=True,reason='INTRA_BAR_PEAK_DRAWDOWN_LOCK'))
    strategy=DualTrackExitStrategy()
    assert strategy.evaluate_exit(p,frame(side,101.),current_price=101.) is None
    assert 'closed_exit_state' not in p
    reason=strategy.evaluate_exit(p,frame(side),current_price=101.)
    assert reason is None
    assert strategy.evaluate_exit(p,current_price=101.) is None


@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_hard_stop_without_candles(side):
    p=position(side)
    assert DualTrackExitStrategy().evaluate_exit(p,current_price=p['sl'])=='EXIT_INITIAL_ATR_HARD_STOP'


@pytest.mark.parametrize('side',['LONG','SHORT'])
@pytest.mark.parametrize('body,reason',[(1.19,None),(1.2,'EMERGENCY_GIANT_REVERSE_CANDLE'),(1.5,'WATERFALL')])
def test_emergency_thresholds(side,body,reason):
    price=100.-body*10 if side=='LONG' else 100.+body*10
    result=PureTrendStrategyV2().check_intra_bar_emergency_exit(position(side),price,dict(open=100.,atr=10.),{})
    assert result is None  # Single candle-body exits retired by explicit authorization.


def test_btc_emergency():
    assert PureTrendStrategyV2().check_intra_bar_emergency_exit(position('LONG'),100.,dict(open=100.,atr=10.),{'is_crashing':True}) is None


@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_runner_closed_candles_do_not_authorize_exit(side):
    from core.services.symbol_runner import process_single_symbol_runner
    f=frame(side);p=position(side)
    # Prevent account loss-limit exit; close is evaluated on frame, quote is unchanged.
    account=SimpleNamespace(positions={'TEST':p},position_meta={},save_state=Mock(),
                            close_position=AsyncMock(return_value=False))
    engine=SimpleNamespace(account=account,tickers={'TEST':101.},_take_over_manual_position=Mock())
    asyncio.run(process_single_symbol_runner(engine,'TEST',300.,None,False,exit_frame=f,exit_quote=101.))
    account.close_position.assert_not_awaited()
    asyncio.run(process_single_symbol_runner(engine,'TEST',301.,None,False,exit_frame=frame(side,101.),exit_quote=101.))
    account.close_position.assert_not_awaited()
