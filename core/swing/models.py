from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

LONG = "LONG"
SHORT = "SHORT"


@dataclass(frozen=True)
class Bar:
    """One bar with its indicators. `ma15` is the KC middle line."""
    open: float
    high: float
    low: float
    close: float
    volume: float
    ma5: float
    ma15: float
    kc_upper: float
    kc_lower: float
    atr: float
    is_closed: bool = True

    @property
    def body(self) -> float:
        return abs(self.close - self.open)

    @property
    def kc_pos(self) -> Optional[float]:
        width = self.kc_upper - self.kc_lower
        if width <= 0:
            return None
        return (self.close - self.kc_lower) / width


@dataclass(frozen=True)
class Position:
    side: str
    bars_held: int
    peak_roe: float
    current_roe: float


@dataclass
class SwingState:
    """Per-symbol memory used by the entry cooldown."""
    last_close_side: Optional[str] = None
    bars_since_close: Optional[int] = None


@dataclass(frozen=True)
class EntrySignal:
    side: str
    mode: str  # MA_SWING / BREAKOUT_MOMENTUM / DUMP_FLUSH_REVERSAL
    entry_at: str  # NEXT_OPEN / BAR_CLOSE


@dataclass(frozen=True)
class ExitDecision:
    reason: str
    order_type: str  # LIMIT / MARKET
    limit_price: Optional[float] = None
