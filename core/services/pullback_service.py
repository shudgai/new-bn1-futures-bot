import re
import pandas as pd
from core.config import PULLBACK_RECLAIM_MIN_ATR

def quality_bonus(reason: str) -> int:
    match = re.search(r"Quality\+(\d+)", reason or "")
    return int(match.group(1)) if match else 0

def format_pullback_order_log(symbol: str, candidate: dict, target: float) -> str:
    return (
        f"📝 [回踩掛單] {symbol} {candidate['side']} "
        f"原始{candidate['score']}分 → 回踩確認"
        f"{candidate['pullback_confirmation_score']}分，掛單 @ {target:.8g}"
    )

def pullback_reversal_confirmed(candidate: dict, candles_1m: pd.DataFrame) -> bool:
    """觸價後必須等包含觸價時刻的 1m K 棒真正收盤，且收回目標外側。"""
    if candles_1m is None or candles_1m.empty or len(candles_1m) < 8:
        return False
    candle = candles_1m.iloc[-1]
    close_time_ms = float(candle["timestamp"]) + 60_000
    if close_time_ms <= float(candidate.get("touched_at", 0.0)) * 1000:
        return False

    # 1m MA5 拐頭向上/向下確認
    closes = candles_1m["close"].astype(float)
    ma5 = closes.rolling(window=5).mean()
    if pd.isna(ma5.iloc[-1]) or pd.isna(ma5.iloc[-2]):
        return False
    ma5_curr = ma5.iloc[-1]
    ma5_prev = ma5.iloc[-2]

    target = float(candidate["target_price"])
    atr = max(float(candidate.get("atr") or 0.0), target * 1e-6)
    reclaim = atr * PULLBACK_RECLAIM_MIN_ATR
    open_price = float(candle["open"])
    close_price = float(candle["close"])
    if candidate["side"] == "LONG":
        return bool(
            close_price > open_price
            and close_price >= target + reclaim
            and ma5_curr > ma5_prev
        )
    return bool(
        close_price < open_price
        and close_price <= target - reclaim
        and ma5_curr < ma5_prev
    )

def classify_pullback_drop(reason: str, candidate: dict) -> str:
    if "等待回踩/反轉確認逾時" in reason:
        return "reversal_timeout" if candidate.get("touched_at") else "touch_timeout"
    if "回調總分不足" in reason:
        return "score_low"
    if "品質不足" in reason or "Quality_Too_Low" in reason:
        return "quality_low"
    if "目標漂移" in reason:
        return "target_drift"
    if "錯側" in reason:
        return "reversal_wrong_side"
    if "槽位" in reason:
        return "slot_full"
    if "保證金" in reason:
        return "insufficient_balance"
    if "熔斷" in reason:
        return "daily_halt"
    if "停止新倉" in reason or "移出牌面" in reason:
        return "symbol_disabled"
    if "條件已變差" in reason:
        return "condition_changed"
    return "other_cancel"
