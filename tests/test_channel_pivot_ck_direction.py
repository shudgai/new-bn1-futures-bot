"""CK direction gates pivots independently of MA15 and live candle color."""
import pytest
from test_channel_pivot_entry import market
from core.channel_pivot_entry import pivot_entry
from core.channel_outer_entry import outside_entry


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
@pytest.mark.parametrize("case", ["aligned", "flat", "mixed", "opposite", "bad", "rail_opposite"])
def test_pivot_ck_direction(side, case):
    frame = market(side)
    sign = 1 if side == "LONG" else -1
    frame["kc_middle"] = 100.
    frame.loc[16:18, "kc_middle"] = [100., 100. + sign, 100. + 2 * sign]
    frame["ma15"] = float("nan")
    if case == "flat": frame.loc[16:18, "kc_middle"] = 100.
    if case == "mixed": frame.loc[16:18, "kc_middle"] = [100., 101., 100.]
    if case == "opposite": frame.loc[16:18, "kc_middle"] = [100., 100. - sign, 100. - 2 * sign]
    if case == "bad": frame.loc[18, "kc_middle"] = float("nan")
    if case == "rail_opposite":
        rail = "kc_upper" if side == "LONG" else "kc_lower"
        frame.loc[18, rail] -= sign
    result = pivot_entry(frame, float(frame.iloc[-1]["close"]))
    assert (result["action"] == "ENTER") == (case == "aligned")
    if case == "aligned": assert result["side"] == side


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_outside_needs_no_color_or_closed_confirmation(side):
    frame = market(side)
    price = 111. if side == "LONG" else 89.
    frame.loc[19, "open"] = price + (1 if side == "LONG" else -1)
    frame["ma15"] = float("nan")
    assert outside_entry(frame, price)["side"] == side


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_single_closed_directional_candle_outside_ck_enters_without_second_bar(side):
    frame = market()
    sign = 1 if side == "LONG" else -1
    frame["kc_middle"] = 100.
    frame.loc[16:18, "kc_middle"] = [100., 100. + sign, 100. + 2 * sign]
    frame.loc[18, ["open", "close", "high", "low"]] = (
        [105., 111., 112., 104.] if side == "LONG" else [95., 89., 96., 88.]
    )
    frame.loc[19, ["open", "close", "high", "low"]] = (
        [111., 109., 112., 108.] if side == "LONG" else [89., 91., 92., 88.]
    )
    result = outside_entry(frame, float(frame.iloc[-1]["close"]))
    assert result == {
        "action": "ENTER",
        "side": side,
        "reason": "KC_CLOSED_OUTSIDE_" + side,
    }


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
@pytest.mark.parametrize("side", ["LONG", "SHORT"])
@pytest.mark.parametrize("case", ["valid", "ck_opposite", "no_pivot", "old_pivot", "wrong_token", "wrong_side", "outside", "outside_opposite"])
async def test_reentry_snapshot_requires_pullback_and_directional_reclaim(side, case):
    from test_channel_swing_execution import _execution_engine, SYMBOL
    frame = market(side)
    price = float(frame.iloc[-1]["close"])
    engine = _execution_engine(frame, side, True)
    engine.account.positions.clear()
    engine.account.save_state = lambda: None
    ticket = dict(side=side, phase="closed", token="close-1", mode="outer_cycle", requires_pullback=True, exit_bar_id=17, pullback_bar=18)
    engine.account.channel_profit_reentries = {SYMBOL: ticket}
    token = "close-1"
    if case == "ck_opposite": frame.loc[16:18, "kc_middle"] = frame.loc[16:18, "kc_middle"].to_numpy()[::-1]
    if case == "no_pivot": frame.loc[18, "open"] = frame.loc[18, "close"]
    if case == "old_pivot": ticket["exit_bar_id"] = 19
    if case == "wrong_token": token = "different"
    if case == "wrong_side": ticket["side"] = "SHORT" if side == "LONG" else "LONG"
    if case.startswith("outside"):
        sign = 1 if side == "LONG" else -1
        price = 100. + (11. * sign if case == "outside" else -11. * sign)
        frame.loc[18, "open"] = frame.loc[18, "close"]
        frame.loc[19, "open"] = price - sign
        frame["ma15"] = float("nan")
    engine.tickers[SYMBOL] = price
    snapshot = await engine._fresh_channel_entry_snapshot(SYMBOL, side, profit_reentry_token=token)
    assert (snapshot is not None) == (case == "outside")
    if snapshot: assert snapshot["signal_code"] == "KC_OUTSIDE_" + side


@pytest.mark.anyio
@pytest.mark.parametrize("side", ["LONG", "SHORT"])
@pytest.mark.parametrize("entry", ["pivot", "outside"])
@pytest.mark.parametrize("block", ["none", "room", "balance", "market_risk", "price_safety"])
async def test_order_safety_for_both_entry_types(side, entry, block, monkeypatch):
    from unittest.mock import AsyncMock
    from core.engine import TradingEngine
    from test_channel_swing_execution import _execution_engine, SYMBOL
    from test_channel_symmetric_rules import market as outer_market
    frame = market(side) if entry == "pivot" else outer_market(side)
    price = float(frame.iloc[-1]["close"])
    engine = _execution_engine(frame, side, True)
    engine.account.positions.clear()
    engine.tickers[SYMBOL] = price
    monkeypatch.setattr("core.engine.DEFAULT_SYMBOLS", [SYMBOL])
    engine._abnormal_market_entry_allowed = lambda *a, **k: block != "market_risk"
    if block == "room": frame["atr"] = .001
    if block == "balance": engine.account.get_available_balance = lambda: 0.
    if block == "price_safety": engine._execution_price_is_safe = AsyncMock(return_value=False)
    decision = TradingEngine._channel_swing_action(frame, price)
    assert decision["action"] == "ENTER" and decision["side"] == side
    result = await engine._execute_confirmed_channel_break(SYMBOL, frame, price, side)
    assert bool(result) == (block == "none"), engine.account.logs
    assert len(engine.account.events) == (1 if block == "none" else 0)


@pytest.mark.anyio
@pytest.mark.parametrize("side", ["LONG", "SHORT"])
@pytest.mark.parametrize("reopen", [False, True])
async def test_confirmed_turn_and_outside_entry_remain_tradable(side, reopen, monkeypatch):
    from core.engine import TradingEngine
    from core.channel_pivot_entry import PIVOT_CODES
    from test_channel_swing_execution import _execution_engine, SYMBOL
    frame = market(side)
    price = float(frame.iloc[-1]["close"])
    if side == "LONG": frame["kc_lower"] = price + .1
    else: frame["kc_upper"] = price - .1
    assert outside_entry(frame, price)["action"] == "WAIT"
    decision = TradingEngine._channel_swing_action(frame, price)
    assert decision["side"] == side and decision["reason"] in PIVOT_CODES
    engine = _execution_engine(frame, side, True)
    engine.account.positions.clear()
    engine.account.save_state = lambda: None
    engine.tickers[SYMBOL] = price
    engine._abnormal_market_entry_allowed = lambda *a, **k: True
    monkeypatch.setattr("core.engine.DEFAULT_SYMBOLS", [SYMBOL])
    if reopen:
        engine.account.channel_profit_reentries = {SYMBOL: dict(side=side, phase="closed", token="turn", exit_bar_id=18)}
        await engine._try_profit_reentry(SYMBOL, frame, price, False)
    else:
        assert await engine._execute_confirmed_channel_break(SYMBOL, frame, price, side)
    assert len(engine.account.events) == int(not reopen), engine.account.logs
    if not reopen: assert engine.account.events[0][2] == side


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_first_closed_turn_enters_before_second_candle_closes(side):
    from core.engine import TradingEngine
    frame = market(side)
    price = float(frame.iloc[-1]["close"])
    # The live second candle can be neutral; only the first closed turn confirms.
    frame.loc[19, "open"] = price
    assert TradingEngine._channel_swing_action(frame, price)["side"] == side
    assert pivot_entry(frame.iloc[:-1], price)["action"] == "WAIT"
    # Advancing a bar without a new pivot expires the old signal.
    frame.loc[20] = frame.loc[19].copy()
    assert pivot_entry(frame, price)["action"] == "WAIT"


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
@pytest.mark.parametrize("case", ["aligned", "flat", "mixed", "opposite", "invalid", "missing", "short_history", "rail_opposite", "live_opposite"])
def test_outside_requires_clear_ck_direction(side, case):
    from test_channel_symmetric_rules import market as outer_market
    frame = outer_market(side)
    price = float(frame.iloc[-1]["close"])
    if case == "flat": frame.loc[16:18, "kc_middle"] = 100.
    if case == "mixed": frame.loc[16:18, "kc_middle"] = [100., 101., 100.]
    if case == "opposite": frame.loc[16:18, "kc_middle"] = frame.loc[16:18, "kc_middle"].to_numpy()[::-1]
    if case == "invalid": frame.loc[18, "kc_middle"] = float("nan")
    if case == "missing": frame = frame.drop(columns="kc_middle")
    if case == "short_history": frame = frame.tail(3)
    if case == "rail_opposite": frame.loc[18, "kc_upper" if side == "LONG" else "kc_lower"] -= 1 if side == "LONG" else -1
    if case == "live_opposite": frame.loc[19, "kc_middle"] = 1. if side == "LONG" else 1000.
    result = outside_entry(frame, price)
    assert (result["action"] == "ENTER") == (case in {"aligned", "live_opposite"})
    if result["action"] == "ENTER":
        assert result["side"] == side


@pytest.mark.anyio
@pytest.mark.parametrize("side", ["LONG", "SHORT"])
@pytest.mark.parametrize("reopen", [False, True])
async def test_outside_order_rejects_newly_unclear_ck(side, reopen, monkeypatch):
    from core.engine import TradingEngine
    from test_channel_swing_execution import _execution_engine, SYMBOL
    from test_channel_symmetric_rules import market as outer_market
    frame = outer_market(side)
    price = float(frame.iloc[-1]["close"])
    engine = _execution_engine(frame, side, True)
    engine.account.positions.clear()
    engine.account.save_state = lambda: None
    engine.tickers[SYMBOL] = price
    monkeypatch.setattr("core.engine.DEFAULT_SYMBOLS", [SYMBOL])
    decision = TradingEngine._channel_swing_action(frame, price)
    assert decision["side"] == side
    signal = {"side": side, "entry_mode": "CHANNEL_SWING", "action": "ENTER_MARKET", "candidate_bar_id": 18,
              "signal_code": decision["reason"], "reason": decision["reason"]}
    if reopen:
        engine.account.channel_profit_reentries = {SYMBOL: dict(side=side, phase="closed", token="trend", exit_bar_id=19)}
        signal["profit_reentry_token"] = "trend"
    # A flat CK direction must block a fresh outer entry and re-entry alike.
    frame.loc[16:18, "kc_middle"] = 100.
    placed = await engine._place_structured_entry(SYMBOL, signal, price)
    assert not placed
    assert not engine.account.events
