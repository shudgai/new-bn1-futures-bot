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
    entry_consolidation_problem,
    quote_beyond_side_outer_rail,
)
from core.services import entry_contract
from core.services.entry_firewall import validate_account_entry


def breakout_frame(side="LONG"):
    stamp = int(time.time() // 60) * 60_000
    rows = [
        dict(open=100.0, close=100.2, high=100.3, low=99.9,
             kc_upper=101.0, kc_middle=100.0, kc_lower=99.0,
             ma3=99.5, ma5=99.5, ma15=99.0, atr=1.0),
        dict(open=100.8, close=101.4, high=101.5, low=100.7,
             kc_upper=101.0, kc_middle=100.1, kc_lower=99.1,
             ma3=100.2, ma5=100.2, ma15=99.5, atr=1.0),
        dict(open=101.3, close=101.6, high=101.7, low=101.2,
             kc_upper=101.3, kc_middle=100.2, kc_lower=99.2,
             ma3=100.5, ma5=100.5, ma15=99.8, atr=1.0),
        dict(open=101.5, close=101.7, high=101.8, low=101.4,
             kc_upper=101.4, kc_middle=100.3, kc_lower=99.3,
             ma3=100.7, ma5=100.7, ma15=100.0, atr=1.0),
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
            ("ma3", "ma3"),
        ):
            frame[target] = 200.0 - source[original]
    frame.attrs.update(timeframe_ms=60_000, entry_finality_verified=True)
    return frame


def inner_channel_frame(side="LONG"):
    """A live directional push held inside its side of the KC channel."""
    frame = breakout_frame(side)
    live = frame.index[-1]
    lower = float(frame.loc[live, "kc_lower"])
    middle = float(frame.loc[live, "kc_middle"])
    upper = float(frame.loc[live, "kc_upper"])
    quote = (middle + upper) / 2 if side == "LONG" else (lower + middle) / 2
    opened = quote - 0.6 if side == "LONG" else quote + 0.6
    frame.loc[live, ["open", "close", "high", "low"]] = [
        opened, quote, quote if side == "LONG" else opened,
        opened if side == "LONG" else quote,
    ]
    return frame


def live_outer_frame(side="LONG"):
    """A live body opens in-channel and has already crossed its own outer rail."""
    frame = breakout_frame(side)
    live = frame.index[-1]
    lower = float(frame.loc[live, "kc_lower"])
    upper = float(frame.loc[live, "kc_upper"])
    opened = (lower + upper) / 2
    quote = upper + 0.6 if side == "LONG" else lower - 0.6
    frame.loc[live, ["open", "close", "high", "low"]] = [
        opened, quote, quote if side == "LONG" else opened,
        opened if side == "LONG" else quote,
    ]
    return frame


def sui_cross_frame(side="LONG"):
    frame = breakout_frame(side)
    ma5 = [99.0, 99.4, 99.8, 100.7]
    ma15 = [99.2, 99.5, 99.6, 100.0]
    if side == "SHORT":
        ma5 = [200.0 - value for value in ma5]
        ma15 = [200.0 - value for value in ma15]
    frame["ma5"] = ma5
    frame["ma15"] = ma15
    return frame


def with_chop_history(frame):
    prefix = []
    first = frame.iloc[0]
    sign = 1 if frame.iloc[1]["kc_middle"] > frame.iloc[0]["kc_middle"] else -1
    for offset in (3, 2, 1):
        row = first.copy()
        row["timestamp"] = float(first["timestamp"]) - offset * 60_000
        row["is_closed"] = True
        row["ma5"] = float(first["ma5"]) - sign * offset * 0.05
        row["ma15"] = float(first["ma15"]) - sign * offset * 0.05
        row["kc_middle"] = float(first["kc_middle"]) - sign * offset * 0.05
        row["kc_upper"] = float(first["kc_upper"]) - sign * offset * 0.05
        row["kc_lower"] = float(first["kc_lower"]) - sign * offset * 0.05
        prefix.append(row)
    result = pd.concat([pd.DataFrame(prefix), frame], ignore_index=True)
    result.attrs.update(frame.attrs)
    return result


def consolidated_candidate_frame(side, kind):
    frame = with_chop_history(sui_cross_frame(side))
    if kind == "flat":
        ma5 = [99.0, 99.1, 99.2, 99.3, 99.4, 99.8]
        ma15 = [99.6, 99.61, 99.62, 99.63, 99.64, 99.65]
        kc_middle = [100.0, 100.01, 100.02, 100.03, 100.04, 100.05]
    else:
        ma5 = [99.0, 99.2, 99.1, 99.2, 99.5, 100.0]
        ma15 = [99.1, 99.2, 99.3, 99.4, 99.6, 99.7]
        kc_middle = [100.0, 100.1, 100.2, 100.3, 100.4, 100.5]
    if side == "SHORT":
        ma5 = [200.0 - value for value in ma5]
        ma15 = [200.0 - value for value in ma15]
        kc_middle = [200.0 - value for value in kc_middle]
    for index in range(6):
        frame.loc[index, "ma5"] = ma5[index]
        frame.loc[index, "ma15"] = ma15[index]
        frame.loc[index, "kc_middle"] = kc_middle[index]
    return frame


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_confirmed_two_candle_breakout_is_general_entry(side):
    frame = breakout_frame(side)
    decision = evaluate_entry_contract(frame, symbol="CAP/USDT")

    assert decision is not None
    assert decision["side"] == side
    assert decision["type"] == f"KC_2BAR_CONFIRM_{side}"
    assert decision["entry_phase"] == "KC_2BAR_CLOSED_CONFIRM"


def test_long_reversal_below_kc_middle_is_blocked(monkeypatch):
    frame = breakout_frame("LONG")
    quote = float(frame.iloc[-1]["kc_middle"]) - 0.01
    candidate = dict(action="ENTER", side="LONG", type="KC_SECOND_THIRD_LONG")
    monkeypatch.setattr(entry_contract, "evaluate_second_third", lambda *args: candidate)
    diagnostics = {}

    decision = evaluate_entry_contract(
        frame, quote, symbol="CAP/USDT", diagnostics=diagnostics
    )

    assert decision is None or decision["type"] != "OPEN_LONG_MOMENTUM_SQUEEZE"
    assert diagnostics["reason"] == "BLOCKED_BELOW_KC_MID"


def test_short_single_closed_breakout_cannot_use_tiny_live_body():
    frame = breakout_frame("SHORT")
    frame.loc[1, "kc_lower"] = 98.0
    decision = evaluate_entry_contract(
        frame, float(frame.iloc[-1]["close"]), symbol="CAP/USDT"
    )

    assert decision is None


def test_kc_momentum_override_resolves_flat_slope():
    from core.services.strategies.outer_strategy import ck_direction, resolve_kc_direction

    assert resolve_kc_direction(0.0, 98.0, 100.0, 99.0, 100.0, 2.0) == "DOWN"
    assert resolve_kc_direction(0.0, 102.0, 100.0, 101.0, 100.0, 2.0) == "UP"
    assert resolve_kc_direction(0.0, 100.2, 100.0, 99.0, 100.0, 2.0) == "UNKNOWN"

    frame = breakout_frame("SHORT")
    frame.loc[[1, 2], ["kc_lower", "kc_middle", "kc_upper"]] = [99., 100., 101.]
    frame.loc[2, ["close", "ma5", "ma15", "atr"]] = [99.4, 99., 100., 2.]
    assert ck_direction(frame) == "SHORT"


def test_intrabar_midline_entry_threshold_and_position_gate():
    from core.services.entry_contract import evaluate_intrabar_breakout_entry

    assert evaluate_intrabar_breakout_entry(None, 99.2, 101.0, 100.0, 2.0) == (
        "OPEN_SHORT_INTRABAR_PENETRATION"
    )
    assert evaluate_intrabar_breakout_entry(None, 100.8, 99.0, 100.0, 2.0) == (
        "OPEN_LONG_INTRABAR_PENETRATION"
    )
    assert evaluate_intrabar_breakout_entry(None, 99.5, 101.0, 100.0, 2.0) is None
    assert evaluate_intrabar_breakout_entry("LONG", 99.0, 101.0, 100.0, 2.0) is None


def test_intrabar_midline_entry_revalidates_width_without_direction_cooldown(monkeypatch):
    from core.services import entry_contract

    frame = breakout_frame("LONG")
    live = frame.index[-1]
    frame.loc[live, ["open", "high", "low", "kc_lower", "kc_middle", "kc_upper"]] = [
        100.5, 101.8, 99.4, 99.1, 100.3, 101.5,
    ]
    diagnostics = {}
    monkeypatch.setattr(
        entry_contract, "post_profit_lock_reason",
        lambda *args: "BLOCKED_BY_POST_PROFIT_COOLDOWN",
    )

    decision = evaluate_entry_contract(
        frame, 99.5, symbol="CAP/USDT", diagnostics=diagnostics
    )

    assert decision is not None
    assert decision["side"] == "SHORT"
    assert decision["type"] == "OPEN_SHORT_INTRABAR_PENETRATION"
    assert decision["entry_phase"] == "KC_MIDLINE_INTRABAR"


def test_intrabar_midline_entry_retains_post_profit_peak_gate(monkeypatch):
    from core.services import entry_contract

    frame = breakout_frame("LONG")
    live = frame.index[-1]
    frame.loc[live, ["open", "high", "low", "kc_lower", "kc_middle", "kc_upper"]] = [
        100.5, 101.8, 99.4, 99.1, 100.3, 101.5,
    ]
    monkeypatch.setattr(
        entry_contract, "post_profit_lock_reason",
        lambda *args: "BLOCKED_BY_PEAK_EXHAUSTION_GATE",
    )
    diagnostics = {}

    decision = evaluate_entry_contract(
        frame, 99.5, symbol="CAP/USDT", diagnostics=diagnostics
    )

    assert decision is None
    assert diagnostics["reason"] == "BLOCKED_BY_PEAK_EXHAUSTION_GATE"


def test_intrabar_midline_entry_passes_account_revalidation():
    frame = breakout_frame("LONG")
    live = frame.index[-1]
    frame.loc[live, ["open", "high", "low", "close", "kc_lower", "kc_middle", "kc_upper"]] = [
        100.5, 101.8, 99.4, 99.5, 99.1, 100.3, 101.5,
    ]
    decision = evaluate_entry_contract(frame, symbol="CAP/USDT")
    assert decision and decision["type"] == "OPEN_SHORT_INTRABAR_PENETRATION"
    account = SimpleNamespace(
        positions={}, trades=[], last_closed_at={},
        entry_frame_provider=AsyncMock(return_value=frame),
    )
    context = dict(
        entry_signal_code=decision["type"],
        channel_confirmation_bar_id=decision["confirmation_bar_id"],
    )

    validated = asyncio.run(
        validate_account_entry(account, "CAP/USDT", "SHORT", context)
    )

    assert validated["pending_signal_id"] == decision["pending_signal_id"]


def band_walk_frame():
    frame = with_chop_history(breakout_frame("LONG"))
    for index in range(6):
        lower = 99.0 + index * 0.03
        middle = 100.0 + index * 0.10
        upper = 101.0 + index * 0.13
        close = 100.5 + index * 0.12
        frame.loc[index, ["open", "high", "low", "close", "kc_lower", "kc_middle",
                          "kc_upper", "ma5", "ma15", "atr"]] = [
            close - 0.1, close + 0.15, close - 0.15, close,
            lower, middle, upper, 100.0 + index * 0.2,
            99.2 + index * 0.15, 1.0,
        ]
    live = frame.index[-1]
    frame.loc[live, ["open", "high", "low", "close", "kc_lower", "kc_middle",
                     "kc_upper", "ma5", "ma15", "atr"]] = [
        102.05, 102.40, 102.00, 102.35, 99.10, 100.60, 102.10,
        101.10, 100.0, 1.0,
    ]
    return frame


def test_band_walking_entry_accepts_healthy_green_body_outside_upper_rail():
    frame = band_walk_frame()
    diagnostics = {}
    decision = evaluate_entry_contract(
        frame, 102.35, symbol="LOBSTER/USDT", diagnostics=diagnostics
    )

    assert decision is not None, diagnostics
    assert decision["side"] == "LONG"
    assert decision["type"] == "KC_BAND_WALK_LONG"
    assert decision["entry_phase"] == "KC_BAND_WALK_ENTRY"


def post_pressure_reentry_frame(live_open, quote):
    frame = breakout_frame("LONG")
    reclaim_setup = live_open < 101.
    rows = [
        (99.0, 100.0, 101.0, 100.1, 99.8, 100.4, 100.0, 99.7),
        (99.1, 100.1, 101.1, 100.2, 100.0, 100.6, 100.2, 99.9),
        (99.2, 100.3, 101.3, 100.4,
         100.8 if reclaim_setup else 101.3, 102.0,
         99.8 if reclaim_setup else 100.8, 100.9),
        (99.3, 100.4, 101.5, 100.6, live_open, max(live_open, quote), min(live_open, quote), quote),
    ]
    for index, values in enumerate(rows):
        lower, middle, upper, ma5, opening, high, low, close = values
        frame.loc[index, ["kc_lower", "kc_middle", "kc_upper", "ma5", "open", "high", "low", "close"]] = [
            lower, middle, upper, ma5, opening, high, low, close,
        ]
    return frame


@pytest.mark.parametrize(
    ("live_open", "quote", "expected_path"),
    [(100.5, 100.7, "MA5_RECLAIM"), (101.8, 102.1, "RED_HIGH_BREAK")],
)
def test_sell_pressure_close_allows_fresh_long_continuation_reentry(
    live_open, quote, expected_path
):
    from core.services.entry_contract import evaluate_entry_contract

    frame = post_pressure_reentry_frame(live_open, quote)
    exit_id = float(frame.iloc[-3]["timestamp"]) + 30000.
    account = SimpleNamespace(
        positions={},
        trades=[dict(
            symbol="CAP/USDT", action="CLOSE_LONG", side="LONG", status="CLOSED",
            id=exit_id, reason="Channel Swing LONG_EXIT_OVERBOUGHT_EXTREME_RATCHET",
            last_profit_exit_side="LONG", last_profit_exit_timestamp=exit_id,
            last_profit_exit_peak_price=105.5,
        )],
    )

    diagnostics = {}
    decision = evaluate_entry_contract(
        frame, quote, account=account, symbol="CAP/USDT", diagnostics=diagnostics
    )

    assert decision is not None, diagnostics
    assert decision["type"] == "RE_ENTRY_LONG_KC_CONTINUATION"
    assert decision["entry_phase"] == "RE_ENTRY_BYPASS"
    assert decision["reentry_path"] == expected_path


def test_non_pressure_close_does_not_create_long_reentry_bypass():
    from core.services.entry_contract import evaluate_entry_contract

    frame = post_pressure_reentry_frame(100.5, 100.7)
    exit_id = float(frame.iloc[-3]["timestamp"]) + 30000.
    account = SimpleNamespace(positions={}, trades=[dict(
        symbol="CAP/USDT", action="CLOSE_LONG", side="LONG", status="CLOSED",
        id=exit_id, reason="manual close",
    )])

    assert evaluate_entry_contract(frame, 100.7, account=account, symbol="CAP/USDT") is None


def test_short_close_current_bar_squeeze_bypasses_structure_and_cooldown():
    from core.services.entry_contract import evaluate_entry_contract

    frame = breakout_frame("LONG")
    live = frame.index[-1]
    frame.loc[live, ["open", "high", "low", "close", "kc_lower", "kc_middle", "kc_upper"]] = [
        100.0, 101.5, 99.9, 100.2, 99.0, 100.2, 101.4,
    ]
    live_ms = float(frame.loc[live, "timestamp"])
    account = SimpleNamespace(positions={}, trades=[dict(
        symbol="CAP/USDT", action="CLOSE_SHORT", side="SHORT", status="CLOSED",
        id=live_ms + 100., reason="confirmed short close",
    )])

    decision = evaluate_entry_contract(
        frame, 101.3, account=account, symbol="CAP/USDT"
    )

    assert decision is not None
    assert decision["side"] == "LONG"
    assert decision["type"] == "OPEN_LONG_MOMENTUM_SQUEEZE"
    assert decision["entry_phase"] == "REVERSAL_SQUEEZE_BYPASS"


def test_old_short_close_cannot_authorize_current_bar_squeeze():
    from core.services.entry_contract import evaluate_entry_contract

    frame = breakout_frame("LONG")
    live = frame.index[-1]
    frame.loc[live, ["open", "high", "low", "close", "kc_lower", "kc_middle", "kc_upper"]] = [
        100.0, 101.5, 99.9, 100.2, 99.0, 100.2, 101.4,
    ]
    live_ms = float(frame.loc[live, "timestamp"])
    account = SimpleNamespace(positions={}, trades=[dict(
        symbol="CAP/USDT", action="CLOSE_SHORT", side="SHORT", status="CLOSED",
        id=live_ms - 60000., reason="confirmed short close",
    )])

    decision = evaluate_entry_contract(frame, 101.3, account=account, symbol="CAP/USDT")
    assert decision is None or decision["type"] != "OPEN_LONG_MOMENTUM_SQUEEZE"


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
@pytest.mark.parametrize("symbol", ["SUI/USDT", "龙虾/USDT"])
def test_sui_and_lobster_accept_the_same_closed_breakout(side, symbol):
    frame = with_chop_history(breakout_frame(side))
    decision = evaluate_entry_contract(frame, symbol=symbol)

    assert decision is not None
    assert decision["type"] == f"KC_2BAR_CONFIRM_{side}"
    assert decision["entry_phase"] == "KC_2BAR_CLOSED_CONFIRM"


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
@pytest.mark.parametrize("symbol", ["SUI/USDT", "龙虾/USDT"])
def test_sui_and_lobster_closed_breakout_do_not_require_ma_cross(side, symbol):
    frame = with_chop_history(breakout_frame(side))
    decision = evaluate_entry_contract(frame, symbol=symbol)

    assert decision is not None
    assert decision["side"] == side
    assert decision["entry_phase"] == "KC_2BAR_CLOSED_CONFIRM"


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
@pytest.mark.parametrize("symbol", ["SUI/USDT", "龙蝦/USDT"])
def test_sui_and_lobster_live_first_breakout_use_the_same_trend_gate(side, symbol):
    frame = with_chop_history(live_outer_frame(side))
    ma5 = [99.0, 99.2, 99.4, 99.6, 99.8, 100.0, 100.2]
    ma15 = [98.0, 98.2, 98.4, 98.6, 98.8, 99.0, 99.2]
    if side == "SHORT":
        ma5 = [200.0 - value for value in ma5]
        ma15 = [200.0 - value for value in ma15]
    frame["ma5"] = ma5
    frame["ma15"] = ma15
    quote = float(frame.iloc[-1]["close"])

    decision = evaluate_entry_contract(frame, quote, symbol=symbol)

    assert decision is not None
    assert decision["side"] == side
    assert decision["type"] == f"KC_LIVE_BODY_BREAKOUT_{side}"
    assert decision["entry_phase"] == "KC_LIVE_OUTER_BREAKOUT"


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
@pytest.mark.parametrize("line", ["kc_middle", "ma15"])
def test_sui_rejects_horizontal_kc_or_ma15(side, line):
    frame = sui_cross_frame(side)
    frame.loc[2, line] = frame.loc[1, line]
    diagnostics = {}

    decision = evaluate_entry_contract(
        frame, symbol="SUI/USDT", diagnostics=diagnostics
    )

    assert decision is None
    assert diagnostics["reason"] == "BLOCKED_KC_MA5_MA15_TREND_MISMATCH"


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
@pytest.mark.parametrize("symbol", ["SUI/USDT", "龙蝦/USDT"])
def test_sui_and_lobster_live_breakout_without_closed_pair_share_result(side, symbol):
    frame = with_chop_history(live_outer_frame(side))
    sign = 1 if side == "LONG" else -1
    rail = "kc_upper" if side == "LONG" else "kc_lower"
    for index in (1, 2):
        edge = float(frame.loc[index, rail])
        close = edge - sign * 0.1
        opening = close - sign * 0.2
        frame.loc[index, ["open", "close", "high", "low"]] = [
            opening, close, max(opening, close) + 0.05,
            min(opening, close) - 0.05,
        ]

    decision = evaluate_entry_contract(
        frame, float(frame.iloc[-1]["close"]), symbol=symbol
    )

    assert decision is not None
    assert decision["side"] == side
    assert decision["entry_phase"] == "KC_LIVE_OUTER_BREAKOUT"


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
@pytest.mark.parametrize("symbol", ["SUI/USDT", "龙虾/USDT"])
@pytest.mark.parametrize(
    "code",
    ["KC_LIVE_BODY_BREAKOUT_{side}", "KC_OUTSIDE_{side}"],
)
def test_sui_and_lobster_share_live_and_continuation_authorities(side, symbol, code):
    diagnostics = {}
    code = code.format(side=side)
    frame = sui_cross_frame(side) if "KC_LIVE_BODY_BREAKOUT" in code else breakout_frame(side)
    if "KC_LIVE_BODY_BREAKOUT" in code:
        breakout_index = frame.index[-2]
        if side == "LONG":
            frame.loc[breakout_index, ["open", "high", "low", "close"]] = [
                101.0, 101.7, 100.9, 101.6,
            ]
        else:
            frame.loc[breakout_index, ["open", "high", "low", "close"]] = [
                99.0, 99.1, 98.3, 98.4,
            ]
    frame = with_chop_history(frame)
    decision = evaluate_entry_contract(
        frame,
        code=code,
        symbol=symbol,
        diagnostics=diagnostics,
    )

    if "KC_LIVE_BODY_BREAKOUT" in code:
        assert decision is not None
        assert decision["side"] == side
        assert decision["entry_phase"] in ("KC_LIVE_OUTER_BREAKOUT", "KC_LIVE_BODY_BREAKOUT")
    else:
        assert decision is None
        assert diagnostics["reason"] == "WAIT_KC_CONTINUATION"


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
@pytest.mark.parametrize(("separation", "expected"), [(0.05, True), (0.15, False)])
def test_lobster_blocks_entry_when_live_ma5_ma15_overlap(side, separation, expected):
    frame = breakout_frame(side)
    if side == "LONG":
        ma5 = [100.0, 101.0, 102.0, 103.0]
        ma15 = [100.0-separation, 101.0-separation, 102.0-separation, 103.0-separation]
    else:
        ma5 = [100.0, 99.0, 98.0, 97.0]
        ma15 = [100.0+separation, 99.0+separation, 98.0+separation, 97.0+separation]
    frame["ma5"] = ma5
    frame["ma15"] = ma15
    frame = with_chop_history(frame)
    diagnostics = {}

    decision = evaluate_entry_contract(
        frame, symbol="龙虾/USDT", diagnostics=diagnostics
    )

    if expected:
        assert decision is None
        assert diagnostics["reason"] == "BLOCKED_MA5_MA15_OVERLAP"
    else:
        assert decision is not None
        assert decision["side"] == side


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_lobster_ma_overlap_boundary_is_inclusive(side):
    frame = breakout_frame(side)
    ma5 = [100.0, 101.0, 102.0, 103.0] if side == "LONG" else [100.0, 99.0, 98.0, 97.0]
    ma15 = [99.9, 100.9, 101.9, 103.0] if side == "LONG" else [100.1, 99.1, 98.1, 97.0]
    frame["ma5"] = ma5
    frame["ma15"] = ma15
    frame = with_chop_history(frame)
    # Match the live adjustment so the final separation is exactly 0.1 ATR.
    closed = frame.iloc[-2]
    quote = float(frame.iloc[-1]["close"])
    delta = quote - float(closed["close"])
    raw_gap = 0.1 - abs(delta * (1.0 / 5.0 - 1.0 / 15.0))
    if side == "LONG":
        frame.loc[frame.index[-2], "ma15"] = float(frame.loc[frame.index[-2], "ma5"]) - raw_gap
    else:
        frame.loc[frame.index[-2], "ma15"] = float(frame.loc[frame.index[-2], "ma5"]) + raw_gap

    assert entry_consolidation_problem(frame, quote) == "BLOCKED_MA5_MA15_OVERLAP"


@pytest.mark.parametrize(
    ("ma5", "ma15", "kc_middle", "expected"),
    [
        ([100, 101, 102, 103, 104, 105],
         [98, 99, 100, 101, 102, 103],
         [100, 101, 102, 103, 104, 105], None),
        ([100, 101, 102, 103, 104, 105],
         [100, 100.01, 100.02, 100.03, 100.04, 100.05],
         [100, 100.01, 100.02, 100.03, 100.04, 100.05],
         "BLOCKED_FLAT_MA15_KC"),
        ([100, 100.2, 100.1, 100.3, 100.2, 100.4],
         [98, 98.2, 98.4, 98.6, 98.8, 99],
         [100, 100.2, 100.4, 100.6, 100.8, 101],
         "BLOCKED_MA5_REGULAR_OSCILLATION"),
    ],
)
def test_consolidation_gate_detects_flat_or_oscillating_market(
    ma5, ma15, kc_middle, expected
):
    frame = with_chop_history(breakout_frame())
    for index in range(6):
        frame.loc[index, "ma5"] = ma5[index]
        frame.loc[index, "ma15"] = ma15[index]
        frame.loc[index, "kc_middle"] = kc_middle[index]

    assert entry_consolidation_problem(frame, float(frame.iloc[-1]["close"])) == expected


@pytest.mark.parametrize("symbol", ["SUI/USDT", "龙虾/USDT"])
@pytest.mark.parametrize("side", ["LONG", "SHORT"])
@pytest.mark.parametrize(
    ("kind", "expected_reason"),
    [
        ("flat", "BLOCKED_FLAT_MA15_KC"),
        ("oscillating", "BLOCKED_MA5_REGULAR_OSCILLATION"),
    ],
)
def test_consolidation_gate_blocks_real_entry_contract(
    symbol, side, kind, expected_reason
):
    frame = consolidated_candidate_frame(side, kind)
    diagnostics = {}

    decision = evaluate_entry_contract(
        frame, symbol=symbol, diagnostics=diagnostics
    )

    if kind == "oscillating":
        assert decision is not None
        assert decision["side"] == side
        assert decision["entry_phase"] in (
            "KC_LIVE_OUTER_BREAKOUT", "KC_2BAR_CLOSED_CONFIRM",
            "KC_CONTINUATION_ENTRY", "KC_LIVE_BODY_BREAKOUT",
        )
    else:
        assert decision is None
        assert diagnostics["reason"] == expected_reason


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
def test_invalid_closed_pair_blocks_entry_but_live_color_does_not(side, mutation):
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
    decision = evaluate_entry_contract(frame, quote, symbol="CAP/USDT")
    if mutation in ("live_red", "live_green"):
        assert decision is not None
        assert decision["type"] == f"KC_2BAR_CONFIRM_{side}"
    else:
        assert decision is None


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_pivot_or_ma_cross_code_is_not_an_entry_authority(side):
    frame = breakout_frame(side)
    assert f"KC_2BAR_CONFIRM_{side}" in ENTRY_CODES
    assert evaluate_entry_contract(frame, code=f"KC_OUTER_PIVOT_{side}") is None
    assert evaluate_entry_contract(frame, code=f"MA5_MA15_LIVE_CROSS_{side}") is None
    assert evaluate_entry_contract(frame, code=f"KC_LIVE_BODY_BREAKOUT_{side}") is None


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_pre_close_breakout_cannot_reopen_after_peak_reversal_close(side):
    frame = breakout_frame(side)
    live_bar = float(frame.iloc[-1]["timestamp"])
    account = SimpleNamespace(
        positions={},
        trades=[dict(
            symbol="LOBSTER/USDT",
            action=f"CLOSE_{side}",
            id=live_bar + 1_000.,
        )],
        last_closed_at={},
    )
    diagnostics = {}

    decision = evaluate_entry_contract(
        frame, symbol="LOBSTER/USDT", account=account, diagnostics=diagnostics
    )

    assert decision is None
    assert diagnostics["reason"] == "WAIT_POST_EXIT_NEW_FORMATION"


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

    live = frame.index[-1]
    frame.loc[live, "close"] = float(frame.loc[live, "kc_middle"])
    with pytest.raises(ValueError):
        asyncio.run(validate_account_entry(account, "CAP/USDT", side, context))


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_account_revalidation_accepts_and_rechecks_live_body_breakout(side):
    frame = live_outer_frame(side)
    live = frame.index[-1]
    lower = float(frame.loc[live, "kc_lower"])
    upper = float(frame.loc[live, "kc_upper"])
    quote = float(frame.loc[live, "close"])

    decision = evaluate_entry_contract(frame, quote, symbol="CAP/USDT")
    assert decision is not None
    assert decision["type"] == f"KC_LIVE_BODY_BREAKOUT_{side}"

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

    frame.loc[live, "close"] = float(frame.loc[live, "kc_middle"])
    with pytest.raises(ValueError, match="最新入口行情不符"):
        asyncio.run(validate_account_entry(account, "CAP/USDT", side, context))


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_account_revalidation_rejects_live_breakout_when_ma15_stops_trending(side):
    frame = live_outer_frame(side)
    quote = float(frame.iloc[-1]["close"])
    decision = evaluate_entry_contract(frame, quote, symbol="CAP/USDT")
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
    frame.loc[frame.index[-2], "ma15"] = frame.iloc[-3]["ma15"]

    with pytest.raises(ValueError, match="最新入口行情不符"):
        asyncio.run(validate_account_entry(account, "CAP/USDT", side, context))


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_account_revalidation_rejects_live_breakout_when_live_ma5_reverses(side):
    frame = live_outer_frame(side)
    quote = float(frame.iloc[-1]["close"])
    decision = evaluate_entry_contract(frame, quote, symbol="CAP/USDT")
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
    previous_ma5 = float(frame.iloc[-2]["ma5"])
    frame.loc[frame.index[-1], "ma5"] = previous_ma5 + (
        1 if side == "SHORT" else -1
    )

    with pytest.raises(ValueError, match="最新入口行情不符"):
        asyncio.run(validate_account_entry(account, "CAP/USDT", side, context))


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
@pytest.mark.parametrize("invalid_bar", [1, 2])
def test_account_revalidation_rejects_entry_if_either_closed_body_disappears(
    side, invalid_bar
):
    frame = breakout_frame(side)
    decision = evaluate_entry_contract(frame, symbol="龍蝦/USDT")
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

    frame.loc[invalid_bar, "close"] = frame.loc[invalid_bar, "open"]

    with pytest.raises(ValueError):
        asyncio.run(validate_account_entry(account, "龍蝦/USDT", side, context))
