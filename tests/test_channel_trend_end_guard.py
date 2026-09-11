import pandas as pd
import pytest

from core.engine import TradingEngine


def frame(side="LONG", mature=True):
    rows = 5
    values = list(range(rows))
    sign = 1 if side == "LONG" else -1
    middle = [100.0 + sign * value for value in values]
    if mature:
        ma3 = [middle_value + sign * 1.0 for middle_value in middle]
        ma15 = middle
        upper = [110.0 + sign * value for value in values]
        lower = [90.0 + sign * value for value in values]
    else:
        ma3 = [middle_value + sign * 1.0 for middle_value in middle]
        ma15 = middle
        upper = [110.0] * rows
        lower = [90.0] * rows
    return pd.DataFrame({
        "open": middle,
        "high": [value + sign * 1.0 for value in middle],
        "low": [value - sign * 1.0 for value in middle],
        "close": [value + sign * 0.5 for value in middle],
        "ma3": ma3,
        "ma15": ma15,
        "atr": [1.0] * rows,
        "volume": [1.0] * rows,
        "kc_upper": upper,
        "kc_lower": lower,
    })


def test_mature_long_outer_entry_blocks_first_favorable_live_candle(monkeypatch):
    monkeypatch.setattr(
        "core.services.swing_service.aligned_entry",
        lambda frame, price: {"action": "ENTER", "side": "LONG", "reason": "KC_OUTSIDE_LONG"},
    )
    result = TradingEngine._channel_swing_action(frame("LONG"), 112.0)
    assert result == {"action": "WAIT", "side": None, "reason": "KC_TREND_END_WAIT"}


def test_mature_short_outer_entry_blocks_first_favorable_live_candle(monkeypatch):
    monkeypatch.setattr(
        "core.services.swing_service.aligned_entry",
        lambda frame, price: {"action": "ENTER", "side": "SHORT", "reason": "KC_OUTSIDE_SHORT"},
    )
    result = TradingEngine._channel_swing_action(frame("SHORT"), 88.0)
    assert result == {"action": "WAIT", "side": None, "reason": "KC_TREND_END_WAIT"}


def test_non_mature_entry_is_not_blocked_by_trend_end_guard(monkeypatch):
    monkeypatch.setattr(
        "core.services.swing_service.aligned_entry",
        lambda frame, price: {"action": "ENTER", "side": "LONG", "reason": "KC_OUTSIDE_LONG"},
    )
    result = TradingEngine._channel_swing_action(frame("LONG", mature=False), 112.0)
    # Terminal guard released, but this five-row fixture cannot estimate room.
    assert result == {"action": "WAIT", "side": None, "reason": "KC_PROFIT_ROOM_DATA_INVALID"}


def test_confirmed_volume_recovery_releases_mature_guard(monkeypatch):
    monkeypatch.setattr(
        "core.services.swing_service.aligned_entry",
        lambda frame, price: {"action": "ENTER", "side": "SHORT", "reason": "KC_OUTSIDE_SHORT"},
    )
    recovered = frame("SHORT")
    recovered.loc[recovered.index[-2], "volume"] = 2.0
    result = TradingEngine._channel_swing_action(recovered, 88.0)
    # Volume recovery does not bypass the independent profit-space requirement.
    assert result == {"action": "WAIT", "side": None, "reason": "KC_PROFIT_ROOM_DATA_INVALID"}


def test_mature_edge_blocks_first_favorable_live_candle(monkeypatch):
    monkeypatch.setattr(
        "core.services.swing_service.aligned_entry",
        lambda frame, price: {"action": "ENTER", "side": "LONG", "reason": "KC_OUTSIDE_LONG"},
    )
    first = frame("LONG")
    first.loc[first.index[-1], ["open", "close"]] = [104.0, 105.0]
    assert TradingEngine._channel_swing_action(first, 105.0)["reason"] == "KC_TREND_END_WAIT"


def test_mature_edge_blocks_after_two_adverse_closed_candles(monkeypatch):
    monkeypatch.setattr(
        "core.services.swing_service.aligned_entry",
        lambda frame, price: {"action": "ENTER", "side": "LONG", "reason": "KC_OUTSIDE_LONG"},
    )
    blocked = frame("LONG")
    blocked.loc[blocked.index[-3:-1], ["open", "close"]] = [[104.0, 103.0], [103.0, 102.0]]
    blocked.loc[blocked.index[-1], ["open", "close"]] = [102.0, 103.0]
    result = TradingEngine._channel_swing_action(blocked, 103.0)
    assert result == {"action": "WAIT", "side": None, "reason": "KC_TREND_END_WAIT"}


@pytest.mark.parametrize("trend", ["LONG", "SHORT"])
@pytest.mark.parametrize("entry", ["LONG", "SHORT"])
def test_terminal_market_blocks_both_directions(trend, entry, monkeypatch):
    monkeypatch.setattr("core.services.swing_service.aligned_entry", lambda *a: {"action": "ENTER", "side": entry})
    f = frame(trend)
    assert TradingEngine._channel_swing_action(f, 100.)["reason"] == "KC_TREND_END_WAIT"
    f.loc[f.index[-1], ["volume", "ma3", "ma15", "kc_upper", "kc_lower"]] = [10000., 1000., 0., 10000., 1.]
    assert TradingEngine._channel_swing_action(f, 100.)["reason"] == "KC_TREND_END_WAIT"


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
@pytest.mark.parametrize("volume,expected", [(1.499, True), (1.5, False), (float("nan"), False)])
def test_terminal_volume_boundary_and_invalid_data(side, volume, expected):
    f = frame(side)
    f.loc[f.index[-2], "volume"] = volume
    assert TradingEngine._channel_terminal_market(f) is expected

@pytest.fixture
def anyio_backend():
    return 'asyncio'


@pytest.mark.anyio
@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
@pytest.mark.parametrize('route', ['fresh', 'cached', 'reentry'])
@pytest.mark.parametrize('terminal', [False, True])
async def test_order_routes_recheck_terminal_market(side, route, terminal, monkeypatch):
    from test_channel_entry_recovery import ready
    from test_channel_swing_execution import _execution_engine, SYMBOL
    from core.services.strategies.outer_strategy import aligned_entry
    f, price = ready(side)
    # Current entry requires a directional live body; the legacy fixture is a doji.
    f.loc[f.index[-1], "open"] = price - (.05 if side == "LONG" else -.05)
    f.loc[f.index[5], 'high' if side == 'LONG' else 'low'] = price + (3. if side == 'LONG' else -3.)
    snapshot = dict(price=price, frame=f.copy(), kc_upper=float(f.iloc[-1]['kc_upper']), kc_lower=float(f.iloc[-1]['kc_lower']))
    sign = 1 if side == 'LONG' else -1
    if terminal:
        indices = f.index[-5:-1]
        for rail in ('kc_upper', 'kc_lower'):
            latest = float(f.iloc[-2][rail])
            f.loc[indices, rail] = [latest - sign * v for v in (.03, .02, .01, 0.)]
        f.loc[indices, 'ma3'] = f.loc[indices, 'ma15'] + sign
        f['volume'] = 1.
        f['vol_ma_20'] = 1.
    assert aligned_entry(f, price)['side'] == side
    assert TradingEngine._channel_terminal_market(f) is terminal
    e = _execution_engine(f, side, True)
    del e._channel_intrabar_ready
    e.account.positions.clear()
    e.account.save_state = lambda: None
    e._abnormal_market_entry_allowed = lambda *a, **k: True
    now = float(f.iloc[-1]['timestamp']) / 1000 + 1
    monkeypatch.setattr('core.engine.time.time', lambda: now)
    monkeypatch.setattr('core.engine.DEFAULT_SYMBOLS', [SYMBOL])
    signal = dict(side=side, entry_mode='CHANNEL_SWING', action='ENTER_MARKET', reason='terminal regression')
    if route == 'reentry':
        signal['profit_reentry_token'] = 'new'
        e.account.channel_profit_reentries = {SYMBOL: dict(side=side, token='new', phase='closed', mode='outer_cycle', requires_pullback=False, exit_bar_id=float(f.iloc[-2]['timestamp']))}
    e.tickers[SYMBOL] = price
    e._observe_channel_entry_quote(SYMBOL, price, now * 1000)
    result = await e._place_structured_entry(SYMBOL, signal, price, channel_snapshot=snapshot if route == 'cached' else None)
    assert bool(result) is (not terminal), e.account.logs
    assert [event[0] for event in e.account.events] == ([] if terminal else ['open'])
