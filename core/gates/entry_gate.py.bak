"""Entry gate pipeline & exports.
Integrates StrictEntryAndImmunityGate, StrictCrossAndKcTrendGate, etc.
"""
from core.gates.strict_entry_and_immunity_gate import StrictEntryAndImmunityGate, default_strict_gate
from core.services.gates.entry_gate import (
    StrictCrossAndKcTrendGate,
    MemeSharedProtectionGate,
    MacroCandleStructureGate,
    FiveLinesConfluenceGate,
    TrendConfirmationFilterGate,
    EntryGate,
    has_recent_golden_cross,
    has_recent_death_cross,
)

strict_gate = default_strict_gate

def evaluate_entry_candidate(candidate_signal: str, df, current_bar_index: int = 0, symbol: str = "UNKNOWN") -> tuple[bool, str]:
    """進場審查點：整合 StrictEntryAndImmunityGate 與各前置審查閘門"""
    if candidate_signal in ['OPEN_LONG', 'OPEN_SHORT', 'LONG', 'SHORT']:
        can_open, reason = strict_gate.can_open(candidate_signal, df, current_bar_index)
        if not can_open:
            print(f"[{symbol}] [進場攔截 VETO] {reason}", flush=True)
            return False, reason
    return True, "PASSED"

__all__ = [
    "StrictEntryAndImmunityGate",
    "default_strict_gate",
    "strict_gate",
    "evaluate_entry_candidate",
    "StrictCrossAndKcTrendGate",
    "MemeSharedProtectionGate",
    "MacroCandleStructureGate",
    "FiveLinesConfluenceGate",
    "TrendConfirmationFilterGate",
    "EntryGate",
    "has_recent_golden_cross",
    "has_recent_death_cross",
]
