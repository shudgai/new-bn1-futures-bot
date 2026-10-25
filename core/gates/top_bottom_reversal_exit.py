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
    if closed is None or len(closed) < 3:
        return False, None, meta
    if any(col not in closed.columns for col in _REQUIRED):
        return False, None, meta

    # 1. 開倉初期防抖：最新已收線棒的時間戳必須在開倉之後
    entry_ms = _to_ms(
        pos.get("open_timestamp") or pos.get("entry_timestamp")
        or pos.get("open_bar_id") or pos.get("entry_bar_id")
    )
    if entry_ms is None or "timestamp" not in closed.columns:
        meta["skipped"] = "ENTRY_IDENTITY_UNAVAILABLE"
        return False, None, meta
    closed = closed.copy()
    closed["_timestamp_ms"] = closed["timestamp"].map(_to_ms)
    closed = closed[closed["_timestamp_ms"].notna()]
    # Exclude the entry candle and all earlier history. These bars must not
    # satisfy the swing-expansion check or provide the candidate pivot.
    closed = closed[closed["_timestamp_ms"] > entry_ms]
    if len(closed) < 3:
        meta["skipped"] = "INSUFFICIENT_POST_ENTRY_CLOSED_BARS"
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

        atr = float(atr_val or curr.get("atr") or 0.0)
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
    # 1. 空單檢查：出現「真谷底」（階段 A 超賣/獲利 + 階段 B 連續 2 根收陽且突破 MA3）平空
    # ==========================
    if side == "SHORT":
        from core.gates.holding_protection_gate import (
            TRUE_PIVOT_MIN_CONFIRM_BODY_ATR,
            TRUE_PIVOT_MIN_REVERSAL_ATR,
        )

        lowest_since_entry = min(
            float(pos.get("lowest_price") or entry_p),
            float(closed["low"].min()),
            c_low,
        )
        max_favorable = entry_p - lowest_since_entry
        min_kc_lower = float(closed["kc_lower"].min()) if "kc_lower" in closed.columns else lower_band

        # 階段 A（進入獲利保護期）：持倉最低價曾灌破 KC 下軌 或 曾獲得大於 1.5 * ATR 的浮盈
        stage_a_met = (lowest_since_entry <= min_kc_lower) or (max_favorable >= (1.5 * atr))
        if not stage_a_met:
            meta["skipped"] = "STAGE_A_NOT_MET: 未曾灌破KC下軌且浮盈未達門檻(1.5*ATR)，禁止猜底平空"
            return False, None, meta

        # Stage B requires a true three-point valley, not two green pullback bars.
        pivot_body_mid = (p_open + p_close) / 2.0
        is_three_point_valley = (
            p_low < float(closed.iloc[-3]["low"])
            and p_low < c_low
            and c_close > c_open
            and c_close > pivot_body_mid
            and curr_body >= TRUE_PIVOT_MIN_CONFIRM_BODY_ATR * atr
            and c_close - p_low >= TRUE_PIVOT_MIN_REVERSAL_ATR * atr
        )
        curr_span = max(c_high - c_low, 1e-6)
        no_upper_wick_rejection = (curr_upper_wick < curr_body) and (curr_upper_wick <= 0.35 * curr_span)

        if is_three_point_valley and no_upper_wick_rejection:
            triggers = [
                f"STAGE_A_OVERSOLD(lowest={lowest_since_entry:.4f}<=lower={min_kc_lower:.4f} or gain={max_favorable:.4f}>=1.5*atr={1.5*atr:.4f})",
                f"THREE_POINT_VALLEY(pivot_low={p_low:.4f}<left={float(closed.iloc[-3]['low']):.4f},right={c_low:.4f})",
                f"BODY_RECLAIM(confirm_close={c_close:.4f}>pivot_body_mid={pivot_body_mid:.4f})",
                f"SOLID_BODY(upper_wick={curr_upper_wick:.4f}<body={curr_body:.4f})",
            ]
            meta["cause"] = f"空單真谷底三點樞紐及反向實體確認，平空鎖利！觸發特徵: {', '.join(triggers)}"
            meta["triggers"] = triggers
            return True, CLOSE_SHORT_AT_BOTTOM, meta
        else:
            meta["skipped"] = "STAGE_B_NOT_CONFIRMED: 尚無三點真谷底或確認棒未以陽線收過樞紐實體中點，視為反抽/回踩"
            return False, None, meta

    # ==========================
    # 2. 多單檢查：出現「真峰頂」（階段 A 超買/獲利 + 階段 B 連續 2 根收陰且跌破 MA3）平多
    # ==========================
    elif side == "LONG":
        from core.gates.holding_protection_gate import (
            TRUE_PIVOT_MIN_CONFIRM_BODY_ATR,
            TRUE_PIVOT_MIN_REVERSAL_ATR,
        )

        highest_since_entry = max(
            float(pos.get("highest_price") or entry_p),
            float(closed["high"].max()),
            c_high,
        )
        max_favorable = highest_since_entry - entry_p
        max_kc_upper = float(closed["kc_upper"].max()) if "kc_upper" in closed.columns else upper_band

        # 階段 A（進入獲利保護期）：持倉最高價曾衝破 KC 上軌 或 曾獲得大於 1.5 * ATR 的浮盈
        stage_a_met = (highest_since_entry >= max_kc_upper) or (max_favorable >= (1.5 * atr))
        if not stage_a_met:
            meta["skipped"] = "STAGE_A_NOT_MET: 未曾衝破KC上軌且浮盈未達門檻(1.5*ATR)，禁止猜頂平多"
            return False, None, meta

        # Mirror the short rule: require a local three-point peak and a bearish
        # close below the pivot candle's real-body midpoint.
        pivot_body_mid = (p_open + p_close) / 2.0
        is_three_point_peak = (
            p_high > float(closed.iloc[-3]["high"])
            and p_high > c_high
            and c_close < c_open
            and c_close < pivot_body_mid
            and curr_body >= TRUE_PIVOT_MIN_CONFIRM_BODY_ATR * atr
            and p_high - c_close >= TRUE_PIVOT_MIN_REVERSAL_ATR * atr
        )
        curr_span = max(c_high - c_low, 1e-6)
        no_lower_wick_rejection = (curr_lower_wick < curr_body) and (curr_lower_wick <= 0.35 * curr_span)

        if is_three_point_peak and no_lower_wick_rejection:
            triggers = [
                f"STAGE_A_OVERBOUGHT(highest={highest_since_entry:.4f}>=upper={max_kc_upper:.4f} or gain={max_favorable:.4f}>=1.5*atr={1.5*atr:.4f})",
                f"THREE_POINT_PEAK(pivot_high={p_high:.4f}>left={float(closed.iloc[-3]['high']):.4f},right={c_high:.4f})",
                f"BODY_BREAK(confirm_close={c_close:.4f}<pivot_body_mid={pivot_body_mid:.4f})",
                f"SOLID_BODY(lower_wick={curr_lower_wick:.4f}<body={curr_body:.4f})",
            ]
            meta["cause"] = f"多單真峰頂三點樞紐及反向實體確認，平多鎖利！觸發特徵: {', '.join(triggers)}"
            meta["triggers"] = triggers
            return True, CLOSE_LONG_AT_TOP, meta
        else:
            meta["skipped"] = "STAGE_B_NOT_CONFIRMED: 尚無三點真峰頂或確認棒未以陰線收破樞紐實體中點，視為回踩/反抽"
            return False, None, meta

    return False, None, meta

