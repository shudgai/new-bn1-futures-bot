"""Every entry needs sufficient net room to a confirmed structural target."""
import math
from typing import Dict, Any

def entry_room(
    frame: Any, price: float, side: str, fee: float, slippage: float, minimum_net: float
) -> Dict[str, Any]:
    """Use completed bars for phase/structure, and the latest quote for costs."""
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
        closed = frame.iloc[:-1].tail(60)
        rows = []
        for _, row in closed.iterrows():
            values = tuple(float(row[k]) for k in ("open", "high", "low", "close"))
            opened, high, low, close = values
            if (not all(math.isfinite(v) and v > 0 for v in values)
                    or not low <= min(opened, close) <= max(opened, close) <= high):
                return invalid
            rows.append(values)
        atr = float(closed.iloc[-1]["atr"])
        if not math.isfinite(atr) or atr <= 0:
            return invalid

        sign = 1 if side == "LONG" else -1
        closes = [row[3] for row in rows[-7:]]
        pushes = [sign * (b - a) for a, b in zip(closes, closes[1:])]
        extension = sign * (closes[-1] - closes[0]) / atr
        last = pushes[-3:]
        weakening = last[0] > last[1] > last[2] and last[0] > 0 and last[2] <= .5 * last[0]
        mature = sum(v > 0 for v in pushes) >= 4 and extension >= 3.
        if not (mature and weakening):
            return dict(allowed=True, checked=False, stage="developing",
                        reason="KC_TREND_ROOM_SKIPPED",
                        detail="尚未符合末端衰退條件，不計算淨利空間。",
                        extension_atr=extension)
        extremes = [row[1] if side == "LONG" else row[2] for row in rows]
        targets = [
            value for i, value in enumerate(extremes[1:-1], start=1)
            if sign * value > sign * extremes[i - 1]
            and sign * value > sign * extremes[i + 1]
            and sign * (value - price) > 0
            and all(sign * later < sign * value for later in extremes[i + 1:])
        ]
        base = dict(checked=True, stage="late" if mature and weakening else "developing", extension_atr=extension)
        if not targets:
            return dict(base, allowed=False, reason="KC_PROFIT_TARGET_UNAVAILABLE",
                        detail="沒有尚未突破的已確認前高／前低可估算空間，暫不進場。")
        target = min(targets, key=lambda value: sign * value)
        entry_fill = price * (1 + sign * slippage)
        exit_fill = target * (1 - sign * slippage)
        net_room = (sign * (exit_fill - entry_fill) - (entry_fill + exit_fill) * fee) / (entry_fill * (1 + fee))
        allowed = net_room > 0 and net_room >= minimum_net
        return dict(base, allowed=allowed, target=target, net_room_pct=net_room * 100,
                    reason="KC_PROFIT_ROOM_OK" if allowed else "KC_PROFIT_ROOM_INSUFFICIENT",
                    detail=f"至未突破前高／前低的剩餘淨空間 {net_room * 100:.4f}%，門檻 {minimum_net * 100:.4f}%。")
    except (AttributeError, TypeError, ValueError, KeyError, IndexError, OverflowError):
        return invalid
