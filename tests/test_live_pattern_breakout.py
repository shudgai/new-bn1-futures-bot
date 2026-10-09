"""Live rail breaks require the user's small pullback then impulse pattern."""
import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pandas as pd
import pytest

from core.services.entry_contract import (
    ENTRY_CODES,
    evaluate_entry_contract,
)
from core.services.entry_firewall import validate_account_entry


def pattern_frame(side="SHORT", pullback_bars=2):
    base = int(time.time() // 60) * 60_000
    sign = 1 if side == "LONG" else -1
    price = 100.0
    rows = []
    # Initial small push follows the eventual breakout direction.
    first_close = price + sign * 0.10
    rows.append((price, first_close))
    price = first_close
    # One to three small bars pull back against the breakout direction.
    for _ in range(pullback_bars):
        close = price - sign * 0.10
        rows.append((price, close))
        price = close

    frame_rows = []
    for index, (opening, close) in enumerate(rows):
        frame_rows.append(dict(
            timestamp=base - (len(rows) - index) * 60_000,
            open=opening, close=close,
            high=max(opening, close) + 0.10,
            low=min(opening, close) - 0.10,
            kc_upper=101.0, kc_middle=100.0, kc_lower=99.0,
            atr=1.0, ma3=100.0, ma5=100.0, ma15=100.0,
            is_closed=True,
        ))
    live_open = price
    live_quote = (101.2 if side == "LONG" else 98.8)
    frame_rows.append(dict(
        timestamp=base, open=live_open, close=live_quote,
        high=max(live_open, live_quote), low=min(live_open, live_quote),
        kc_upper=101.0, kc_middle=100.0, kc_lower=99.0,
        atr=1.0, ma3=100.0, ma5=100.0, ma15=100.0,
        is_closed=False,
    ))
    frame = pd.DataFrame(frame_rows)
    frame.attrs.update(timeframe_ms=60_000, entry_finality_verified=True)
    return frame, live_quote


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
@pytest.mark.parametrize("pullback_bars", [1, 2, 3])
def test_live_pattern_breakout_accepts_small_counter_candles_then_large_break(
    side, pullback_bars
):
    frame, quote = pattern_frame(side, pullback_bars)

    decision = evaluate_entry_contract(frame, quote, symbol="龙虾/USDT")

    assert decision is not None
    assert decision["side"] == side
    assert decision["type"] == f"KC_LIVE_PATTERN_BREAKOUT_{side}"
    assert decision["entry_phase"] == "KC_LIVE_PATTERN_BREAKOUT"
    assert decision["live_pattern_pullback_bars"] == pullback_bars
    assert decision["live_pattern_body_atr"] >= 0.5


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_live_pattern_breakout_is_in_entry_whitelist_and_revalidated(side):
    frame, quote = pattern_frame(side, 2)
    decision = evaluate_entry_contract(frame, quote, symbol="龙虾/USDT")
    account = SimpleNamespace(
        positions={}, trades=[], last_closed_at={},
        entry_frame_provider=AsyncMock(return_value=frame),
    )
    context = dict(
        entry_signal_code=decision["type"],
        channel_confirmation_bar_id=decision["confirmation_bar_id"],
    )

    assert decision["type"] in ENTRY_CODES
    validated = asyncio.run(
        validate_account_entry(account, "龙虾/USDT", side, context)
    )
    assert validated["pending_signal_id"] == decision["pending_signal_id"]


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
@pytest.mark.parametrize("failure", ["inside_channel", "small_body", "outside_open", "bad_pattern"])
def test_live_pattern_breakout_fails_closed_on_invalid_shape_or_channel_price(side, failure):
    frame, quote = pattern_frame(side, 2)
    sign = 1 if side == "LONG" else -1
    live = frame.index[-1]
    if failure == "inside_channel":
        quote = 100.0
    elif failure == "small_body":
        frame.loc[live, "open"] = 100.9 if side == "LONG" else 99.1
        quote = 101.1 if side == "LONG" else 98.9
    elif failure == "outside_open":
        frame.loc[live, "open"] = 101.1 if side == "LONG" else 98.9
        quote = 102.0 if side == "LONG" else 98.0
    elif failure == "bad_pattern":
        row = frame.index[-2]
        frame.loc[row, "close"] = frame.loc[row, "open"] + sign * 0.10
        frame.loc[row, "high"] = max(frame.loc[row, "open"], frame.loc[row, "close"]) + 0.10
        frame.loc[row, "low"] = min(frame.loc[row, "open"], frame.loc[row, "close"]) - 0.10

    diagnostics = {}
    assert evaluate_entry_contract(
        frame, quote, symbol="龙虾/USDT", diagnostics=diagnostics
    ) is None
    assert diagnostics["reason"] != f"KC_LIVE_PATTERN_BREAKOUT_{side}"


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_live_pattern_requires_the_correct_breakout_side(side):
    frame, _ = pattern_frame(side, 2)
    wrong_side_quote = 98.8 if side == "LONG" else 101.2

    assert evaluate_entry_contract(frame, wrong_side_quote, symbol="龙虾/USDT") is None


@pytest.mark.parametrize("actual_side", ["LONG", "SHORT"])
def test_live_pattern_code_cannot_authorize_the_opposite_side(actual_side):
    frame, quote = pattern_frame(actual_side, 2)
    wrong_side = "SHORT" if actual_side == "LONG" else "LONG"

    assert evaluate_entry_contract(
        frame, quote, code=f"KC_LIVE_PATTERN_BREAKOUT_{wrong_side}",
        symbol="龍蝦/USDT",
    ) is None


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_scanner_sends_the_side_of_the_live_outer_rail_break(side):
    from core.services.symbol_runner import process_single_symbol_runner

    frame, quote = pattern_frame(side, 2)
    engine = SimpleNamespace(
        account=SimpleNamespace(positions={}, log=Mock()),
        tickers={"龍蝦/USDT": quote},
        _execute_confirmed_channel_break=AsyncMock(),
    )

    asyncio.run(process_single_symbol_runner(
        engine, "龍蝦/USDT", time.time(), None, False, exit_frame=frame
    ))

    call = engine._execute_confirmed_channel_break.await_args
    assert call.args[3] == side
    assert call.kwargs["v8_reason"] == f"KC_LIVE_PATTERN_BREAKOUT_{side}"


def test_existing_qualified_outside_continuation_remains_available():
    from test_breakout_only_entry import breakout_frame

    frame = breakout_frame("LONG")
    prior_live = frame.index[-1]
    prior = frame.iloc[prior_live].copy()
    frame.loc[prior_live, "is_closed"] = True
    next_bar = prior.copy()
    next_bar["timestamp"] = float(prior["timestamp"]) + 60_000
    next_bar["is_closed"] = False
    next_bar["open"] = 101.7
    next_bar["close"] = 101.9
    next_bar["high"] = 102.0
    next_bar["low"] = 101.6
    next_bar["kc_upper"] = 101.5
    next_bar["kc_middle"] = 100.4
    next_bar["kc_lower"] = 99.4
    next_bar["ma5"] = 101.2
    next_bar["ma15"] = 100.2
    frame.loc[len(frame)] = next_bar
    account = SimpleNamespace(
        positions={}, trades=[], last_closed_at={},
        breakout_qualification={"龍蝦/USDT": dict(
            side="LONG", pending_signal_id="qualified:1",
            breakout_bar_id=float(prior["timestamp"]),
        )},
    )

    decision = evaluate_entry_contract(
        frame, 102.0, code="KC_OUTSIDE_LONG",
        account=account, symbol="龍蝦/USDT"
    )

    assert decision is not None
    assert decision["side"] == "LONG"
    assert decision["type"] == "KC_OUTSIDE_LONG"
    assert decision["entry_phase"] == "KC_CONTINUATION_ENTRY"
