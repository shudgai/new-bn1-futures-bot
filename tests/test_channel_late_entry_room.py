"""All phases require net room to confirmed structure."""
from unittest.mock import AsyncMock
import pytest
from core.engine import TradingEngine
from core.channel_entry_room import entry_room
from test_channel_swing_execution import _execution_engine, _narrow_channel_frame, SYMBOL


@pytest.fixture
def anyio_backend():
    return "asyncio"


def phase_frame(side="LONG", late=True, target=110.):
    f = _narrow_channel_frame()
    f["atr"] = 1.
    f["kc_upper"], f["kc_lower"] = 102., 98.
    closes = [100., 101., 102., 103., 104.6, 105.4, 105.7] if late else [100., 101., 102., 103., 104., 105., 106.]
    f.loc[12:18, "close"] = closes
    f["open"] = f["close"].shift(1).fillna(100.)
    f.loc[19, ["open", "close"]] = [closes[-1], closes[-1] + .1]
    f["high"] = f[["open", "close"]].max(axis=1) + .05
    f["low"] = f[["open", "close"]].min(axis=1) - .05
    if target is not None:
        f.loc[6, "high"] = target
    f["ma3"] = f["close"].rolling(3).mean().fillna(100.)
    if side == "SHORT":
        for key in ("open", "close", "ma3"):
            f[key] = 200 - f[key]
        f["high"], f["low"] = 200 - f["low"].copy(), 200 - f["high"].copy()
        f["kc_upper"], f["kc_lower"] = 200 - f["kc_lower"].copy(), 200 - f["kc_upper"].copy()
    return f


def room(f, side, price=None):
    return entry_room(f, float(f.iloc[-1]["close"]) if price is None else price,
                      side, .0005, .0001, .0015)


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_strong_push_blocks_near_resistance_or_without_target(side):
    f = phase_frame(side, late=False, target=106.2)
    sign = 1 if side == "LONG" else -1
    for price in (float(f.iloc[-1]["close"]), float(f.iloc[-2]["close"]) + sign * 2):
        r = room(f, side, price)
        assert not r["allowed"] and r["checked"]
        assert r["stage"] == "developing"


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_weakening_without_mature_extension_is_not_late(side):
    f = phase_frame(side)
    f["atr"] = 10.
    r = room(f, side)
    assert r["allowed"] and r["checked"]


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
@pytest.mark.parametrize("target,allowed", [(110., True), (105.9, False)])
def test_late_structural_space_after_actual_costs(side, target, allowed):
    f = phase_frame(side, target=target)
    r = room(f, side)
    expected_target = target if side == "LONG" else 200 - target
    assert r["checked"] and r["stage"] == "late"
    assert r["allowed"] is allowed
    assert r["target"] == pytest.approx(expected_target)
    price = float(f.iloc[-1]["close"])
    sign = 1 if side == "LONG" else -1
    entry, exit = price * (1 + sign * .0001), expected_target * (1 - sign * .0001)
    expected = (sign * (exit - entry) - (entry + exit) * .0005) / (entry * 1.0005) * 100
    assert r["net_room_pct"] == pytest.approx(expected)
    if allowed:
        # The structural target may lie farther away than one ATR.
        assert sign * (r["target"] - float(f.iloc[-2]["close"])) > f.iloc[-2]["atr"]


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_late_without_a_confirmed_target_does_not_invent_one(side):
    r = room(phase_frame(side, target=None), side)
    assert not r["allowed"]
    assert r["reason"] == "KC_PROFIT_TARGET_UNAVAILABLE"
    assert "net_room_pct" not in r and "target" not in r


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_already_broken_or_touched_pivot_is_not_reused(side):
    f = phase_frame(side, target=110.)
    key = "high" if side == "LONG" else "low"
    f.loc[6, key] = 106.2 if side == "LONG" else 93.8
    # A later flat-topped pair touches that peak: it is neither an untouched
    # obstacle nor a new strict pivot. Keep a farther valid structural target.
    f.loc[8:9, key] = f.loc[6, key]
    f.loc[3, key] = 110. if side == "LONG" else 90.
    r = room(f, side)
    assert r["target"] == (110. if side == "LONG" else 90.)


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_closest_unbroken_pivot_is_selected(side):
    f = phase_frame(side)
    f.loc[9, "high" if side == "LONG" else "low"] = 108. if side == "LONG" else 92.
    assert room(f, side)["target"] == (108. if side == "LONG" else 92.)


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_live_candle_cannot_change_phase_or_supply_a_confirmed_target(side):
    f = phase_frame(side)
    before = room(f, side)
    f.loc[19, "atr"] = 1e6
    f.loc[19, "high"], f.loc[19, "low"] = 1000., .01
    assert room(f, side) == before


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
@pytest.mark.parametrize("invalid", ["nan_atr", "bad_ohlc", "no_history", "invalid_price"])
def test_invalid_data_still_blocks(side, invalid):
    f = phase_frame(side)
    price = float(f.iloc[-1]["close"])
    if invalid == "nan_atr": f.loc[18, "atr"] = float("nan")
    if invalid == "bad_ohlc": f.loc[18, "high"] = f.loc[18, "low"] - 1.
    if invalid == "no_history": f = f.tail(7)
    if invalid == "invalid_price": price = float("nan")
    r = room(f, side, price)
    assert not r["allowed"] and r["stage"] == "invalid"


@pytest.mark.anyio
@pytest.mark.parametrize("side", ["LONG", "SHORT"])
@pytest.mark.parametrize("route", ["ordinary", "strict", "reentry", "cached"])
@pytest.mark.parametrize("late", [False, True])
async def test_final_order_checks_phase_for_every_route(side, route, late, monkeypatch):
    f = phase_frame(side, late=late, target=105.9 if late else 106.2)
    price = float(f.iloc[-1]["close"])
    e = _execution_engine(f, side, True)
    e.account.positions.clear()
    e.tickers[SYMBOL] = price
    monkeypatch.setattr("core.engine.aligned_entry_ready", lambda *a: True)
    e._channel_intrabar_ready = lambda *a: True
    e.account.save_state = lambda: None
    e._abnormal_market_entry_allowed = lambda *a, **kw: True
    monkeypatch.setattr("core.engine.DEFAULT_SYMBOLS", [SYMBOL])
    snapshot = dict(price=price, kc_upper=102., kc_lower=98., frame=f)
    e._fresh_channel_entry_snapshot = AsyncMock(return_value=snapshot)
    signal = dict(side=side, score=100, entry_mode="CHANNEL_SWING", action="ENTER_MARKET",
                  reason="test", profit_room_pct=-99., estimated_profit_target=1.)
    if route == "strict":
        signal["signal_code"] = "KC_UPPER_BREAKOUT_STRICT" if side == "LONG" else "KC_LOWER_BREAKOUT_STRICT"
    if route == "reentry": signal["profit_reentry_token"] = "ticket"
    result = await e._place_structured_entry(
        SYMBOL, signal, price, channel_snapshot=snapshot if route == "cached" else None)
    assert not result, e.account.logs
    assert not e.account.events
    assert any("KC_PROFIT_ROOM_INSUFFICIENT" in message for message, _ in e.account.logs), e.account.logs



@pytest.mark.anyio
@pytest.mark.parametrize("side", ["LONG", "SHORT"])
async def test_quote_refresh_makes_late_order_fail_if_room_disappears(side, monkeypatch):
    f = phase_frame(side, target=110.)
    price = float(f.iloc[-1]["close"])
    assert room(f, side, price)["allowed"]
    e = _execution_engine(f, side, True)
    e.account.positions.clear()
    e.tickers[SYMBOL] = price
    monkeypatch.setattr("core.engine.aligned_entry_ready", lambda *a: True)
    e._channel_intrabar_ready = lambda *a: True
    monkeypatch.setattr("core.engine.DEFAULT_SYMBOLS", [SYMBOL])
    quote = 109.99 if side == "LONG" else 90.01
    e._fresh_channel_entry_snapshot = AsyncMock(return_value=dict(
        frame=f, price=quote, kc_upper=102., kc_lower=98.))
    signal = dict(side=side, entry_mode="CHANNEL_SWING", action="ENTER_MARKET")
    assert not await e._place_structured_entry(SYMBOL, signal, price)
    assert not e.account.events
