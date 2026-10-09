import pytest
import asyncio
from types import SimpleNamespace
from unittest.mock import Mock

from core.services.entry_contract import ENTRY_CODES, evaluate_entry_contract
from test_breakout_only_entry import breakout_frame, live_outer_frame


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_valid_two_closed_bar_breakout_is_directional(side):
    frame = breakout_frame(side)
    decision = evaluate_entry_contract(frame, symbol="TEST/USDT")

    assert decision is not None
    assert decision["side"] == side
    assert decision["type"] == f"KC_2BAR_CONFIRM_{side}"


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_continuation_signal_is_rejected_before_entry_evaluation(side):
    frame = breakout_frame(side)
    diagnostics = {}
    assert evaluate_entry_contract(
        frame, code=f"KC_OUTSIDE_{side}", symbol="TEST/USDT",
        diagnostics=diagnostics,
    ) is None
    assert diagnostics["reason"] == "WAIT_KC_CONTINUATION"


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_live_body_breakout_enters_on_first_qualifying_outer_rail_cross(side):
    frame = live_outer_frame(side)
    live = frame.index[-1]
    quote = float(frame.loc[live, "close"])

    decision = evaluate_entry_contract(
        frame, quote, symbol="TEST/USDT"
    )

    assert f"KC_LIVE_BODY_BREAKOUT_{side}" in ENTRY_CODES
    assert decision is not None
    assert decision["side"] == side
    assert decision["type"] == f"KC_LIVE_BODY_BREAKOUT_{side}"
    assert decision["entry_phase"] == "KC_LIVE_OUTER_BREAKOUT"
    assert decision["confirmation_bar_id"] == frame.iloc[-1]["timestamp"]


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_unfilled_live_breakout_falls_back_to_confirmed_two_bar_breakout(side):
    frame = live_outer_frame(side)
    live = frame.index[-1]
    quote = float(frame.loc[live, "close"])
    frame.loc[live, "open"] = quote - 0.2 if side == "LONG" else quote + 0.2

    # The instant signal is below its minimum body size, but the two closed
    # candles already form a confirmed breakout and the quote remains outside.
    assert evaluate_entry_contract(
        frame, quote, f"KC_LIVE_BODY_BREAKOUT_{side}", symbol="TEST/USDT"
    ) is None
    fallback = evaluate_entry_contract(frame, quote, symbol="TEST/USDT")
    assert fallback is not None
    assert fallback["type"] == f"KC_2BAR_CONFIRM_{side}"
    assert fallback["entry_phase"] == "KC_2BAR_CLOSED_CONFIRM"


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_no_outer_rail_break_means_no_entry(side):
    frame = breakout_frame(side)
    live = frame.index[-1]
    lower = float(frame.loc[live, "kc_lower"])
    upper = float(frame.loc[live, "kc_upper"])
    quote = (lower + upper) / 2

    assert evaluate_entry_contract(frame, quote, symbol="TEST/USDT") is None


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_later_continuation_requires_prior_breakout_qualification(side):
    frame = breakout_frame(side)
    breakout = frame.index[-2]
    continuation_bar = frame.index[-1]
    # The breakout was observed but not filled. Its qualification carries into
    # the next bar, where a same-side live push remains strictly outside KC.
    frame.loc[continuation_bar, "is_closed"] = True
    frame.loc[continuation_bar, ["open", "close", "high", "low"]] = (
        [101.7, 101.55, 101.8, 101.4] if side == "LONG"
        else [98.3, 98.45, 98.6, 98.2]
    )
    next_bar = frame.iloc[-1].copy()
    next_bar["timestamp"] += 60_000
    next_bar["is_closed"] = False
    if side == "LONG":
        next_bar["open"], next_bar["close"] = 101.7, 102.3
        next_bar["high"], next_bar["low"] = 102.4, 101.6
        next_bar["kc_upper"], next_bar["kc_middle"], next_bar["kc_lower"] = 101.5, 100.4, 99.4
        next_bar["ma5"], next_bar["ma15"] = 101.1, 100.1
    else:
        next_bar["open"], next_bar["close"] = 98.3, 97.7
        next_bar["high"], next_bar["low"] = 98.4, 97.6
        next_bar["kc_upper"], next_bar["kc_middle"], next_bar["kc_lower"] = 100.6, 99.6, 98.5
        next_bar["ma5"], next_bar["ma15"] = 98.9, 99.9
    frame.loc[len(frame)] = next_bar

    qualification = {
        "side": side,
        "pending_signal_id": f"prior-breakout-{side}",
        "breakout_bar_id": float(frame.loc[breakout, "timestamp"]),
    }
    account = SimpleNamespace(
        positions={}, trades=[], last_closed_at={},
        breakout_qualification={"TEST/USDT": qualification},
    )
    quote = float(next_bar["close"])
    decision = evaluate_entry_contract(
        frame, quote, account=account, symbol="TEST/USDT"
    )

    assert decision is not None
    assert decision["type"] == f"KC_OUTSIDE_{side}"
    assert decision["entry_phase"] == "KC_CONTINUATION_ENTRY"
    assert decision["qualification_signal_id"] == qualification["pending_signal_id"]

    account.breakout_qualification.clear()
    assert evaluate_entry_contract(
        frame, quote, account=account, symbol="TEST/USDT"
    ) is None


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_unfilled_live_breakout_is_saved_for_later_continuation(side):
    from core.engine import TradingEngine

    frame = live_outer_frame(side)
    quote = float(frame.iloc[-1]["close"])
    qualifications = {}
    account = SimpleNamespace(
        positions={}, trades=[], last_closed_at={},
        breakout_qualification=qualifications,
        record_qualification=Mock(
            side_effect=lambda symbol, value: qualifications.__setitem__(symbol, value)
        ),
        log=Mock(),
    )
    engine = TradingEngine.__new__(TradingEngine)
    engine.account = account

    opened = asyncio.run(engine._execute_confirmed_channel_break(
        "TEST/USDT", frame, quote, side, daily_halt=True,
        v8_reason=f"KC_LIVE_BODY_BREAKOUT_{side}",
    ))

    assert opened is False
    assert qualifications["TEST/USDT"]["side"] == side
    assert qualifications["TEST/USDT"]["breakout_bar_id"] == float(frame.iloc[-1]["timestamp"])


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_live_body_breakout_rejects_small_body_and_late_chase(side):
    frame = live_outer_frame(side)
    live = frame.index[-1]
    lower = float(frame.loc[live, "kc_lower"])
    upper = float(frame.loc[live, "kc_upper"])
    rail = upper if side == "LONG" else lower
    code = f"KC_LIVE_BODY_BREAKOUT_{side}"

    frame.loc[live, "open"] = rail
    small_body_quote = rail + 0.1 if side == "LONG" else rail - 0.1
    assert evaluate_entry_contract(frame, small_body_quote, code, symbol="TEST/USDT") is None

    late_quote = rail + 3.1 if side == "LONG" else rail - 3.1
    assert evaluate_entry_contract(frame, late_quote, code, symbol="TEST/USDT") is None


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_live_body_breakout_requires_open_inside_channel(side):
    frame = live_outer_frame(side)
    live = frame.index[-1]
    lower = float(frame.loc[live, "kc_lower"])
    upper = float(frame.loc[live, "kc_upper"])
    rail = upper if side == "LONG" else lower
    frame.loc[live, "open"] = rail + 0.1 if side == "LONG" else rail - 0.1
    quote = rail + 0.7 if side == "LONG" else rail - 0.7

    assert evaluate_entry_contract(
        frame, quote, f"KC_LIVE_BODY_BREAKOUT_{side}", symbol="TEST/USDT"
    ) is None


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_directional_k1_must_begin_inside_the_kc_channel(side):
    frame = breakout_frame(side)
    index = frame.index[-3]
    edge = "kc_upper" if side == "LONG" else "kc_lower"
    frame.loc[index, "open"] = (
        frame.loc[index, edge] + (0.1 if side == "LONG" else -0.1)
    )

    assert evaluate_entry_contract(frame, symbol="TEST/USDT") is None
