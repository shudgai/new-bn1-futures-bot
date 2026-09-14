"""Channel Swing entries no longer use structural profit-room estimates."""
from unittest.mock import AsyncMock
import pytest
from core.services.entry_room_service import entry_room
from core.services.strategies.outer_strategy import aligned_entry_ready
from channel_test_frames import closed_outer_entry_frame
from test_channel_swing_execution import _execution_engine, SYMBOL

@pytest.fixture(autouse=True)
def _profit_room_enabled(_pin_strategy_switches, monkeypatch):
    """本檔專門驗證淨利空間；其他測試預設關閉以免被合成框架的目標不足擋下。"""
    monkeypatch.setattr("core.config.CHANNEL_PROFIT_ROOM_ENABLED", True)


@pytest.fixture
def anyio_backend(): return 'asyncio'

@pytest.mark.anyio
@pytest.mark.parametrize('side',['LONG','SHORT'])
@pytest.mark.parametrize('cached',[False,True])
@pytest.mark.parametrize('reentry',[False,True])
@pytest.mark.parametrize('target',['far','near','missing'])
@pytest.mark.parametrize('scale',[1., .00003])
async def test_all_routes_ignore_profit_room(side,cached,reentry,target,scale,monkeypatch):
    f=closed_outer_entry_frame(side);price=float(f.iloc[-1]['close'])
    sign=1 if side=='LONG' else -1
    rail='high' if side=='LONG' else 'low'
    if target!='missing': f.loc[f.index[5],rail]=price+sign*(3. if target=='far' else .01)
    for column in ('open','high','low','close','kc_upper','kc_lower','kc_middle','ema_20','ma3','ma15','atr'):
        f[column] *= scale
    price *= scale
    assert aligned_entry_ready(f,price,side)
    e=_execution_engine(f,side,True);e.account.positions.clear();e.account.save_state=lambda:None
    e._channel_chop_state = lambda _: {"detected": False, "clear_direction": None}
    e.tickers[SYMBOL]=price;e._abnormal_market_entry_allowed=lambda *a,**k:True
    snapshot=dict(frame=f,price=price,kc_upper=float(f.iloc[-1]['kc_upper']),kc_lower=float(f.iloc[-1]['kc_lower']))
    e._fresh_channel_entry_snapshot=AsyncMock(return_value=snapshot)
    monkeypatch.setattr('core.engine.DEFAULT_SYMBOLS',[SYMBOL])
    signal=dict(side=side,score=100,entry_mode='CHANNEL_SWING',action='ENTER_MARKET',reason='room validation',
                profit_room_pct=99.,estimated_profit_target=999.)
    if reentry:signal['profit_reentry_token']='old-profit'
    result=await e._place_structured_entry(SYMBOL,signal,price,channel_snapshot=snapshot if cached else None)
    assert result, e.account.logs
    assert len(e.account.events) == 1
    assert signal['profit_room_checked'] is False
    assert 'estimated_profit_target' not in signal
    assert 'profit_room_pct' not in signal

@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_live_extreme_cannot_supply_target(side):
    f=closed_outer_entry_frame(side);price=float(f.iloc[-1]['close'])
    f.loc[f.index[-1],'high' if side=='LONG' else 'low']=150. if side=='LONG' else 50.
    r=entry_room(f,price,side,.0005,.0001,.0015)
    assert not r['allowed'] and r['reason']=='KC_PROFIT_TARGET_UNAVAILABLE'


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_pepe_diagnostics_reports_one_hour_direction_before_entry_details(side):
    from core.services.entry_diagnostics_service import entry_diagnostics
    from core.engine import TradingEngine
    from types import SimpleNamespace
    f = closed_outer_entry_frame(side)
    for col in ('open','high','low','close','kc_upper','kc_lower','kc_middle','ema_20','ma3','ma15','atr'):
        f[col] *= .00003
    f['timestamp'] = [i * 60000. for i in range(len(f))]
    now = float(f.iloc[-1]['timestamp']) / 1000 + 1
    price = float(f.iloc[-1]['close'])
    e = TradingEngine.__new__(TradingEngine)
    e.is_running = True
    e.account = SimpleNamespace(positions={}, channel_profit_reentries={})
    e._channel_entry_quote_times = {'1000PEPE/USDT': now}
    e._channel_candle_entry_blocked = lambda *a: False
    e._channel_candidate_bar_id = lambda *a: 1
    e.st_direction_1h_cache = {'1000PEPE/USDT': -1 if side == 'LONG' else 1}
    before = f.copy(deep=True)
    d = entry_diagnostics(e, '1000PEPE/USDT', f, price, now)
    assert d['reason'] == 'KC_1H_DIRECTION_WAIT'
    assert d['message'] == '1H方向不同，暫不開倉'
    assert f.equals(before)


@pytest.mark.anyio
@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
async def test_real_snapshot_room_failure_then_recovery(side, monkeypatch):
    from test_channel_entry_recovery import ready
    f, price = ready(side)
    sign = 1 if side == 'LONG' else -1
    f.loc[f.index[-1], 'open'] = price - sign * .05
    key = 'high' if side == 'LONG' else 'low'
    f.loc[f.index[5], key] = price + sign * .01
    e = _execution_engine(f, side, True)
    e.account.positions.clear()
    e.account.save_state = lambda: None
    e._channel_chop_state = lambda _: {"detected": False, "clear_direction": None}
    del e._channel_intrabar_ready
    e._abnormal_market_entry_allowed = lambda *a, **kw: True
    now = float(f.iloc[-1]['timestamp']) / 1000 + 1
    monkeypatch.setattr('core.engine.time.time', lambda: now)
    monkeypatch.setattr('core.engine.DEFAULT_SYMBOLS', [SYMBOL])
    e.tickers[SYMBOL] = price
    e._observe_channel_entry_quote(SYMBOL, price, now * 1000)
    signal = dict(side=side, entry_mode='CHANNEL_SWING', action='ENTER_MARKET', reason='room recovery')
    assert not await e._place_structured_entry(SYMBOL, signal, price)
    assert not getattr(e, '_channel_invalid_entry_candidates', set())
    assert e.account.logs or not e.account.events
    f.loc[f.index[5], key] = price + sign * 3.
    assert await e._place_structured_entry(SYMBOL, signal, price), e.account.logs
    assert len(e.account.events) == 1
