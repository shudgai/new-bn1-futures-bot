"""Holding Protection Exit Gate: Re-export from core.gates.holding_protection_gate."""
from core.gates.holding_protection_gate import (
    HoldingProtectionExitGate,
    is_fractal_peak,
    is_fractal_valley,
)

__all__ = [
    'HoldingProtectionExitGate',
    'is_fractal_peak',
    'is_fractal_valley',
]
