"""反向開單冷卻鎖 (Reverse Entry Cooldown Lock).

防止剛平倉立即反向開單（多轉空、空轉多冷卻與大趨勢確認）。
做成多空雙向對稱的完整守衛，同時涵蓋「剛平多禁止秒開空」與「剛平空禁止秒開多」。
"""
import time
from typing import Any, Optional, Tuple
import pandas as pd


def check_reverse_cooldown_lock(
    symbol: str,
    target_action: str,
    current_price: float,
    current_bar_index: int,
    last_closed_trade: Optional[dict[str, Any]],
    ma60: float,
) -> Tuple[str, Optional[str]]:
    """
    【反向開單冷卻鎖】防止剛平倉立即反向開單（多轉空、空轉多冷卻與大趨勢確認）
    
    返回: (target_action, filter_reason)
    """
    if not last_closed_trade or not last_closed_trade.get('side'):
        return target_action, None

    last_side = str(last_closed_trade['side']).lower()
    last_exit_index = int(last_closed_trade.get('bar_index', 0))
    bars_since_last_exit = current_bar_index - last_exit_index

    # 1. 剛平多單（LONG），若想反手開空（OPEN_SHORT）：
    if target_action == "OPEN_SHORT" and last_side in ('long', 'buy'):
        # 必須滿足：平多後至少經過 3 根 K 棒，且價格實體已穩穩跌破 60MA
        if bars_since_last_exit < 3 or (ma60 > 0 and current_price > ma60):
            filter_reason = (
                f"平多冷卻中 (已過 {max(0, bars_since_last_exit)}/3 根K棒，"
                f"或價格未收在 60MA 下方 [現價 {current_price:.6g} > 60MA {ma60:.6g}])"
            )
            return "WAIT", filter_reason

    # 2. 剛平空單（SHORT），若想反手開多（OPEN_LONG）：
    elif target_action == "OPEN_LONG" and last_side in ('short', 'sell'):
        # 必須滿足：平空後至少經過 3 根 K 棒，且價格實體已穩穩站上 60MA
        if bars_since_last_exit < 3 or (ma60 > 0 and current_price < ma60):
            filter_reason = (
                f"平空冷卻中 (已過 {max(0, bars_since_last_exit)}/3 根K棒，"
                f"或價格未收在 60MA 上方 [現價 {current_price:.6g} < 60MA {ma60:.6g}])"
            )
            return "WAIT", filter_reason

    return target_action, None


def evaluate_reverse_cooldown_guard(
    symbol: str,
    target_action: str,
    current_price: float,
    last_closed_trade: Optional[dict[str, Any]],
    frame: Optional[pd.DataFrame] = None,
) -> Tuple[bool, str, Optional[str]]:
    """
    自動計算當前 K 棒 index 與 60MA，並執行反向冷卻鎖審查。
    
    返回: (allowed: bool, action: str, reason: Optional[str])
    """
    now_ms = time.time() * 1000
    current_bar_index = int(now_ms // 60_000)
    
    ma60 = 0.0
    if frame is not None and len(frame) > 0:
        try:
            last_bar = frame.iloc[-1]
            if "timestamp" in last_bar and float(last_bar["timestamp"]) > 0:
                current_bar_index = int(float(last_bar["timestamp"]) // 60_000)
            if "ma60" in last_bar and float(last_bar["ma60"]) > 0:
                ma60 = float(last_bar["ma60"])
            elif "close" in frame.columns:
                closes = frame["close"].tail(60)
                ma60 = float(closes.mean())
        except Exception:
            ma60 = 0.0

    action, reason = check_reverse_cooldown_lock(
        symbol=symbol,
        target_action=target_action,
        current_price=float(current_price),
        current_bar_index=current_bar_index,
        last_closed_trade=last_closed_trade,
        ma60=ma60,
    )
    
    allowed = (action != "WAIT")
    return allowed, action, reason
