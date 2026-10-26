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
        # 妖幣敏捷放寬：平多後冷卻 3 根 K 棒；滿 3 根後不因滯後 60MA 阻斷順勢開空
        if bars_since_last_exit < 3:
            filter_reason = f"平多冷卻中 (已過 {max(0, bars_since_last_exit)}/3 根K棒)"
            return "WAIT", filter_reason

    # 2. 剛平空單（SHORT），若想反手開多（OPEN_LONG）：
    elif target_action == "OPEN_LONG" and last_side in ('short', 'sell'):
        # 妖幣敏捷放寬：平空後冷卻 3 根 K 棒；滿 3 根後不因滯後 60MA 阻斷順勢開多
        if bars_since_last_exit < 3:
            filter_reason = f"平空冷卻中 (已過 {max(0, bars_since_last_exit)}/3 根K棒)"
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


def check_same_direction_reentry_cooldown(
    target_action: str,
    last_closed_trade: Optional[dict[str, Any]],
    cooldown_seconds: float = 120.0,
    now_ts: Optional[float] = None,
) -> Tuple[bool, Optional[str]]:
    """Block same-direction re-entry until the cooldown has expired.

    - 多單平倉後，阻擋同方向重新開多 (OPEN_LONG)。
    - 空單平倉後，阻擋同方向重新開空 (OPEN_SHORT)。
    - 反手進場不在此處阻擋（由 evaluate_reverse_cooldown_guard 處理）。
    - 遇到無效或未來時間戳時採保守策略拒絕 (Fail-Closed)。
    """
    if target_action not in ("OPEN_LONG", "OPEN_SHORT"):
        return True, None
    if not last_closed_trade:
        return True, None

    last_side = str(last_closed_trade.get("side", "")).lower()
    same_direction = (
        (target_action == "OPEN_LONG" and last_side in ("long", "buy"))
        or
        (target_action == "OPEN_SHORT" and last_side in ("short", "sell"))
    )
    if not same_direction:
        return True, None

    try:
        import math
        cooldown = float(cooldown_seconds)
        exit_ts = float(last_closed_trade.get("time", 0))
        now = time.time() if now_ts is None else float(now_ts)
        if (
            not math.isfinite(cooldown)
            or cooldown < 0
            or not math.isfinite(exit_ts)
            or exit_ts <= 0
            or not math.isfinite(now)
            or now < exit_ts
        ):
            return False, "INVALID_REENTRY_COOLDOWN_TIMESTAMP"
        elapsed = now - exit_ts
        remaining = cooldown - elapsed
        if remaining > 0:
            return False, (
                f"SAME_DIRECTION_REENTRY_COOLDOWN: "
                f"elapsed={elapsed:.1f}s, "
                f"remaining={remaining:.1f}s, "
                f"required={cooldown:.1f}s"
            )
    except (TypeError, ValueError, OverflowError):
        return False, "INVALID_REENTRY_COOLDOWN_STATE"
    return True, None


def check_closed_candle_freshness(
    last_candle_timestamp_ms: float,
    timeframe_ms: int,
    *,
    timestamp_is_open_time: bool = True,
    now_ms: Optional[float] = None,
    grace_ms: int = 30_000,
    max_delay_ms: Optional[float] = None,
) -> Tuple[bool, Optional[str]]:
    """Validate the age of the latest confirmed closed candle.

    Args:
        last_candle_timestamp_ms: Timestamp in milliseconds of the latest closed candle.
        timeframe_ms: Candle timeframe in milliseconds (60_000 for 1m, 180_000 for 3m, 900_000 for 15m).
        timestamp_is_open_time: If True, last_candle_timestamp_ms represents candle open time,
                                and close time = open_time + timeframe_ms.
                                If False, last_candle_timestamp_ms is already close time.
        now_ms: Current time in milliseconds. If None, time.time() * 1000 is used.
        grace_ms: Default grace period in ms if max_delay_ms is not specified.
        max_delay_ms: Custom maximum delay in ms after candle close.
                      If None, defaults to:
                      - 1m (60_000ms): 90_000ms (90s after close)
                      - 3m (180_000ms): 210_000ms (210s after close)
                      - 15m (900_000ms): 960_000ms (960s after close)

    Returns:
        (is_fresh: bool, rejection_reason: Optional[str])
    """
    if timeframe_ms not in (60_000, 180_000, 900_000):
        return False, f"UNSUPPORTED_TIMEFRAME: {timeframe_ms}ms"

    try:
        import math
        stamp = float(last_candle_timestamp_ms)
        now = (
            time.time() * 1000.0
            if now_ms is None
            else float(now_ms)
        )
        if (
            not math.isfinite(stamp)
            or not math.isfinite(now)
            or stamp <= 0
            or grace_ms < 0
        ):
            return False, "INVALID_CANDLE_TIMESTAMP"

        # Determine candle close time
        candle_close_ms = stamp + timeframe_ms if timestamp_is_open_time else stamp

        if now < candle_close_ms:
            # Candle has not closed yet (or stamp is in the future)
            return False, f"CANDLE_NOT_YET_CLOSED: now_ms={now:.0f} < close_ms={candle_close_ms:.0f}"

        elapsed_after_close_ms = now - candle_close_ms

        # Determine threshold
        if max_delay_ms is not None:
            threshold_ms = float(max_delay_ms)
        else:
            timeframe_defaults = {
                60_000: 90_000.0,     # 1m: 90s delay after close
                180_000: 210_000.0,   # 3m: 210s delay after close
                900_000: 960_000.0,   # 15m: 960s (16m) delay after close
            }
            threshold_ms = timeframe_defaults.get(timeframe_ms, float(timeframe_ms + grace_ms))

        if elapsed_after_close_ms > threshold_ms:
            return False, (
                f"STALE_CANDLE_DATA: elapsed={elapsed_after_close_ms / 1000.0:.1f}s > "
                f"max_allowed={threshold_ms / 1000.0:.1f}s (tf={timeframe_ms // 1000}s)"
            )

        return True, None
    except (TypeError, ValueError, OverflowError):
        return False, "INVALID_CANDLE_TIMESTAMP_STATE"

