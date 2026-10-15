import asyncio
import logging
from types import SimpleNamespace

import pandas as pd
import pytest

from core.gates.pipeline import EntryGatePipeline, ma5_ma15_cross_chop_problem
from core.services.entry_contract import evaluate_entry_contract
from core.services.entry_firewall import validate_account_entry
from core.services.strategies.unified_entry_strategy import UnifiedEntryStrategy


CROSS_BLOCK = "BLOCKED_MA5_MA15_CROSS_CHOP"
GAP_BLOCK = "BLOCKED_MA5_MA15_GAP_TOO_NARROW"


def frame_with_spreads(spreads, *, atr=0.1, live=False):
    rows = []
    closed_spreads = spreads[:-1] if live else spreads
    for index, spread in enumerate(closed_spreads):
        rows.append({
            "timestamp": (index + 1) * 60_000,
            "is_closed": True,
            "open": 100.0,
            "high": 101.0,
            "low": 99.0,
            "close": 100.0,
            "atr": atr,
            "ma5": 100.0 + spread,
            "ma15": 100.0,
            "kc_upper": 101.0,
            "kc_middle": 100.0,
            "kc_lower": 99.0,
        })
    if live:
        rows.append({
            "timestamp": (len(rows) + 1) * 60_000,
            "is_closed": False,
            "open": 100.0,
            "high": 101.0,
            "low": 99.0,
            "close": 100.0,
            "atr": atr,
            "ma5": 100.0 + spreads[-1],
            "ma15": 100.0,
            "kc_upper": 101.0,
            "kc_middle": 100.0,
            "kc_lower": 99.0,
        })
    result = pd.DataFrame(rows)
    result.attrs["timeframe_ms"] = 60_000
    return result


def test_chop_guard_blocks_one_recent_cross_while_ma_gap_is_not_recovered():
    frame = frame_with_spreads([0.5, 0.5, 0.5, -0.5, 0.5, 0.13])

    assert ma5_ma15_cross_chop_problem(frame, 100.0) == CROSS_BLOCK


def test_chop_guard_allows_old_cross_after_six_bar_window():
    frame = frame_with_spreads([0.5, -0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 0.13])

    assert ma5_ma15_cross_chop_problem(frame, 100.0) is None


def test_chop_guard_does_not_count_an_unclosed_live_cross():
    frame = frame_with_spreads(
        [0.5, 0.5, 0.5, 0.5, 0.5, 0.5, -0.5],
        atr=0.1,
        live=True,
    )

    assert ma5_ma15_cross_chop_problem(frame, 98.0) is None


def test_chop_guard_recalculates_live_gap_from_latest_quote():
    frame = frame_with_spreads([0.11] * 7, live=True)

    assert ma5_ma15_cross_chop_problem(frame, 100.0) == GAP_BLOCK
    assert ma5_ma15_cross_chop_problem(frame, 100.2) is None


def test_explosive_meme_breakout_uses_relaxed_gap_without_relaxing_other_symbols(monkeypatch):
    from core.gates.pipeline import SpatialBrain

    frame = frame_with_spreads([0.0] * 7, atr=0.1, live=True)
    frame.loc[:, 'kc_upper'] = 100.25
    frame.loc[:, 'kc_lower'] = 99.75
    frame.loc[:, 'kc_middle'] = 100.0
    monkeypatch.setattr(
        SpatialBrain, 'analyze', staticmethod(lambda *_args: SimpleNamespace(state='EXPLOSIVE_EXPANSION')),
    )

    assert ma5_ma15_cross_chop_problem(
        frame, 100.26, symbol='CAP/USDT', side='LONG',
    ) is None
    assert ma5_ma15_cross_chop_problem(
        frame, 100.26, symbol='BTC/USDT', side='LONG',
    ) == GAP_BLOCK


def test_chop_guard_fails_closed_without_six_closed_one_minute_bars():
    frame = frame_with_spreads([0.5] * 5)

    assert ma5_ma15_cross_chop_problem(frame, 100.0) == "BLOCKED_INVALID_MA_CROSS_HISTORY"


@pytest.mark.parametrize(
    ("spread", "expected"),
    [
        (0.119, GAP_BLOCK),
        (0.12, None),
        (0.121, None),
    ],
)
def test_chop_guard_applies_point_twelve_percent_live_gap_threshold(
    spread, expected,
):
    frame = frame_with_spreads([spread] * 6)

    assert ma5_ma15_cross_chop_problem(frame, 100.0) == expected


@pytest.mark.parametrize(
    ("spread", "expected"),
    [
        (0.149, CROSS_BLOCK),
        (0.15, None),
        (0.151, None),
    ],
)
def test_recent_cross_requires_gap_to_reach_point_fifteen_percent(
    spread, expected,
):
    frame = frame_with_spreads([0.5, 0.5, 0.5, -0.5, 0.5, spread])

    assert ma5_ma15_cross_chop_problem(frame, 100.0) == expected


@pytest.mark.parametrize(
    ("side", "previous", "current", "expected"),
    [
        ("LONG", (100.0, 100.0), (100.4, 100.2), None),
        ("LONG", (100.0, 100.0), (100.4, 100.0), "BLOCKED_MA5_MA15_SLOPE_MISALIGNED"),
        ("LONG", (100.0, 100.0), (100.4, 99.9), "BLOCKED_MA5_MA15_SLOPE_MISALIGNED"),
        ("SHORT", (100.0, 100.0), (99.6, 99.8), None),
        ("SHORT", (100.0, 100.0), (99.6, 100.0), "BLOCKED_MA5_MA15_SLOPE_MISALIGNED"),
        ("SHORT", (100.0, 100.0), (99.6, 100.1), "BLOCKED_MA5_MA15_SLOPE_MISALIGNED"),
    ],
)
def test_chop_guard_requires_both_ma_slopes_to_be_strictly_directional(
    side, previous, current, expected,
):
    frame = frame_with_spreads([0.5] * 6)
    frame.loc[frame.index[-2], ["ma5", "ma15"]] = previous
    frame.loc[frame.index[-1], ["ma5", "ma15"]] = current

    assert ma5_ma15_cross_chop_problem(
        frame, 100.0, side=side,
    ) == expected


def test_chop_guard_logs_metrics_and_decision(caplog):
    frame = frame_with_spreads([0.5] * 6)
    caplog.set_level(logging.INFO, logger="EntryChopCheck")

    assert ma5_ma15_cross_chop_problem(
        frame, 100.0, symbol="CAP/USDT", log_result=True,
    ) is None

    assert (
        "[ChopCheck] Cross6=0/1, MAGap=0.500000%, MA5Slope=0.000000%, "
        "MA15Slope=0.000000% -> Status: PASSED symbol=CAP/USDT"
    ) in caplog.text










