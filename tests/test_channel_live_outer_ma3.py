import asyncio
import copy
import pytest
from core.channel_outer_entry import aligned_entry, aligned_entry_ready, two_closed_bodies_ready, sustained_trend_ready, live_ma3_direction_ready, live_candle_color_ready
from core.channel_entry_diagnostics import entry_diagnostics
from test_channel_ck_reverse import setup
from test_channel_live_pivot import prepare, quote, anyio_backend
from test_channel_swing_execution import SYMBOL


def outer_setup(side, monkeypatch):
    f, p, e = setup(side, monkeypatch)
    e.account.positions.clear()
    e.symbol_rotation.last_rotation_at = 1.
    e.is_running = True
    sign = 1 if side == 'LONG' else -1
    # Neither completed candle has the requested colour; a historical pattern cannot qualify.
    f.loc[f.index[-3:-1], 'open'] = f.loc[f.index[-3:-1], 'close'] + sign * .1
    f['high'] = f[['open', 'close']].max(axis=1) + .1
    f['low'] = f[['open', 'close']].min(axis=1) - .1
    f.loc[5, 'high' if side == 'LONG' else 'low'] = p + sign * 3.
    e._channel_exit_frames = {SYMBOL: f}
    clock = [1202.]
    monkeypatch.setattr('core.engine.time.time', lambda: clock[0])
    quote(e, clock, p)
    assert not two_closed_bodies_ready(f, side)
    assert not sustained_trend_ready(f, side)
    return f, p, e, clock


@pytest.mark.anyio
@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
@pytest.mark.parametrize('route', ['quote', 'scan', 'direct', 'normal_reentry'])
async def test_first_outside_quote_needs_no_two_candles(side, route, monkeypatch):
    f, p, e, clock = outer_setup(side, monkeypatch)
    assert aligned_entry(f, p)['reason'] == 'KC_LIVE_OUTER_' + side
    if route == 'normal_reentry':
        e.account.channel_profit_reentries = {SYMBOL: dict(token='normal', mode='outer_cycle',
            side=side, phase='closed', requires_pullback=False, exit_bar_id=1140000)}
        await e._try_profit_reentry(SYMBOL, f, p, False)
    elif route == 'direct':
        await e._execute_confirmed_channel_break(SYMBOL, f, p, side)
    elif route == 'scan':
        await e._process_single_symbol(SYMBOL, clock[0], None, False)
    else:
        await asyncio.gather(*(e._channel_quote_pivot_entry(SYMBOL, p) for _ in range(2)))
    assert [x[0] for x in e.account.events] == ['open'], e.account.logs
    assert e.account.positions[SYMBOL]['side'] == side


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_entry_requires_green_long_or_red_short_live_candle(side, monkeypatch):
    f, p, _, _ = outer_setup(side, monkeypatch)
    opened = float(f.iloc[-1]['open'])
    good = p if side == 'LONG' else p
    assert live_candle_color_ready(f, good, side)
    bad = opened - .01 if side == 'LONG' else opened + .01
    assert not live_candle_color_ready(f, bad, side)
    monkeypatch.setattr('core.channel_outer_entry.live_candle_color_ready', lambda *args: False)
    assert aligned_entry(f, p)['reason'] == 'KC_LIVE_CANDLE_DIRECTION_WAIT'


@pytest.mark.anyio
@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
@pytest.mark.parametrize('invalid', ['ck_flat', 'ck_outer_reverse', 'ck_nan', 'ma_flat', 'ma_reverse', 'ma_nan', 'touch', 'inside', 'room', 'stale'])
async def test_live_outside_still_requires_direction_and_risk(side, invalid, monkeypatch):
    f, p, e, clock = outer_setup(side, monkeypatch)
    sign = 1 if side == 'LONG' else -1
    if invalid == 'ck_flat':
        f.loc[f.index[-2], 'kc_middle'] = f.iloc[-3]['kc_middle']
    elif invalid == 'ck_outer_reverse':
        rail = 'kc_upper' if side == 'LONG' else 'kc_lower'
        f.loc[f.index[-2], rail] = f.iloc[-3][rail] - sign * .01
    elif invalid == 'ck_nan':
        f.loc[f.index[-2], 'kc_middle'] = float('nan')
    elif invalid.startswith('ma_'):
        f.loc[f.index[-4], 'close'] = float('nan') if invalid == 'ma_nan' else p + (sign * .1 if invalid == 'ma_reverse' else 0)
        f['high'] = f[['open', 'close']].max(axis=1) + .1
        f['low'] = f[['open', 'close']].min(axis=1) - .1
    elif invalid in ('touch', 'inside'):
        p = float(f.iloc[-1]['kc_upper' if side == 'LONG' else 'kc_lower']) - (sign * .01 if invalid == 'inside' else 0)
        quote(e, clock, p)
    elif invalid == 'room':
        f.loc[5, 'high' if side == 'LONG' else 'low'] = p
    else:
        clock[0] += 6
    await e._channel_quote_pivot_entry(SYMBOL, p)
    assert not e.account.events, e.account.logs


@pytest.mark.anyio
@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
@pytest.mark.parametrize('route', ['pivot', 'cached', 'normal_reentry', 'ck_reverse'])
@pytest.mark.parametrize('invalid', ['ck_flat', 'ma_reverse'])
async def test_all_new_legs_revalidate_ck_and_ma3(side, route, invalid, monkeypatch):
    f, p, e, clock = prepare(side, monkeypatch)
    sign = 1 if side == 'LONG' else -1
    for offset in [0, -.1, -.09]: quote(e, clock, p + sign * offset)
    price = p - sign * .09
    if invalid == 'ck_flat':
        f.loc[f.index[-2], 'kc_middle'] = f.iloc[-3]['kc_middle']
    else:
        f.loc[f.index[-4], 'close'] = price + sign * .1
        f['high'] = f[['open', 'close']].max(axis=1) + .1
        f['low'] = f[['open', 'close']].min(axis=1) - .1
    if route == 'pivot':
        await e._channel_quote_pivot_entry(SYMBOL, price)
    elif route == 'normal_reentry':
        e.account.channel_profit_reentries = {SYMBOL: dict(token='normal', mode='outer_cycle',
            side=side, phase='closed', requires_pullback=False, exit_bar_id=1140000)}
        await e._try_profit_reentry(SYMBOL, f, price, False)
    elif route == 'cached':
        signal = dict(side=side, score=100, entry_mode='CHANNEL_SWING', action='ENTER_MARKET')
        snapshot = dict(frame=f, price=price, kc_upper=float(f.iloc[-1]['kc_upper']), kc_lower=float(f.iloc[-1]['kc_lower']))
        await e._place_structured_entry(SYMBOL, signal, price, channel_snapshot=snapshot)
    else:
        old = 'SHORT' if side == 'LONG' else 'LONG'
        e.account.positions[SYMBOL] = dict(side=old, entry_price=price, open_timestamp=1201.,
                                         qty=1., entry_mode='CHANNEL_SWING')
        await e._try_ck_reverse(SYMBOL, f, price, False)
    assert not any(x[0] == 'open' for x in e.account.events), e.account.logs
    if route == 'ck_reverse' and invalid == 'ma_reverse':
        assert [x[0] for x in e.account.events] == ['close']


@pytest.mark.anyio
@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
async def test_abnormal_reentry_needs_pullback_then_live_reclaim_not_two_bodies(side, monkeypatch):
    f, p, e, clock = outer_setup(side, monkeypatch)
    e.account.channel_profit_reentries = {SYMBOL: dict(token='abnormal', mode='outer_cycle',
        side=side, phase='closed', requires_pullback=True, exit_bar_id=1140000)}
    await e._try_profit_reentry(SYMBOL, f, p, False)
    assert not e.account.events
    # Later same candle inside CK observes the required pullback, without any candle closing.
    inside = float(f.iloc[-1]['kc_upper'] + f.iloc[-1]['kc_lower']) / 2
    await e._try_profit_reentry(SYMBOL, f, inside, False)
    quote(e, clock, p)
    await e._try_profit_reentry(SYMBOL, f, p, False)
    assert [x[0] for x in e.account.events] == ['open'], e.account.logs


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_chart_explains_ma3_wait(side, monkeypatch):
    f, p, e, clock = outer_setup(side, monkeypatch)
    f.loc[f.index[-4], 'close'] = p
    f['high'] = f[['open', 'close']].max(axis=1) + .1
    f['low'] = f[['open', 'close']].min(axis=1) - .1
    assert entry_diagnostics(e, SYMBOL, f, p, clock[0])['reason'] == 'KC_LIVE_MA3_DIRECTION_WAIT'
