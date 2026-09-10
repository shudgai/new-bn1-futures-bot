"""Closed bodies qualify breakouts; aligned trend entries and order gates remain independent."""
import pytest
from core.engine import TradingEngine
from core.channel_outer_entry import (
    aligned_entry, confirmed_outer_breakout_ready, outside_reentry, two_closed_bodies_ready,
)
from test_channel_next_live_push import push_frame
from test_channel_aligned_entry import aligned_frame
from test_channel_swing_execution import _execution_engine, _narrow_channel_frame, SYMBOL


@pytest.fixture
def anyio_backend():
    return "asyncio"


def continuation_frame(side):
    f = push_frame("LONG")
    f.loc[9, ["open", "close"]] = [101.5, 103.]
    f.loc[10, ["open", "close"]] = [103., 103.2]
    f.loc[11, ["open", "close"]] = [103.2, 103.3]
    f["high"] = f[["open", "close"]].max(axis=1) + .1
    f["low"] = f[["open", "close"]].min(axis=1) - .1
    if side == "SHORT":
        for k in ("open", "close", "high", "low", "kc_upper", "kc_lower", "ma3"):
            f[k] = 200 - f[k]
        f["high"], f["low"] = f["low"].copy(), f["high"].copy()
        f["kc_upper"], f["kc_lower"] = f["kc_lower"].copy(), f["kc_upper"].copy()
    return f


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
@pytest.mark.parametrize("bar", [-3, -2])
@pytest.mark.parametrize("invalid", ["opposite", "doji", "small_body", "nan"])
def test_closed_bodies_qualify_breakout_but_not_aligned_trend(side, bar, invalid):
    f = aligned_frame(side, "breakout")
    price = float(f.iloc[-1]["close"])
    assert aligned_entry(f, price)["reason"] == "KC_CONTINUATION_" + side
    assert confirmed_outer_breakout_ready(f, price, side)
    idx = f.index[bar]
    sign = 1 if side == "LONG" else -1
    closed = float(f.loc[idx, "close"])
    if invalid == "opposite":
        f.loc[idx, "open"] = closed + sign * .05
    elif invalid == "doji":
        f.loc[idx, "open"] = closed
    elif invalid == "small_body":
        f.loc[idx, "open"] = closed - sign * .001
    else:
        f.loc[idx, "open"] = float("nan")
    assert not two_closed_bodies_ready(f, side)
    assert not confirmed_outer_breakout_ready(f, price, side)
    expected = (dict(action="WAIT", side=None, reason="KC_MA_ALIGNMENT_WAIT")
                if invalid == "nan" else
                dict(action="ENTER", side=side, reason="KC_MIDDLE_TREND_" + side))
    assert aligned_entry(f, price) == expected
    assert TradingEngine._channel_swing_action(f, price) == expected
    reentry = outside_reentry(f, price, side)
    assert reentry["action"] == expected["action"]
    if invalid != "nan":
        assert reentry == expected


@pytest.mark.anyio
@pytest.mark.parametrize("side", ["LONG", "SHORT"])
async def test_order_snapshot_falls_back_to_trend_and_rechecks_alignment(side):
    f = aligned_frame(side, "breakout")
    price = float(f.iloc[-1]["close"])
    e = _execution_engine(f, side, True)
    e.account.positions.clear()
    e.tickers[SYMBOL] = price
    bar = e._channel_candidate_bar_id(f)
    assert await e._fresh_channel_entry_snapshot(SYMBOL, side, bar) is not None
    f.loc[f.index[-3], "open"] = f.iloc[-3]["close"]
    snapshot = await e._fresh_channel_entry_snapshot(SYMBOL, side, bar)
    assert snapshot is not None
    assert aligned_entry(snapshot["frame"], price)["reason"] == "KC_MIDDLE_TREND_" + side
    f.loc[f.index[-2], "ma15"] = f.iloc[-3]["ma15"]
    assert await e._fresh_channel_entry_snapshot(SYMBOL, side, bar) is None


@pytest.mark.anyio
@pytest.mark.parametrize("side", ["LONG", "SHORT"])
@pytest.mark.parametrize("pending", [False, True])
async def test_unarmed_ma3_turn_holds(side, pending):
    f = _narrow_channel_frame()
    sign = 1 if side == "LONG" else -1
    f["kc_upper"], f["kc_lower"] = 110., 90.
    f["atr"] = 100.
    f["timestamp"] = [60_000 * (i + 1) for i in range(len(f))]
    f.loc[15:18, "close"] = [100 + sign * x for x in (0., 2., 4., 3.)]
    price = 100. + sign
    f.loc[19, ["open", "close"]] = price
    f["ma3"] = f["close"].rolling(3).mean()
    f["high"] = f[["open", "close"]].max(axis=1) + .1
    f["low"] = f[["open", "close"]].min(axis=1) - .1
    assert sign * (f.iloc[-2]["ma3"] - f.iloc[-3]["ma3"]) > 0
    assert sign * (f.iloc[-1]["ma3"] - f.iloc[-2]["ma3"]) < 0
    e = _execution_engine(f, side, True)
    e.account.save_state = lambda: None
    e.account.positions[SYMBOL].update(
        entry_price=100 + sign * 5, qty=1., open_timestamp=1.,
        channel_live_ma3_turn_exit_pending=pending,
        channel_outer_ma3_turn_exit_pending=pending,
    )
    e.tickers[SYMBOL] = price
    await e._process_single_symbol(SYMBOL, 2., None, False)
    assert not any("處理失敗" in text for text, _ in e.account.logs)
    assert not e.account.events
    assert SYMBOL in e.account.positions


@pytest.mark.anyio
@pytest.mark.parametrize("side", ["LONG", "SHORT"])
@pytest.mark.parametrize("cached", [False, True])
async def test_cached_or_fresh_signal_cannot_bypass_invalid_candle(side, cached, monkeypatch):
    from unittest.mock import AsyncMock
    f = aligned_frame(side, "breakout")
    price = float(f.iloc[-1]["close"])
    assert aligned_entry(f, price)["side"] == side
    f.loc[f.index[-3], "open"] = float("nan")
    e = _execution_engine(f, side, True)
    e.account.positions.clear()
    monkeypatch.setattr("core.engine.DEFAULT_SYMBOLS", [SYMBOL])
    snapshot = dict(price=price, kc_upper=102., kc_lower=98., frame=f)
    e._fresh_channel_entry_snapshot = AsyncMock(return_value=snapshot)
    signal = dict(side=side, entry_mode="CHANNEL_SWING", action="ENTER_MARKET")
    assert not await e._place_structured_entry(
        SYMBOL, signal, price, channel_snapshot=snapshot if cached else None)
    assert not e.account.events
    assert any("MA3／MA15／KC未同向或入口確認失效" in message for message, _ in e.account.logs)
