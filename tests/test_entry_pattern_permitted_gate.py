import pytest

from core.gates.entry_pattern_permitted_gate import (
    EntryPatternPermittedGate,
    PERMITTED_ENTRY_PATTERNS,
)


@pytest.mark.parametrize("pattern", sorted(PERMITTED_ENTRY_PATTERNS))
def test_permitted_entry_patterns_pass(pattern):
    assert EntryPatternPermittedGate.check_signal({
        "entry_pattern": pattern,
        "entry_signal_mode": pattern,
        "entry_mode": "WILD_SWING",
    }) == (True, f"ENTRY_PATTERN_PERMITTED:{pattern}")


@pytest.mark.parametrize(
    "signal",
    [
        {},
        {"entry_pattern": None},
        {"entry_pattern": "STANDARD"},
        {"entry_pattern": "WILD_SWING"},
        {"entry_pattern": "MA_SWING"},
        {"entry_pattern": "REVERSAL", "entry_signal_mode": "CROSSOVER"},
    ],
)
def test_unpermitted_or_missing_entry_patterns_fail_closed(signal):
    assert EntryPatternPermittedGate.check_signal(signal) == (
        False, "REJECT_UNAUTHORIZED_ENTRY_PATTERN",
    )


def test_top_bottom_is_a_reversal_alias():
    assert EntryPatternPermittedGate.check_signal({
        "entry_pattern": "TOP_BOTTOM",
    }) == (True, "ENTRY_PATTERN_PERMITTED:REVERSAL")


def test_entry_mode_can_carry_a_pattern_when_explicit_pattern_fields_are_absent():
    assert EntryPatternPermittedGate.check_signal({
        "entry_mode": "BREAKOUT",
    }) == (True, "ENTRY_PATTERN_PERMITTED:BREAKOUT")


def test_family_entry_mode_is_not_itself_an_entry_pattern():
    assert EntryPatternPermittedGate.check_signal({
        "entry_mode": "WILD_SWING",
    }) == (False, "REJECT_UNAUTHORIZED_ENTRY_PATTERN")
