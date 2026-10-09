"""Loss-stop evaluation; Channel Swing position loss exits are retired."""
import math
from typing import Dict, Any, Optional
import pandas as pd
from core import config
from core.interfaces.exit_interface import IExitStrategy


def hard_stop_reason(position, price):
    # Channel Swing position loss stops are disabled by explicit user policy.
    # Account-level circuit breakers are independent of this position exit.
    if str(position.get("entry_mode") or "").upper() == "CHANNEL_SWING":
        return None
    pending = position.get("channel_hard_stop_pending")
    if pending in ("MARGIN_LOSS", "PRICE_LOSS"):
        return pending
    try:
        entry = float(position["entry_price"])
        qty = abs(float(position["qty"]))
        leverage = float(position.get("leverage") or 1.)
        margin = float(position.get("margin") or entry * qty / leverage)
        price = float(price)
        side = position["side"]
        if side not in ("LONG", "SHORT") or not all(math.isfinite(v) and v > 0 for v in (entry, qty, leverage, margin, price)):
            return None
        loss = (entry - price) if side == "LONG" else (price - entry)
        if config.MAX_POSITION_MARGIN_LOSS_RATIO > 0 and loss * qty >= margin * config.MAX_POSITION_MARGIN_LOSS_RATIO:
            return "MARGIN_LOSS"
        if config.MAX_ACCEPTABLE_LOSS_PCT < 0 and loss / entry >= -config.MAX_ACCEPTABLE_LOSS_PCT:
            return "PRICE_LOSS"
    except (KeyError, TypeError, ValueError, ZeroDivisionError):
        pass
    return None


async def enforce_hard_stop(account, symbol, price):
    position = account.positions.get(symbol)
    if not position:
        return False
    meta = account.position_meta.get(symbol, {})
    if str(position.get("entry_mode") or meta.get("entry_mode") or "").upper() != "CHANNEL_SWING":
        return False
    # Retire previously persisted loss-stop retries as well as new triggers.
    changed = False
    for source in (position, meta):
        changed = source.pop("channel_hard_stop_pending", None) is not None or changed
    if changed:
        account.save_state()
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
