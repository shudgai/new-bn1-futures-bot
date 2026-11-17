"""Account-level circuit breakers are separate from position exit rules."""
from typing import Dict, Any, Optional
from collections.abc import Mapping
import pandas as pd
from core.interfaces.exit_interface import IExitStrategy

POSITION_HARD_STOPS_ENABLED = False
NEW_POSITION_LOSS_POLICY = "NO_PRICE_ATR_STOP_V1"


def price_atr_stop_disabled(
    position: Mapping[str, Any], metadata: Optional[Mapping[str, Any]] = None,
) -> bool:
    """The all-position policy disables numeric stops, not account emergencies."""
    if not POSITION_HARD_STOPS_ENABLED:
        return True
    policy = position.get("position_loss_policy")
    if policy is None and isinstance(metadata, Mapping):
        policy = metadata.get("position_loss_policy")
    return policy == NEW_POSITION_LOSS_POLICY


def hard_stop_reason(position, price):
    """Evaluate market circuit breaker / hard stop (loss >= 2.0% or 3.0 ATR)."""
    if not isinstance(position, Mapping):
        return None
    entry_price = float(position.get("entry_price") or 0.0)
    if entry_price <= 0 or not price or price <= 0:
        return None
    side = str(position.get("side") or "").upper()
    if side not in ("LONG", "SHORT"):
        return None

    loss_pct = (entry_price - price) / entry_price if side == "LONG" else (price - entry_price) / entry_price
    atr = float(position.get("entry_atr") or position.get("atr") or 0.0)
    loss_atr = ((entry_price - price) if side == "LONG" else (price - entry_price)) / atr if atr > 0 else 0.0

    if loss_pct >= 0.02 or (atr > 0 and loss_atr >= 3.0):
        return "CIRCUIT_BREAKER_HARD_SL"
    return None


async def enforce_hard_stop(account, symbol, price):
    """Enforce real-time market circuit breaker / hard stop on incoming price."""
    from core.symbol_aliases import to_canonical_display_symbol
    canonical_sym = to_canonical_display_symbol(symbol)
    positions = getattr(account, "positions", {})
    pos = positions.get(canonical_sym) or positions.get(symbol)
    if not pos or abs(float(pos.get("amount", 0.0) or pos.get("qty", 0.0))) <= 0:
        return False

    reason = hard_stop_reason(pos, price)
    if reason:
        from core.services.execution_manager import ExecutionManager
        entry_price = float(pos.get("entry_price") or 0.0)
        side = str(pos.get("side") or "").upper()
        loss_pct = (entry_price - price) / entry_price if side == "LONG" else (price - entry_price) / entry_price
        log_msg = (
            f"⚡ [CIRCUIT_BREAKER_HARD_SL] {canonical_sym} {side} 達到熔斷硬止損！"
            f"現價={price} 進場價={entry_price} 虧損率={loss_pct*100:.2f}% (門檻 -2.0%)，"
            f"毫秒級市價熔斷平倉！"
        )
        if hasattr(account, "log"):
            account.log(log_msg, "CRITICAL")

        closed = await ExecutionManager.close_position(
            account,
            canonical_sym,
            price,
            reason,
            meta={"is_bar_closed": False},
        )
        return bool(closed)

    return False


class HardStopExitStrategy(IExitStrategy):
    """OOP Strategy class implementing IExitStrategy for hard stop loss evaluation."""

    def evaluate_exit(
        self,
        position: Dict[str, Any],
        frame: pd.DataFrame,
        price: float,
        **kwargs: Any
    ) -> Optional[str]:
        reason = hard_stop_reason(position, price)
        if reason:
            return "HARD_STOP_" + reason
        return None
