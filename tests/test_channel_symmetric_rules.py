"""Paired regressions for the user-authorized LONG/SHORT rules."""
import pytest
from unittest.mock import AsyncMock
from core.engine import TradingEngine
from test_channel_late_entry_room import phase_frame
from core.services.exits.profit_protection_service import directional_entry_ready, reentry_gate
from test_channel_swing_execution import _execution_engine, _narrow_channel_frame, SYMBOL

@pytest.fixture(autouse=True)
def _profit_room_enabled(_pin_strategy_switches, monkeypatch):
    """本檔專門驗證淨利空間；其他測試預設關閉以免被合成框架的目標不足擋下。"""
    monkeypatch.setattr("core.config.CHANNEL_PROFIT_ROOM_ENABLED", True)


@pytest.fixture
def anyio_backend():
    return 'asyncio'


def market(side):
    f = _narrow_channel_frame()
    f['kc_upper'], f['kc_lower'], f['atr'] = 102., 98., 2.
    f.loc[16:18, 'ma15'] = [99., 100., 101.]
    f['kc_middle'] = 100.
    f.loc[16:18, 'kc_middle'] = [98.8, 99.4, 100.]
    f.loc[17, ['open','close','high','low']] = [101.,103.,103.1,100.9]
    f.loc[18, ['open','close','high','low']] = [103.,104.,104.1,102.9]
    f.loc[19, ['open','close','high','low']] = [104.,104.5,104.6,103.9]
    if side == 'SHORT':
        original = f.copy()
        for key in ('open','close','ma3','ma15','kc_middle'):
            f[key] = 200. - original[key]
        f['high'], f['low'] = 200.-original['low'], 200.-original['high']
        f['kc_upper'], f['kc_lower'] = 200.-original['kc_lower'], 200.-original['kc_upper']
    return f

@pytest.mark.parametrize('side', ['LONG','SHORT'])
@pytest.mark.parametrize('bad', ['none','one_closed','doji_first','doji_second','opposite_live','slope'])
def test_outer_reentry_two_closed_bodies_and_live_direction(side,bad):
    f = market(side); price = float(f.iloc[-1]['close'])
    sign = 1 if side == 'LONG' else -1
    if bad == 'one_closed':
        f.loc[17,['open','close','high','low']] = [100.,100.,100.1,99.9]
    elif bad == 'doji_first':
        f.loc[17,'open'] = f.loc[17,'close']
    elif bad == 'doji_second':
        f.loc[18,'open'] = f.loc[18,'close']
    elif bad == 'opposite_live':
        f.loc[19,'open'] = price + sign
    elif bad == 'slope':
        rail = 'kc_upper' if side == 'LONG' else 'kc_lower'
        f.loc[17:19,rail] = [float(f.loc[19,rail])+sign*.2, float(f.loc[19,rail])+sign*.1, float(f.loc[19,rail])]
    result = TradingEngine._channel_swing_action(f,price, outer_entry_only=True)
    assert (result['action'] == 'ENTER') is (bad == 'none'),result
    if bad == 'none':
        assert result['side'] == side

@pytest.mark.parametrize('side', ['LONG','SHORT'])
@pytest.mark.parametrize('bad', ['none','no_pullback','old_break','one_closed','slope'])
def test_reentry_requires_new_two_closed_break_after_pullback(side,bad):
    f=market(side); price=float(f.iloc[-1]['close'])
    ticket={'side':side,'pulled_back_inside':True,'pullback_bar':17.}
    if bad=='no_pullback': ticket={'side':side}
    elif bad=='old_break': ticket['pullback_bar']=18.
    elif bad=='one_closed': f.loc[18,'open']=f.loc[18,'close']
    elif bad=='slope':
        rail='kc_upper' if side=='LONG' else 'kc_lower'; sign=1 if side=='LONG' else -1
        f.loc[17,rail]+=sign*.2
    assert (reentry_gate(ticket,f,price)=='ready') is (bad=='none')

@pytest.mark.parametrize('side', ['LONG','SHORT'])
@pytest.mark.parametrize('chased', [False,True])
def test_directional_profit_room(side,chased,monkeypatch):
    monkeypatch.setattr('core.engine.NET_PROFIT_GUARANTEE_BUFFER', .006)
    f=phase_frame(side); sign=1 if side=='LONG' else -1
    price=float(f.iloc[-1]['close'])+sign*(4.1 if chased else 0.)
    r=TradingEngine._channel_profit_room(f,price,side)
    assert r['allowed'] is (not chased),r

@pytest.mark.parametrize('side', ['LONG','SHORT'])
def test_energy_decline_alone_does_not_block_developing_move(side,monkeypatch):
    monkeypatch.setattr(TradingEngine,'_channel_held_momentum_is_declining',staticmethod(lambda f,s: s==side))
    f=phase_frame(side,late=False)
    r=TradingEngine._channel_profit_room(f,float(f.iloc[-1]['close']),side)
    assert r['allowed'] and not r['checked']
    assert r['reason']=='KC_TREND_ROOM_SKIPPED'

@pytest.mark.parametrize('side', ['LONG','SHORT'])
def test_adverse_long_body_exits_before_middle(side):
    f=market(side); sign=1 if side=='LONG' else -1
    f.loc[:18,'open']=100.; f.loc[:18,'close']=100.2
    f.loc[19,'open']=100.+sign*4.
    result=TradingEngine._channel_swing_action(f,100.+sign*.7,side)
    assert result['action']=='EXIT',result
    assert result['reason']==('KC_LONG_LIVE_RED_LONG_EXIT' if side=='LONG' else 'KC_SHORT_LIVE_GREEN_LONG_EXIT')

@pytest.mark.anyio
@pytest.mark.parametrize('side', ['LONG','SHORT'])
@pytest.mark.parametrize('success',[True,False])
async def test_real_losing_position_middle_and_retry(side,success):
    f=market(side); f.loc[19,'open']=100.; sign=1 if side=='LONG' else -1
    e=_execution_engine(f,side,success); e.account.save_state=lambda:None
    e.account.positions[SYMBOL].update(entry_price=100.+sign*5.,qty=1.,open_timestamp=1.)
    e.tickers[SYMBOL]=100.
    await e._process_single_symbol(SYMBOL,2.,None,False)
    assert len(e.account.events)==1,e.account.logs
    assert e.account.events[0][3].endswith(f'KC_{side}_UNARMED_MIDDLE_EXIT')
    assert (SYMBOL in e.account.positions) is (not success)
    if not success:
        await e._process_single_symbol(SYMBOL,3.,None,False)
        assert len(e.account.events)==2

@pytest.mark.anyio
@pytest.mark.parametrize('side',['LONG','SHORT'])
@pytest.mark.parametrize('token',[None,'reopen'])
async def test_final_order_rechecks_room_for_both(side,token,monkeypatch):
    f=phase_frame(side,target=105.9); price=float(f.iloc[-1]['close'])
    e=_execution_engine(f,side,True); e.account.positions.clear()
    monkeypatch.setattr('core.engine.DEFAULT_SYMBOLS',[SYMBOL])
    e._fresh_channel_entry_snapshot=AsyncMock(return_value={'price':price,'kc_upper':102.,'kc_lower':98.,'frame':f})
    signal={'side':side,'entry_mode':'CHANNEL_SWING','action':'ENTER_MARKET','profit_reentry_token':token}
    assert not await e._place_structured_entry(SYMBOL,signal,price)
    assert not e.account.events
    assert any('KC_PROFIT_ROOM_INSUFFICIENT' in m for m,_ in e.account.logs)


@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_nearby_support_or_resistance_caps_room(side):
    f=phase_frame(side,target=105.9)
    result=TradingEngine._channel_profit_room(f,float(f.iloc[-1]['close']),side)
    assert result['target']==pytest.approx(105.9 if side=='LONG' else 94.1)
    assert not result['allowed']


@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_middle_slope_alone_blocks(side):
    f=market(side); sign=1 if side=='LONG' else -1
    f['kc_middle']=100.
    f.loc[17:19,'kc_middle']=[100.+sign*.2,100.+sign*.1,100.]
    assert not directional_entry_ready(f,float(f.iloc[-1]['close']),side)
