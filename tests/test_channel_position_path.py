import pytest
import pandas as pd
from core.engine import TradingEngine
from test_channel_swing_execution import _execution_engine, SYMBOL


def frame_for(side):
    f = pd.DataFrame({"timestamp": [60000 * (i + 1) for i in range(8)],
        "open": 100., "close": 100., "high": 100.2, "low": 99.8,
        "ma3": 100., "ma15": 101. if side == "LONG" else 99.,
        "kc_upper": 102., "kc_lower": 98., "atr": 4.})
    return f


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
@pytest.mark.parametrize("ratio", [.39, .40, .41])
@pytest.mark.parametrize("cross", [False, True])
def test_middle_strict_cross_and_space(side, ratio, cross):
    f = frame_for(side)
    sign = 1 if side == "LONG" else -1
    f["ma15"] = 102 - 4 * ratio if side == "LONG" else 98 + 4 * ratio
    f.loc[3, "ma3"] = 103 if side == "LONG" else 97
    f.loc[4:, "ma3"] = 100
    price = 100 - sign * .1 if cross else 100
    f.loc[6:7, "close"] = price
    f.loc[7, "open"] = 100.  # Immediate opposite body beyond the middle.
    result = TradingEngine._channel_swing_action(f, price, side, position_open_timestamp=120)
    assert result["action"] == ("EXIT" if cross else "HOLD")


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_preentry_outside_does_not_unlock(side):
    f = frame_for(side)
    f.loc[0, "ma3"] = 103 if side == "LONG" else 97
    price = 99.9 if side == "LONG" else 100.1
    assert TradingEngine._channel_swing_action(f, price, side, position_open_timestamp=120)["action"] == "HOLD"


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_path_survives_window_roll_and_resets_for_new_position(side):
    f = frame_for(side); state = {}
    f.loc[2, "ma3"] = 103 if side == "LONG" else 97
    _, ready = TradingEngine._channel_position_path(f, side, 120, state)
    assert ready
    _, ready = TradingEngine._channel_position_path(f.iloc[-4:], side, 120, state)
    assert ready
    _, ready = TradingEngine._channel_position_path(f.iloc[-4:], side, 420, state)
    assert not ready


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_waterfall_inside_holds_before_path_is_ready(side):
    f = frame_for(side); f["atr"] = 1.
    price = 99. if side == "LONG" else 101.
    result = TradingEngine._channel_swing_action(f, price, side, position_open_timestamp=420)
    assert result["action"] == "HOLD"


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_waterfall_opposite_outer_break_requires_confirmation(side):
    f = frame_for(side); f["atr"] = 1.
    price = 97. if side == "LONG" else 103.
    result = TradingEngine._channel_swing_action(f, price, side, position_open_timestamp=420)
    assert result["action"] == "HOLD"


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_first_tiny_reverse_body_after_surge_holds_without_reentry(side):
    f = frame_for(side); f["atr"] = 1.
    peak = 103. if side == "LONG" else 97.
    f.loc[6, "close"] = peak
    f.loc[7, "open"] = peak
    price = peak + (-.01 if side == "LONG" else .01)
    result = TradingEngine._channel_swing_action(f, price, side, position_open_timestamp=120)
    assert result["action"] == "HOLD"
    assert TradingEngine._channel_swing_action(f, price, side, position_open_timestamp=480)["action"] == "HOLD"


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_continuation_needs_current_price_outside(side):
    f = frame_for(side)
    sign = 1 if side == "LONG" else -1
    f["ma15"] = [100 + sign * i * .01 for i in range(8)]
    f.loc[5, ["open", "close"]] = [100+sign*2.1, 100+sign*2.3]
    f.loc[6, ["open", "close"]] = [100+sign*2.3, 100+sign*2.5]
    f.loc[7, ["open", "close"]] = 100+sign*2.5
    f["high"] = f[["open", "close"]].max(axis=1)+.1
    f["low"] = f[["open", "close"]].min(axis=1)-.1
    assert TradingEngine._channel_swing_action(f, 100+sign*2.5)["action"] == "ENTER"
    assert TradingEngine._channel_swing_action(f, 100)["action"] == "WAIT"


@pytest.mark.anyio
@pytest.mark.parametrize("success", [True, False])
async def test_waterfall_scan_keeps_position(success):
    f = frame_for("LONG"); f["atr"] = 1.
    f.loc[7, "close"] = 99.
    e = _execution_engine(f, "LONG", success)
    e.account.positions[SYMBOL]["open_timestamp"] = 420
    e.account.save_state = lambda: None
    e.market_prebreakout_directions = {}
    e.tickers[SYMBOL] = 99.
    await e._process_single_symbol(SYMBOL, now_time=500, btc_1m_turn=None, daily_halt=False)
    assert not e.account.events, e.account.logs
    assert SYMBOL in e.account.positions


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_normal_opposite_break_requires_postentry_path(side):
    f = frame_for(side); f["atr"] = 10.
    sign = -1 if side == "LONG" else 1
    f.loc[5, ["open", "close"]] = [100., 100+sign*2.2]
    f.loc[6, ["open", "close"]] = [100+sign*2.2, 100+sign*2.5]
    f.loc[7, ["open", "close"]] = 100+sign*2.5
    f["high"] = f[["open", "close"]].max(axis=1)+.1
    f["low"] = f[["open", "close"]].min(axis=1)-.1
    price = float(f.iloc[-1]["close"])
    assert TradingEngine._channel_swing_action(f, price, side, position_open_timestamp=120)["action"] == "HOLD"
    f.loc[2, "ma3"] = 103 if side == "LONG" else 97
    assert TradingEngine._channel_swing_action(f, price, side, position_open_timestamp=120)["action"] == "REVERSE"


@pytest.mark.anyio
@pytest.mark.parametrize("success", [True, False])
async def test_live_waterfall_cannot_close_or_reverse_without_confirmation(success):
    f = frame_for("SHORT"); f["atr"] = 1.
    f.loc[7, "close"] = 103.
    e = _execution_engine(f, "SHORT", success)
    e.account.positions[SYMBOL]["open_timestamp"] = 420
    events = e.account.events
    async def snapshot(*args, **kwargs):
        return {"price": 103., "frame": f}
    async def place(symbol, signal, price):
        assert symbol not in e.account.positions
        events.append(("open", symbol, signal["side"]))
        return True
    e._fresh_channel_entry_snapshot = snapshot
    e._place_structured_entry = place
    assert not await e._execute_confirmed_channel_break(SYMBOL, f, 103., "LONG")
    assert not events
    assert SYMBOL in e.account.positions


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
@pytest.mark.parametrize("closed", [False, True])
def test_two_abnormal_opposite_bodies_do_not_exit(side, closed):
    f = frame_for(side); f["atr"] = 1.
    sign = -1 if side == "LONG" else 1
    start = 5 if closed else 6
    f.loc[start, ["open", "close"]] = [100., 100+sign*.6]
    f.loc[start+1, ["open", "close"]] = [100+sign*.6, 100+sign*1.2]
    if closed:
        f.loc[7, ["open", "close"]] = 100+sign*1.2
    price = float(f.iloc[-1]["close"])
    result = TradingEngine._channel_swing_action(f, price, side, position_open_timestamp=120)
    assert result["action"] == "HOLD"
    assert TradingEngine._channel_swing_action(f, price, side, position_open_timestamp=480)["action"] == "HOLD"
    f.loc[start, "close"] = 100+sign*.2
    assert TradingEngine._channel_swing_action(f, price, side, position_open_timestamp=120)["action"] == "HOLD"


@pytest.mark.anyio
async def test_cross_only_never_trades_with_legacy_mode_disabled(monkeypatch):
    import core.config as config
    monkeypatch.setattr(config, "ENABLE_CONTINUOUS_REVERSE_MODE", False)
    f=frame_for("LONG")
    f.loc[6:7, "ma3"] = [99., 101.]
    e=_execution_engine(f, "LONG", True)
    e.account.save_state=lambda: None
    e.market_prebreakout_directions={}
    e.tickers[SYMBOL]=100.
    await e._process_single_symbol(SYMBOL, 500., None, False)
    assert not e.account.events
    assert not any("處理失敗" in message for message, _ in e.account.logs)


@pytest.mark.anyio
async def test_retired_pending_orders_cancel_without_fill_check():
    e=_execution_engine(frame_for("LONG"), "LONG", True)
    e.account.pending_limit_orders={SYMBOL: {"entry_context": {"entry_mode": "MA3_MA15_LIMIT"}}}
    calls=[]
    async def cancel(symbol, reason):
        calls.append(symbol)
        e.account.pending_limit_orders.pop(symbol)
    e.account.cancel_pending_limit=cancel
    await e._validate_pending_limit_orders(500.)
    assert calls == [SYMBOL]
    assert not e.account.events


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
@pytest.mark.parametrize("inside", [False, True])
@pytest.mark.parametrize("space", [.25, .50, .60])
def test_surge_turn_respects_current_ma3_and_channel_space(side, inside, space):
    f = frame_for(side)
    sign = 1 if side == "LONG" else -1
    f["atr"] = .5
    f["ma15"] = 102 - 4*space if side == "LONG" else 98 + 4*space
    f.loc[4, "ma3"] = 103 if side == "LONG" else 97
    f.loc[5, "ma3"] = 100  # This position already returned inside once.
    f.loc[6, ["open", "close"]] = [100., 100+sign*3]
    f.loc[6, "ma3"] = 103 if side == "LONG" else 97
    f.loc[7, "ma3"] = 100 if inside else (103 if side == "LONG" else 97)
    # Small opposite body past the middle, so only MA3 can prevent the exit; space is no longer a gate.
    price = 100-sign*.1
    f.loc[7, ["open", "close"]] = [price+sign*.1, price]
    result = TradingEngine._channel_swing_action(f, price, side, position_open_timestamp=120)
    assert result["action"] == ("EXIT" if inside else "HOLD")  # No closed-candle wait.


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
@pytest.mark.parametrize("pair", [False, True])
@pytest.mark.parametrize("closed", [False, True])
@pytest.mark.parametrize("distance", [0., .2])
def test_adverse_bodies_hold_at_or_outside_favorable_rail(side, pair, closed, distance):
    f = frame_for(side)
    f["atr"] = 1.
    direction = 1 if side == "LONG" else -1
    rail = 102. if side == "LONG" else 98.
    price = rail + direction * distance
    end = 6 if closed else 7
    if pair:
        f.loc[end-1, ["open", "close"]] = [price+direction*1.2, price+direction*.6]
        f.loc[end, ["open", "close"]] = [price+direction*.6, price]
    else:
        f.loc[end, ["open", "close"]] = [price+direction*1.2, price]
    if closed:
        f.loc[7, ["open", "close"]] = price
    f["high"] = f[["open", "close"]].max(axis=1)+.1
    f["low"] = f[["open", "close"]].min(axis=1)-.1
    # Include a completed MA3 path: the hold must not rely on missing history.
    f.loc[2, "ma3"] = rail + direction
    assert TradingEngine._channel_swing_action(
        f, price, side, position_open_timestamp=120,
    )["action"] == "HOLD"


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
@pytest.mark.parametrize("closed_space", [.39, .40, .4106594857, .50, .60])
@pytest.mark.parametrize("live_space", [.25, .50])
def test_exit_independent_of_live_and_closed_space(side, closed_space, live_space):
    f = frame_for(side)
    f.loc[3, "ma3"] = 103. if side == "LONG" else 97.
    for index, ratio in ((6, closed_space), (7, live_space)):
        f.loc[index, "ma15"] = 102-4*ratio if side == "LONG" else 98+4*ratio
    price = 99.9 if side == "LONG" else 100.1
    f.loc[6:7, "close"] = price
    f.loc[7, "open"] = 100.  # Immediate opposite body beyond the middle.
    result = TradingEngine._channel_swing_action(f, price, side, position_open_timestamp=120)
    assert result["action"] == "EXIT"


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
@pytest.mark.parametrize("closed_offset", [-.1, 0., .1])
@pytest.mark.parametrize("live_offset", [-.1, 0., .1])
def test_middle_exit_uses_live_body_independently_of_closed_candle(side, closed_offset, live_offset):
    f = frame_for(side)
    direction = 1 if side == "LONG" else -1
    f.loc[3, "ma3"] = 100+direction*3
    f.loc[6, "close"] = 100+direction*closed_offset
    f.loc[6, "low"] = 99.  # Wick crossing must not count as a close.
    f.loc[6, "high"] = 101.
    price = 100+direction*live_offset
    f.loc[7, "open"] = 100.
    f.loc[7, "close"] = price
    result = TradingEngine._channel_swing_action(f, price, side, position_open_timestamp=120)
    assert result["action"] == ("EXIT" if live_offset < 0 else "HOLD")
