"""Account-layer close gate for WILD_SWING positions.

Whitelist only: a swing position may close solely through the swing ExitGatekeeper
(reason prefix "Swing "), the account circuit breaker, the market-wide breaker, the
data-loss watchdog, or an explicit manual close. Any other reason (legacy per-trade
hard stops such as HARD_STOP_1_2_ATR, PRICE_LOSS, MARGIN_LOSS, ATR/MA exits, ...) is rejected.
"""
from __future__ import annotations

from .account_guard import BREAKER_REASON

ALLOWED_PREFIXES = ("Swing ", BREAKER_REASON, "全市場熔斷", "Watchdog", "手動平倉")


def wild_swing_close_allowed(close_reason: str) -> bool:
    return str(close_reason or "").startswith(ALLOWED_PREFIXES)
