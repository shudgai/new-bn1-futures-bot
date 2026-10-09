"""Production calls with isolated accounts/exchanges; never read or write live state."""
import asyncio
import copy
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pandas as pd
import pytest

from core import config
from core.engine import TradingEngine
from core.paper_account import PaperAccount
from core.testnet_account import BinanceTestnetAccount
from core.services.entry_contract import evaluate_entry_contract
from core.services.canonical_entry import evaluate_two_closed_kc_breakout
from core.services.entry_firewall import validate_account_entry
from core.services.symbol_runner import process_single_symbol_runner

PEPE = '1000PEPE/USDT'
LOBSTER = '龙虾/USDT'


def frame_for(side='LONG'):
    now = int(time.time()//60)*60000
    rows = []
    for i,(o,c,ma) in enumerate([(100.5,100.7,100.5),(100.9,101.2,100.7),
                                  (101.2,101.3,100.9),(101.3,101.4,101.1)]):
        rail_offset = i * .05
        rows.append(dict(timestamp=now-(3-i)*60000,open=o,close=c,high=max(o,c)+.01,
                         low=min(o,c)-.01,atr=1.,kc_upper=101.+rail_offset,
                         kc_middle=100.+rail_offset,kc_lower=99.+rail_offset,
                         ma5=ma,ma15=100.+rail_offset,ma3=ma,is_closed=i<3))
    f=pd.DataFrame(rows)
    if side=='SHORT':
        original=f.copy()
        for a,b in [('open','open'),('close','close'),('high','low'),('low','high'),
                    ('kc_upper','kc_lower'),('kc_lower','kc_upper'),('kc_middle','kc_middle'),
                    ('ma5','ma5'),('ma15','ma15'),('ma3','ma3')]: f[a]=200-original[b]
    f.attrs.update(timeframe_ms=60000,entry_finality_verified=True)
    return f


def held(side='LONG'):
    sign=1 if side=='LONG' else -1
    return dict(side=side,entry_price=100.,qty=1.,amount=100.,margin=100.,leverage=1.,
                open_timestamp=time.time()-180,entry_mode='CHANNEL_SWING',entry_atr=1.,
                initial_sl=100-sign*1.5,sl=100-sign*1.5,tp=0.,unrealized_pnl=0.)


@pytest.fixture(autouse=True)
def isolate(monkeypatch):
    monkeypatch.setattr(PaperAccount,'load_state',lambda self:None)
    monkeypatch.setattr(PaperAccount,'save_state',lambda self:None)
    monkeypatch.setattr(BinanceTestnetAccount,'_load_state',lambda self:None)
    monkeypatch.setattr(BinanceTestnetAccount,'save_state',lambda self:None)
    monkeypatch.setattr('core.services.entry_finality.READ_INTERVAL_SECONDS',0.)
    monkeypatch.setattr('core.services.pre_entry_space_shadow.record_pre_entry_space_shadow',lambda **kwargs:None)
    monkeypatch.setattr('core.services.reversal_shadow_logger.record_reversal_shadow_candidates',lambda *args:None)


def account_for(kind):
    if kind=='paper':
        a=PaperAccount();a.balance=1000.
    else:
        exchange=SimpleNamespace(create_order=AsyncMock(return_value={'id':'mock','status':'closed'}),
            request=AsyncMock(return_value={'algoId':'mock-stop'}),cancel_order=AsyncMock(),
            fetch_order=AsyncMock(return_value={'status':'canceled','filled':0.}),
            amount_to_precision=lambda symbol,q:str(q),price_to_precision=lambda symbol,p:str(p))
        a=BinanceTestnetAccount(exchange)
        a.refresh=AsyncMock(return_value=1000.)
    a.log=Mock()
    return a


def engine_for(account,symbol,frame):
    e=object.__new__(TradingEngine);e.account=account;e.is_running=True
    e.exchange=SimpleNamespace(fetch_time=AsyncMock(return_value=float(frame.iloc[-1].timestamp)+10000))
    e.tickers={symbol:float(frame.iloc[-1].close)}
    e.fetch_klines=AsyncMock(return_value=frame)
    e.strategy=SimpleNamespace(compute_indicators=lambda f:f)
    e.symbol_rotation=SimpleNamespace(get_dynamic_leverage=lambda *args:2)
    e._execution_price_is_safe=AsyncMock(return_value=True)
    return e


def test_actual_config_only_lobster_new_board():
    assert config.DEFAULT_SYMBOLS==[LOBSTER]
    assert PEPE in config.ENTRY_DISABLED_SYMBOLS
    assert not config.is_entry_disabled(LOBSTER)


@pytest.mark.parametrize('side',['LONG','SHORT'])
@pytest.mark.parametrize('phase',['FIRST','PYRAMID','POST_EXIT_REENTRY','CONTINUATION_REENTRY'])
def test_valid_pepe_signal_never_calls_open_position(monkeypatch,side,phase):
    # Even a stale/cached universe containing PEPE cannot bypass the entry denylist.
    monkeypatch.setattr('core.engine.DEFAULT_SYMBOLS',[PEPE,LOBSTER])
    f=frame_for(side)
    assert evaluate_two_closed_kc_breakout(f.iloc[1],f.iloc[2],side)=='TWO_CLOSED_KC_BREAKOUT_'+side
    d=evaluate_entry_contract(f,symbol=PEPE)
    assert d and d['side']==side  # Signal is genuinely valid; do not mock formation.
    a=account_for('paper');a.open_position=AsyncMock(return_value=True)
    if phase=='PYRAMID':a.positions[PEPE]=held(side)
    if phase in ('POST_EXIT_REENTRY','CONTINUATION_REENTRY'):
        a.trades=[dict(symbol=PEPE,action='CLOSE_'+side,id=float(f.iloc[0].timestamp))]
    e=engine_for(a,PEPE,f)
    before=copy.deepcopy(a.positions)
    signal=dict(entry_mode='CHANNEL_SWING',side=side,signal_code=d['type'],
                candidate_bar_id=d['confirmation_bar_id'],entry_phase=phase)
    assert not asyncio.run(e._place_structured_entry_locked(PEPE,signal,float(f.iloc[-1].close)))
    if phase!='PYRAMID':
        asyncio.run(process_single_symbol_runner(e,PEPE,time.time(),None,False,exit_frame=f))
    a.open_position.assert_not_awaited()
    assert a.positions==before


@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_lobster_actual_pipeline_opens(side):
    f=frame_for(side);a=account_for('paper');e=engine_for(a,LOBSTER,f)
    a.open_position=AsyncMock(wraps=a.open_position)
    asyncio.run(process_single_symbol_runner(e,LOBSTER,time.time(),None,False,exit_frame=f))
    a.open_position.assert_awaited_once()
    assert a.positions[LOBSTER]['side']==side
    assert len(a.trades)==1


@pytest.mark.parametrize('kind',['paper','testnet'])
@pytest.mark.parametrize('route',['market','limit','pyramid','reentry','continuation'])
def test_account_routes_cannot_add_pepe_exposure(kind,route):
    a=account_for(kind)
    if route=='pyramid':a.positions[PEPE]=held()
    before=copy.deepcopy(a.positions)
    context=dict(is_manual=True,manual_entry=True,source='MANUAL',dca_stage=2,
                 entry_phase=route,entry_mode='CHANNEL_SWING')
    async def attempt():
        if route=='limit':
            return await a.place_limit_entry(PEPE,'LONG',100.,20.,98.5,0.,'test',atr=1.,entry_context=context)
        return await a.open_position(PEPE,'LONG',100.,20.,98.5,0.,'test',atr=1.,entry_context=context)
    try:
        assert not asyncio.run(attempt())
    except ValueError as exc:
        assert 'ENTRY_DISABLED_SYMBOL' in str(exc)
    assert a.positions==before
    assert not a.pending_limit_orders
    assert not a.trades
    if kind=='testnet':a._raw_create_order.assert_not_awaited()


@pytest.mark.parametrize('symbol',[PEPE,'1000PEPEUSDT',PEPE+':USDT','1000pepe/usdt'])
@pytest.mark.parametrize('context',[{}, {'manual_entry':True}, {'source':'MANUAL'},
                                   {'entry_snapshot':{'approved':True}}, {'dca_stage':3}])
def test_firewall_aliases_manual_cache_and_dca_cannot_bypass(symbol,context):
    a=SimpleNamespace(entry_frame_provider=AsyncMock())
    with pytest.raises(ValueError,match='ENTRY_DISABLED_SYMBOL'):
        asyncio.run(validate_account_entry(a,symbol,'LONG',context))
    a.entry_frame_provider.assert_not_awaited()


@pytest.mark.parametrize('route',['send','exchange'])
@pytest.mark.parametrize('reduce_params',[{}, {'reduceOnly':False}, {'reduceOnly':True}, {'closePosition':'true'}])
def test_testnet_physical_order_boundary_only_allows_reduction(route,reduce_params):
    a=account_for('testnet')
    context={'manual_entry':True}
    async def attempt():
        if route=='send':
            return await a._send_order(PEPE,'market','sell',1.,params=reduce_params,entry_context=context)
        return await a.exchange.create_order(PEPE,'market','buy',1.,None,
                                            dict(reduce_params,_entry_context=context))
    if any(reduce_params.values()):
        asyncio.run(attempt());a._raw_create_order.assert_awaited_once()
    else:
        with pytest.raises(ValueError,match='ENTRY_DISABLED_SYMBOL'):asyncio.run(attempt())
        a._raw_create_order.assert_not_awaited()


@pytest.mark.parametrize('kind',['paper','testnet'])
def test_pending_pepe_entry_is_cancelled_without_closing_position(kind):
    a=account_for(kind);a.positions[PEPE]=held();before=copy.deepcopy(a.positions)
    a.pending_limit_orders[PEPE]=dict(order_id='pending-entry',side='LONG',qty=1.,target_price=100.)
    a.open_position=AsyncMock();a.close_position=AsyncMock()
    asyncio.run(a.check_pending_limit_orders())
    assert not a.pending_limit_orders
    assert a.positions==before
    a.open_position.assert_not_awaited();a.close_position.assert_not_awaited()
    if kind=='testnet':
        a.exchange.cancel_order.assert_awaited_once_with('pending-entry',PEPE)
        a._raw_create_order.assert_not_awaited()


def test_cancel_race_partial_fill_is_still_managed():
    a=account_for('testnet');a.positions[PEPE]=held()
    a.pending_limit_orders[PEPE]=dict(order_id='pending-entry',side='LONG',qty=1.,target_price=100.,
        sl=98.5,tp=0.,reason='old-entry',atr=1.,leverage=1,signal_score=100,amount_usdt=100.)
    a.exchange.fetch_order.return_value={'status':'canceled','filled':.2,'average':100.}
    a._finalize_new_position=AsyncMock()
    asyncio.run(a.check_pending_limit_orders())
    a._finalize_new_position.assert_awaited_once()
    assert a._finalize_new_position.await_args.args[3]==.2
    a._raw_create_order.assert_not_awaited()


@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_existing_paper_position_valuation_sl_and_hard_stop_execute(side):
    a=account_for('paper');a.positions[PEPE]=held(side);a.close_position=AsyncMock(return_value=True)
    sign=1 if side=='LONG' else -1
    asyncio.run(a.update_positions({PEPE:100+sign*.1}))
    assert a.positions[PEPE]['mark_price']==100+sign*.1
    a.close_position.assert_not_awaited()
    assert asyncio.run(a.trail_stop_loss(PEPE,100-sign*1.4,False))
    asyncio.run(a.update_positions({PEPE:100-sign*10.}))
    a.close_position.assert_awaited_once()
    assert 'HARD_STOP' in a.close_position.await_args.args[2]


def test_existing_testnet_update_and_native_sl_still_execute():
    a=account_for('testnet');a.positions[PEPE]=held()
    asyncio.run(a.update_positions({PEPE:100.1}))
    a.refresh.assert_awaited_once() # CHANNEL_SWING is valued by refresh; ticker exits are intentionally skipped.
    asyncio.run(a._create_protection_order(PEPE,'sell','STOP_MARKET',1.,98.5))
    assert a.exchange.request.await_args.args[3]['reduceOnly']=='true'


@pytest.mark.parametrize('kind',['paper','testnet'])
def test_existing_pepe_scan_reaches_actual_hard_stop(kind):
    a=account_for(kind);a.positions[PEPE]=held();a.close_position=AsyncMock(return_value=True)
    f=frame_for();e=engine_for(a,PEPE,f)
    symbols=TradingEngine._entry_scan_symbol_snapshot(config.DEFAULT_SYMBOLS,config.DEFAULT_SYMBOLS,
                                                     a.positions,{},True,2)
    assert PEPE in symbols
    asyncio.run(process_single_symbol_runner(e,PEPE,time.time(),None,False,exit_frame=f,exit_quote=90.))
    a.close_position.assert_awaited_once()
    assert 'HARD_STOP' in a.close_position.await_args.args[2]


def test_cancel_failure_keeps_order_tracked_and_does_not_submit():
    a=account_for('testnet');a.positions[PEPE]=held()
    a.pending_limit_orders[PEPE]=dict(order_id='pending-entry',side='LONG',qty=1.,target_price=100.)
    a.exchange.cancel_order.side_effect=RuntimeError('simulated exchange timeout')
    asyncio.run(a.check_pending_limit_orders())
    assert PEPE in a.pending_limit_orders
    assert PEPE in a.positions
    a._raw_create_order.assert_not_awaited()


def test_environment_cannot_reenable_pepe():
    import os,subprocess,sys,json
    env=dict(os.environ,ENTRY_DISABLED_SYMBOLS='',DEFAULT_SYMBOLS=PEPE+','+LOBSTER)
    result=subprocess.check_output([sys.executable,'-c',
        'import json; from core.config import DEFAULT_SYMBOLS,is_entry_disabled; '
        'print(json.dumps([DEFAULT_SYMBOLS,is_entry_disabled("1000PEPEUSDT")]))'],env=env,text=True)
    assert json.loads(result.strip())==[[LOBSTER],True]
