"""Current MA3/MA15 contract: production scan, snapshot, exits and transport."""
import asyncio
import copy
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pandas as pd
import pytest

from core.engine import TradingEngine
from core.services.entry_service import check_entry_signals, supported_entry_reason
from core.services.strategies.unified_entry_strategy import UnifiedEntryStrategy
from core.services.exits.entry_atr_protection import initialize_atr_protection, atr_exit_reason
from core.services.exits.profit_protection_service import ProfitProtectionExitStrategy
from core.services.symbol_runner import process_single_symbol_runner


def candles(side='LONG', body=1.):
    f=pd.DataFrame([dict(timestamp=60000*(i+1), open=100., close=100., high=105., low=95.,
                         ma3=100., ma15=100., atr=2., kc_middle=100., kc_upper=110., kc_lower=90.,
                         is_closed=i<3) for i in range(4)])
    f.loc[2,['open','close','ma3','ma15']]=[102.-body,102.,101.,100.]
    if side=='SHORT':
        for key in ('open','close','ma3','ma15'):
            f[key]=200-f[key]
    f.attrs['timeframe_ms']=60000
    return f


def position(side):
    p=dict(side=side,entry_price=100.,qty=1.,open_timestamp=180.,entry_mode='CHANNEL_SWING')
    initialize_atr_protection(p,100.,side,2.)
    return p


@pytest.mark.parametrize('side',['LONG','SHORT'])
@pytest.mark.parametrize('body,allowed',[(.999,False),(1.,True),(2.4,True)])
def test_closed_cross_body_threshold_inside_channel(side,body,allowed):
    f=candles(side,body)
    result=check_entry_signals(f,side,0)
    assert (result['action']=='ENTER') is allowed
    if allowed:
        assert result['entry_atr']==2
        assert result['bypass_flat_check'] is (body>=2.4)
        assert supported_entry_reason(result['reason'],side)
        engine=SimpleNamespace(account=SimpleNamespace(positions={},trades=[],last_closed_at={}))
        assert UnifiedEntryStrategy().evaluate_entry(f,100.,side,engine=engine,symbol='X')[0]


@pytest.mark.parametrize('side',['LONG','SHORT'])
@pytest.mark.parametrize('change',['no_cross','touch','wrong_color','not_above_both','live_only','nan','zero_atr'])
def test_reject_false_or_unclosed_cross(side,change):
    f=candles(side); sign=1 if side=='LONG' else -1
    if change=='no_cross': f.loc[1,'ma3']=100+sign
    if change=='touch': f.loc[2,'ma3']=f.loc[2,'ma15']
    if change=='wrong_color': f.loc[2,'open']=f.loc[2,'close']+sign
    if change=='not_above_both': f.loc[2,'ma3']=f.loc[2,'close']
    if change=='live_only': f.loc[2,'is_closed']=False
    if change=='nan': f.loc[2,'ma15']=float('nan')
    if change=='zero_atr': f.loc[2,'atr']=0
    assert check_entry_signals(f,side,0)['action']=='WAIT'


@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_long_body_only_bypasses_flat_check(side,monkeypatch):
    monkeypatch.setattr('core.services.swing_service.channel_terminal_market',lambda f:True)
    assert check_entry_signals(candles(side,1.),side,0)['reason']=='WAIT_FLAT_MARKET'
    assert check_entry_signals(candles(side,2.4),side,0)['action']=='ENTER'
    f=candles(side,2.4); f.loc[1,'ma3']=f.loc[2,'ma3']
    assert check_entry_signals(f,side,0)['action']=='WAIT'


@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_real_scan_and_fresh_snapshot_use_same_cross(side):
    async def run():
        f=candles(side); price=float(f.iloc[2]['close'])
        account=SimpleNamespace(positions={},position_meta={},trades=[],last_closed_at={},log=Mock(),channel_profit_reentries={})
        e=SimpleNamespace(account=account,_channel_exit_frames={},_last_exit_bar_id={},get_velocity_drop_ratio=Mock(return_value=0),
            _execute_confirmed_channel_break=AsyncMock(return_value=False),fetch_klines=AsyncMock(return_value=f),
            strategy=SimpleNamespace(compute_indicators=lambda f:f),tickers={'X':price})
        e._channel_intrabar_ready=lambda *a,**kw:TradingEngine._channel_intrabar_ready(e,*a,**kw)
        e._channel_candidate_bar_id=TradingEngine._channel_candidate_bar_id
        await process_single_symbol_runner(e,'X',0,None,False,exit_frame=f,exit_quote=price)
        e._execute_confirmed_channel_break.assert_awaited_once()
        snap=await TradingEngine._fresh_channel_entry_snapshot(e,'X',side,240000)
        assert snap and supported_entry_reason(snap['signal_code'],side)
        f.loc[2,'ma3']=f.loc[2,'ma15']
        assert await TradingEngine._fresh_channel_entry_snapshot(e,'X',side,240000) is None
    asyncio.run(run())


@pytest.mark.parametrize('side',['LONG','SHORT'])
@pytest.mark.parametrize('which',['TP','SL','KC'])
def test_exit_without_reverse_cross_and_retry_after_restart(side,which):
    p=position(side); f=candles(side); sign=1 if side=='LONG' else -1
    f.loc[2,'close']=100-sign*.1 if which=='KC' else 100+sign
    f.loc[2,'atr']=999. # Anchored ATR must not drift.
    price={'TP':100+sign*4,'SL':100-sign*3,'KC':100+sign*.5}[which]
    reason=ProfitProtectionExitStrategy().evaluate_exit(p,f,price)
    assert reason.startswith({'TP':'EXIT_TAKE_PROFIT','SL':'EXIT_STOP_LOSS','KC':'EXIT_KC_MIDDLE'}[which])
    assert atr_exit_reason(json.loads(json.dumps(p)),100.)==reason


@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_live_middle_touch_and_preentry_close_do_not_exit(side):
    f=candles(side); p=position(side); sign=1 if side=='LONG' else -1
    f.loc[2,'close']=100.; f.loc[3,'close']=100-sign
    assert ProfitProtectionExitStrategy().evaluate_exit(p,f,100.) is None
    f.loc[2,'close']=100-sign
    p['open_timestamp']=240.
    assert ProfitProtectionExitStrategy().evaluate_exit(p,f,100.) is None


@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_three_minute_close_time_and_tp_priority(side):
    f=candles(side); f.attrs['timeframe_ms']=180000
    f['timestamp']=[180000,360000,540000,720000]
    p=position(side); p['open_timestamp']=600.; sign=1 if side=='LONG' else -1
    f.loc[2,'close']=100-sign
    assert ProfitProtectionExitStrategy().evaluate_exit(copy.deepcopy(p),f,100.)=='EXIT_KC_MIDDLE_CLOSED'
    assert ProfitProtectionExitStrategy().evaluate_exit(p,f,100+sign*4).startswith('EXIT_TAKE_PROFIT')


@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_old_ma_peak_and_fixed_lock_states_do_not_exit(side):
    p=position(side); f=candles(side)
    p['channel_profit_protection']={'pending':True,'reason':'FIXED_NET_PROFIT_LOCK_EXIT','locked_net':8}
    p['channel_peak_abnormal']={'pending':True}
    assert ProfitProtectionExitStrategy().evaluate_exit(p,f,100.) is None


@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_testnet_native_stop_uses_actual_fill_atr(side,tmp_path,monkeypatch):
    import core.testnet_account as tm
    from test_testnet_account import FakeTestnetExchange
    monkeypatch.setattr(tm,'STATE_FILE',str(tmp_path/'account.json'))
    monkeypatch.setattr(tm,'DATA_DIR',str(tmp_path))
    monkeypatch.setattr(tm,'notify_email',lambda *a,**k:None)
    monkeypatch.setattr(tm,'ENABLE_EXCHANGE_INITIAL_STOP_LOSS',True)
    monkeypatch.setattr(tm,'DISABLE_TAKE_PROFIT',True)
    monkeypatch.setattr(tm.BinanceTestnetAccount,'credentials_configured',staticmethod(lambda:True))
    async def run():
        ex=FakeTestnetExchange(); account=tm.BinanceTestnetAccount(ex)
        await account.initialize()
        assert await account.open_position('DOGE/USDT',side,100.,10.,0.,0.,'cross',atr=2.,leverage=1,
                                            entry_context={'entry_mode':'CHANNEL_SWING'})
        p=account.positions['DOGE/USDT']; sign=1 if side=='LONG' else -1
        stops=[o for o in ex.orders if o['type']=='STOP_MARKET']
        assert float(stops[-1]['params']['triggerPrice'])==pytest.approx(p['entry_price']-sign*3)
        assert p['atr_tp']==pytest.approx(p['entry_price']+sign*4)
    asyncio.run(run())


@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_runner_middle_exit_persists_before_failed_market_order(side):
    async def run():
        p=position(side); f=candles(side); sign=1 if side=='LONG' else -1
        f.loc[2,'close']=100-sign
        account=SimpleNamespace(positions={'X':p},position_meta={},log=Mock(),save_state=Mock())
        async def reject(*args,**kwargs):
            assert account.position_meta['X']['channel_profit_protection']['pending']
            assert args[1]==100.5
            assert kwargs['is_limit'] is False
            return False
        account.close_position=AsyncMock(side_effect=reject)
        e=SimpleNamespace(account=account,_channel_exit_frames={},_last_exit_bar_id={},
            get_velocity_drop_ratio=Mock(return_value=0),_take_over_manual_position=Mock())
        await process_single_symbol_runner(e,'X',0,None,False,exit_frame=f,exit_quote=100.5,exit_only=True)
        account.close_position.assert_awaited_once()
        assert 'X' in account.positions
        restored=json.loads(json.dumps(account.position_meta['X']))
        assert restored['channel_profit_protection']['reason']=='EXIT_KC_MIDDLE_CLOSED'
        assert atr_exit_reason({**p,**restored},100.)=='EXIT_KC_MIDDLE_CLOSED'
    asyncio.run(run())


@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_reverse_cross_alone_and_profit_retreat_do_not_exit(side):
    p=position(side); f=candles(side); sign=1 if side=='LONG' else -1
    f.loc[2,'close']=100+sign
    f.loc[2,'ma3']=100-sign*2
    f.loc[2,'ma15']=100+sign*2
    strategy=ProfitProtectionExitStrategy()
    assert strategy.evaluate_exit(p,f,100+sign*3.9) is None
    assert strategy.evaluate_exit(p,f,100+sign*.1) is None


@pytest.mark.parametrize('side',['LONG','SHORT'])
@pytest.mark.parametrize('which',['TP','SL'])
def test_paper_account_market_exit_at_latest_price(side,which,tmp_path,monkeypatch):
    import core.paper_account as pm
    monkeypatch.setattr(pm,'STATE_FILE',str(tmp_path/'paper.json'))
    monkeypatch.setattr(pm,'DATA_DIR',str(tmp_path))
    monkeypatch.setattr(pm,'notify_email',lambda *a,**k:None)
    # Isolate strategy TP/SL from separately retained account loss limits.
    monkeypatch.setattr('core.config.MAX_ACCEPTABLE_LOSS_PCT',-1.)
    monkeypatch.setattr('core.config.MAX_POSITION_MARGIN_LOSS_RATIO',1.)
    async def run():
        a=pm.PaperAccount(); a.balance=1000.
        assert await a.open_position('DOGE/USDT',side,100.,10.,0.,0.,'cross',atr=2.,leverage=1,
                                     entry_context={'entry_mode':'CHANNEL_SWING'})
        p=a.positions['DOGE/USDT']; sign=1 if side=='LONG' else -1
        price=p['entry_price']+sign*(4.1 if which=='TP' else -3.1)
        await a.update_positions({'DOGE/USDT':price})
        assert 'DOGE/USDT' not in a.positions
        assert any(('EXIT_TAKE_PROFIT' if which=='TP' else 'EXIT_STOP_LOSS') in t.get('reason','') for t in a.trades)
    asyncio.run(run())
