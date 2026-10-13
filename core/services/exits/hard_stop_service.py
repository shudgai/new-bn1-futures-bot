"""Account-level loss-stop evaluation for open positions."""
import math
from typing import Dict, Any, Optional
import pandas as pd
from core import config
from core.interfaces.exit_interface import IExitStrategy


def hard_stop_reason(position, price):
    pending = position.get("channel_hard_stop_pending")
    if pending in ("MARGIN_LOSS", "PRICE_LOSS", "HARD_STOP_1_2_ATR"):
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

        # 開倉初期生命週期保護（前 90 秒內）：
        # 只要未擊穿 1.2 ATR 硬停損線，嚴禁因保證金浮動（MARGIN_LOSS）或盤中跳價秒平！
        open_ts = float(position.get("open_timestamp") or position.get("entry_timestamp") or 0.0)
        if open_ts > 0 and open_ts < 1e11:
            open_ts *= 1000.0
        import time
        now_ms = time.time() * 1000.0
        entry_atr = float(position.get("entry_atr") or 0.0)
        if open_ts > 0 and (now_ms - open_ts) < 90_000.0:
            if entry_atr > 0 and loss >= 1.2 * entry_atr:
                return "HARD_STOP_1_2_ATR"
            return None

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
    pending = position.get("channel_hard_stop_pending") or meta.get("channel_hard_stop_pending")
    if pending in ("MARGIN_LOSS", "PRICE_LOSS"):
        position["channel_hard_stop_pending"] = pending
        reason = pending
    else:
        reason = hard_stop_reason(position, price)
    if reason is None:
        return False

    position["channel_hard_stop_pending"] = reason
    meta["channel_hard_stop_pending"] = reason
    account.save_state()
    closed = await account.close_position(
        symbol, price, f"ACCOUNT_HARD_STOP_{reason}", is_manual=True,
    )
    if closed:
        return True

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
