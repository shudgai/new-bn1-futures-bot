"""Closed-candle swing-pivot entry and exhaustion-exit evidence."""

from __future__ import annotations

import math
from typing import Any

import pandas as pd

from core.services.candle_data import closed_entry_candles


def _num(row: Any, key: str) -> float:
    value = float(row.get(key))
    if not math.isfinite(value):
        raise ValueError(key)
    return value


def evaluate_swing_pivot_entry(frame: Any) -> dict[str, Any] | None:
    """Authorize entry only after a closed three-bar peak/valley confirmation."""
    if not isinstance(frame, pd.DataFrame):
        return None
    try:
        if float(frame.attrs.get("timeframe_ms", 0) or 0) != 60_000.0:
            return None
    except (TypeError, ValueError, OverflowError):
        return None
    closed = closed_entry_candles(frame)
    if len(closed) < 3:
        return None
    left, pivot, current = closed.iloc[-3], closed.iloc[-2], closed.iloc[-1]
    try:
        close, opening = _num(current, "close"), _num(current, "open")
        high_left, low_left = _num(left, "high"), _num(left, "low")
        high_pivot, low_pivot = _num(pivot, "high"), _num(pivot, "low")
        high_confirm, low_confirm = _num(current, "high"), _num(current, "low")
        pivot_open, pivot_close = _num(pivot, "open"), _num(pivot, "close")
        atr, pivot_atr = _num(current, "atr"), _num(pivot, "atr")
        if min(close, opening, high_left, low_left, high_pivot, low_pivot,
               high_confirm, low_confirm, pivot_open, pivot_close, atr, pivot_atr) <= 0:
            return None
    except (KeyError, TypeError, ValueError, OverflowError):
        return None

    pivot_mid = (pivot_open + pivot_close) / 2.0
    body = abs(close - opening)
    kc_middle = float(current.get("kc_middle", (float(current.get("kc_upper", close)) + float(current.get("kc_lower", close))) / 2.0))
    kc_upper = float(current.get("kc_upper", close + 2.0 * atr))
    kc_lower = float(current.get("kc_lower", close - 2.0 * atr))
    bandwidth = (kc_upper - kc_lower) / max(kc_middle, 1e-9)

    # 拒絕通道極度壓縮死水區間 (Bandwidth < 0.0035) 雜訊假峰谷
    if bandwidth < 0.0035:
        return None

    ma3_curr = float(current.get("ma3", close))
    ma3_prev = float(pivot.get("ma3", ma3_curr))
    ma15_curr = float(current.get("ma15", kc_middle))
    ma15_prev = float(pivot.get("ma15", ma15_curr))

    dist_curr = abs(ma3_curr - ma15_curr)
    dist_prev = abs(ma3_prev - ma15_prev)

    # 任務 2：多頭谷底轉折提前開多（SWING_PIVOT_LONG）
    # 條件：價格在 KC 通道下半區（close <= kc_middle） + MA3 向上拐頭（ma3_curr > ma3_prev）且與 MA15 距離比前根縮小（即將金叉） + 當前為實體陽線（close > open）且收盤價站上 MA3（close >= ma3_curr）。
    # 豁免：【完全豁免】kc_middle_slope >= 0 限制（允許中軌走平或向下）。
    is_swing_pivot_long = bool(
        close <= kc_middle
        and ma3_curr > ma3_prev
        and (dist_curr < dist_prev or ma3_curr >= ma15_curr)
        and close > opening
        and close >= ma3_curr
    )

    # 傳統三點谷底樞紐判定 (保留相容性)
    is_three_point_valley = (
        low_pivot < low_left and low_pivot < low_confirm
        and close > opening and close > pivot_mid
        and body >= 0.35 * atr
        and close - low_pivot >= 0.45 * pivot_atr
        and low_pivot <= kc_middle + 0.25 * atr
        and kc_upper - close >= 0.35 * atr
    )

    if is_swing_pivot_long or is_three_point_valley:
        return {
            "side": "LONG", "entry_type": "SWING_PIVOT",
            "direction_override": True,
            "is_swing_pivot": True,
            "stop_loss_price": low_pivot, "atr": atr,
            "bar_timestamp": float(current.get("timestamp", 0.0)),
            "pivot_price": low_pivot,
            "pivot_bar_timestamp": float(pivot.get("timestamp", 0.0)),
            "confirmation_bar_timestamp": float(current.get("timestamp", 0.0)),
        }

    # 任務 2：空頭頂部轉折提前開空（SWING_PIVOT_SHORT）
    # 條件：價格在 KC 通道上半區（close >= kc_middle） + MA3 向下拐頭（ma3_curr < ma3_prev）且與 MA15 距離比前根縮小（即將死叉） + 當前為實體陰線（close < open）且收盤價跌破 MA3（close <= ma3_curr）。
    # 豁免：【完全豁免】kc_middle_slope <= 0 限制（允許中軌走平或向上）。
    is_swing_pivot_short = bool(
        close >= kc_middle
        and ma3_curr < ma3_prev
        and (dist_curr < dist_prev or ma3_curr <= ma15_curr)
        and close < opening
        and close <= ma3_curr
    )

    # 傳統三點頂部樞紐判定 (保留相容性)
    is_three_point_peak = (
        high_pivot > high_left and high_pivot > high_confirm
        and close < opening and close < pivot_mid
        and body >= 0.35 * atr
        and high_pivot - close >= 0.45 * pivot_atr
        and high_pivot >= kc_middle - 0.25 * atr
        and close - kc_lower >= 0.35 * atr
    )

    if is_swing_pivot_short or is_three_point_peak:
        return {
            "side": "SHORT", "entry_type": "SWING_PIVOT",
            "direction_override": True,
            "is_swing_pivot": True,
            "stop_loss_price": high_pivot, "atr": atr,
            "bar_timestamp": float(current.get("timestamp", 0.0)),
            "pivot_price": high_pivot,
            "pivot_bar_timestamp": float(pivot.get("timestamp", 0.0)),
            "confirmation_bar_timestamp": float(current.get("timestamp", 0.0)),
        }
    return None


def evaluate_outer_rail_reversal_exit(position: Any, frame: Any) -> dict[str, Any] | None:
    """Return closed-bar peak/valley reversal evidence after outer-rail contact."""
    if not isinstance(frame, pd.DataFrame):
        return None
    try:
        if float(frame.attrs.get("timeframe_ms", 0) or 0) != 60_000.0:
            return None
    except (TypeError, ValueError, OverflowError):
        return None
    side = str((position or {}).get("side") or "").upper()
    if side not in ("LONG", "SHORT"):
        return None
    bars = closed_entry_candles(frame)
    if len(bars) < 2:
        return None
    try:
        opened = float((position or {}).get("open_timestamp") or (position or {}).get("entry_timestamp") or 0.0)
        opened_ms = opened * 1000 if 0 < opened < 1e11 else opened
        entry_bar = math.floor(opened_ms / 60_000) * 60_000 if opened_ms > 0 else 0
        current, previous = bars.iloc[-1], bars.iloc[-2]
        stamp = _num(current, "timestamp")
        if stamp <= entry_bar:
            return None
        c_open, c_close = _num(current, "open"), _num(current, "close")
        p_open, p_close = _num(previous, "open"), _num(previous, "close")
        high, low = _num(current, "high"), _num(current, "low")
        upper, lower = _num(current, "kc_upper"), _num(current, "kc_lower")
        prev_upper, prev_lower = _num(previous, "kc_upper"), _num(previous, "kc_lower")
        ma3, atr = _num(current, "ma3"), _num(current, "atr")
        ma15 = float(current.get("ma15", current.get("kc_middle", 0.0)))
        if atr <= 0 or min(upper, lower, prev_upper, prev_lower, ma3) <= 0:
            return None
        body = abs(c_close - c_open)
        prev_body = abs(p_close - p_open)
        upper_wick = high - max(c_open, c_close)
        lower_wick = min(c_open, c_close) - low
    except (KeyError, TypeError, ValueError, OverflowError):
        return None

    # 任務 4：多空持倉退出機制（中間抗洗盤，極限峰谷即刻鎖利）
    # 2. 極限峰谷主動平倉（收線即刻鎖利，不待均線交叉）：
    # 多單峰頂平倉（EXIT LONG AT PEAK）：價格曾觸及或衝出 KC 上軌，出現：
    #   A. 軌外大陰吞噬：prev_k['close'] > prev_kc_upper 且當前陰線實體吃掉前根綠 K 實體 60% 以上 (curr_body >= 0.6 * prev_body)
    #   B. 高位天地針：upper_wick >= 1.2 * body 且當根收陰
    #   C. 實體跌破 MA3 快線：close < ma3_curr 且 body >= 0.5 * atr
    # 空單谷底平倉（EXIT SHORT AT BOTTOM）：價格曾觸及或跌破 KC 下軌，出現：
    #   A. 軌外大陽吞噬：prev_k['close'] < prev_kc_lower 且當前陽線實體吃掉前根紅 K 實體 60% 以上 (curr_body >= 0.6 * prev_body)
    #   B. 低位下刺針：lower_wick >= 1.2 * body 且當根收陽
    #   C. 實體突破 MA3 快線：close > ma3_curr 且 body >= 0.5 * atr
    if side == "LONG":
        eligible = bars.loc[bars["timestamp"].astype(float) > entry_bar]
        touched = bool((eligible["high"].astype(float) >= eligible["kc_upper"].astype(float)).any())
        engulf = (
            p_close > p_open and p_close > prev_upper and c_close < c_open
            and prev_body > 0 and body >= 0.6 * prev_body
        )
        wick = c_close < c_open and upper_wick >= 1.2 * max(body, 1e-12)
        ma3_break = c_close < c_open and c_close < ma3 and body >= 0.5 * atr
        label = "EXIT_LONG_AT_PEAK"
        reason = (
            "OUTER_RAIL_ENGULFING" if engulf
            else "OUTER_RAIL_LONG_WICK" if wick
            else "OUTER_RAIL_MA3_BREAK" if ma3_break
            else None
        )
    else:
        eligible = bars.loc[bars["timestamp"].astype(float) > entry_bar]
        touched = bool((eligible["low"].astype(float) <= eligible["kc_lower"].astype(float)).any())
        engulf = (
            p_close < p_open and p_close < prev_lower and c_close > c_open
            and prev_body > 0 and body >= 0.6 * prev_body
        )
        wick = c_close > c_open and lower_wick >= 1.2 * max(body, 1e-12)
        ma3_break = c_close > c_open and c_close > ma3 and body >= 0.5 * atr
        label = "EXIT_SHORT_AT_BOTTOM"
        reason = (
            "OUTER_RAIL_ENGULFING" if engulf
            else "OUTER_RAIL_LONG_WICK" if wick
            else "OUTER_RAIL_MA3_BREAK" if ma3_break
            else None
        )

    if not touched or reason is None:
        return None
    return {
        "reason": label, "trigger": reason, "side": side,
        "bar_timestamp": stamp, "body": body, "previous_body": prev_body,
        "atr": atr, "upper_wick": upper_wick, "lower_wick": lower_wick,
        "kc_rail_touched": True,
    }
