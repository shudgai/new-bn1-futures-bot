"""
檔案位置: core/services/strategies/dual_entry_strategies.py
職責:
嚴格落實最新交易策略修改規格：
1. KC 首次破軌確認 (First Breakout Confirmation)
   - LONG:
     * Bar 1: open < kc_upper 且 close > kc_upper (實體穿越，非影線穿越)
     * Bar 2: close > open (陽線) 且 low > kc_upper (最低價嚴格高於當根 KC 上軌)
     * Bar 2 收盤確認產生訊號
   - SHORT:
     * Bar 1: open > kc_lower 且 close < kc_lower (實體跌破，非影線跌破)
     * Bar 2: close < open (陰線) 且 high < kc_lower (最高價嚴格低於當根 KC 下軌)
     * Bar 2 收盤確認產生訊號

2. KC 軌道外延續開倉 (Continuation Entry)
   - 首次破軌後進入軌道外延續狀態
   - 只要價格持續在軌外：
     * LONG: 當根收盤 low > kc_upper 且 close > open (陽線)
     * SHORT: 當根收盤 high < kc_lower 且 close < open (陰線)
   - 若價格回到 KC 內 (LONG 時 close <= kc_upper 或 low <= kc_upper；SHORT 時 close >= kc_lower 或 high >= kc_lower)，延續狀態終止，必須重新等待首次破軌。

3. MA3 方向過濾 (MA3 Direction Filter - 硬性規則)
   - MA3 向下 (curr_ma3 < prev_ma3): 100% 嚴禁新增任何多單 (KC 首次、KC 延續、MA 金叉皆禁止)
   - MA3 向上 (curr_ma3 > prev_ma3): 100% 嚴禁新增任何空單 (KC 首次、KC 延續、MA 死叉皆禁止)
   - MA3 持平 (curr_ma3 == prev_ma3): 方向過濾不否決，放行檢查其他條件
   - 嚴格限制: 僅限制「新增開倉」，絕不因 MA3 方向變化而平掉既有持倉！

4. MA3／MA15 均線交叉開倉 (MA Cross Entry)
   - LONG: 前一根 ma3 <= ma15 且當根 ma3 > ma15 (真正黃金交叉，非持續多頭排列)
   - SHORT: 前一根 ma3 >= ma15 且當根 ma3 < ma15 (真正死亡交叉，非持續空頭排列)
   - 均須通過 MA3 方向過濾
"""

import math
from typing import Dict, Any, Optional, Tuple
import pandas as pd
import numpy as np


class DualEntryDetector:
    """
    純數學零前視偏差訊號檢測器
    """

    @staticmethod
    def get_ma3_direction(df_bars: pd.DataFrame, current_idx: int) -> str:
        """
        以已收盤 K 線判定 MA3 方向:
        - "DOWN": 當根數值低於前一根 (curr_ma3 < prev_ma3)
        - "UP": 當根數值高於前一根 (curr_ma3 > prev_ma3)
        - "FLAT": 當根數值等於前一根 (curr_ma3 == prev_ma3)
        """
        if current_idx < 1 or df_bars is None or current_idx >= len(df_bars):
            return "FLAT"

        bar_prev = df_bars.iloc[current_idx - 1]
        bar_curr = df_bars.iloc[current_idx]

        if "ma3" not in bar_prev or "ma3" not in bar_curr:
            return "FLAT"

        p_ma3 = float(bar_prev["ma3"])
        c_ma3 = float(bar_curr["ma3"])

        if not (math.isfinite(p_ma3) and math.isfinite(c_ma3)):
            return "FLAT"

        if c_ma3 < p_ma3:
            return "DOWN"
        elif c_ma3 > p_ma3:
            return "UP"
        else:
            return "FLAT"

    @staticmethod
    def is_ma3_direction_allowed(df_bars: pd.DataFrame, current_idx: int, side: str) -> bool:
        """
        MA3 硬性方向過濾裁決:
        - 做多 (LONG): 當 MA3 正在向下 ("DOWN")，禁止新增任何多單 -> 回傳 False
        - 做空 (SHORT): 當 MA3 正在向上 ("UP")，禁止新增任何空單 -> 回傳 False
        - 持平 ("FLAT") 或順向: 放行 -> 回傳 True
        """
        direction = DualEntryDetector.get_ma3_direction(df_bars, current_idx)
        if side == "LONG" and direction == "DOWN":
            return False
        if side == "SHORT" and direction == "UP":
            return False
        return True

    @staticmethod
    def check_kc_initial_breakout(
        df_bars: pd.DataFrame,
        current_idx: int,
        apply_ma3_filter: bool = True
    ) -> Optional[Dict[str, Any]]:
        """
        KC 首次兩根破軌確認 (First Breakout Confirmation)
        - bar1 (current_idx - 1): 實體穿越 KC 外軌
        - bar2 (current_idx): 同向收盤且整根 K 棒完全懸浮於軌外
        """
        if current_idx < 1 or df_bars is None or current_idx >= len(df_bars):
            return None

        bar1 = df_bars.iloc[current_idx - 1]
        bar2 = df_bars.iloc[current_idx]

        req_keys = ["open", "high", "low", "close", "kc_upper", "kc_lower", "kc_middle", "atr"]
        for k in req_keys:
            if k not in bar1 or k not in bar2:
                return None
            v1, v2 = float(bar1[k]), float(bar2[k])
            if not (math.isfinite(v1) and math.isfinite(v2)):
                return None

        b1_open, b1_close, b1_low, b1_high = float(bar1["open"]), float(bar1["close"]), float(bar1["low"]), float(bar1["high"])
        b1_kcu, b1_kcl = float(bar1["kc_upper"]), float(bar1["kc_lower"])

        b2_open, b2_close, b2_low, b2_high = float(bar2["open"]), float(bar2["close"]), float(bar2["low"]), float(bar2["high"])
        b2_kcu, b2_kcl = float(bar2["kc_upper"]), float(bar2["kc_lower"])

        # ── 1. LONG 首次破軌 ──
        # Bar 1: 開盤價 < 上軌 且 收盤價 > 上軌 (實體突破)
        if b1_open < b1_kcu and b1_close > b1_kcu:
            # Bar 2: 陽線 (close > open) 且 最低價 > 上軌 (完全在軌外)
            if b2_close > b2_open and b2_low > b2_kcu:
                if apply_ma3_filter and not DualEntryDetector.is_ma3_direction_allowed(df_bars, current_idx, "LONG"):
                    return None  # MA3 向下阻斷多單

                return {
                    "signal_source": "KC_BREAKOUT_INITIAL",
                    "side": "LONG",
                    "signal_bar_idx": current_idx,
                    "signal_time": str(bar2.get("datetime", bar2.get("timestamp", ""))),
                    "kc_upper": b2_kcu,
                    "kc_lower": float(bar2["kc_lower"]),
                    "kc_middle": float(bar2["kc_middle"]),
                    "ma3": float(bar2.get("ma3", np.nan)),
                    "ma15": float(bar2.get("ma15", np.nan)),
                    "atr": float(bar2["atr"]),
                    "intended_exec_price": b2_close,
                    "base_sl": min(b1_low, b2_low) - (0.5 * float(bar2["atr"]))
                }

        # ── 2. SHORT 首次破軌 ──
        # Bar 1: 開盤價 > 下軌 且 收盤價 < 下軌 (實體跌破)
        if b1_open > b1_kcl and b1_close < b1_kcl:
            # Bar 2: 陰線 (close < open) 且 最高價 < 下軌 (完全在軌外)
            if b2_close < b2_open and b2_high < b2_kcl:
                if apply_ma3_filter and not DualEntryDetector.is_ma3_direction_allowed(df_bars, current_idx, "SHORT"):
                    return None  # MA3 向上阻斷空單

                return {
                    "signal_source": "KC_BREAKOUT_INITIAL",
                    "side": "SHORT",
                    "signal_bar_idx": current_idx,
                    "signal_time": str(bar2.get("datetime", bar2.get("timestamp", ""))),
                    "kc_upper": float(bar2["kc_upper"]),
                    "kc_lower": b2_kcl,
                    "kc_middle": float(bar2["kc_middle"]),
                    "ma3": float(bar2.get("ma3", np.nan)),
                    "ma15": float(bar2.get("ma15", np.nan)),
                    "atr": float(bar2["atr"]),
                    "intended_exec_price": b2_close,
                    "base_sl": max(b1_high, b2_high) + (0.5 * float(bar2["atr"]))
                }

        return None

    @staticmethod
    def check_kc_continuation_signal(
        df_bars: pd.DataFrame,
        current_idx: int,
        in_continuation_side: Optional[str],  # "LONG", "SHORT", 或 None
        apply_ma3_filter: bool = True
    ) -> Optional[Dict[str, Any]]:
        """
        KC 軌道外延續開倉 (Continuation Entry)
        前提: 已經由首次破軌進入軌道外延續狀態 (in_continuation_side 不為 None)
        - 做多延續: low > kc_upper 且 close > open (陽線) 且 MA3 非向下
        - 做空延續: high < kc_lower 且 close < open (陰線) 且 MA3 非向上
        """
        if current_idx < 1 or df_bars is None or current_idx >= len(df_bars) or in_continuation_side is None:
            return None

        bar = df_bars.iloc[current_idx]
        prev_bar = df_bars.iloc[current_idx - 1]

        req_keys = ["open", "high", "low", "close", "kc_upper", "kc_lower", "kc_middle", "atr"]
        for k in req_keys:
            if k not in bar:
                return None
            v = float(bar[k])
            if not math.isfinite(v):
                return None

        b_open, b_close, b_low, b_high = float(bar["open"]), float(bar["close"]), float(bar["low"]), float(bar["high"])
        kcu, kcl = float(bar["kc_upper"]), float(bar["kc_lower"])
        p_low = float(prev_bar["low"]) if "low" in prev_bar else b_low
        p_high = float(prev_bar["high"]) if "high" in prev_bar else b_high

        # ── 1. 做多延續 ──
        if in_continuation_side == "LONG":
            if b_low > kcu and b_close > b_open:
                if apply_ma3_filter and not DualEntryDetector.is_ma3_direction_allowed(df_bars, current_idx, "LONG"):
                    return None  # MA3 向下禁止新增多單

                return {
                    "signal_source": "KC_CONTINUATION",
                    "side": "LONG",
                    "signal_bar_idx": current_idx,
                    "signal_time": str(bar.get("datetime", bar.get("timestamp", ""))),
                    "kc_upper": kcu,
                    "kc_lower": kcl,
                    "kc_middle": float(bar["kc_middle"]),
                    "ma3": float(bar.get("ma3", np.nan)),
                    "ma15": float(bar.get("ma15", np.nan)),
                    "atr": float(bar["atr"]),
                    "intended_exec_price": b_close,
                    "base_sl": min(p_low, b_low) - (0.5 * float(bar["atr"]))
                }

        # ── 2. 做空延續 ──
        elif in_continuation_side == "SHORT":
            if b_high < kcl and b_close < b_open:
                if apply_ma3_filter and not DualEntryDetector.is_ma3_direction_allowed(df_bars, current_idx, "SHORT"):
                    return None  # MA3 向上禁止新增空單

                return {
                    "signal_source": "KC_CONTINUATION",
                    "side": "SHORT",
                    "signal_bar_idx": current_idx,
                    "signal_time": str(bar.get("datetime", bar.get("timestamp", ""))),
                    "kc_upper": kcu,
                    "kc_lower": kcl,
                    "kc_middle": float(bar["kc_middle"]),
                    "ma3": float(bar.get("ma3", np.nan)),
                    "ma15": float(bar.get("ma15", np.nan)),
                    "atr": float(bar["atr"]),
                    "intended_exec_price": b_close,
                    "base_sl": max(p_high, b_high) + (0.5 * float(bar["atr"]))
                }

        return None

    @staticmethod
    def is_price_returned_inside_kc(df_bars: pd.DataFrame, current_idx: int, side: str) -> bool:
        """
        檢驗價格是否已回到 KC 通道內:
        - 做多狀態: 若收盤價回到上軌內側 (close <= kc_upper)，延續狀態終止
        - 做空狀態: 若收盤價回到下軌內側 (close >= kc_lower)，延續狀態終止
        """
        if current_idx < 0 or df_bars is None or current_idx >= len(df_bars):
            return True

        bar = df_bars.iloc[current_idx]
        b_close = float(bar["close"])
        kcu = float(bar["kc_upper"])
        kcl = float(bar["kc_lower"])

        if side == "LONG":
            return b_close <= kcu
        elif side == "SHORT":
            return b_close >= kcl
        return True

    @staticmethod
    def check_ma_cross_signal(
        df_bars: pd.DataFrame,
        current_idx: int,
        apply_ma3_filter: bool = True
    ) -> Optional[Dict[str, Any]]:
        """
        MA3／MA15 交叉開倉 (MA Cross Entry)
        - LONG: 前一根 ma3 <= ma15 且當根 ma3 > ma15，且當根 MA3 不得向下
        - SHORT: 前一根 ma3 >= ma15 且當根 ma3 < ma15，且當根 MA3 不得向上
        """
        if current_idx < 1 or df_bars is None or current_idx >= len(df_bars):
            return None

        bar_prev = df_bars.iloc[current_idx - 1]
        bar_curr = df_bars.iloc[current_idx]

        for k in ["ma3", "ma15", "atr", "close", "kc_upper", "kc_lower", "kc_middle", "low", "high"]:
            if k not in bar_prev or k not in bar_curr:
                return None
            v_p, v_c = float(bar_prev[k]), float(bar_curr[k])
            if not (math.isfinite(v_p) and math.isfinite(v_c)):
                return None

        p_ma3, p_ma15 = float(bar_prev["ma3"]), float(bar_prev["ma15"])
        c_ma3, c_ma15 = float(bar_curr["ma3"]), float(bar_curr["ma15"])

        # ── 1. 做多黃金交叉 ──
        if p_ma3 <= p_ma15 and c_ma3 > c_ma15:
            if apply_ma3_filter and not DualEntryDetector.is_ma3_direction_allowed(df_bars, current_idx, "LONG"):
                return None  # 當根 MA3 向下阻斷多單

            return {
                "signal_source": "MA_CROSS",
                "side": "LONG",
                "signal_bar_idx": current_idx,
                "signal_time": str(bar_curr.get("datetime", bar_curr.get("timestamp", ""))),
                "kc_upper": float(bar_curr["kc_upper"]),
                "kc_lower": float(bar_curr["kc_lower"]),
                "kc_middle": float(bar_curr["kc_middle"]),
                "ma3": c_ma3,
                "ma15": c_ma15,
                "atr": float(bar_curr["atr"]),
                "intended_exec_price": float(bar_curr["close"]),
                "base_sl": min(float(bar_prev["low"]), float(bar_curr["low"])) - (0.5 * float(bar_curr["atr"]))
            }

        # ── 2. 做空死亡交叉 ──
        if p_ma3 >= p_ma15 and c_ma3 < c_ma15:
            if apply_ma3_filter and not DualEntryDetector.is_ma3_direction_allowed(df_bars, current_idx, "SHORT"):
                return None  # 當根 MA3 向上阻斷空單

            return {
                "signal_source": "MA_CROSS",
                "side": "SHORT",
                "signal_bar_idx": current_idx,
                "signal_time": str(bar_curr.get("datetime", bar_curr.get("timestamp", ""))),
                "kc_upper": float(bar_curr["kc_upper"]),
                "kc_lower": float(bar_curr["kc_lower"]),
                "kc_middle": float(bar_curr["kc_middle"]),
                "ma3": c_ma3,
                "ma15": c_ma15,
                "atr": float(bar_curr["atr"]),
                "intended_exec_price": float(bar_curr["close"]),
                "base_sl": max(float(bar_prev["high"]), float(bar_curr["high"])) + (0.5 * float(bar_curr["atr"]))
            }

        return None
