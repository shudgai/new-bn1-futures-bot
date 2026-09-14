"""Every entry needs sufficient net room to a confirmed structural target."""
import math
from typing import Dict, Any

from core import config

def entry_room(
    frame: Any, price: float, side: str, fee: float, slippage: float, minimum_net: float
) -> Dict[str, Any]:
    """Use completed bars for phase/structure, and the latest quote for costs."""
    if not getattr(config, "CHANNEL_PROFIT_ROOM_ENABLED", True):
        return dict(allowed=True, checked=True, stage="developing", target=0.0, net_room_pct=100.0,
                    reason="KC_PROFIT_ROOM_OK", detail="淨利空間攔截已停用（設定）。")
    invalid = dict(allowed=False, checked=False, stage="invalid",
                   reason="KC_PROFIT_ROOM_DATA_INVALID", detail="走勢或價格資料無效，暫不進場。")
    try:
        price = float(price)
        if side not in ("LONG", "SHORT") or frame is None or len(frame) < 8:
            return invalid
        if not all(math.isfinite(v) for v in (price, fee, slippage, minimum_net)):
            return invalid
        if price <= 0 or not 0 <= fee < 1 or not 0 <= slippage < 1 or minimum_net < 0:
            return invalid
        if len(frame) < 8:
            return invalid
        closed = frame.iloc[:-1].tail(60)
        rows = []
        for _, row in closed.iterrows():
            values = tuple(float(row[k]) for k in ("open", "high", "low", "close"))
            opened, high, low, close = values
            if (not all(math.isfinite(v) and v > 0 for v in values)
                    or not low <= min(opened, close) <= max(opened, close) <= high):
                return invalid
            rows.append(values)
        atr = float(frame.iloc[-2]["atr"])
        if not math.isfinite(atr) or atr <= 0:
            return invalid
        history = frame.iloc[:-1].tail(60)
        if history.empty:
            return invalid
        swing_high = float(history["high"].max())
        swing_low = float(history["low"].min())
        if not all(math.isfinite(v) for v in (swing_high, swing_low, price)):
            return invalid

        if side == "LONG":
            targets = history.loc[history["high"].astype(float) > price, "high"]
            target = float(targets.min()) if not targets.empty else float("nan")
            space = target - price if math.isfinite(target) else 0.0
            detail = (f"已突破前波峰 {swing_high:.10g}，進入加速段。"
                      if not math.isfinite(target) else
                      f"距前方結構目標 {space:.10g}。")
        else:
            targets = history.loc[history["low"].astype(float) < price, "low"]
            target = float(targets.max()) if not targets.empty else float("nan")
            space = price - target if math.isfinite(target) else 0.0
            detail = (f"已跌破前波谷 {swing_low:.10g}，進入加速段。"
                      if not math.isfinite(target) else
                      f"距前方結構目標 {space:.10g}。")
        gross_pct = space / price if price > 0 else 0.0
        net_pct = gross_pct - 2.0 * (float(fee) + float(slippage))
        target_available = math.isfinite(target)
        allowed = target_available and net_pct >= minimum_net
        reason = (
            "KC_PROFIT_ROOM_OK" if allowed
            else "KC_PROFIT_ROOM_INSUFFICIENT" if target_available
            else "KC_PROFIT_TARGET_UNAVAILABLE"
        )
        return dict(allowed=allowed, checked=True, stage="developing",
                    target=target if math.isfinite(target) else None,
                    gross_room_pct=gross_pct * 100.0,
                    net_room_pct=net_pct * 100.0,
                    reason=reason,
                    detail=detail)
    except (AttributeError, TypeError, ValueError, KeyError, IndexError, OverflowError):
        return invalid
