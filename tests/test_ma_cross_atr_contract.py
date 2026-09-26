"""Active MA entry and one-minute chandelier contract, with offline transport."""
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
from core.services.exit_service import STOP_REASON, MIDDLE_REASON


@pytest.fixture(autouse=True)
def isolated_clock_and_logs(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    (tmp_path / 'data').mkdir()
    monkeypatch.setattr('core.services.exit_service.time.time', lambda: 421.)


def candles(side='LONG', body=1.):
    f = pd.DataFrame([dict(timestamp=60000*(i+1), open=100., close=100.,
                          high=103., low=99., ma3=100., ma15=100., atr=2.,
                          kc_middle=100., kc_upper=110., kc_lower=90.,
                          is_closed=i<6) for i in range(7)])
    f.loc[5, ['open','close','ma3']] = [102.-body,102.,101.]
    if side == 'SHORT':
        original = f.copy()
        for a,b in [('open','open'),('close','close'),('high','low'),('low','high'),
                    ('ma3','ma3'),('ma15','ma15')]:
            f[a] = 200-original[b]
    f.attrs['timeframe_ms'] = 60000
    return f


def position(side):
    p = dict(side=side, entry_price=100., qty=.1, open_timestamp=360., entry_mode='CHANNEL_SWING')
    initialize_atr_protection(p,100.,side,2.)
    return p


def exit_frame(side, opened=101., close=101., high=102., low=100.):
    f = pd.DataFrame([dict(timestamp=360000,open=opened,close=close,high=high,low=low,
                          atr=2.,kc_middle=100.,ma3=101.,ma15=100.,is_closed=True)])
    if side == 'SHORT':
        original = f.copy()
        for a,b in [('open','open'),('close','close'),('high','low'),('low','high'),
                    ('ma3','ma3'),('ma15','ma15')]:
            f[a] = 200-original[b]
    f.attrs['timeframe_ms'] = 60000
    return f


@pytest.mark.parametrize('side',['LONG','SHORT'])
@pytest.mark.parametrize('body',[.999,1.,2.4])
def test_closed_cross_has_no_retired_half_atr_body_floor(side,body):
    f = candles(side,body)
    result = check_entry_signals(f,side,0)
    assert result['action'] == 'ENTER'
    assert result['entry_atr'] == 2.
    assert result['bypass_flat_check'] is True
    assert supported_entry_reason(result['reason'],side)
    assert UnifiedEntryStrategy().evaluate_entry(f,102. if side=='LONG' else 98.,side)[0]


@pytest.mark.parametrize('side',['LONG','SHORT'])
@pytest.mark.parametrize('change',['no_cross','touch','wrong_color','live_only','nan','zero_atr','infinite_atr','missing_ma'])
def test_reject_false_unclosed_or_invalid_cross(side,change):
    f = candles(side); sign = 1 if side=='LONG' else -1
    if change=='no_cross': f.loc[4,'ma3']=100+sign
    if change=='touch': f.loc[5,'ma3']=f.loc[5,'ma15']
    if change=='wrong_color': f.loc[5,'open']=f.loc[5,'close']+sign
    if change=='live_only': f.loc[5,'is_closed']=False
    if change=='nan': f.loc[5,'ma15']=float('nan')
    if change=='zero_atr': f.loc[5,'atr']=0.
    if change=='infinite_atr': f.loc[5,'atr']=float('inf')
    if change=='missing_ma': f=f.drop(columns='ma15')
    assert check_entry_signals(f,side,0)['action']=='WAIT'


@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_cross_bypasses_retired_flat_filter_but_requires_cross(side,monkeypatch):
    monkeypatch.setattr('core.services.swing_service.channel_terminal_market',lambda f:True)
    assert check_entry_signals(candles(side,1.),side,0)['action']=='ENTER'
    f=candles(side); f.loc[4,'ma3']=f.loc[5,'ma3']
    assert check_entry_signals(f,side,0)['action']=='WAIT'


@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_real_scan_and_fresh_snapshot_use_same_cross(side):
    async def run():
        f=candles(side); price=float(f.iloc[5]['close'])
        account=SimpleNamespace(positions={},position_meta={},trades=[],last_closed_at={},log=Mock(),channel_profit_reentries={})
        e=SimpleNamespace(account=account,_channel_exit_frames={},_last_exit_bar_id={},get_velocity_drop_ratio=Mock(return_value=0),
            _execute_confirmed_channel_break=AsyncMock(return_value=False),fetch_klines=AsyncMock(return_value=f),
            strategy=SimpleNamespace(compute_indicators=lambda f:f),tickers={'X':price})
        e._channel_intrabar_ready=lambda *a,**kw:TradingEngine._channel_intrabar_ready(e,*a,**kw)
        e._channel_candidate_bar_id=TradingEngine._channel_candidate_bar_id
        await process_single_symbol_runner(e,'X',0,None,False,exit_frame=f,exit_quote=price)
        e._execute_confirmed_channel_break.assert_awaited_once()
        snap=await TradingEngine._fresh_channel_entry_snapshot(e,'X',side,420000)
        assert snap and supported_entry_reason(snap['signal_code'],side)
        f.loc[5,'ma3']=f.loc[5,'ma15']
        assert await TradingEngine._fresh_channel_entry_snapshot(e,'X',side,420000) is None
    asyncio.run(run())


@pytest.mark.parametrize('side',['LONG','SHORT'])
@pytest.mark.parametrize('which',['TRAIL','SL','KC'])
def test_closed_exit_and_retry_after_restart(side,which):
    p=position(side)
    f = {'TRAIL':lambda:exit_frame(side,105.,103.,107.,102.),
         'SL':lambda:exit_frame(side,100.,96.,100.,95.),
         'KC':lambda:exit_frame(side,100.5,99.5,101.,99.)}[which]()
    expected = MIDDLE_REASON if which=='KC' else STOP_REASON
    assert ProfitProtectionExitStrategy().evaluate_exit(p,f,100.) == expected
    restored=json.loads(json.dumps(p))
    assert atr_exit_reason(restored,100.) == expected
    assert restored['chandelier_state']['pending']


@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_live_middle_touch_and_preentry_close_do_not_exit(side):
    f=exit_frame(side,101.,100.,101.,99.); p=position(side)
    assert ProfitProtectionExitStrategy().evaluate_exit(p,f,100.) is None
    f=exit_frame(side,100.5,99.5,101.,99.); f['is_closed']=False
    assert ProfitProtectionExitStrategy().evaluate_exit(position(side),f,100.) is None
    f['is_closed']=True; p=position(side); p['open_timestamp']=420.
    assert ProfitProtectionExitStrategy().evaluate_exit(p,f,100.) is None


@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_three_minute_frame_cannot_authorize_one_minute_exit(side):
    f=exit_frame(side,100.5,99.5,101.,99.); f.attrs['timeframe_ms']=180000
    p=position(side); sign=1 if side=='LONG' else -1
    assert ProfitProtectionExitStrategy().evaluate_exit(p,f,100+sign*4) is None
    assert p['atr_tp']==p['tp']==0.


@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_old_ma_peak_and_fixed_lock_states_do_not_exit(side):
    p=position(side)
    p['channel_profit_protection']={'pending':True,'reason':'FIXED_NET_PROFIT_LOCK_EXIT','locked_net':8}
    p['channel_peak_abnormal']={'pending':True}
    assert ProfitProtectionExitStrategy().evaluate_exit(p,exit_frame(side),100.) is None


@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_testnet_native_stop_uses_actual_fill_atr_without_tp(side,tmp_path,monkeypatch):
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
        assert p['atr_tp']==p['tp']==0.
        assert not any(o['type']=='TAKE_PROFIT_MARKET' for o in ex.orders)
    asyncio.run(run())


@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_runner_middle_exit_persists_before_failed_market_order(side):
    async def run():
        p=position(side); f=exit_frame(side,100.5,99.5,101.,99.)
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
        assert restored['channel_profit_protection']['reason']==MIDDLE_REASON
        assert atr_exit_reason({**p,**restored},100.)==MIDDLE_REASON
    asyncio.run(run())


@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_reverse_cross_and_intrabar_retreat_do_not_exit(side):
    p=position(side); f=exit_frame(side); sign=1 if side=='LONG' else -1
    f['ma3']=100-sign*2; f['ma15']=100+sign*2
    strategy=ProfitProtectionExitStrategy()
    assert strategy.evaluate_exit(p,f,100+sign*6) is None
    stop=p['atr_sl']
    assert strategy.evaluate_exit(p,f,100+sign*.1) is None
    assert p['atr_sl']==stop


@pytest.mark.parametrize('side',['LONG','SHORT'])
@pytest.mark.parametrize('which',['TRAIL','SL'])
def test_paper_account_retries_closed_signal_at_latest_price(side,which,tmp_path,monkeypatch):
    import core.paper_account as pm
    monkeypatch.setattr(pm,'STATE_FILE',str(tmp_path/'paper.json'))
    monkeypatch.setattr(pm,'DATA_DIR',str(tmp_path))
    monkeypatch.setattr('core.config.MAX_ACCEPTABLE_LOSS_PCT',-1.)
    monkeypatch.setattr('core.config.MAX_POSITION_MARGIN_LOSS_RATIO',1.)
    async def run():
        a=pm.PaperAccount(); a.balance=1000.
        assert await a.open_position('DOGE/USDT',side,100.,10.,0.,0.,'cross',atr=2.,leverage=1,
                                     entry_context={'entry_mode':'CHANNEL_SWING'})
        p=a.positions['DOGE/USDT']; p['open_timestamp']=360.
        f=exit_frame(side,105.,103.,107.,102.) if which=='TRAIL' else exit_frame(side,100.,96.,100.,95.)
        assert ProfitProtectionExitStrategy().evaluate_exit(p,f,100.)==STOP_REASON
        await a.update_positions({'DOGE/USDT':100.})
        assert 'DOGE/USDT' not in a.positions
        assert any(STOP_REASON in t.get('reason','') for t in a.trades)
    asyncio.run(run())


@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_trailing_line_stays_tight_when_atr_expands(side,monkeypatch):
    p=position(side); sign=1 if side=='LONG' else -1
    f=exit_frame(side,104.,106.,107.,103.)
    assert ProfitProtectionExitStrategy().evaluate_exit(p,f,100+sign*6) is None
    assert p['atr_sl']==pytest.approx(100+sign*4)
    assert p['atr_tp']==p['tp']==0.
    f=exit_frame(side,106.,105.,106.,104.)
    f['timestamp']=420000; f['atr']=4.
    monkeypatch.setattr('core.services.exit_service.time.time',lambda:481.)
    assert ProfitProtectionExitStrategy().evaluate_exit(p,f,100+sign*5) is None
    assert p['atr_sl']==pytest.approx(100+sign*4)
    f=exit_frame(side,105.,103.5,105.,103.)
    f['timestamp']=480000; f['atr']=4.
    monkeypatch.setattr('core.services.exit_service.time.time',lambda:541.)
    assert ProfitProtectionExitStrategy().evaluate_exit(p,f,100+sign*3.5)==STOP_REASON
