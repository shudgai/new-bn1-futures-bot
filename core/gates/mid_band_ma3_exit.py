"""
core/gates/mid_band_ma3_exit.py
===============================
KC 中軌 + MA3 方向之多空對稱平倉規則（2026-10-23 最新授權）。

核心原則：破中軌後，若短均線 MA3 方向對部位有利，允許暫緩平倉爭取更好出場點；
反彈／回測受阻或收盤擊穿反側外軌時，必須強制平倉。

判定一律只使用「已收線」K 棒（盤中毛刺不觸發）：
- raw_df 含 `is_closed` 欄位時，取 is_closed=True 的列；
- 否則沿用 ExitGate 慣例，最後一列視為盤中棒並排除。

多單（最新已收線棒 close < kc_middle 時才介入）：
1. MA3 下彎 (ma3 < ma3_prev)               -> LONG_MID_BAND_EXIT_TREND_BROKEN
2. MA3 上彎或走平 (ma3 >= ma3_prev)：
   B. close <= kc_lower                     -> CLOSE_LONG_HARD_STOP_LOSS（優先於 A）
   A. 前一根已收線亦收在中軌下方，且當根 high >= kc_middle 但 close < kc_middle
                                            -> LONG_MID_BAND_EXIT_BOUNCE_RESISTANCE
   其餘                                      -> HOLD_LONG_MID_BAND_WAIT_BOUNCE
空單完全鏡像。

出場 A 之所以要求「前一根已收線也在中軌另一側」：跌破中軌的那根 K 棒本身是從中軌上方
跌下來，high >= mid 幾乎必然成立；若不排除，暫緩分支永遠不會發生。
"""
from __future__ import annotations

import math
from typing import Any, Dict, Optional, Tuple

import pandas as pd

ACTION_EXIT = "EXIT"
ACTION_HOLD_WAIT = "HOLD_WAIT"

LONG_TREND_BROKEN = "LONG_MID_BAND_EXIT_TREND_BROKEN"
LONG_BOUNCE_RESISTANCE = "LONG_MID_BAND_EXIT_BOUNCE_RESISTANCE"
LONG_HARD_STOP = "CLOSE_LONG_HARD_STOP_LOSS"
LONG_HOLD_WAIT = "HOLD_LONG_MID_BAND_WAIT_BOUNCE"

SHORT_TREND_BROKEN = "SHORT_MID_BAND_EXIT_TREND_BROKEN"
SHORT_PULLBACK_SUPPORT = "SHORT_MID_BAND_EXIT_PULLBACK_SUPPORT"
SHORT_HARD_STOP = "CLOSE_SHORT_HARD_STOP_LOSS"
SHORT_HOLD_WAIT = "HOLD_SHORT_MID_BAND_WAIT_PULLBACK"

_REQUIRED = ("open", "high", "low", "close", "kc_middle", "kc_upper", "kc_lower")


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


def evaluate_mid_band_ma3_exit(
    side: str,
    raw_df: Any,
    entry_timestamp: Any = None,
) -> Tuple[Optional[str], Optional[str], Dict[str, Any]]:
    """
    回傳 (action, reason, meta)：
    - action = "EXIT"      -> 立即平倉，reason 為出場代碼
    - action = "HOLD_WAIT" -> 暫緩平倉（MA3 方向有利、尚未遇阻）
    - action = None        -> 本規則不介入（未破中軌、資料不足或無效）
    """
    side = str(side or "").upper()
    meta: Dict[str, Any] = {"rule": "MID_BAND_MA3_EXIT"}
    if side not in ("LONG", "SHORT"):
        return None, None, meta

    closed = _closed_bars(raw_df)
    if closed is None or len(closed) < 4:
        return None, None, meta
    if any(col not in closed.columns for col in _REQUIRED):
        return None, None, meta

    # 僅評估進場後才收線的 K 棒，避免進場棒本身（或進場前）觸發
    entry_ms = _to_ms(entry_timestamp)
    if entry_ms is not None and "timestamp" in closed.columns:
        last_ts = _to_ms(closed["timestamp"].iloc[-1])
        if last_ts is not None and last_ts <= entry_ms:
            meta["skipped"] = "NO_CLOSED_BAR_AFTER_ENTRY"
            return None, None, meta

    try:
        closes = pd.to_numeric(closed["close"], errors="coerce")
        if "ma3" in closed.columns:
            ma3_series = pd.to_numeric(closed["ma3"], errors="coerce")
        else:
            ma3_series = closes.rolling(window=3).mean()
        cur = closed.iloc[-1]
        prev = closed.iloc[-2]
        close = float(cur["close"])
        high = float(cur["high"])
        low = float(cur["low"])
        mid = float(cur["kc_middle"])
        upper = float(cur["kc_upper"])
        lower = float(cur["kc_lower"])
        prev_close = float(prev["close"])
        prev_mid = float(prev["kc_middle"])
        ma3 = float(ma3_series.iloc[-1])
        ma3_prev = float(ma3_series.iloc[-2])
    except (KeyError, IndexError, TypeError, ValueError):
        return None, None, meta

    values = (close, high, low, mid, upper, lower, prev_close, prev_mid, ma3, ma3_prev)
    if not all(math.isfinite(v) and v > 0 for v in values):
        return None, None, meta

    meta.update(
        closed_close=close, closed_high=high, closed_low=low,
        kc_middle=mid, kc_upper=upper, kc_lower=lower,
        prev_close=prev_close, prev_kc_middle=prev_mid,
        ma3=round(ma3, 10), ma3_prev=round(ma3_prev, 10),
    )

    if side == "LONG":
        if close >= mid:
            return None, None, meta
        if ma3 < ma3_prev:
            meta["cause"] = f"多單收線跌破中軌且 MA3 下彎 (close={close:.6f} < mid={mid:.6f}, ma3={ma3:.6f} < {ma3_prev:.6f})，立即平多"
            return ACTION_EXIT, LONG_TREND_BROKEN, meta
        if close <= lower:
            meta["cause"] = f"多單暫緩期間收線擊穿 KC 下軌 (close={close:.6f} <= lower={lower:.6f})，強制平多"
            return ACTION_EXIT, LONG_HARD_STOP, meta
        if prev_close < prev_mid and high >= mid:
            meta["cause"] = f"多單反彈觸碰中軌未能站回 (high={high:.6f} >= mid={mid:.6f}, close={close:.6f} < mid)，中軌轉壓力，平多"
            return ACTION_EXIT, LONG_BOUNCE_RESISTANCE, meta
        meta["cause"] = f"多單跌破中軌但 MA3 上彎 (ma3={ma3:.6f} >= {ma3_prev:.6f})，暫緩平倉等待反彈"
        return ACTION_HOLD_WAIT, LONG_HOLD_WAIT, meta

    # SHORT
    if close <= mid:
        return None, None, meta
    if ma3 > ma3_prev:
        meta["cause"] = f"空單收線突破中軌且 MA3 上翹 (close={close:.6f} > mid={mid:.6f}, ma3={ma3:.6f} > {ma3_prev:.6f})，立即平空"
        return ACTION_EXIT, SHORT_TREND_BROKEN, meta
    if close >= upper:
        meta["cause"] = f"空單暫緩期間收線擊穿 KC 上軌 (close={close:.6f} >= upper={upper:.6f})，強制平空"
        return ACTION_EXIT, SHORT_HARD_STOP, meta
    if prev_close > prev_mid and low <= mid:
        meta["cause"] = f"空單回測中軌跌不回去 (low={low:.6f} <= mid={mid:.6f}, close={close:.6f} > mid)，中軌轉支撐，平空"
        return ACTION_EXIT, SHORT_PULLBACK_SUPPORT, meta
    meta["cause"] = f"空單突破中軌但 MA3 下彎 (ma3={ma3:.6f} <= {ma3_prev:.6f})，暫緩平倉等待回測"
    return ACTION_HOLD_WAIT, SHORT_HOLD_WAIT, meta
