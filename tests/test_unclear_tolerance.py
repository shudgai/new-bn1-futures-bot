"""Tests for the 1-bar UNCLEAR CK exit tolerance gate.

Covers all 5 edge cases identified in root-cause analysis:
  1. REVERSED exits immediately (no grace period).
  2. First UNCLEAR bar defers exit (returns None).
  3. Second distinct closed bar still UNCLEAR → exits.
  4. Same-bar multi-tick does NOT double-trigger.
  5. Direction recovery between bars clears the flag.
"""
import pytest
import pandas as pd
from core.services.swing_service import (
    channel_ck_exit_with_tolerance,
    _UNCLEAR_BAR_KEY,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_frame(*rows):
    """Build a minimal DataFrame with KC columns from (lower, middle, upper, ts) tuples."""
    data = [
        {
            "kc_lower": lo, "kc_middle": mid, "kc_upper": hi,
            "timestamp": ts, "open": 1.0, "high": 1.1, "low": 0.9, "close": 1.0,
        }
        for (lo, mid, hi, ts) in rows
    ]
    return pd.DataFrame(data)


def _rising():
    """Two closed bars with rising middle and rising upper -> ck_direction = LONG."""
    return _make_frame(
        (0.9,  1.00, 1.10, 1000.0),
        (0.91, 1.01, 1.11, 2000.0),
        (0.92, 1.02, 1.12, 3000.0),
    )


def _reversed():
    """Two closed bars with falling middle and falling lower -> ck_direction = SHORT."""
    return _make_frame(
        (0.9,  1.00, 1.10, 1000.0),
        (0.88, 0.98, 1.08, 2000.0),
        (0.87, 0.97, 1.07, 3000.0),
    )


def _unclear():
    """Middle is flat -> UNCLEAR (ck_direction returns None)."""
    return _make_frame(
        (0.9,  1.00, 1.10, 1000.0),
        (0.91, 1.00, 1.09, 2000.0),   # middle flat -> UNCLEAR
        (0.92, 1.00, 1.08, 3000.0),
    )


def _unclear_bar2():
    """Same UNCLEAR pattern but with a newer last-closed bar_id."""
    return _make_frame(
        (0.91, 1.00, 1.09, 2000.0),
        (0.92, 1.00, 1.08, 3000.0),   # still UNCLEAR, new bar
        (0.93, 1.00, 1.07, 4000.0),
    )


# ---------------------------------------------------------------------------
# Edge Case 1: REVERSED exits immediately (no grace period)
# ---------------------------------------------------------------------------

def test_reversed_exits_immediately_long():
    pos = {"side": "LONG"}
    result = channel_ck_exit_with_tolerance(_reversed(), "LONG", pos)
    assert result == "KC_CK_DIRECTION_REVERSED_EXIT"
    assert _UNCLEAR_BAR_KEY not in pos


def test_reversed_exits_immediately_short():
    pos = {"side": "SHORT"}
    result = channel_ck_exit_with_tolerance(_rising(), "SHORT", pos)
    assert result == "KC_CK_DIRECTION_REVERSED_EXIT"
    assert _UNCLEAR_BAR_KEY not in pos


# ---------------------------------------------------------------------------
# Edge Case 2: First UNCLEAR bar defers exit (returns None)
# ---------------------------------------------------------------------------

def test_unclear_first_bar_defers():
    pos = {}
    result = channel_ck_exit_with_tolerance(_unclear(), "LONG", pos)
    assert result is None, "Should NOT exit on first UNCLEAR bar"
    assert pos.get(_UNCLEAR_BAR_KEY) == 2000.0, "Must record last-closed bar_id"


# ---------------------------------------------------------------------------
# Edge Case 3: Second distinct bar still UNCLEAR -> exit
# ---------------------------------------------------------------------------

def test_unclear_second_bar_triggers_exit():
    pos = {_UNCLEAR_BAR_KEY: 2000.0}
    result = channel_ck_exit_with_tolerance(_unclear_bar2(), "LONG", pos)
    assert result == "KC_CK_DIRECTION_UNCLEAR_EXIT"
    assert _UNCLEAR_BAR_KEY not in pos, "Flag must be cleared after exit"


# ---------------------------------------------------------------------------
# Edge Case 4: Same-bar multi-tick does NOT advance counter
# ---------------------------------------------------------------------------

def test_same_bar_multi_tick_holds():
    pos = {_UNCLEAR_BAR_KEY: 2000.0}
    # _unclear() last-closed bar is 2000 -> same bar as recorded
    result = channel_ck_exit_with_tolerance(_unclear(), "LONG", pos)
    assert result is None, "Same-bar tick must NOT trigger exit"
    assert pos.get(_UNCLEAR_BAR_KEY) == 2000.0, "bar_id must stay unchanged"


# ---------------------------------------------------------------------------
# Edge Case 5: Direction recovery clears the flag
# ---------------------------------------------------------------------------

def test_direction_recovery_clears_flag():
    pos = {_UNCLEAR_BAR_KEY: 2000.0}
    result = channel_ck_exit_with_tolerance(_rising(), "LONG", pos)
    assert result is None, "Matching direction must not exit"
    assert _UNCLEAR_BAR_KEY not in pos, "Tolerance flag must be cleared on recovery"


# ---------------------------------------------------------------------------
# Guard: missing side / empty frame -> safe no-op
# ---------------------------------------------------------------------------

def test_no_side_returns_none():
    pos = {}
    assert channel_ck_exit_with_tolerance(_unclear(), None, pos) is None


def test_empty_frame_returns_none():
    pos = {}
    assert channel_ck_exit_with_tolerance(pd.DataFrame(), "LONG", pos) is None
