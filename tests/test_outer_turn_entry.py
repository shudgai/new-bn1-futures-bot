import asyncio
from types import SimpleNamespace
from unittest.mock import Mock, AsyncMock

import pandas as pd
import pytest

from core.services.outer_turn_entry import evaluate_outer_turn, observation_store
from core.services.strategies.unified_entry_strategy import UnifiedEntryStrategy
from core.services.entry_service import supported_entry_reason
from core.engine import TradingEngine


def candles(side='LONG'):
    sign = 1 if side == 'LONG' else -1
    return pd.DataFrame([dict(timestamp=(i+1)*60000, open=100., high=101., low=99.,
                              close=100., atr=1., ma3=100.+sign, ma15=100., kc_middle=100.+i*sign,
                              kc_upper=110., kc_lower=90., is_closed=i<3)
                         for i in range(4)])


@pytest.mark.parametrize('side,prices', [('LONG',[89,88,88.5]),('SHORT',[111,112,111.5])])
def test_observed_turn_and_equal_quote_revalidation(side, prices):
    state={}; f=candles(side)
    results=[evaluate_outer_turn(f,p,side,state,'X',i)[0] for i,p in enumerate(prices)]
    assert results == [False,False,True]
    assert evaluate_outer_turn(f,prices[-1],side,state,'X',3)[0]
    assert supported_entry_reason(f'KC_OUTER_TURN_{side}',side)


@pytest.mark.parametrize('side,prices', [('LONG',[89,89.2,89.4]),('SHORT',[111,110.8,110.6]),
                                       ('LONG',[111,112,111.5]),('SHORT',[89,88,88.5])])
def test_no_monotonic_chase_or_old_breakout(side,prices):
    state={}
    assert not any(evaluate_outer_turn(candles(side),p,side,state,'X',i)[0] for i,p in enumerate(prices))


@pytest.mark.parametrize('change', ['touch','inside','new_bar','direction','flat','invalid','gap','adverse','restart'])
def test_ready_turn_invalidated(change):
    state={};f=candles()
    for i,p in enumerate([89,88,88.5]):
        evaluate_outer_turn(f,p,'LONG',state,'X',i)
    p=88.5;now=3
    if change=='touch': p=90
    if change=='inside': p=95
    if change=='new_bar': f.loc[3,'timestamp']+=60000
    if change=='direction': f.loc[2,'kc_middle']=100
    if change=='flat': f.loc[2,'kc_middle']=101
    if change=='invalid': f.loc[2,'kc_middle']=float('nan')
    if change=='gap': now=8
    if change=='adverse': p=88.4
    if change=='restart': state={}
    assert not evaluate_outer_turn(f,p,'LONG',state,'X',now)[0]


def test_unclosed_mid_and_wicks_cannot_supply_turn():
    state={};f=candles();f.loc[3,['low','high','kc_middle']]=[1,999,1]
    assert not evaluate_outer_turn(f,89,'LONG',state,'X',0)[0]
    assert not evaluate_outer_turn(f,89,'LONG',state,'X',1)[0]
    assert not evaluate_outer_turn(f,88,'LONG',state,'X',2)[0]
    assert evaluate_outer_turn(f,88.5,'LONG',state,'X',3)[0]


@pytest.mark.parametrize('code', ['MOMENTUM_ENGULFING_LONG','MOMENTUM_BREAKOUT_C1_SHORT',
                                 'CONFIRMED_KC_BREAKOUT_LONG','TRACK_A_EXTREME_REVERSAL_LONG',
                                 'TREND_RELAY_SHORT'])
def test_legacy_entry_codes_cannot_submit(code):
    assert not supported_entry_reason(code,'LONG')
    assert not supported_entry_reason(code,'SHORT')


def test_strategy_requires_shared_observation_and_does_not_pyramid():
    strategy=UnifiedEntryStrategy(); f=candles();state={}
    assert not strategy.evaluate_entry(f,89,'LONG')[0]
    for i,p in enumerate([89,88,88.5]):
        result=strategy.evaluate_entry(f,p,'LONG',observations=state,symbol='X',now=i)
    assert result[0]
    assert not strategy.evaluate_entry(f,88.5,'LONG',observations=state,symbol='X',now=3,existing_pos={'side':'LONG'})[0]
    assert not state


def test_production_quote_gate_rechecks_rail_and_blocks_stale_quote():
    import time
    engine=SimpleNamespace(account=SimpleNamespace(positions={},log=Mock()),_channel_entry_quote_times={})
    f=candles();state=observation_store(engine)
    now=time.monotonic()
    for i,p in enumerate([89,88,88.5]):
        evaluate_outer_turn(f,p,'LONG',state,'X',now-2+i)
    assert TradingEngine._channel_intrabar_ready(engine,'X',f,88.5,'LONG')
    assert not TradingEngine._channel_intrabar_ready(engine,'X',f,90,'LONG')
    for i,p in enumerate([89,88,88.5]):
        evaluate_outer_turn(f,p,'LONG',state,'X',now+i*.01)
    engine._channel_entry_quote_times['X']=time.time()-10
    assert not TradingEngine._channel_intrabar_ready(engine,'X',f,88.5,'LONG')


@pytest.mark.parametrize('side,prices', [('LONG',[89,88,88.5]),('SHORT',[111,112,111.5])])
def test_real_runner_only_submits_after_observed_outer_turn(side, prices):
    from core.services.symbol_runner import process_single_symbol_runner
    account=SimpleNamespace(positions={}, position_meta={}, log=Mock())
    engine=SimpleNamespace(account=account, _channel_exit_frames={}, _last_exit_bar_id={},
                           get_velocity_drop_ratio=Mock(return_value=0),
                           _execute_confirmed_channel_break=AsyncMock(return_value=False))
    async def scenario():
        for price in prices:
            await process_single_symbol_runner(engine,'X',0,None,False,exit_frame=candles(side),exit_quote=price)
    asyncio.run(scenario())
    engine._execute_confirmed_channel_break.assert_awaited_once()
    call=engine._execute_confirmed_channel_break.await_args
    assert call.args[3] == side
    assert call.kwargs['v8_reason'] == f'KC_OUTER_TURN_{side}'
    assert not any('處理失敗' in str(call) for call in account.log.call_args_list)


@pytest.mark.parametrize('side,sign,start', [('LONG',1,89.),('SHORT',-1,111.)])
@pytest.mark.parametrize('scale',[1.,.000001])
def test_recovery_threshold_and_fixed_atr(side,sign,start,scale):
    f=candles(side)
    for col in ['open','high','low','close','atr','ma3','ma15','kc_middle','kc_upper','kc_lower']:
        f[col] *= scale
    state={}
    def quote(price,now):
        return evaluate_outer_turn(f,price*scale,side,state,'X',now)[0]
    assert not quote(start,0)
    assert not quote(start-sign*.5,1)
    # A changed ATR in the same observation cannot lower the frozen threshold.
    f.loc[2,'atr']=.1*scale
    assert not quote(start-sign*.45,2)
    # Nor can it raise the threshold once an observation is in progress.
    f.loc[2,'atr']=10*scale
    assert quote(start-sign*.4,3)
    assert quote(start-sign*.4,4)
    assert not quote(start-sign*.42,4.1)
    assert not quote(start-sign*.41,4.2)  # Old peak recovery cannot authorize another turn.
    assert quote(start-sign*.32,4.3)


@pytest.mark.parametrize('side', ['LONG','SHORT'])
@pytest.mark.parametrize('invalid', ['opposite','equal','missing','nan','infinite','zero'])
def test_ma_alignment_invalidates_ready_signal(side,invalid):
    f=candles(side);state={};prices=[89,88,88.5] if side=='LONG' else [111,112,111.5]
    for i,p in enumerate(prices):
        result=evaluate_outer_turn(f,p,side,state,'X',i)
    assert result[0]
    if invalid=='opposite': f.loc[2,'ma3']=99 if side=='LONG' else 101
    if invalid=='equal': f.loc[2,'ma3']=100
    if invalid=='missing': f=f.drop(columns='ma15')
    if invalid=='nan': f.loc[2,'ma15']=float('nan')
    if invalid=='infinite': f.loc[2,'ma3']=float('inf')
    if invalid=='zero': f.loc[2,'ma15']=0
    assert not evaluate_outer_turn(f,prices[-1],side,state,'X',3)[0]
    assert not state
    assert not evaluate_outer_turn(candles(side),prices[-1],side,state,'X',4)[0]


@pytest.mark.parametrize('side', ['LONG','SHORT'])
def test_only_closed_ma_alignment_is_used_no_new_cross_required(side):
    f=candles(side);state={}
    # Both closed rows already have the correct arrangement; live MAs are opposite.
    f.loc[3,'ma3']=1 if side=='LONG' else 999
    prices=[89,88,88.5] if side=='LONG' else [111,112,111.5]
    for i,p in enumerate(prices):
        result=evaluate_outer_turn(f,p,side,state,'X',i)
    assert result[0]


def test_production_revalidation_rejects_changed_closed_ma():
    import time
    engine=SimpleNamespace(account=SimpleNamespace(positions={},log=Mock()),_channel_entry_quote_times={})
    f=candles();state=observation_store(engine);now=time.monotonic()
    for i,p in enumerate([89,88,88.5]):
        evaluate_outer_turn(f,p,'LONG',state,'X',now-2+i)
    f.loc[2,'ma3']=99
    assert not TradingEngine._channel_intrabar_ready(engine,'X',f,88.5,'LONG')
    assert not state
