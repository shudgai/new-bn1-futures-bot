"""
core/services/strategies/anti_chop_agile_strategy.py
===================================================
量化合約策略優化核心引擎：根絕震盪洗盤、過濾假突破與落實敏捷出場

三大模組：
1. 模組 1：進場門禁重構 (Anti-Chop & Momentum Entry Gate)
   - 大趨勢一票否決 (Macro Trend Filter)
   - 震盪死魚盤與中軸糾纏過濾 (Chop & Squeeze Filter)
   - 兩根收盤破軌確認 (Close-based 2-Bar Breakout)
   - 超級強動能脈衝豁免 (Super Impulse Bypass: 實體 >= 1.0 ATR, 低影線)
   - 過度延伸防追高 (Anti-Chase Bias Filter: 乖離 > 2.5 ATR)

2. 模組 2：平倉冷卻防禦 (Cooldown & Data Freshness Guard)
   - 同方向 120 秒重新進場冷卻鎖 (Same-Direction Re-entry Cooldown)
   - 行情資料新鮮度校驗 (Timeframe-Aware Candle Freshness)

3. 模組 3：出場與止盈防禦 (Agile & Pullback Exit Gate)
   - 健康回踩不平倉護航 (Hold on Pullback: 慢線中軌內縮量回抽不平)
   - MA3 極速 V 轉 / 倒 V 頂敏捷逃頂逃底 (Fast Momentum Reversal Exit)
   - 棘輪式極值回撤止盈 (Ratchet Profit Lock: 浮盈 >= 2.0 ATR, 回吐 45% 市價鎖利)
   - 盤中中軌秒破打斷 (Intra-bar Midline Breach Cut: 突破中軌 + 0.2 ATR 即時跳車)
"""
import math
import logging
from typing import Dict, Any, Tuple, Optional
import pandas as pd
import numpy as np

from core.services.reverse_entry_cooldown_lock import (
    check_same_direction_reentry_cooldown,
    check_closed_candle_freshness,
)

logger = logging.getLogger(__name__)


# =============================================================================
# 模組 1：進場門禁重構 (Anti-Chop & Momentum Entry Gate)
# =============================================================================

class AntiChopEntryGate:
    """進場門禁裁決器：杜絕震盪死魚盤磨損，只做確定性主升浪/主跌浪。"""

    MIN_SLOPE_THRESHOLD: float = 0.00001   # 中軌平緩判斷閾值
    MIDLINE_BOX_ATR_RATIO: float = 0.30     # 中軸震盪糾纏盒 (0.3 ATR)
    OVEREXTENDED_BIAS_ATR: float = 2.50     # 乖離過大防追高門檻 (2.5 ATR)
    SUPER_IMPULSE_MIN_BODY_ATR: float = 1.0 # 超級強動能最小實體 (1.0 ATR)
    SUPER_IMPULSE_MAX_WICK_RATIO: float = 0.35 # 超級強動能最大逆向影線比例

    @staticmethod
    def evaluate_entry(
        df_bars: pd.DataFrame,
        target_side: str,
        current_quote: float,
        last_closed_trade: Optional[Dict[str, Any]] = None,
        now_ts: Optional[float] = None,
    ) -> Tuple[bool, Optional[str], Dict[str, Any]]:
        """全流程進場檢驗：
        
        Args:
            df_bars: 包含歷史 K 線與指標 (close, open, high, low, atr, kc_middle, kc_upper, kc_lower, kc_middle_slope)
            target_side: "LONG" 或 "SHORT"
            current_quote: 最新盤中報價
            last_closed_trade: 該幣種上次平倉資訊 (用於同向冷卻)
            now_ts: 當前時間戳 (秒)

        Returns:
            (allowed: bool, reject_reason: Optional[str], metadata: dict)
        """
        side = target_side.upper()
        if side not in ("LONG", "SHORT"):
            return False, "INVALID_TARGET_SIDE", {}

        if df_bars is None or len(df_bars) < 3:
            return False, "INSUFFICIENT_DATA", {}

        bar_curr = df_bars.iloc[-1]
        bar_prev = df_bars.iloc[-2]

        c_close = float(bar_curr["close"])
        c_open = float(bar_curr["open"])
        c_high = float(bar_curr["high"])
        c_low = float(bar_curr["low"])

        atr = float(bar_curr.get("atr", 0.0))
        if atr <= 0:
            atr = max(c_high - c_low, 1e-8)

        kc_mid = float(bar_curr.get("kc_middle", (float(bar_curr.get("kc_upper", c_close)) + float(bar_curr.get("kc_lower", c_close))) / 2.0))
        kc_up = float(bar_curr.get("kc_upper", kc_mid + 2.0 * atr))
        kc_low = float(bar_curr.get("kc_lower", kc_mid - 2.0 * atr))

        # 中軌斜率提取
        if "kc_middle_slope" in bar_curr and math.isfinite(float(bar_curr["kc_middle_slope"])):
            kc_slope = float(bar_curr["kc_middle_slope"])
        elif len(df_bars) >= 4 and "kc_middle" in df_bars.columns:
            kc_slope = float(df_bars["kc_middle"].iloc[-1]) - float(df_bars["kc_middle"].iloc[-3])
        else:
            kc_slope = 0.0

        body = abs(c_close - c_open)
        meta: Dict[str, Any] = {
            "side": side,
            "quote": current_quote,
            "close": c_close,
            "kc_middle": kc_mid,
            "kc_upper": kc_up,
            "kc_lower": kc_low,
            "kc_slope": kc_slope,
            "atr": atr,
            "body": body,
            "body_atr": body / atr,
        }

        # ---------------------------------------------------------------------
        # 1. 檢驗超級強動能脈衝 (Super Impulse Bypass)
        # ---------------------------------------------------------------------
        # 若出現確定性超強實體棒，順應中軌且無長影線，可直接豁免冷卻與第二根確認！
        is_super_impulse = False
        if side == "LONG":
            upper_wick = c_high - max(c_open, c_close)
            is_super_impulse = (
                (c_close > c_open)
                and (body >= AntiChopEntryGate.SUPER_IMPULSE_MIN_BODY_ATR * atr)
                and (kc_slope >= -AntiChopEntryGate.MIN_SLOPE_THRESHOLD)
                and (upper_wick <= AntiChopEntryGate.SUPER_IMPULSE_MAX_WICK_RATIO * body)
                and (c_close >= kc_up)
            )
        elif side == "SHORT":
            lower_wick = min(c_open, c_close) - c_low
            is_super_impulse = (
                (c_close < c_open)
                and (body >= AntiChopEntryGate.SUPER_IMPULSE_MIN_BODY_ATR * atr)
                and (kc_slope <= AntiChopEntryGate.MIN_SLOPE_THRESHOLD)
                and (lower_wick <= AntiChopEntryGate.SUPER_IMPULSE_MAX_WICK_RATIO * body)
                and (c_close <= kc_low)
            )

        meta["is_super_impulse"] = is_super_impulse

        # ---------------------------------------------------------------------
        # 2. 同方向 120 秒重新進場冷卻鎖（非強動能脈衝時嚴格執行）
        # ---------------------------------------------------------------------
        target_action = f"OPEN_{side}"
        if not is_super_impulse:
            cooldown_allowed, cooldown_reason = check_same_direction_reentry_cooldown(
                target_action=target_action,
                last_closed_trade=last_closed_trade,
                cooldown_seconds=120.0,
                now_ts=now_ts,
            )
            if not cooldown_allowed:
                logger.info("⛔ [%s攔截] 原因: 同向冷卻鎖生效中 (%s)", target_action, cooldown_reason)
                return False, cooldown_reason, meta

        # ---------------------------------------------------------------------
        # 3. 大趨勢一票否決 (Macro Trend Filter)
        # ---------------------------------------------------------------------
        if side == "LONG" and kc_slope < -AntiChopEntryGate.MIN_SLOPE_THRESHOLD:
            msg = f"中軌大角度下行 (slope={kc_slope:.6f})，嚴禁逆勢摸底做多！"
            logger.info("⛔ [開多攔截] 原因: %s", msg)
            return False, "BLOCKED_LONG_ON_BEARISH_TREND", {**meta, "cause": msg}

        if side == "SHORT" and kc_slope > AntiChopEntryGate.MIN_SLOPE_THRESHOLD:
            msg = f"中軌大角度上行 (slope={kc_slope:.6f})，嚴禁逆勢摸頂做空！"
            logger.info("⛔ [開空攔截] 原因: %s", msg)
            return False, "BLOCKED_SHORT_ON_BULLISH_TREND", {**meta, "cause": msg}

        # ---------------------------------------------------------------------
        # 4. 震盪死魚盤與中軸糾纏一票否決 (Chop & Squeeze Filter)
        # ---------------------------------------------------------------------
        # A. 中軌走平 (無趨勢)
        is_flat_middle = abs(kc_slope) < AntiChopEntryGate.MIN_SLOPE_THRESHOLD

        # B. 箱體中軸糾纏區間 (|price - middle| < 0.3 * ATR)
        dist_to_middle = abs(c_close - kc_mid)
        is_midline_entangled = dist_to_middle < (AntiChopEntryGate.MIDLINE_BOX_ATR_RATIO * atr)

        # C. 通道帶寬極度收窄 (Squeeze)
        is_bandwidth_squeeze = False
        if len(df_bars) >= 20 and "kc_upper" in df_bars.columns and "kc_lower" in df_bars.columns:
            bw_series = (df_bars["kc_upper"] - df_bars["kc_lower"])
            max_bw_20 = float(bw_series.iloc[-20:].max())
            curr_bw = float(kc_up - kc_low)
            if max_bw_20 > 0 and curr_bw <= 0.25 * max_bw_20:
                is_bandwidth_squeeze = True

        if (is_flat_middle or is_midline_entangled or is_bandwidth_squeeze) and not is_super_impulse:
            msg = (
                f"死魚盤/中軸糾纏一票否決: flat={is_flat_middle} (slope={kc_slope:.6f}), "
                f"mid_box={is_midline_entangled} (dist={dist_to_middle:.6f} < 0.3*ATR={0.3*atr:.6f}), "
                f"squeeze={is_bandwidth_squeeze}"
            )
            logger.info("⛔ [%s攔截] 原因: %s", target_action, msg)
            return False, "BLOCKED_CHOP_AND_SQUEEZE", {**meta, "cause": msg}

        # ---------------------------------------------------------------------
        # 5. 過度延伸防追高防線 (Anti-Chase Bias Filter)
        # ---------------------------------------------------------------------
        bias = abs(current_quote - kc_mid)
        bias_atr = bias / atr
        if bias_atr > AntiChopEntryGate.OVEREXTENDED_BIAS_ATR:
            msg = f"乖離過大防追高: 距慢線偏離 {bias_atr:.2f} ATR > 2.5 ATR，禁止追單，等待回調！"
            logger.info("⛔ [%s攔截] 原因: %s", target_action, msg)
            return False, "BLOCKED_OVEREXTENDED_BIAS", {**meta, "bias_atr": bias_atr, "cause": msg}

        # ---------------------------------------------------------------------
        # 6. 兩根收盤破軌確認 (Close-based 2-Bar Breakout)
        # ---------------------------------------------------------------------
        # 超級脈衝直接豁免；一般破軌要求連續 2 根收盤站穩軌道外
        if is_super_impulse:
            logger.info("⚡ [%s放行] 超級強動能脈衝觸發 (body=%.2f ATR)，豁免冷卻直接進場！", target_action, body / atr)
            return True, "AUTHORIZED_BY_SUPER_IMPULSE", meta

        p_close = float(bar_prev["close"])
        p_kcu = float(bar_prev.get("kc_upper", kc_up))
        p_kcl = float(bar_prev.get("kc_lower", kc_low))

        if side == "LONG":
            two_bars_outside = (p_close > p_kcu) and (c_close > kc_up)
            if not two_bars_outside:
                return False, "WAITING_2BAR_CLOSE_CONFIRMATION", meta
            return True, "AUTHORIZED_BY_2BAR_BREAKOUT_LONG", meta

        elif side == "SHORT":
            two_bars_outside = (p_close < p_kcl) and (c_close < kc_low)
            if not two_bars_outside:
                return False, "WAITING_2BAR_CLOSE_CONFIRMATION", meta
            return True, "AUTHORIZED_BY_2BAR_BREAKOUT_SHORT", meta

        return False, "NO_VALID_ENTRY", meta


# =============================================================================
# 模組 3：出場與止盈防禦 (Agile & Pullback Exit Gate)
# =============================================================================

class AntiChopExitGate:
    """敏捷出場裁決器：兼顧單邊趨勢死抱（回踩不平）與極端 V 轉秒逃命。"""

    RATCHET_TRIGGER_ATR: float = 2.0      # 棘輪鎖利啟動浮盈 (2.0 ATR)
    RATCHET_GIVEBACK_RATIO: float = 0.45  # 棘輪極值回撤容忍度 (45%)
    INTRABAR_BREACH_BUFFER_ATR: float = 0.20  # 盤中中軌秒破緩衝 (0.2 ATR)

    @staticmethod
    def evaluate_exit(
        df_bars: pd.DataFrame,
        position_side: str,
        entry_price: float,
        current_quote: float,
        peak_favorable_price: Optional[float] = None,
    ) -> Tuple[bool, Optional[str], Dict[str, Any]]:
        """多空對稱出場審查：
        
        優先級階梯：
        1. 盤中中軌秒破緊急打斷 (Intra-bar Midline Breach Cut)
        2. MA3 極速 V 轉 / 倒 V 頂敏捷逃頂逃底 (Fast Momentum Reversal Exit)
        3. 棘輪極值回撤止盈 (Ratchet Profit Lock)
        4. 健康回踩不平倉護航 (Hold on Pullback: 慢線中軌下方縮量回抽一票否決平倉)
        """
        side = position_side.upper()
        if side not in ("LONG", "SHORT"):
            return False, None, {}

        if df_bars is None or len(df_bars) < 3:
            return False, None, {}

        bar_t = df_bars.iloc[-1]
        bar_p1 = df_bars.iloc[-2]
        bar_p2 = df_bars.iloc[-3]

        c_close = float(bar_t["close"])
        c_open = float(bar_t["open"])

        atr = float(bar_t.get("atr", 0.0))
        if atr <= 0:
            atr = max(float(bar_t["high"]) - float(bar_t["low"]), 1e-8)

        kc_mid = float(bar_t.get("kc_middle", (float(bar_t.get("kc_upper", c_close)) + float(bar_t.get("kc_lower", c_close))) / 2.0))
        if "kc_middle_slope" in bar_t and math.isfinite(float(bar_t["kc_middle_slope"])):
            kc_slope = float(bar_t["kc_middle_slope"])
        elif len(df_bars) >= 4 and "kc_middle" in df_bars.columns:
            kc_slope = float(df_bars["kc_middle"].iloc[-1]) - float(df_bars["kc_middle"].iloc[-3])
        else:
            kc_slope = 0.0

        # 安全提取 MA3
        if "ma3" in df_bars.columns:
            ma3_t = float(bar_t["ma3"])
            ma3_p1 = float(bar_p1["ma3"])
            ma3_p2 = float(bar_p2["ma3"])
        else:
            closes = df_bars["close"].tail(5)
            ma3_t = float(closes.iloc[-3:].mean())
            ma3_p1 = float(closes.iloc[-4:-1].mean())
            ma3_p2 = float(closes.iloc[-5:-2].mean())

        meta: Dict[str, Any] = {
            "side": side,
            "entry_price": entry_price,
            "quote": current_quote,
            "close": c_close,
            "kc_middle": kc_mid,
            "kc_slope": kc_slope,
            "atr": atr,
            "ma3_t": ma3_t,
            "ma3_p1": ma3_p1,
            "ma3_p2": ma3_p2,
        }

        # ---------------------------------------------------------------------
        # 1. 盤中中軌秒破緊急打斷 (Intra-bar Midline Breach Cut)
        # ---------------------------------------------------------------------
        if side == "SHORT":
            breach_level = kc_mid + (AntiChopExitGate.INTRABAR_BREACH_BUFFER_ATR * atr)
            if current_quote >= breach_level:
                msg = f"空單盤中結構秒破: 報價({current_quote:.6f}) 暴衝上穿中軌+0.2ATR({breach_level:.6f})，不等收盤即刻市價止損！"
                logger.warning("🚨 [敏捷出場] %s", msg)
                return True, "EXIT_BY_INTRABAR_MIDLINE_BREACH", {**meta, "cause": msg}

        elif side == "LONG":
            breach_level = kc_mid - (AntiChopExitGate.INTRABAR_BREACH_BUFFER_ATR * atr)
            if current_quote <= breach_level:
                msg = f"多單盤中結構秒破: 報價({current_quote:.6f}) 暴跌下破中軌-0.2ATR({breach_level:.6f})，不等收盤即刻市價止損！"
                logger.warning("🚨 [敏捷出場] %s", msg)
                return True, "EXIT_BY_INTRABAR_MIDLINE_BREACH", {**meta, "cause": msg}

        # ---------------------------------------------------------------------
        # 2. MA3 極速 V 轉 / 倒 V 頂敏捷逃頂逃底 (Fast Momentum Reversal Exit)
        # ---------------------------------------------------------------------
        if side == "SHORT":
            ma3_v_bottom = (ma3_t > ma3_p1) and (ma3_p1 <= ma3_p2)
            price_above_ma3 = c_close > ma3_t
            bull_body = c_close - c_open
            strong_bull_candle = (bull_body > 0) and (bull_body >= 0.50 * atr)

            if ma3_v_bottom and price_above_ma3 and strong_bull_candle:
                msg = (
                    f"空單谷底 MA3 V 轉敏捷平倉: MA3勾頭向上(curr={ma3_t:.6f} > p1={ma3_p1:.6f})，"
                    f"收盤站上MA3(close={c_close:.6f} > ma3={ma3_t:.6f})，"
                    f"多方實體反攻({bull_body:.6f} >= 0.5ATR={0.5*atr:.6f})，即刻市價平空！"
                )
                logger.info("🚨 [敏捷出場] %s", msg)
                return True, "EXIT_SHORT_BY_MA3_V_BOTTOM", {
                    **meta,
                    "bull_body": bull_body,
                    "body_atr_ratio": bull_body / atr,
                    "cause": msg,
                }

        elif side == "LONG":
            ma3_inverted_v_top = (ma3_t < ma3_p1) and (ma3_p1 >= ma3_p2)
            price_below_ma3 = c_close < ma3_t
            bear_body = c_open - c_close
            strong_bear_candle = (bear_body > 0) and (bear_body >= 0.50 * atr)

            if ma3_inverted_v_top and price_below_ma3 and strong_bear_candle:
                msg = (
                    f"多單天花板 MA3 倒 V 頂敏捷平倉: MA3拐頭向下(curr={ma3_t:.6f} < p1={ma3_p1:.6f})，"
                    f"收盤跌破MA3(close={c_close:.6f} < ma3={ma3_t:.6f})，"
                    f"空方實體下殺({bear_body:.6f} >= 0.5ATR={0.5*atr:.6f})，即刻市價平多！"
                )
                logger.info("🚨 [敏捷出場] %s", msg)
                return True, "EXIT_LONG_BY_MA3_INVERTED_V_TOP", {
                    **meta,
                    "bear_body": bear_body,
                    "body_atr_ratio": bear_body / atr,
                    "cause": msg,
                }

        # ---------------------------------------------------------------------
        # 3. 棘輪式極值回撤止盈 (Ratchet Profit Lock)
        # ---------------------------------------------------------------------
        if peak_favorable_price is not None and peak_favorable_price > 0:
            if side == "SHORT":
                max_gain = entry_price - peak_favorable_price
                if max_gain >= AntiChopExitGate.RATCHET_TRIGGER_ATR * atr:
                    rebound = current_quote - peak_favorable_price
                    if rebound >= (AntiChopExitGate.RATCHET_GIVEBACK_RATIO * max_gain):
                        msg = (
                            f"空單棘輪回撤止盈: 最大浮盈曾達 {max_gain/atr:.2f} ATR (低點={peak_favorable_price:.6f})，"
                            f"自極值反彈 {rebound/max_gain:.1%} >= 45%，盤中即刻鎖利！"
                        )
                        logger.info("🏁 [棘輪止盈] %s", msg)
                        return True, "EXIT_BY_RATCHET_MAX_PROFIT_DRAWDOWN", {**meta, "cause": msg}

            elif side == "LONG":
                max_gain = peak_favorable_price - entry_price
                if max_gain >= AntiChopExitGate.RATCHET_TRIGGER_ATR * atr:
                    pullback = peak_favorable_price - current_quote
                    if pullback >= (AntiChopExitGate.RATCHET_GIVEBACK_RATIO * max_gain):
                        msg = (
                            f"多單棘輪回撤止盈: 最大浮盈曾達 {max_gain/atr:.2f} ATR (高點={peak_favorable_price:.6f})，"
                            f"自極值回調 {pullback/max_gain:.1%} >= 45%，盤中即刻鎖利！"
                        )
                        logger.info("🏁 [棘輪止盈] %s", msg)
                        return True, "EXIT_BY_RATCHET_MAX_PROFIT_DRAWDOWN", {**meta, "cause": msg}

        # ---------------------------------------------------------------------
        # 4. 健康回踩不平倉護航 (Hold on Pullback Defense)
        # ---------------------------------------------------------------------
        # 空單：中軌向下且收盤價仍在棕色中軌之下，微幅小綠 K 視為良性空中換手，一票否決平空！
        if side == "SHORT" and kc_slope < -0.000001 and c_close < kc_mid:
            msg = f"空單中軌趨勢護航: 中軌向下(slope={kc_slope:.6f}) 且收盤在慢線下(close={c_close:.6f} < mid={kc_mid:.6f})，微幅反彈良性換手，一票否決平空！"
            return False, "HOLD_PULLBACK_BELOW_KC_MIDDLE", {**meta, "cause": msg}

        # 多單：中軌向上且收盤價仍在棕色中軌之上，微幅小紅 K 視為良性空中換手，一票否決平多！
        if side == "LONG" and kc_slope > 0.000001 and c_close > kc_mid:
            msg = f"多單中軌趨勢護航: 中軌向上(slope={kc_slope:.6f}) 且收盤在慢線上(close={c_close:.6f} > mid={kc_mid:.6f})，微幅回踩良性換手，一票否決平多！"
            return False, "HOLD_PULLBACK_ABOVE_KC_MIDDLE", {**meta, "cause": msg}

        return False, "HOLD_POSITION", meta
