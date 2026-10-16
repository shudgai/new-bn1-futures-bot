"""Entry gate pipeline & exports.
Integrates StrictEntryAndImmunityGate, StrictCrossAndKcTrendGate, etc.
"""
from core.gates.breakout_continuation_entry_gate import BreakoutContinuationEntryGate
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
    from core.gates.strict_entry_and_immunity_gate import resolve_kc_trend
    kc_trend = resolve_kc_trend(df)

    if candidate_signal in ['OPEN_LONG', 'LONG']:
        if kc_trend != 'UP':
            msg = f"CRITICAL_BLOCK: Forbidden to open LONG when KC is {kc_trend} (Must be UP)"
            print(f"[{symbol}] [進場攔截 VETO] {msg}", flush=True)
            return False, msg
    elif candidate_signal in ['OPEN_SHORT', 'SHORT']:
        if kc_trend != 'DOWN':
            msg = f"CRITICAL_BLOCK: Forbidden to open SHORT when KC is {kc_trend} (Must be DOWN)"
            print(f"[{symbol}] [進場攔截 VETO] {msg}", flush=True)
            return False, msg

    if candidate_signal in ['OPEN_LONG', 'OPEN_SHORT', 'LONG', 'SHORT']:
        side = 'LONG' if 'LONG' in candidate_signal else 'SHORT'
        quote = float(df.iloc[current_bar_index].get('close', 0.0)) if df is not None and len(df) > 0 else None
        exh_ok, exh_reason = MemeSharedProtectionGate.validate_trend_exhaustion(df, side, quote)
        if not exh_ok:
            print(f"[{symbol}] [進場攔截 VETO] {exh_reason}", flush=True)
            return False, exh_reason
        if quote is not None and quote > 0:
            room_ok, room_reason = MemeSharedProtectionGate.validate_profit_room(df, side, quote)
            if not room_ok:
                print(f"[{symbol}] [進場攔截 VETO] {room_reason}", flush=True)
                return False, room_reason

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
    "BreakoutContinuationEntryGate",
    "has_recent_golden_cross",
    "has_recent_death_cross",
]
