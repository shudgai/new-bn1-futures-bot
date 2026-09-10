"""Two completed bodies at entry; an MA3 turn alone never closes a position."""
import pytest
from core.engine import TradingEngine
from core.channel_outer_entry import (
    continuation_entry, outside_reentry, two_closed_bodies_ready,
)
from test_channel_next_live_push import push_frame
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
@pytest.mark.parametrize("route", ["continuation"])
@pytest.mark.parametrize("bar", [-3, -2])
@pytest.mark.parametrize("invalid", ["opposite", "doji", "wick", "nan"])
def test_each_closed_body_is_required(side, route, bar, invalid):
    f = push_frame(side) if route == "push" else continuation_frame(side)
    price = (103.2 if route == "push" else 103.4) if side == "LONG" else (96.8 if route == "push" else 96.6)
    fn = continuation_entry
    assert fn(f, price)["side"] == side
    idx = f.index[bar]
    if invalid == "opposite":
        opened, closed = f.loc[idx, ["open", "close"]]
        f.loc[idx, ["open", "close"]] = [closed, opened]
    elif invalid == "doji":
        f.loc[idx, "open"] = f.loc[idx, "close"]
    elif invalid == "wick":
        f.loc[idx, ["high", "low"]] = [120., 80.]
    else:
        f.loc[idx, "open"] = float("nan")
    assert not two_closed_bodies_ready(f, side)
    assert fn(f, price)["action"] == "WAIT"
    assert TradingEngine._channel_swing_action(f, price)["action"] == "WAIT"
    assert outside_reentry(f, price, side)["action"] == "WAIT"


@pytest.mark.anyio
@pytest.mark.parametrize("side", ["LONG", "SHORT"])
async def test_order_snapshot_rechecks_two_closed_bodies(side):
    f = continuation_frame(side)
    price = 103.4 if side == "LONG" else 96.6
    e = _execution_engine(f, side, True)
    e.account.positions.clear()
    e.tickers[SYMBOL] = price
    bar = e._channel_candidate_bar_id(f)
    assert await e._fresh_channel_entry_snapshot(SYMBOL, side, bar) is not None
    f.loc[f.index[-3], "open"] = f.iloc[-3]["close"]
    assert await e._fresh_channel_entry_snapshot(SYMBOL, side, bar) is None


@pytest.mark.anyio
@pytest.mark.parametrize("side", ["LONG", "SHORT"])
@pytest.mark.parametrize("pending", [False, True])
async def test_ma3_turn_alone_does_not_sell(side, pending):
    f = _narrow_channel_frame()
    sign = 1 if side == "LONG" else -1
    f["kc_upper"], f["kc_lower"] = 110., 90.
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
    assert e.account.events == []
    assert SYMBOL in e.account.positions


@pytest.mark.anyio
@pytest.mark.parametrize("side", ["LONG", "SHORT"])
@pytest.mark.parametrize("cached", [False, True])
async def test_cached_or_fresh_signal_cannot_bypass_body_gate(side, cached, monkeypatch):
    from unittest.mock import AsyncMock
    f = continuation_frame(side)
    f.loc[f.index[-3], "open"] = f.iloc[-3]["close"]
    price = 103.4 if side == "LONG" else 96.6
    e = _execution_engine(f, side, True)
    e.account.positions.clear()
    monkeypatch.setattr("core.engine.DEFAULT_SYMBOLS", [SYMBOL])
    snapshot = dict(price=price, kc_upper=102., kc_lower=98., frame=f)
    e._fresh_channel_entry_snapshot = AsyncMock(return_value=snapshot)
    signal = dict(side=side, entry_mode="CHANNEL_SWING", action="ENTER_MARKET")
    assert not await e._place_structured_entry(
        SYMBOL, signal, price, channel_snapshot=snapshot if cached else None)
    assert not e.account.events
    assert any("缺少已收線實體破軌與下一根同色確認" in message for message, _ in e.account.logs)
