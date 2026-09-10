"""MA3/MA15/KC direction is shared by trend, breakout and order validation."""
from unittest.mock import AsyncMock

import pytest

from core.channel_outer_entry import aligned_direction, aligned_entry, outside_reentry
from core.channel_outer_entry import aligned_entry_ready
from core.engine import TradingEngine
from channel_test_frames import closed_outer_entry_frame
from test_channel_swing_execution import _execution_engine, SYMBOL


@pytest.fixture
def anyio_backend():
    return "asyncio"


def aligned_frame(side="LONG", route="trend"):
    f = closed_outer_entry_frame("LONG")
    f.loc[16:19, "ma15"] = [99.4, 99.5, 99.6, 99.7]
    if route == "trend":
        f.loc[16:19, "open"] = [100., 100.1, 100.2, 100.3]
        f.loc[16:19, "close"] = [100.1, 100.2, 100.3, 100.4]
        f["high"] = f[["open", "close"]].max(axis=1) + .1
        f["low"] = f[["open", "close"]].min(axis=1) - .1
        f["ma3"] = f["close"].rolling(3).mean().fillna(100.)
    if side == "SHORT":
        for key in ("open", "close", "ma3", "ma15", "kc_middle", "ema_20"):
            f[key] = 200 - f[key]
        f["high"], f["low"] = 200-f["low"].copy(), 200-f["high"].copy()
        f["kc_upper"], f["kc_lower"] = 200-f["kc_lower"].copy(), 200-f["kc_upper"].copy()
    return f


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
@pytest.mark.parametrize("route", ["trend", "breakout"])
def test_only_breakout_route_and_closed_direction(side, route):
    f = aligned_frame(side, route)
    price = float(f.iloc[-1]["close"])
    result = aligned_entry(f, price)
    code = "KC_MIDDLE_TREND_" if route == "trend" else "KC_CONTINUATION_"
    if route == "breakout":
        assert result == dict(action="ENTER", side=side, reason=code+side)
    else:
        assert result["action"] == "WAIT"
    assert TradingEngine._channel_swing_action(f, price) == result
    f.loc[19, ["ma3", "ma15", "kc_middle"]] = [float("nan"), 1., 1000.]
    assert aligned_entry(f, price) == result


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
@pytest.mark.parametrize("route", ["trend", "breakout"])
@pytest.mark.parametrize("bad", ["ma3_flat", "ma3_reverse", "ma15_flat", "ma15_reverse", "ordering", "ck", "rail", "nan", "ohlc"])
def test_neither_route_bypasses_alignment_or_invalid_data(side, route, bad):
    f = aligned_frame(side, route)
    sign = 1 if side == "LONG" else -1
    if bad.startswith("ma3_") or bad.startswith("ma15_"):
        key = bad.split("_")[0]
        f.loc[18, key] = f.loc[17, key] - (sign if bad.endswith("reverse") else 0)
    elif bad == "ordering":
        f.loc[17:18, "ma15"] = f.loc[17:18, "ma3"] + sign
    elif bad == "ck":
        f["kc_middle"] = 100.
    elif bad == "rail":
        key = "kc_upper" if side == "LONG" else "kc_lower"
        f.loc[18, key] = f.loc[17, key] - sign
    elif bad == "nan":
        f.loc[18, "ma15"] = float("nan")
    else:
        f.loc[18, "low"] = f.loc[18, "high"] + 1
    price = float(f.iloc[-1]["close"])
    expected = 'ENTER' if route == 'breakout' and bad in (
        'ma3_flat', 'ma3_reverse', 'ma15_flat', 'ma15_reverse', 'ordering', 'nan') else 'WAIT'
    assert aligned_entry(f, price)["action"] == expected
    assert outside_reentry(f, price, side)["action"] == expected


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_order_validation_requires_live_ma3_slope_and_matching_candle(side):
    f = aligned_frame(side, "breakout")
    price = float(f.iloc[-1]["close"])
    assert aligned_entry_ready(f, price, side)

    f.loc[19, "ma3"] = f.loc[18, "ma3"]  # stale indicator is ignored
    assert aligned_entry_ready(f, price, side)
    assert not aligned_entry_ready(f, float(f.iloc[-4]["close"]), side)

    f = aligned_frame(side)
    f.loc[19, "open"] = price + (1.0 if side == "LONG" else -1.0)
    assert not aligned_entry_ready(f, price, side)


@pytest.mark.anyio
@pytest.mark.parametrize("side", ["LONG", "SHORT"])
@pytest.mark.parametrize("route", ["trend", "breakout"])
@pytest.mark.parametrize("block", ["none", "fresh_ma", "risk", "room", "balance", "minute", "halt"])
async def test_scan_and_real_order_keep_risk_checks(side, route, block, monkeypatch):
    f = aligned_frame(side, route)
    price = float(f.iloc[-1]["close"])
    e = _execution_engine(f, side, True)
    e.account.positions.clear()
    e.account.save_state = lambda: None
    e.tickers[SYMBOL] = price
    e._channel_chop_state = lambda *a: {"detected": False}
    e._abnormal_market_entry_allowed = lambda *a, **k: block != "risk"
    e._channel_profit_room = lambda *a: dict(allowed=block != "room", reason="room", checked=False)
    if block == "balance": e.account.get_available_balance = lambda: 0.
    if block == "minute": e._channel_candle_entry_blocked = lambda *a, **k: True
    if block == "fresh_ma":
        fresh = f.copy()
        fresh.loc[18, "ma15"] = fresh.loc[17, "ma15"]
        e.fetch_klines = AsyncMock(side_effect=[f.copy(), fresh])
    monkeypatch.setattr("core.engine.DEFAULT_SYMBOLS", [SYMBOL])
    await e._process_single_symbol(SYMBOL, 1., None, block == "halt")
    assert len(e.account.events) == int(block in ("none", "fresh_ma") and route == "breakout"), e.account.logs
    assert not any("處理失敗" in text for text, _ in e.account.logs)


@pytest.mark.anyio
@pytest.mark.parametrize("side", ["LONG", "SHORT"])
@pytest.mark.parametrize("cached", [False, True])
async def test_cached_order_rechecks_alignment(side, cached, monkeypatch):
    f = aligned_frame(side)
    price = float(f.iloc[-1]["close"])
    e = _execution_engine(f, side, True)
    e.account.positions.clear()
    f.loc[18, "kc_middle"] = f.loc[17, "kc_middle"]
    snapshot = dict(price=price, frame=f, kc_upper=float(f.iloc[-1]["kc_upper"]), kc_lower=float(f.iloc[-1]["kc_lower"]))
    e._fresh_channel_entry_snapshot = AsyncMock(return_value=snapshot)
    monkeypatch.setattr("core.engine.DEFAULT_SYMBOLS", [SYMBOL])
    signal = dict(side=side, entry_mode="CHANNEL_SWING", action="ENTER_MARKET")
    assert not await e._place_structured_entry(SYMBOL, signal, price, channel_snapshot=snapshot if cached else None)
    assert not e.account.events


@pytest.mark.anyio
@pytest.mark.parametrize("side", ["LONG", "SHORT"])
@pytest.mark.parametrize("case", ["normal", "abnormal_inside", "abnormal_no_pullback", "recovered", "fresh_ma"])
async def test_profit_reentry_keeps_pullback_and_fresh_direction(side, case, monkeypatch):
    f = aligned_frame(side, "breakout")
    f["timestamp"] = list(range(len(f)))
    price = float(f.iloc[-1]["close"])
    e = _execution_engine(f, side, True)
    e.account.positions.clear()
    e.account.save_state = lambda: None
    e.tickers[SYMBOL] = price
    e._abnormal_market_entry_allowed = lambda *a, **k: True
    e._channel_profit_room = lambda *a: dict(allowed=True, checked=False)
    ticket = dict(side=side, token="aligned", phase="closed", mode="outer_cycle", exit_bar_id=15,
                  requires_pullback=case.startswith("abnormal") or case == "recovered")
    if case == "abnormal_inside":
        price = (float(f.iloc[-1]["kc_upper"]) + float(f.iloc[-1]["kc_lower"])) / 2
    if case == "recovered": ticket["pullback_bar"] = 16
    e.account.channel_profit_reentries = {SYMBOL: ticket}
    if case == "fresh_ma":
        fresh = f.copy()
        fresh.loc[18, "ma15"] = fresh.loc[17, "ma15"]
        e.fetch_klines = AsyncMock(return_value=fresh)
    monkeypatch.setattr("core.engine.DEFAULT_SYMBOLS", [SYMBOL])
    await e._try_profit_reentry(SYMBOL, f, price, False)
    assert len(e.account.events) == int(case in ("normal", "recovered", "fresh_ma")), e.account.logs


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
@pytest.mark.parametrize("outer", [False, True])
def test_breakout_after_close_requires_new_closed_signal(side, outer):
    f = aligned_frame(side, "breakout")
    f["timestamp"] = list(range(len(f)))
    info = dict(side=side, require_new_closed_break=True, allow_new_outer_signal=outer, exit_bar_id=18)
    args = ("ENTER", False, side, f, info, SYMBOL)
    assert TradingEngine._channel_peak_exit_reentry_blocked(*args)
    info["exit_bar_id"] = 17
    assert TradingEngine._channel_peak_exit_reentry_blocked(*args)
    info["exit_bar_id"] = 16
    assert not TradingEngine._channel_peak_exit_reentry_blocked(*args)


def test_three_red_small_middle_cannot_bypass_two_closed_bodies():
    from test_channel_three_red_entry import three_red_frame
    f = three_red_frame()
    f.loc[9:10, "ma15"] = [99.1, 99.]
    assert aligned_entry(f, 96.4)["action"] == "WAIT"
    f.loc[10, "ma15"] = 99.2
    assert aligned_entry(f, 96.4)["action"] == "WAIT"


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_trend_signal_does_not_close_or_reverse_held_position(side):
    f = aligned_frame(side)
    held = "SHORT" if side == "LONG" else "LONG"
    assert TradingEngine._channel_swing_action(f, float(f.iloc[-1]["close"]), held)["action"] == "HOLD"
