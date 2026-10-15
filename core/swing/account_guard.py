"""Account-level circuit breaker: the only hard stop in the system."""
from __future__ import annotations

import math
from typing import Dict, Optional

BREAKER_REASON = "ACCOUNT_CIRCUIT_BREAKER_HARD_STOP"


def unrealized_pnl(positions: Dict[str, dict], prices: Dict[str, float]) -> float:
    total = 0.0
    for symbol, pos in positions.items():
        price = prices.get(symbol)
        try:
            entry, qty, price = float(pos["entry_price"]), abs(float(pos["qty"])), float(price)
        except (KeyError, TypeError, ValueError):
            continue
        if not all(math.isfinite(v) and v > 0 for v in (entry, qty, price)):
            continue
        total += (price - entry) * qty if str(pos.get("side")).upper() == "LONG" else (entry - price) * qty
    return total


def breaker_reason(
    equity: float, day_start_equity: float, max_daily_loss_pct: float, equity_floor: float = 0.0,
) -> Optional[str]:
    """Tripped when total equity breaks the floor or the day's loss limit. Invalid input: no trip."""
    if not math.isfinite(equity):
        return None
    if equity_floor > 0 and equity <= equity_floor:
        return "EQUITY_FLOOR"
    if max_daily_loss_pct > 0 and day_start_equity > 0:
        loss_pct = (day_start_equity - equity) / day_start_equity * 100.0
        if loss_pct >= max_daily_loss_pct:
            return "DAILY_LOSS_LIMIT"
    return None


async def enforce_account_breaker(account, prices: Dict[str, float], max_daily_loss_pct: float,
                                  equity_floor: float = 0.0, today: Optional[str] = None) -> bool:
    """Flatten every position and halt new entries for the day when the breaker trips."""
    equity = float(account.get_wallet_balance()) + unrealized_pnl(account.positions, prices)
    reason = breaker_reason(equity, float(getattr(account, "daily_start_balance", 0.0) or 0.0),
                            max_daily_loss_pct, equity_floor)
    if reason is None:
        return False
    account.circuit_breaker_date = today
    account.log(f"🛑 [帳戶熔斷] {reason} equity={equity:.2f}，全域平倉並停止開新倉", "DANGER")
    for symbol in list(account.positions):
        price = prices.get(symbol) or float(account.positions[symbol].get("entry_price") or 0.0)
        await account.close_position(symbol, float(price), f"{BREAKER_REASON}_{reason}", is_manual=True)
    return True


def breaker_active(account, today: str) -> bool:
    return getattr(account, "circuit_breaker_date", None) == today
