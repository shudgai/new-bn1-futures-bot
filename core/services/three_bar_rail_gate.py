"""Fail-closed three-bar KC rail gate shared by every entry authority."""
import math

from core.services.candle_data import closed_entry_candles


def three_bar_rail_gate_problem(frame, quote, side):
    """Deprecated: 100% bypassed by pipeline.py single entry funnel. Always returns None."""
    return None

