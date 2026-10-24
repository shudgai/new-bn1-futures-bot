"""
core/gates/top_bottom_reversal_exit.py
======================================
極值轉折點（V 轉 / 倒 V 轉）見頂/見底即平倉規則（2026-10-23 最新授權）。

核心原則：
- 空單在最低點出現下影線打底、MA3 觸底勾頭或低檔陽包陰，不等回摸中軌，立即平空鎖住最大跌幅利潤！
- 多單在最高點出現上影線衝高回落、MA3 見頂下彎或高檔陰包陽，不等回踩中軌，立即平多賣在衝高轉折處！

防洗盤與防抖動機制：
1. 嚴格限定僅用已收線 K 棒（排除未定型盤中毛刺）。
2. 開倉初期防抖：最新已收線棒的時間戳必須在開倉之後，不在進場同根棒內被微幅雜訊誤平。
3. 波段展開防守：持倉必須曾有實質波段展開（曾獲利 >= 0.25 ATR，或波段極值曾探至/逼近 KC 外軌，或峰值 ROE >= 0.4%）。
"""
from __future__ import annotations

import math
from typing import Any, Dict, Optional, Tuple

import pandas as pd

CLOSE_SHORT_AT_BOTTOM = "CLOSE_SHORT_AT_BOTTOM"
CLOSE_LONG_AT_TOP = "CLOSE_LONG_AT_TOP"

_REQUIRED = ("open", "high", "low", "close", "kc_upper", "kc_lower")


def _closed_bars(raw_df: Any) -> Optional[pd.DataFrame]:
    if not isinstance(raw_df, pd.DataFrame) or raw_df.empty:
        return None
    if "is_closed" in raw_df.columns:
        mask = raw_df["is_closed"].fillna(False).astype(bool)
        return raw_df[mask]
    return raw_df.iloc[:-1]


def _to_ms(value: Any) -> Optional[float]:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(v) or v <= 0:
        return None
    return v * 1000.0 if v < 1e11 else v


def evaluate_top_bottom_reversal_exit(
    pos: Dict[str, Any],
    raw_df: Any,
    atr_val: Optional[float] = None,
) -> Tuple[bool, Optional[str], Dict[str, Any]]:
    """
    評估是否出現「谷底」立即平空或「峰頂」立即平多。
    回傳 (should_exit, reason, meta)
    """
    meta: Dict[str, Any] = {"rule": "TOP_BOTTOM_REVERSAL_EXIT"}
    if not pos:
        return False, None, meta

    side = str(pos.get("side") or "").upper()
    if side not in ("LONG", "SHORT"):
        return False, None, meta

    entry_p = float(pos.get("entry_price") or 0.0)
    if entry_p <= 0:
        return False, None, meta

    closed = _closed_bars(raw_df)
    if closed is None or len(closed) < 4:
        return False, None, meta
    if any(col not in closed.columns for col in _REQUIRED):
        return False, None, meta

    # 1. 開倉初期防抖：最新已收線棒的時間戳必須在開倉之後
    entry_ms = _to_ms(pos.get("open_timestamp") or pos.get("entry_timestamp"))
    if entry_ms is not None and "timestamp" in closed.columns:
        last_ts = _to_ms(closed["timestamp"].iloc[-1])
        if last_ts is not None and last_ts <= entry_ms:
            meta["skipped"] = "ENTRY_BAR_PROTECTION"
            return False, None, meta

    try:
        closes = pd.to_numeric(closed["close"], errors="coerce")
        if "ma3" in closed.columns:
            ma3_series = pd.to_numeric(closed["ma3"], errors="coerce")
        else:
            ma3_series = closes.rolling(window=3).mean()

        curr = closed.iloc[-1]
        prev = closed.iloc[-2]

        c_open, c_high, c_low, c_close = (
            float(curr["open"]), float(curr["high"]), float(curr["low"]), float(curr["close"])
        )
        p_open, p_high, p_low, p_close = (
            float(prev["open"]), float(prev["high"]), float(prev["low"]), float(prev["close"])
        )

        lower_band = float(curr["kc_lower"])
        upper_band = float(curr["kc_upper"])
        prev_lower_band = float(prev.get("kc_lower", lower_band))
        prev_upper_band = float(prev.get("kc_upper", upper_band))

        ma3_curr = float(ma3_series.iloc[-1]) if len(ma3_series) >= 1 and not pd.isna(ma3_series.iloc[-1]) else None
        ma3_prev = float(ma3_series.iloc[-2]) if len(ma3_series) >= 2 and not pd.isna(ma3_series.iloc[-2]) else None
        ma3_prev2 = float(ma3_series.iloc[-3]) if len(ma3_series) >= 3 and not pd.isna(ma3_series.iloc[-3]) else None

        atr = float(atr_val or curr.get("atr") or (entry_p * 0.01))
    except (KeyError, IndexError, TypeError, ValueError):
        return False, None, meta

    vals = (c_open, c_high, c_low, c_close, p_open, p_high, p_low, p_close,
            lower_band, upper_band, atr)
    if not all(math.isfinite(v) and v > 0 for v in vals):
        return False, None, meta

    curr_body = abs(c_close - c_open)
    curr_lower_wick = min(c_open, c_close) - c_low
    curr_upper_wick = c_high - max(c_open, c_close)

    prev_body = abs(p_close - p_open)
    prev_lower_wick = min(p_open, p_close) - p_low
    prev_upper_wick = p_high - max(p_open, p_close)

    # 2. 波段展開防抖（必須有實質波段展開或獲利，嚴禁進場瞬間微幅震盪誤平）
    leverage = float(pos.get("leverage") or 5.0)
    stored_peak_roe = float(pos.get("peak_roe") or 0.0)

    meta.update(
        side=side,
        close=c_close,
        ma3_curr=round(ma3_curr, 6) if ma3_curr is not None else None,
        ma3_prev=round(ma3_prev, 6) if ma3_prev is not None else None,
        ma3_prev2=round(ma3_prev2, 6) if ma3_prev2 is not None else None,
    )

    # ==========================
    # 1. 空單檢查：出現「谷底」立即平空
    # ==========================
    if side == "SHORT":
        lowest_since_entry = min(
            float(pos.get("lowest_price") or entry_p),
            float(closed["low"].min()),
            c_low,
        )
        max_favorable = entry_p - lowest_since_entry
        favorable_roe = (max_favorable / entry_p) * leverage if entry_p > 0 else 0.0
        peak_roe = max(stored_peak_roe, favorable_roe)

        recent_low = min(c_low, p_low)
        has_trend_unfolded = (
            (lowest_since_entry <= (lower_band * 1.002) and recent_low <= (lower_band * 1.005))
            or (max_favorable >= (0.25 * atr) and recent_low <= (lower_band * 1.002))
        )
        if not has_trend_unfolded:
            meta["skipped"] = "NO_FAVORABLE_MOVE_YET"
            return False, None, meta

        # 條件 1: MA3 明顯轉折向上 且 收盤實體站上 MA3 (谷底 V 轉)
        ma3_bending_up = (
            ma3_curr is not None and ma3_prev is not None and ma3_prev2 is not None
            and (ma3_curr > ma3_prev) and (ma3_prev <= ma3_prev2)
        )
        close_above_ma3 = (ma3_curr is not None and c_close > ma3_curr)

        # 條件 2: 下影線探底回升 (長下影線 >= 1.5 倍實體 且 探破或逼近 KC 下軌)
        # 防線一：MA3 向下且收盤受壓於 MA3 之下時，嚴禁因普通下影線平空 (VETO_EXIT_MA3_STRONG_DOWN)
        is_ma3_down_thrust = (ma3_curr is not None and ma3_prev is not None and (ma3_curr < ma3_prev) and (c_close <= ma3_curr))
        # 唯有超巨型探底針（下影線 >= 3.0 倍實體 且 下影線 >= 0.8*ATR 且 收盤被抽回上半部），才視為極端插針反轉
        is_extreme_needle_bottom = (
            (curr_lower_wick >= curr_body * 3.0)
            and (curr_lower_wick >= 0.8 * atr)
            and (c_close >= c_low + curr_lower_wick * 0.5)
        )
        pin_bar_bottom = (
            (curr_lower_wick >= curr_body * 1.5)
            and (c_low < lower_band)
            and (c_close >= c_low + curr_lower_wick * 0.4)
            and (is_extreme_needle_bottom or (not is_ma3_down_thrust and c_close > c_open and c_close > p_high))
        )

        # 條件 3: 低檔陽包陰吞噬反轉 (Bullish Engulfing)
        bullish_engulfing = (
            (p_close < p_open)
            and (c_close > c_open)
            and (c_close >= p_open)
            and (c_open <= p_close)
            and (recent_low <= lower_band * 1.002)
        )

        if (ma3_bending_up and close_above_ma3) or pin_bar_bottom or bullish_engulfing:
            triggers = []
            if ma3_bending_up and close_above_ma3:
                triggers.append(f"MA3_V_BOTTOM(ma3={ma3_curr:.6f}>{ma3_prev:.6f}<=prev2,close={c_close:.6f}>ma3)")
            if pin_bar_bottom:
                triggers.append(f"PIN_BAR_BOTTOM(wick={curr_lower_wick:.6f}>=1.5*body,close={c_close:.6f},low={c_low:.6f}<lower={lower_band:.6f})")
            if bullish_engulfing:
                triggers.append(f"BULLISH_ENGULFING(c_close={c_close:.6f}>=p_open={p_open:.6f})")

            meta["cause"] = f"空單谷底轉折確立，不等回摸中軌立即平空鎖利！觸發特徵: {', '.join(triggers)}"
            meta["triggers"] = triggers
            return True, CLOSE_SHORT_AT_BOTTOM, meta

    # ==========================
    # 2. 多單檢查：出現「峰頂」立即平多
    # ==========================
    elif side == "LONG":
        highest_since_entry = max(
            float(pos.get("highest_price") or entry_p),
            float(closed["high"].max()),
            c_high,
        )
        max_favorable = highest_since_entry - entry_p
        favorable_roe = (max_favorable / entry_p) * leverage if entry_p > 0 else 0.0
        peak_roe = max(stored_peak_roe, favorable_roe)

        recent_high = max(c_high, p_high)
        has_trend_unfolded = (
            (highest_since_entry >= (upper_band * 0.998) and recent_high >= (upper_band * 0.995))
            or (max_favorable >= (0.25 * atr) and recent_high >= (upper_band * 0.998))
        )
        if not has_trend_unfolded:
            meta["skipped"] = "NO_FAVORABLE_MOVE_YET"
            return False, None, meta

        # 條件 1: MA3 明顯轉折下彎 且 收盤實體跌破 MA3 (盤中碰觸不計，解決 21:06 提早平倉問題)
        ma3_bending_down = (
            ma3_curr is not None and ma3_prev is not None and ma3_prev2 is not None
            and (ma3_curr < ma3_prev) and (ma3_prev >= ma3_prev2)
        )
        close_below_ma3 = (ma3_curr is not None and c_close < ma3_curr)

        # 條件 2: 上影線避雷針轉折衝高受阻 (長上影線 >= 1.5 倍實體 且 衝破或逼近 KC 上軌)
        # 防線一：MA3 向上且收盤穩站 MA3 之上時，嚴禁因普通上影線平多 (VETO_EXIT_MA3_STRONG_UP)
        is_ma3_up_thrust = (ma3_curr is not None and ma3_prev is not None and (ma3_curr > ma3_prev) and (c_close >= ma3_curr))
        # 唯有超巨型避雷針（上影線 >= 3.0 倍實體 且 上影線 >= 0.8*ATR 且 收盤被砸回下半部），才視為極端插針反轉
        is_extreme_needle_top = (
            (curr_upper_wick >= curr_body * 3.0)
            and (curr_upper_wick >= 0.8 * atr)
            and (c_close <= c_high - curr_upper_wick * 0.5)
        )
        pin_bar_top = (
            (curr_upper_wick >= curr_body * 1.5)
            and (c_high > upper_band)
            and (c_close <= c_high - curr_upper_wick * 0.4)
            and (is_extreme_needle_top or (not is_ma3_up_thrust and c_close < c_open and c_close < p_low))
        )

        # 條件 3: 高檔陰包陽吞噬反轉 (Bearish Engulfing)
        bearish_engulfing = (
            (p_close > p_open)
            and (c_close < c_open)
            and (c_close <= p_open or (c_open - c_close) >= (p_close - p_open) * 0.8)
            and (recent_high >= upper_band * 0.998)
        )

        # 峰頂平倉核心：只有在「MA3 明顯轉折下彎」且「收盤跌破 MA3」，或「出現明確衝高回落避雷針/陰包陽吞噬」才平倉
        if (ma3_bending_down and close_below_ma3) or pin_bar_top or bearish_engulfing:
            triggers = []
            if ma3_bending_down and close_below_ma3:
                triggers.append(f"MA3_A_TOP(ma3={ma3_curr:.6f}<{ma3_prev:.6f}>=prev2,close={c_close:.6f}<ma3)")
            if pin_bar_top:
                triggers.append(f"PIN_BAR_TOP(wick={curr_upper_wick:.6f}>=1.5*body,close={c_close:.6f},high={c_high:.6f}>upper={upper_band:.6f})")
            if bearish_engulfing:
                triggers.append(f"BEARISH_ENGULFING(c_close={c_close:.6f}<=p_open={p_open:.6f})")

            meta["cause"] = f"多單峰頂轉折確立，不等回踩中軌立即平多鎖利！觸發特徵: {', '.join(triggers)}"
            meta["triggers"] = triggers
            return True, CLOSE_LONG_AT_TOP, meta

    return False, None, meta
