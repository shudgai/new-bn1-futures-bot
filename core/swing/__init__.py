"""Clean swing-trading core: ratchet lock, smart entry engine, dump-flush reversal."""
from .models import Bar, EntrySignal, ExitDecision, Position, SwingState
from .ratchet import RATCHET_ACTIVATE_ROE, RATCHET_STEP_ROE, floor_roe
from .exits import ExitGatekeeper, evaluate_exit
from .entry import evaluate_entry
from .reversal import evaluate_dump_reversal

__all__ = [
    "Bar", "EntrySignal", "ExitDecision", "Position", "SwingState",
    "RATCHET_ACTIVATE_ROE", "RATCHET_STEP_ROE", "floor_roe",
    "ExitGatekeeper", "evaluate_exit", "evaluate_entry", "evaluate_dump_reversal",
]
