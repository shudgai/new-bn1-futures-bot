"""Three closed red candles may bridge a small middle body after a breakout."""
import pytest

from core.channel_outer_entry import (
    confirmed_outer_breakout_ready, continuation_entry, outside_reentry,
    three_closed_short_breakout_ready,
)
from core.engine import TradingEngine
from test_channel_two_closed_entry import continuation_frame
from test_channel_swing_execution import _execution_engine, SYMBOL


@pytest.fixture
def anyio_backend():
    return "asyncio"


def three_red_frame():
    f = continuation_frame("SHORT")
    f.loc[8:11, "open"] = [98.5, 97., 96.95, 96.5]
    f.loc[8:11, "close"] = [97., 96.95, 96.5, 96.4]
    f["high"] = f[["open", "close"]].max(axis=1) + .2
    f["low"] = f[["open", "close"]].min(axis=1) - .2
    f.loc[8:11, "kc_lower"] = [98., 97.9, 97.8, 97.7]
    f.loc[8:11, "kc_middle"] = [100., 99.9, 99.8, 99.7]
    f.loc[8:11, "ma3"] = [98., 97.5, 97., 96.8]
    return f


def test_small_middle_red_allows_entry_and_reentry():
    f = three_red_frame()
    for decision in (continuation_entry(f, 96.4), outside_reentry(f, 96.4, "SHORT"),
                     TradingEngine._channel_swing_action(f, 96.4)):
        assert decision["action"] == "ENTER"
        assert decision["side"] == "SHORT"
    assert confirmed_outer_breakout_ready(f, 96.4, "SHORT")
    assert not confirmed_outer_breakout_ready(f, 96.4, "SHORT", allow_three_short=False)


@pytest.mark.parametrize("invalid", [
    "middle_green", "middle_doji", "first_small", "third_small", "no_cross",
    "middle_inside", "third_inside", "nan", "invalid_ohlc", "invalid_rail",
    "live_inside", "third_unfinished",
])
def test_three_red_rejects_incomplete_or_invalid_confirmation(invalid):
    f = three_red_frame()
    price = 96.4
    if invalid == "middle_green":
        f.loc[9, "open"] = 96.9
    elif invalid == "middle_doji":
        f.loc[9, "open"] = f.loc[9, "close"]
    elif invalid in ("first_small", "third_small"):
        f.loc[8 if invalid == "first_small" else 10, ["high", "low"]] = [110., 90.]
    elif invalid == "no_cross":
        f.loc[8, "open"] = 97.5
    elif invalid == "middle_inside":
        f.loc[9, "kc_lower"] = 96.9
    elif invalid == "third_inside":
        f.loc[10, "kc_lower"] = 96.4
    elif invalid == "nan":
        f.loc[8, "low"] = float("nan")
    elif invalid == "invalid_ohlc":
        f.loc[9, "low"] = 97.1
    elif invalid == "invalid_rail":
        f.loc[8, "kc_upper"] = 97.
    elif invalid == "live_inside":
        price = 97.7
    else:
        f = f.iloc[:-1]
    assert not three_closed_short_breakout_ready(f, price)
    assert not confirmed_outer_breakout_ready(f, price, "SHORT")
    assert continuation_entry(f, price)["action"] == "WAIT"
    assert outside_reentry(f, price, "SHORT")["action"] == "WAIT"


@pytest.mark.anyio
async def test_snapshot_revalidates_three_red_and_current_price():
    f = three_red_frame()
    e = _execution_engine(f, "SHORT", True)
    e.account.positions.clear()
    e.tickers[SYMBOL] = 96.4
    bar = e._channel_candidate_bar_id(f)
    assert await e._fresh_channel_entry_snapshot(SYMBOL, "SHORT", bar) is not None
    e.tickers[SYMBOL] = 97.7
    assert await e._fresh_channel_entry_snapshot(SYMBOL, "SHORT", bar) is None
    e.tickers[SYMBOL] = 96.4
    f.loc[9, "open"] = f.loc[9, "close"]
    assert await e._fresh_channel_entry_snapshot(SYMBOL, "SHORT", bar) is None


@pytest.mark.parametrize("outer", [False, True])
def test_reentry_uses_first_of_three_as_breakout_time(outer):
    f = three_red_frame()
    info = dict(require_new_closed_break=True, allow_new_outer_signal=outer,
                side="SHORT", exit_bar_id=f.loc[8, "timestamp"])
    args = ("ENTER", False, "SHORT", f, info, SYMBOL)
    assert TradingEngine._channel_peak_exit_reentry_blocked(*args, live_price=96.4)
    info["exit_bar_id"] = f.loc[7, "timestamp"]
    assert not TradingEngine._channel_peak_exit_reentry_blocked(*args, live_price=96.4)


def test_three_green_does_not_receive_short_exception():
    f = three_red_frame()
    for key in ("open", "close", "high", "low", "kc_upper", "kc_lower", "ma3", "kc_middle"):
        f[key] = 200 - f[key]
    f["high"], f["low"] = f["low"].copy(), f["high"].copy()
    f["kc_upper"], f["kc_lower"] = f["kc_lower"].copy(), f["kc_upper"].copy()
    assert not confirmed_outer_breakout_ready(f, 103.6, "LONG")


@pytest.mark.anyio
@pytest.mark.parametrize("cached", [False, True])
async def test_order_gate_cannot_reuse_three_red_after_middle_changes(cached, monkeypatch):
    from unittest.mock import AsyncMock
    f = three_red_frame()
    e = _execution_engine(f, "SHORT", True)
    e.account.positions.clear()
    monkeypatch.setattr("core.engine.DEFAULT_SYMBOLS", [SYMBOL])
    f.loc[9, "open"] = f.loc[9, "close"]
    snapshot = dict(price=96.4, kc_upper=102., kc_lower=97.7, frame=f)
    e._fresh_channel_entry_snapshot = AsyncMock(return_value=snapshot)
    signal = dict(side="SHORT", entry_mode="CHANNEL_SWING", action="ENTER_MARKET")
    assert not await e._place_structured_entry(
        SYMBOL, signal, 96.4, channel_snapshot=snapshot if cached else None)
    assert not e.account.events
    assert any("缺少已收線實體破軌" in message for message, _ in e.account.logs)


@pytest.mark.anyio
@pytest.mark.parametrize("case", ["normal", "abnormal", "recovered", "same_bar", "room", "changed"])
async def test_three_red_profit_reentry_preserves_other_gates(case, monkeypatch):
    from unittest.mock import AsyncMock
    f = three_red_frame()
    e = _execution_engine(f, "SHORT", True)
    e.account.positions.clear()
    e.account.save_state = lambda: None
    e.tickers[SYMBOL] = 96.4
    e._abnormal_market_entry_allowed = lambda *a, **k: True
    e._channel_profit_room = lambda *a: dict(
        allowed=case != "room", reason="KC_PROFIT_ROOM_INSUFFICIENT", net_room_pct=1., target=95.)
    ticket = dict(side="SHORT", token="three-red", phase="closed", mode="outer_cycle",
                  requires_pullback=case in ("abnormal", "recovered"), exit_bar_id=6000.)
    if case == "recovered":
        ticket["pullback_bar"] = 7000.
    if case == "same_bar":
        ticket["exit_bar_id"] = 11000.
    e.account.channel_profit_reentries = {SYMBOL: ticket}
    fresh = f.copy()
    if case == "changed":
        fresh.loc[9, "open"] = fresh.loc[9, "close"]
    e.fetch_klines = AsyncMock(return_value=fresh)
    monkeypatch.setattr("core.engine.DEFAULT_SYMBOLS", [SYMBOL])
    await e._try_profit_reentry(SYMBOL, f, 96.4, False)
    assert len(e.account.events) == int(case in ("normal", "recovered")), e.account.logs
