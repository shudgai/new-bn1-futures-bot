"""Fail-closed allowlist for automatic entry signal patterns."""

from collections.abc import Mapping
from typing import Any, Optional


PERMITTED_ENTRY_PATTERNS = frozenset({
    "BREAKOUT",
    "CONTINUATION",
    "CROSSOVER",
    "REVERSAL",
})

ENTRY_PATTERN_ALIASES = {
    "TOP_BOTTOM": "REVERSAL",
}


class EntryPatternPermittedGate:
    """Authorize only explicitly supported WILD_SWING entry patterns."""

    @staticmethod
    def normalize(pattern: Any) -> Optional[str]:
        if not isinstance(pattern, str):
            return None
        normalized = pattern.strip().upper()
        normalized = ENTRY_PATTERN_ALIASES.get(normalized, normalized)
        return normalized if normalized in PERMITTED_ENTRY_PATTERNS else None

    @classmethod
    def check_pattern(cls, pattern: Any) -> tuple[bool, str]:
        normalized = cls.normalize(pattern)
        if normalized is None:
            return False, "REJECT_UNAUTHORIZED_ENTRY_PATTERN"
        return True, f"ENTRY_PATTERN_PERMITTED:{normalized}"

    @classmethod
    def check_signal(cls, signal: Any) -> tuple[bool, str]:
        if not isinstance(signal, Mapping):
            return False, "REJECT_UNAUTHORIZED_ENTRY_PATTERN"

        pattern = signal.get("entry_pattern")
        legacy_mode = signal.get("entry_signal_mode")
        entry_mode = signal.get("entry_mode")
        if pattern is None and legacy_mode is None:
            return cls.check_pattern(entry_mode)
        normalized_pattern = cls.normalize(pattern)
        normalized_legacy_mode = cls.normalize(legacy_mode)

        if pattern is not None and normalized_pattern is None:
            return False, "REJECT_UNAUTHORIZED_ENTRY_PATTERN"
        if legacy_mode is not None and normalized_legacy_mode is None:
            return False, "REJECT_UNAUTHORIZED_ENTRY_PATTERN"
        if (
            normalized_pattern is not None
            and normalized_legacy_mode is not None
            and normalized_pattern != normalized_legacy_mode
        ):
            return False, "REJECT_UNAUTHORIZED_ENTRY_PATTERN"

        return cls.check_pattern(
            normalized_pattern or normalized_legacy_mode
        )
