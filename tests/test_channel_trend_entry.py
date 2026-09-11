"""Confirmed trend entries do not depend on KC rail location or MA3."""
import pytest
from core.channel_outer_entry import aligned_entry, entry_trend_direction, ck_direction
from core.engine import TradingEngine
from channel_test_frames import closed_outer_entry_frame
from test_channel_swing_execution import _execution_engine, SYMBOL

@pytest.fixture
def anyio_backend(): return 'asyncio'

def trend_frame(side, location='inside', scale=1.):
    f = closed_outer_entry_frame(side)
    sign = 1 if side == 'LONG' else -1
    price = {'inside':100., 'upper':102.1, 'lower':98., 'above':104., 'below':96.}[location]
    # Weakening, yet still positive directional CK movement.
    f.loc[f.index[-5:-1], 'kc_middle'] = [100 + sign * x for x in (0., .3, .5, .6)]
    # The directional outer rail moves against the middle trend.
    rail = 'kc_upper' if side == 'LONG' else 'kc_lower'
    f.loc[f.index[-3], rail] = float(f.iloc[-2][rail]) + sign * .2
    # Both MA3 and live body can be adverse without an abnormal live body.
    f.loc[f.index[-4], 'close'] = price + sign * .2
    f.loc[f.index[-1], ['open','close']] = [price + sign * .05, price]
    f['high'] = f[['open','close']].max(axis=1) + .1
    f['low'] = f[['open','close']].min(axis=1) - .1
    f.loc[f.index[5], 'high' if side == 'LONG' else 'low'] = 110. if side == 'LONG' else 90.
    f['timestamp'] = [(i+1)*60000. for i in range(len(f))]
    for col in ('open','high','low','close','kc_upper','kc_lower','kc_middle','ema_20','ma3','ma15','atr'):
        f[col] *= scale
    return f, price * scale

@pytest.mark.parametrize('side', ['LONG','SHORT'])
@pytest.mark.parametrize('location', ['inside','upper','lower','above','below'])
@pytest.mark.parametrize('scale', [1., .00003])
def test_trend_ignores_rail_position_and_old_direction_filters(side, location, scale):
    f, price = trend_frame(side, location, scale)
    assert entry_trend_direction(f) == side
    assert ck_direction(f) != side  # Exit direction helper retains its old contract.
    assert aligned_entry(f, price) == dict(action='ENTER', side=side, reason='KC_TREND_'+side)
    assert TradingEngine._channel_swing_action(f, price)['side'] == side
    f.loc[f.index[-1], 'kc_middle'] = float('nan')
    assert entry_trend_direction(f) == side  # Never use the forming CK value.

@pytest.mark.parametrize('side', ['LONG','SHORT'])
@pytest.mark.parametrize('invalid', ['flat','nan','zero'])
def test_flat_or_invalid_confirmed_trend_waits(side, invalid):
    f, price = trend_frame(side)
    f.loc[f.index[-2], 'kc_middle'] = {'flat':float(f.iloc[-3]['kc_middle']), 'nan':float('nan'), 'zero':0.}[invalid]
    assert aligned_entry(f, price)['action'] == 'WAIT'

@pytest.mark.anyio
@pytest.mark.parametrize('side', ['LONG','SHORT'])
@pytest.mark.parametrize('location', ['inside','upper','lower','above','below'])
@pytest.mark.parametrize('route', ['fresh','cached','reentry','scan','quote'])
async def test_real_orders_use_trend_inside_or_outside(side, location, route, monkeypatch):
    f, price = trend_frame(side, location, .00003)
    e = _execution_engine(f, side, True)
    e.account.positions.clear(); e.account.save_state = lambda: None
    del e._channel_intrabar_ready
    e._abnormal_market_entry_allowed = lambda *a, **kw: True
    now = float(f.iloc[-1]['timestamp'])/1000+1
    monkeypatch.setattr('core.engine.time.time', lambda: now)
    monkeypatch.setattr('core.engine.DEFAULT_SYMBOLS', [SYMBOL])
    e.tickers[SYMBOL] = price
    e._observe_channel_entry_quote(SYMBOL, price, now*1000)
    signal = dict(side=side, entry_mode='CHANNEL_SWING', action='ENTER_MARKET', reason='trend', signal_code='KC_TREND_'+side)
    if route == 'reentry':
        signal['profit_reentry_token'] = 'new'
        e.account.channel_profit_reentries = {SYMBOL:dict(side=side,token='new',phase='closed',mode='outer_cycle',requires_pullback=False,exit_bar_id=float(f.iloc[-2]['timestamp']))}
    snapshot = dict(frame=f.copy(),price=price,kc_upper=float(f.iloc[-1]['kc_upper']),kc_lower=float(f.iloc[-1]['kc_lower']))
    if route in ('scan', 'quote'):
        monkeypatch.setattr('core.engine.SYMBOL_ROTATION_ENABLED', False)
        e._ck_reverse_new_leg_halted = lambda: False
        result = (await e._execute_confirmed_channel_break(SYMBOL, f, price, side)
                  if route == 'scan' else await e._try_live_pivot_entry(SYMBOL, f, price))
    else:
        result = await e._place_structured_entry(SYMBOL, signal, price, channel_snapshot=snapshot if route=='cached' else None)
    assert result, e.account.logs
    assert [x[0] for x in e.account.events] == ['open']

@pytest.mark.anyio
@pytest.mark.parametrize('side', ['LONG','SHORT'])
@pytest.mark.parametrize('block', ['direction','room','adverse','same_bar','quote'])
async def test_cached_trend_cannot_bypass_fresh_guards(side, block, monkeypatch):
    f, price = trend_frame(side)
    e = _execution_engine(f, side, True)
    e.account.positions.clear(); e.account.save_state = lambda: None
    del e._channel_intrabar_ready
    e._abnormal_market_entry_allowed = lambda *a, **kw: True
    now = float(f.iloc[-1]['timestamp'])/1000+1
    monkeypatch.setattr('core.engine.time.time', lambda: now)
    monkeypatch.setattr('core.engine.DEFAULT_SYMBOLS', [SYMBOL])
    e.tickers[SYMBOL] = price; e._observe_channel_entry_quote(SYMBOL, price, now*1000)
    snapshot = dict(frame=f.copy(),price=price,kc_upper=float(f.iloc[-1]['kc_upper']),kc_lower=float(f.iloc[-1]['kc_lower']))
    if block=='direction': f.loc[f.index[-2],'kc_middle'] = float(f.iloc[-3]['kc_middle'])
    if block=='room': f.loc[f.index[5], 'high' if side=='LONG' else 'low'] = price + (.01 if side=='LONG' else -.01)
    if block=='adverse':
        f.loc[f.index[-1], 'open'] = price + (1. if side=='LONG' else -1.)
        f.loc[f.index[-1], 'high'] = max(price,float(f.iloc[-1]['open']))+.1
        f.loc[f.index[-1], 'low'] = min(price,float(f.iloc[-1]['open']))-.1
    if block=='same_bar': e.account.last_closed_at = {SYMBOL:now}
    if block=='quote': e._channel_entry_quote_times[SYMBOL] = now-6
    signal = dict(side=side,entry_mode='CHANNEL_SWING',action='ENTER_MARKET',reason='trend',signal_code='KC_TREND_'+side)
    assert not await e._place_structured_entry(SYMBOL,signal,price,channel_snapshot=snapshot)
    assert not e.account.events
