"""Owner-approved entry limits for PEPE and Lobster, excluding CAP."""

from core.symbol_aliases import is_lobster_symbol, is_pepe_symbol

MAX_EXTENSION_ATR = 3.5
MAX_CONTINUATION_BARS = 12


def uses_relaxed_entry_limits(symbol: str | None) -> bool:
    return is_lobster_symbol(symbol) or is_pepe_symbol(symbol)


def is_exhausted_wick(wick: float, body: float, candle_range: float) -> bool:
    return wick > 1.5 * body and wick > 0.65 * candle_range
