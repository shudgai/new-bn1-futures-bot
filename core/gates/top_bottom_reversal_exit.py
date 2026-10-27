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
    if closed is None or len(closed) < 1:
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
    if len(closed) < 1:
        meta["skipped"] = "INSUFFICIENT_POST_ENTRY_CLOSED_BARS"
        return False, None, meta

    try:
        closes = pd.to_numeric(closed["close"], errors="coerce")
        if "ma3" in closed.columns:
            ma3_series = pd.to_numeric(closed["ma3"], errors="coerce")
        else:
            ma3_series = closes.rolling(window=3).mean()

        curr = closed.iloc[-1]
        prev = closed.iloc[-2] if len(closed) >= 2 else curr

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
    # 獲取 MA15 與 MA3 歷史數值
    ma15_val = float(curr.get("ma15", curr.get("kc_middle", (upper_band + lower_band) / 2.0)))
    ma15_prev = float(prev.get("ma15", ma15_val))
    stop_loss_price = float(pos.get("stop_loss_price") or 0.0)

    # ==========================
    # 0. 緊急大瀑布 / 熔斷斷路器（極端行情防護，最高優先級）
    # 單根超大實體反向 K 棒（body >= 1.8 * atr）且直接貫穿 MA15 慢線
    # ==========================
    if side == "LONG":
        is_waterfall_dump = (
            (c_open - c_close >= 1.8 * atr)
            and (c_open >= ma15_val and c_close < ma15_val)
        )
        if is_waterfall_dump:
            meta["cause"] = f"極端大瀑布崩盤熔斷: 單根大陰棒(body={c_open - c_close:.6f} >= 1.8*ATR)貫穿MA15，緊急離場！"
            meta["triggers"] = ["EXTREME_WATERFALL_BREAKER"]
            return True, "EXIT_BY_EXTREME_WATERFALL", meta
    elif side == "SHORT":
        is_waterfall_pump = (
            (c_close - c_open >= 1.8 * atr)
            and (c_open <= ma15_val and c_close > ma15_val)
        )
        if is_waterfall_pump:
            meta["cause"] = f"極端垂直軋空暴漲熔斷: 單根大陽棒(body={c_close - c_open:.6f} >= 1.8*ATR)貫穿MA15，緊急離場！"
            meta["triggers"] = ["EXTREME_WATERFALL_BREAKER"]
            return True, "EXIT_BY_EXTREME_WATERFALL", meta

    # ==========================
    # 0.5 結構失效平倉（僅在轉折判定失敗時離場）
    # ==========================
    if stop_loss_price > 0:
        if side == "LONG" and c_close < stop_loss_price:
            meta["cause"] = f"多單結構失效離場: 收盤價(close={c_close:.6f})跌破谷底前根最低點({stop_loss_price:.6f})"
            meta["triggers"] = ["STRUCTURAL_FAILURE_EXIT"]
            return True, "STRUCTURAL_FAILURE_EXIT", meta
        elif side == "SHORT" and c_close > stop_loss_price:
            meta["cause"] = f"空單結構失效離場: 收盤價(close={c_close:.6f})突破頂部前根最高點({stop_loss_price:.6f})"
            meta["triggers"] = ["STRUCTURAL_FAILURE_EXIT"]
            return True, "STRUCTURAL_FAILURE_EXIT", meta

    if len(closed) < 2:
        meta["skipped"] = "INSUFFICIENT_POST_ENTRY_CLOSED_BARS"
        return False, None, meta

    # ==========================
    # 1. 空單檢查：貫徹趨勢鎖定，未達條件 1/2/3/4 絕對禁止平空
    # ==========================
    if side == "SHORT":
        lowest_since_entry = min(
            float(pos.get("lowest_price") or entry_p),
            float(closed["low"].min()),
            c_low,
        )
        min_kc_lower = float(closed["kc_lower"].min()) if "kc_lower" in closed.columns else lower_band

        # 價格曾觸及或跌破 KC 下軌
        touched_lower = (lowest_since_entry <= min_kc_lower) or (p_low <= prev_lower_band) or (c_low <= lower_band)

        # 趨勢鎖定 (Trend Lock) 空單四項谷底離場硬門檻：
        # 條件 1: 谷底 MA3 實體向上金叉 MA15 慢線
        is_ma3_golden_cross = bool(
            ma3_curr is not None and ma3_prev is not None
            and ma3_prev <= ma15_prev and ma3_curr > ma15_val
        )
        # 條件 2: 軌外大陽吞噬 (前根紅 K 跌破下軌，當前大陽線實體吃掉前根 60% 以上，且具備反轉突破動能)
        is_engulfing_bottom = bool(
            touched_lower
            and p_close < prev_lower_band
            and p_close < p_open
            and c_close > c_open
            and prev_body > 0
            and curr_body >= 0.6 * prev_body
            and (curr_body >= 0.4 * atr or c_close > p_open or c_close > p_high)
        )
        # 條件 3: 低位長下影天地針 (下影線 >= 1.2倍實體，下影線顯著 >= 0.5*ATR，當前收陽，觸及或跌破下軌)
        is_pinbar_bottom = bool(
            touched_lower
            and curr_lower_wick >= 1.2 * curr_body
            and curr_lower_wick >= 0.5 * atr
            and c_close > c_open
            and (c_low <= lower_band or p_low <= prev_lower_band)
        )
        # 條件 4: 突破波段起跌高點結構失效 (真正的結構破壞，至少回溯 20 根或進場起跌點，且收盤站上 MA15)
        prior_closed = closed.iloc[:-1]
        entry_price_val = float(pos.get("entry_price") or 0.0)
        pos_high_val = float(pos.get("highest_price") or 0.0)
        if len(prior_closed) > 0:
            lookback_bars = min(len(prior_closed), 45)
            swing_high_calc = float(prior_closed["high"].iloc[-lookback_bars:].max())
            true_swing_high = max(swing_high_calc, entry_price_val, pos_high_val)
        else:
            true_swing_high = max(entry_price_val, pos_high_val)

        is_structure_broken_short = bool(
            c_close > true_swing_high
            and c_close > ma15_val
            and c_close > c_open
        )

        # ⚠️ 關鍵硬性卡死：未滿足金叉、軌外吞噬、長下影天地針或波段前高結構破壞，平空邏輯必須直接 return False！
        if not (is_ma3_golden_cross or is_engulfing_bottom or is_pinbar_bottom or is_structure_broken_short):
            meta["skipped"] = "TREND_LOCK_ACTIVE: 空單未達谷底極限條件(壓制於MA15且未金叉)，強制持倉拿滿整波空頭趨勢！"
            return False, None, meta

        if is_ma3_golden_cross:
            meta["cause"] = f"空單觸發谷底離場 1: MA3 向上金叉 MA15 (ma3={ma3_curr:.6f} > ma15={ma15_val:.6f})，獲利離場！"
            meta["triggers"] = ["MA3_GOLDEN_CROSS"]
            return True, CLOSE_SHORT_AT_BOTTOM, meta

        if is_engulfing_bottom:
            meta["cause"] = f"空單觸發谷底離場 2: 軌外大陽吞噬 (body={curr_body:.6f} >= 0.6*prev={prev_body:.6f})，獲利離場！"
            meta["triggers"] = ["OUTER_RAIL_ENGULFING"]
            return True, CLOSE_SHORT_AT_BOTTOM, meta

        if is_pinbar_bottom:
            meta["cause"] = f"空單觸發谷底離場 3: 低位長下影天地針 (lower_wick={curr_lower_wick:.6f} >= 1.2*body={curr_body:.6f})，獲利離場！"
            meta["triggers"] = ["PINBAR_BOTTOM"]
            return True, CLOSE_SHORT_AT_BOTTOM, meta

        if is_structure_broken_short:
            meta["cause"] = f"空單觸發谷底離場 4: 突破波段起跌前高結構失效 (close={c_close:.6f} > swing_high={true_swing_high:.6f})，獲利離場！"
            meta["triggers"] = ["STRUCTURE_BROKEN_BOTTOM"]
            return True, CLOSE_SHORT_AT_BOTTOM, meta

        meta["skipped"] = "TREND_LOCK_ACTIVE: 空單未達谷底，強制持倉繼續向下走"
        return False, None, meta

    # ==========================
    # 2. 多單檢查：貫徹趨勢鎖定，未達條件 1/2/3/4 絕對禁止平多
    # ==========================
    elif side == "LONG":
        highest_since_entry = max(
            float(pos.get("highest_price") or entry_p),
            float(closed["high"].max()),
            c_high,
        )
        max_kc_upper = float(closed["kc_upper"].max()) if "kc_upper" in closed.columns else upper_band

        # 價格曾觸及或衝出 KC 上軌
        touched_upper = (highest_since_entry >= max_kc_upper) or (p_high >= prev_upper_band) or (c_high >= upper_band)

        # 趨勢鎖定 (Trend Lock) 多單四項峰頂離場硬門檻：
        # 條件 1: 峰頂 MA3 實體向下死叉 MA15 慢線
        is_ma3_dead_cross = bool(
            ma3_curr is not None and ma3_prev is not None
            and ma3_prev >= ma15_prev and ma3_curr < ma15_val
        )
        # 條件 2: 軌外大陰吞噬 (前根綠 K 衝出上軌，當前大陰線實體吃掉前根 60% 以上，且具備反轉跌破動能)
        is_engulfing_peak = bool(
            touched_upper
            and p_close > prev_upper_band
            and p_close > p_open
            and c_close < c_open
            and prev_body > 0
            and curr_body >= 0.6 * prev_body
            and (curr_body >= 0.4 * atr or c_close < p_open or c_close < p_low)
        )
        # 條件 3: 高位長上影天地針 (上影線 >= 1.2倍實體，上影線顯著 >= 0.5*ATR，當前收陰，觸及或突破上軌)
        is_pinbar_peak = bool(
            touched_upper
            and curr_upper_wick >= 1.2 * curr_body
            and curr_upper_wick >= 0.5 * atr
            and c_close < c_open
            and (c_high >= upper_band or p_high >= prev_upper_band)
        )
        # 條件 4: 跌破波段起漲低點結構失效 (真正的結構破壞，至少回溯 20 根或進場起漲點，且收盤跌破 MA15)
        prior_closed = closed.iloc[:-1]
        entry_price_val = float(pos.get("entry_price") or 0.0)
        pos_low_val = float(pos.get("lowest_price") or 0.0)
        valid_pos_lows = [p for p in (entry_price_val, pos_low_val) if p > 0]
        if len(prior_closed) > 0:
            lookback_bars = min(len(prior_closed), 45)
            swing_low_calc = float(prior_closed["low"].iloc[-lookback_bars:].min())
            true_swing_low = min([swing_low_calc] + valid_pos_lows)
        else:
            true_swing_low = min(valid_pos_lows) if valid_pos_lows else entry_p

        is_structure_broken = bool(
            c_close < true_swing_low
            and c_close < ma15_val
            and c_close < c_open
        )

        # ⚠️ 關鍵硬性卡死：未滿足死叉、軌外大陰吞噬、高位長上影或波段前低結構破壞，平多邏輯必須直接 return False！
        if not (is_ma3_dead_cross or is_engulfing_peak or is_pinbar_peak or is_structure_broken):
            meta["skipped"] = "TREND_LOCK_ACTIVE: 多單未達峰頂極限條件(守穩MA15且未死叉)，強制持倉拿滿整波多頭趨勢！"
            return False, None, meta

        if is_ma3_dead_cross:
            meta["cause"] = f"多單觸發峰頂離場 1: MA3 向下死叉 MA15 (ma3={ma3_curr:.6f} < ma15={ma15_val:.6f})，獲利離場！"
            meta["triggers"] = ["MA3_DEATH_CROSS"]
            return True, CLOSE_LONG_AT_TOP, meta

        if is_engulfing_peak:
            meta["cause"] = f"多單觸發峰頂離場 2: 軌外大陰吞噬 (body={curr_body:.6f} >= 0.6*prev={prev_body:.6f})，獲利離場！"
            meta["triggers"] = ["OUTER_RAIL_ENGULFING"]
            return True, CLOSE_LONG_AT_TOP, meta

        if is_pinbar_peak:
            meta["cause"] = f"多單觸發峰頂離場 3: 高位長上影天地針 (upper_wick={curr_upper_wick:.6f} >= 1.2*body={curr_body:.6f})，獲利離場！"
            meta["triggers"] = ["PINBAR_PEAK"]
            return True, CLOSE_LONG_AT_TOP, meta

        if is_structure_broken:
            meta["cause"] = f"多單觸發峰頂離場 4: 跌破前棒低點結構失效 (close={c_close:.6f} < prev_low={p_low:.6f})，獲利離場！"
            meta["triggers"] = ["STRUCTURE_BROKEN_PEAK"]
            return True, CLOSE_LONG_AT_TOP, meta

        meta["skipped"] = "TREND_LOCK_ACTIVE: 多單未達峰頂，強制持倉繼續向上走"
        return False, None, meta

    return False, None, meta

