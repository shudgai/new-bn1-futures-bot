"""Account loss limits shared by Channel Swing quotes and account updates.
Implements IExitStrategy interface.
"""
import math
from typing import Dict, Any, Optional
import pandas as pd
from core import config
from core.interfaces.exit_interface import IExitStrategy


def retire_initial_atr_pending(position, meta=None):
    """Revoke obsolete ATR close retries, without changing account loss limits."""
    meta = meta if meta is not None else {}
    if str(position.get("entry_mode") or meta.get("entry_mode") or "").upper() != "CHANNEL_SWING":
        return False
    changed = False
    for source in (position, meta):
        if source.get("channel_hard_stop_pending") == "INITIAL_ATR":
            source.pop("channel_hard_stop_pending")
            changed = True
        for key in ("peak_trailing_state", "closed_exit_state", "exit_protection_snapshot"):
            state = source.get(key)
            if not isinstance(state, dict):
                continue
            if (state.get("pending") == "EXIT_INITIAL_ATR_HARD_STOP"
                    or state.get("reason") == "EXIT_INITIAL_ATR_HARD_STOP"
                    or state.get("trigger") == "INITIAL_ATR"):
                if key == "peak_trailing_state":
                    state.pop("pending", None)
                    state.pop("trigger", None)
                else:
                    source.pop(key)
                changed = True
    return changed


def hard_stop_reason(position, price):
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
        from core.services.structure_risk_sizing import POLICY, FULL_SLOT_POLICY
        budget=margin*config.MAX_POSITION_MARGIN_LOSS_RATIO
        if position.get('structure_risk_policy') in (POLICY, FULL_SLOT_POLICY):
            budget=float(position.get('structure_risk_budget_usdt') or 0.)
            if not math.isfinite(budget) or budget<=0:
                return 'MARGIN_LOSS'
        if budget > 0 and loss * qty >= budget:
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
    if retire_initial_atr_pending(position, meta):
        account.save_state()
    try:
        price = float(price)
    except (TypeError, ValueError):
        return False
    if not math.isfinite(price) or price <= 0:
        return False
    reason = hard_stop_reason({**meta, **position}, price)
    if not reason:
        return False
    if getattr(account, "channel_profit_reentries", {}).pop(symbol, None) is not None:
        account.save_state()
    if position.get("channel_hard_stop_pending") != reason:
        position["channel_hard_stop_pending"] = reason
        account.position_meta.setdefault(symbol, {})["channel_hard_stop_pending"] = reason
        account.save_state()
    return bool(await account.close_position(symbol, price, "Channel Swing HARD_STOP " + reason, is_manual=True))


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
