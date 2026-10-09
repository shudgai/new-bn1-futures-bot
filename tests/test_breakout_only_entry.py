"""Only a confirmed, directional two-candle KC breakout can authorize entry."""
import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pandas as pd
import pytest

from core.services.entry_contract import (
    ENTRY_CODES,
    evaluate_entry_contract,
    quote_beyond_side_outer_rail,
)
from core.services.entry_firewall import validate_account_entry


def breakout_frame(side="LONG"):
    stamp = int(time.time() // 60) * 60_000
    rows = [
        dict(open=100.0, close=100.2, high=100.3, low=99.9,
             kc_upper=101.0, kc_middle=100.0, kc_lower=99.0,
             ma5=99.5, ma15=99.0, atr=1.0),
        dict(open=100.8, close=101.4, high=101.5, low=100.7,
             kc_upper=101.0, kc_middle=100.1, kc_lower=99.1,
             ma5=100.2, ma15=99.5, atr=1.0),
        dict(open=101.3, close=101.6, high=101.7, low=101.2,
             kc_upper=101.3, kc_middle=100.2, kc_lower=99.2,
             ma5=100.5, ma15=99.8, atr=1.0),
        dict(open=101.5, close=101.7, high=101.8, low=101.4,
             kc_upper=101.4, kc_middle=100.3, kc_lower=99.3,
             ma5=100.7, ma15=100.0, atr=1.0),
    ]
    for index, row in enumerate(rows):
        row.update(timestamp=stamp - (3 - index) * 60_000,
                   is_closed=index < 3)
    frame = pd.DataFrame(rows)
    if side == "SHORT":
        source = frame.copy()
        for target, original in (
            ("open", "open"), ("close", "close"),
            ("high", "low"), ("low", "high"),
            ("kc_upper", "kc_lower"), ("kc_lower", "kc_upper"),
            ("kc_middle", "kc_middle"), ("ma5", "ma5"), ("ma15", "ma15"),
        ):
            frame[target] = 200.0 - source[original]
    frame.attrs.update(timeframe_ms=60_000, entry_finality_verified=True)
    return frame


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_only_two_closed_same_color_outer_breakout_with_live_followthrough(side):
    frame = breakout_frame(side)
    decision = evaluate_entry_contract(frame, symbol="CAP/USDT")

    assert decision is not None
    assert decision["side"] == side
    assert decision["type"] == f"KC_2BAR_CONFIRM_{side}"
    assert decision["entry_phase"] == "KC_2BAR_CLOSED_CONFIRM"
    assert decision["breakout_bar_id"] == frame.iloc[-3]["timestamp"]
    assert decision["pair_confirmation_bar_id"] == frame.iloc[-2]["timestamp"]


@pytest.mark.parametrize(
    ("side", "mutation"),
    [
        ("LONG", "first_inside"),
        ("LONG", "second_red"),
        ("LONG", "live_red"),
        ("SHORT", "first_inside"),
        ("SHORT", "second_green"),
        ("SHORT", "live_green"),
    ],
)
def test_wrong_side_or_unconfirmed_candles_never_authorize_entry(side, mutation):
    frame = breakout_frame(side)
    sign = 1 if side == "LONG" else -1
    if mutation == "first_inside":
        frame.loc[1, "close"] = frame.loc[1, "kc_upper" if side == "LONG" else "kc_lower"]
    elif mutation == "second_red":
        frame.loc[2, "open"] = frame.loc[2, "close"] + 0.1
    elif mutation == "second_green":
        frame.loc[2, "open"] = frame.loc[2, "close"] - 0.1
    elif mutation == "live_red":
        frame.loc[3, "open"] = frame.loc[3, "close"] + 0.1
    elif mutation == "live_green":
        frame.loc[3, "open"] = frame.loc[3, "close"] - 0.1

    quote = float(frame.iloc[-1]["close"])
    if mutation in ("live_red", "live_green"):
        quote = float(frame.iloc[-1]["open"])
    assert sign * (quote - float(frame.iloc[-1]["kc_upper" if side == "LONG" else "kc_lower"])) > 0
    assert evaluate_entry_contract(frame, quote, symbol="CAP/USDT") is None


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_pivot_or_ma_cross_code_is_not_an_entry_authority(side):
    frame = breakout_frame(side)
    assert f"KC_2BAR_CONFIRM_{side}" in ENTRY_CODES
    assert evaluate_entry_contract(frame, code=f"KC_OUTER_PIVOT_{side}") is None
    assert evaluate_entry_contract(frame, code=f"MA5_MA15_LIVE_CROSS_{side}") is None


@pytest.mark.parametrize(
    ("side", "opposite_rail"),
    [("LONG", "kc_lower"), ("SHORT", "kc_upper")],
)
def test_opposite_outer_rail_never_authorizes_the_requested_side(side, opposite_rail):
    frame = breakout_frame(side)
    quote = float(frame.iloc[-1][opposite_rail])

    assert not quote_beyond_side_outer_rail(frame, side, quote)
    assert evaluate_entry_contract(frame, quote, symbol="CAP/USDT") is None


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_account_revalidation_rechecks_the_same_breakout(side):
    frame = breakout_frame(side)
    decision = evaluate_entry_contract(frame, symbol="CAP/USDT")
    account = SimpleNamespace(
        positions={},
        trades=[],
        last_closed_at={},
        entry_frame_provider=AsyncMock(return_value=frame),
    )
    context = dict(
        entry_signal_code=decision["type"],
        channel_confirmation_bar_id=decision["confirmation_bar_id"],
    )
    validated = asyncio.run(
        validate_account_entry(account, "CAP/USDT", side, context)
    )
    assert validated["pending_signal_id"] == decision["pending_signal_id"]

    frame.loc[3, "open"] = frame.loc[3, "close"] + (0.1 if side == "LONG" else -0.1)
    with pytest.raises(ValueError, match="OPPOSITE_LIVE_CANDLE_COLOR"):
        asyncio.run(validate_account_entry(account, "CAP/USDT", side, context))
