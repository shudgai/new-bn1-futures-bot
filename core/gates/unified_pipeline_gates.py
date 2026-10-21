"""
core/gates/unified_pipeline_gates.py
====================================
三層流水線之【裁決層 (Pure Math Gates)】
依據 2026/10/21 盤面覆盤與最新防洗/防追急衝標準重構：
- 解決「買在急衝頂部、平在盤整底部、主升浪全程踏空」的問題。
"""
from typing import Dict, Any, Tuple, Optional, Union
import pandas as pd
from core.services.data_pipeline import MarketDataSnapshot


class ExitGate:
    """
    出場裁決唯一裁決官：
    1. 階梯鎖利/保本底線 -> True (PROFIT_LOCK)
    2. 純數學真峰谷力竭 (外軌大黑吞噬 / 確切三點樞紐力竭) -> True (TRUE_PEAK / TRUE_BOTTOM)
    3. 盤整黏合嚴禁平多 (未達連續兩根收破 EMA40 強制 VETO_HOLD)
    """

    @staticmethod
    def evaluate(
        pos: Dict[str, Any],
        market_data: Union[MarketDataSnapshot, Dict[str, Any]],
        current_price: Optional[float] = None,
        atr: Optional[float] = None,
        kc_slope: Optional[float] = None,
    ) -> Tuple[bool, str, Dict[str, Any]]:
        if not pos or not market_data:
            return False, "VETO_HOLD: 無有效持倉或市場數據", {}

        # 支援 MarketDataSnapshot 或純 dict
        if isinstance(market_data, MarketDataSnapshot):
            quote = market_data.current_price
            atr_val = market_data.atr
            slope = market_data.kc_middle_slope
            curr_k = market_data.curr_k
            prev_k = market_data.prev_k
            ma5 = market_data.ma5
            ma15 = market_data.ma15
            ema_fast = market_data.ema_fast
            ema_slow = market_data.ema_slow
            kc_up = market_data.kc_upper
            kc_mid = market_data.kc_middle
            kc_low = market_data.kc_lower
            raw_df = market_data.raw_df
        else:
            quote = float(current_price or market_data.get("current_price", 0.0))
            atr_val = float(atr or market_data.get("atr", quote * 0.01))
            slope = float(kc_slope or market_data.get("kc_middle_slope", 0.0))
            curr_k = market_data.get("curr_k", {})
            prev_k = market_data.get("prev_k", {})
            ma5 = float(market_data.get("ma5", quote))
            ma15 = float(market_data.get("ma15", quote))
            kc_mid = float(market_data.get("kc_middle", quote))
            ema_fast = float(market_data.get("ema_fast", ma15))
            ema_slow = float(market_data.get("ema_slow", kc_mid))
            kc_up = float(market_data.get("kc_upper", quote * 1.01))
            kc_low = float(market_data.get("kc_lower", quote * 0.99))
            raw_df = market_data.get("raw_df")

        side = str(pos.get("side", "")).upper()
        entry_p = float(pos.get("entry_price") or 0.0)
        if entry_p <= 0 or quote <= 0:
            return False, "VETO_HOLD: 報價無效", {}

        # 損益計算
        unrealized_pct = ((quote - entry_p) / entry_p) if side == "LONG" else ((entry_p - quote) / entry_p)
        peak_roe = float(pos.get("peak_roe", 0.0) or pos.get("highest_pnl_pct", 0.0) or unrealized_pct)
        peak_roe = max(peak_roe, unrealized_pct)
        pos["peak_roe"] = peak_roe

        meta = {
            "unrealized_pct": unrealized_pct,
            "peak_roe": peak_roe,
            "quote": quote,
            "atr": atr_val,
            "kc_slope": slope,
            "ema_fast": ema_fast,
            "ema_slow": ema_slow,
        }

        atr_pct = (atr_val / entry_p) if entry_p > 0 else 0.01

        # ── 1. 階梯鎖利 / 保本底線 (PROFIT_LOCK) ──
        # 1-A: 達標 10% 鎖 7% (利潤達到 10% 以上，保證留存 70% 利潤)
        if peak_roe >= 0.10:
            min_retained_pnl = peak_roe * 0.70
            if unrealized_pct <= min_retained_pnl:
                return True, "PROFIT_LOCK", {
                    **meta,
                    "cause": f"10%鎖7%階梯利潤觸發: peak_roe={peak_roe:.2%}, curr={unrealized_pct:.2%} <= floor={min_retained_pnl:.2%}",
                }

        # ── 0. 反側外軌破位異常緊急平倉 (OPPOSITE_KC_BAND_BREACH，AGENTS.md 最優先防護) ──
        if side == "LONG" and quote < kc_low:
            return True, "OPPOSITE_KC_BAND_BREACH", {
                **meta,
                "cause": f"多單跌破反側KC下軌異常逃命: quote={quote:.4f} < kc_lower={kc_low:.4f}！",
            }
        elif side == "SHORT" and quote > kc_up:
            return True, "OPPOSITE_KC_BAND_BREACH", {
                **meta,
                "cause": f"空單突破反側KC上軌異常逃命: quote={quote:.4f} > kc_upper={kc_up:.4f}！",
            }

        # 帳戶硬止損 (3.0 ATR 防穿倉)
        hard_loss_threshold = -3.0 * atr_pct
        if unrealized_pct <= hard_loss_threshold:
            return True, "PROFIT_LOCK", {
                **meta,
                "cause": f"硬止損避險: loss={unrealized_pct:.2%} <= threshold={hard_loss_threshold:.2%}",
            }

        # ── 2. 純數學真峰谷力竭 (TRUE_PEAK / TRUE_BOTTOM) 與波段防守 ──
        if raw_df is not None and len(raw_df) >= 3:
            c_curr = raw_df.iloc[-1]
            c_prev = raw_df.iloc[-2]
            c_prev2 = raw_df.iloc[-3] if len(raw_df) >= 3 else c_prev

            if side == "LONG":
                # 主升浪健康回踩防洗保護 (Healthy Pullback Invariant) - 最優先防洗
                # 趨勢背景完好：KC 中軌向上、EMA15 >= EMA40 多頭排列，收盤依然高於中軌且守穩均線結構 (close >= ema_fast > kc_mid)，小陰線回測一律 VETO_HOLD 續抱！
                is_healthy_pullback = (
                    isinstance(market_data, MarketDataSnapshot)
                    and len(raw_df) >= 5
                    and slope >= 0
                    and ema_fast >= ema_slow
                    and c_curr["close"] > kc_mid
                    and c_curr["close"] >= ema_fast
                )
                if is_healthy_pullback:
                    return False, "VETO_HOLD", {
                        **meta,
                        "cause": f"多單主升浪健康回踩中，KC中軌依然向上 (kc_middle_slope={slope:.6f}) 且 close={c_curr['close']:.4f} >= ema_fast={ema_fast:.4f} > kc_mid={kc_mid:.4f}，維持續抱，嚴禁平多！",
                    }

                # ── 觸發器 0：早期真峰頂樞紐確立即刻平多 (Early Pivot Peak Exit) ──
                # 1. 頂部大陰吞噬：當前棒為超過 1.5 ATR 的實體巨黑棒，且跌破前棒完整 K 棒的最低點 (吞噬前整根 K)
                is_bearish_engulfing = (c_curr["close"] < c_curr["open"]) and (c_curr["close"] < c_prev["low"]) and ((c_curr["open"] - c_curr["close"]) >= 1.5 * atr_val)
                # 2. 三點確定性真峰頂樞紐：中間棒 High 嚴格高於前後兩棒 High，且中間棒為實體紅陽棒衝高（或觸及KC上軌）；第三棒收黑跌破中間棒實體中點
                is_pivot_peak = (c_prev["high"] > c_prev2["high"]) and (c_prev["high"] > c_curr["high"]) and ((c_prev["close"] > c_prev["open"]) or (c_prev["high"] >= kc_up))
                c_prev_mid = (c_prev["open"] + c_prev["close"]) / 2.0
                is_pivot_exhaustion = (c_curr["close"] < c_prev_mid) and (c_curr["close"] < c_curr["open"])
                # 3. 頂部射擊之星：前棒上影線 >= 1.5 倍 body 且 >= 0.4 ATR，當前棒收黑確認，且前棒必須是近期相對高點
                upper_wick_prev = c_prev["high"] - max(c_prev["open"], c_prev["close"])
                body_prev = abs(c_prev["open"] - c_prev["close"])
                is_shooting_star = (c_prev["high"] > c_prev2["high"]) and (c_prev["high"] > c_curr["high"]) and (upper_wick_prev >= 1.5 * max(body_prev, 0.2 * atr_val)) and (upper_wick_prev >= 0.4 * atr_val) and (c_curr["close"] < c_curr["open"]) and (c_curr["close"] < c_prev["close"])

                is_true_peak = is_bearish_engulfing or (is_pivot_peak and is_pivot_exhaustion) or is_shooting_star

                if is_true_peak and (unrealized_pct > 0.003 or peak_roe >= 0.008):
                    return True, "EARLY_PIVOT_PEAK_EXIT", {
                        **meta,
                        "cause": f"早期真峰頂樞紐確立(engulfing={is_bearish_engulfing}, pivot={is_pivot_peak}, star={is_shooting_star})，不必等待跌破中軌，立即平多落袋！",
                    }

                # ── 觸發器 0-B：跌破 KC 中軌平多 (LONG_EXIT_KC_MID_BREACH) ──
                # 只有在大週期均線已轉弱/死叉 (ema_fast < ema_slow) 且波段回吐時，收破中軌才執行保護性落袋平多
                if ema_fast < ema_slow and (c_curr["close"] < kc_mid or quote < kc_mid) and (peak_roe >= max(0.6 * atr_pct, 0.004) and unrealized_pct <= peak_roe * 0.60):
                    return True, "LONG_EXIT_KC_MID_BREACH", {
                        **meta,
                        "cause": f"多單均線轉弱且跌破KC中軌 (close={c_curr['close']:.4f} < kc_mid={kc_mid:.4f})，波段回吐利潤鎖定平多！",
                    }

                # ── 觸發器 0-C：MA5 關鍵防守線失守且轉折平多 (LONG_EXIT_MA5_REVERSAL，AGENTS.md 模組五) ──
                ma5_curr = float(curr_k.get("ma5", c_curr["close"]))
                ma5_prev = float(prev_k.get("ma5", ma5_curr))
                if ema_fast < ema_slow and c_curr["close"] < kc_up and c_curr["close"] < ma5_curr and ma5_curr < ma5_prev and peak_roe >= max(0.6 * atr_pct, 0.004) and unrealized_pct <= peak_roe * 0.60:
                    return True, "LONG_EXIT_MA5_REVERSAL", {
                        **meta,
                        "cause": f"多單收盤跌回上軌內側且跌破MA5防守線 (close={c_curr['close']:.4f} < ma5={ma5_curr:.4f})，動能衰竭平多落袋！",
                    }

                # 趨勢不變續抱：大週期多頭 (ema_fast >= ema_slow) 維持續抱！
                if ema_fast >= ema_slow:
                    return False, "VETO_HOLD", {
                        **meta,
                        "cause": f"多單大週期趨勢依然向上且守穩結構 (kc_middle_slope={slope:.6f}, ema_fast={ema_fast:.5f} >= ema_slow={ema_slow:.5f})，維持續抱！",
                    }

            elif side == "SHORT":
                # 主跌浪健康反抽防洗保護 (Healthy Pullback Invariant) - 最優先防洗
                # 趨勢背景完好：KC 中軌向下、EMA15 <= EMA40 空頭排列，受制於快線結構 (close <= ema_fast < kc_mid)，小陽線反抽一律 VETO_HOLD 續抱！
                is_healthy_pullback = (
                    isinstance(market_data, MarketDataSnapshot)
                    and len(raw_df) >= 5
                    and slope <= 0
                    and ema_fast <= ema_slow
                    and c_curr["close"] <= ema_fast
                    and c_curr["close"] < kc_mid
                )
                if is_healthy_pullback:
                    return False, "VETO_HOLD", {
                        **meta,
                        "cause": f"空單主跌浪健康反抽中，KC中軌依然向下 (kc_middle_slope={slope:.6f}) 且 close={c_curr['close']:.4f} <= ema_fast={ema_fast:.4f} < kc_mid={kc_mid:.4f}，維持續抱，嚴禁平空！",
                    }

                # ── 觸發器 2：早期真谷底樞紐確立即刻平空 (Early Pivot Valley Exit - 第一優先) ──
                # 1. 底部大陽吞噬：前棒為黑陰棒，當前棒為超過 1.5 ATR 的實體巨紅棒，且突破前棒完整 K 棒的最高點 (吞噬前整根 K)
                is_bullish_engulfing = (c_prev["close"] < c_prev["open"]) and (c_curr["close"] > c_curr["open"]) and (c_curr["close"] > c_prev["high"]) and ((c_curr["close"] - c_curr["open"]) >= 1.5 * atr_val)
                # 2. 三點確定性真谷底樞紐：中間棒 Low 嚴格低於前後兩棒 Low，且中間棒為實體黑陰棒探底（或觸及KC下軌）；第三棒收紅衝破中間棒實體中點
                is_pivot_bottom = (c_prev["low"] < c_prev2["low"]) and (c_prev["low"] < c_curr["low"]) and ((c_prev["close"] < c_prev["open"]) or (c_prev["low"] <= kc_low))
                c_prev_mid = (c_prev["open"] + c_prev["close"]) / 2.0
                is_pivot_exhaustion = (c_curr["close"] > c_prev_mid) and (c_curr["close"] > c_curr["open"])
                # 3. 底部錘頭線：前棒下影線 >= 1.5 倍 body 且 >= 0.4 ATR，當前棒收紅確認，且前棒必須是近期相對高點
                lower_wick_prev = min(c_prev["open"], c_prev["close"]) - c_prev["low"]
                body_prev = abs(c_prev["open"] - c_prev["close"])
                is_hammer = (c_prev["low"] < c_prev2["low"]) and (c_prev["low"] < c_curr["low"]) and (lower_wick_prev >= 1.5 * max(body_prev, 0.2 * atr_val)) and (lower_wick_prev >= 0.4 * atr_val) and (c_curr["close"] > c_curr["open"]) and (c_curr["close"] > c_prev["close"])

                is_true_valley = is_bullish_engulfing or (is_pivot_bottom and is_pivot_exhaustion) or is_hammer

                if is_true_valley and (unrealized_pct > 0.003 or peak_roe >= 0.008):
                    return True, "EARLY_PIVOT_VALLEY_EXIT", {
                        **meta,
                        "cause": f"早期真谷底樞紐確立(engulfing={is_bullish_engulfing}, pivot={is_pivot_bottom}, hammer={is_hammer})，不必等待中軌即刻平空！",
                    }

                # ── 觸發器 1：EMA15 向上金叉 EMA40 即刻平空 (EMA Golden Cross Exit) ──
                if ema_fast > ema_slow:
                    return True, "SHORT_EXIT_ON_EMA_CROSS", {
                        **meta,
                        "cause": f"EMA15向上金叉EMA40 (ema_fast={ema_fast:.4f} > ema_slow={ema_slow:.4f})，波段反轉立即平空！",
                    }

                # ── 觸發器 1-B：收復突破 KC 中軌平空 (SHORT_EXIT_KC_MID_BREACH) ──
                # 只有在大週期均線已轉弱/金叉 (ema_fast > ema_slow) 且波段回吐時，突破中軌才執行保護性落袋平空
                if ema_fast > ema_slow and (c_curr["close"] > kc_mid or quote > kc_mid) and (peak_roe >= max(0.6 * atr_pct, 0.004) and unrealized_pct <= peak_roe * 0.60):
                    return True, "SHORT_EXIT_KC_MID_BREACH", {
                        **meta,
                        "cause": f"空單均線轉弱且突破收復KC中軌 (close={c_curr['close']:.4f} > kc_mid={kc_mid:.4f})，波段回吐利潤鎖定平空！",
                    }

                # ── 觸發器 1-C：MA5 關鍵防守線突破且轉折平空 (SHORT_EXIT_MA5_REVERSAL，AGENTS.md 模組五) ──
                ma5_curr = float(curr_k.get("ma5", c_curr["close"]))
                ma5_prev = float(prev_k.get("ma5", ma5_curr))
                if ema_fast > ema_slow and c_curr["close"] > kc_low and c_curr["close"] > ma5_curr and ma5_curr > ma5_prev and peak_roe >= max(0.6 * atr_pct, 0.004) and unrealized_pct <= peak_roe * 0.60:
                    return True, "SHORT_EXIT_MA5_REVERSAL", {
                        **meta,
                        "cause": f"空單收盤漲回下軌內側且突破MA5防守線 (close={c_curr['close']:.4f} > ma5={ma5_curr:.4f})，動能衰竭平空落袋！",
                    }

                # 趨勢不變續抱：大週期空頭 (ema_fast <= ema_slow) 維持續抱！
                if ema_fast <= ema_slow:
                    return False, "VETO_HOLD", {
                        **meta,
                        "cause": f"空單大週期趨勢依然向下且受制於中軌 (kc_middle_slope={slope:.6f}, ema_fast={ema_fast:.5f} <= ema_slow={ema_slow:.5f})，未到空頭趨勢末端，維持死抱！",
                    }

        # ── 3. 趨勢終結離場（防反轉擴大） ──
        if side == "LONG":
            if ema_fast >= ema_slow:
                return False, "VETO_HOLD", {
                    **meta,
                    "cause": f"多單大週期均線依然多頭 (ema_fast={ema_fast:.5f} >= ema_slow={ema_slow:.5f})，均線未死叉前嚴禁判定趨勢終結，維持續抱！",
                }

            two_bars_below_ema_slow = False
            if raw_df is not None and len(raw_df) >= 2:
                bar1_close = float(raw_df.iloc[-1]["close"])
                bar2_close = float(raw_df.iloc[-2]["close"])
                two_bars_below_ema_slow = (bar1_close < ema_slow) and (bar2_close < ema_slow)

            if two_bars_below_ema_slow:
                # 滿足均線死叉且連續兩根實質跌破慢線，確認趨勢終結離場
                return True, "TREND_TERMINATED_SLOW_BREAK", {
                    **meta,
                    "cause": "均線死叉且連續兩根收盤實質破位EMA40慢線，多頭趨勢徹底終結平倉",
                }

            return False, "VETO_HOLD", {
                **meta,
                "cause": "未達連續兩根收破EMA40慢線，盤整洗盤一律抱牢，嚴禁平多！",
            }

        elif side == "SHORT":
            if ema_fast <= ema_slow:
                return False, "VETO_HOLD", {
                    **meta,
                    "cause": f"空單大週期均線依然空頭 (ema_fast={ema_fast:.5f} <= ema_slow={ema_slow:.5f})，均線未金叉前嚴禁判定趨勢終結，維持續抱！",
                }

            two_bars_above_ema_slow = False
            if raw_df is not None and len(raw_df) >= 2:
                bar1_close = float(raw_df.iloc[-1]["close"])
                bar2_close = float(raw_df.iloc[-2]["close"])
                two_bars_above_ema_slow = (bar1_close > ema_slow) and (bar2_close > ema_slow)

            if two_bars_above_ema_slow:
                # 滿足均線金叉且連續兩根實質突破慢線，確認趨勢終結離場
                return True, "TREND_TERMINATED_SLOW_BREAK", {
                    **meta,
                    "cause": "均線金叉且連續兩根收盤實質破位EMA40慢線，空頭趨勢徹底終結平倉",
                }

            return False, "VETO_HOLD", {
                **meta,
                "cause": "未達連續兩根收破EMA40慢線，反彈洗盤一律抱牢，嚴禁平空！",
            }


class EntryGate:
    """
    進場裁決唯一裁決官：
    1. 禁止追急衝針（長影線 + 乖離過大一票否決，防 09:12 買在天花板）
    2. 抓準 09:45 盤整突破的「主升浪起點」
    3. 主升浪中途「回踩收腳延續開倉（模式三，吃 10:00 第二波主升段）」
    """

    @staticmethod
    def evaluate(
        signal_type: str,
        market_data: Union[MarketDataSnapshot, Dict[str, Any]],
        last_exit_context: Optional[Dict[str, Any]] = None,
        atr: Optional[float] = None,
        kc_slope: Optional[float] = None,
    ) -> Tuple[bool, str, Dict[str, Any]]:
        if not signal_type or not market_data:
            return False, "VETO_NO_SIGNAL: 無有效訊號", {}

        if "LONG" in signal_type.upper():
            signal_side = "LONG"
        elif "SHORT" in signal_type.upper():
            signal_side = "SHORT"
        elif any(b in signal_type.upper() for b in ("BREAKOUT", "CONTINUATION", "OUTSIDE", "BURST")):
            # 支援 CONFIRMED_2BAR_BREAKOUT 等未帶 LONG/SHORT 的訊號，自動由最新價或均線推斷
            quote_val = float(market_data.current_price if isinstance(market_data, MarketDataSnapshot) else market_data.get("current_price", 0.0))
            kc_up_val = float(market_data.kc_upper if isinstance(market_data, MarketDataSnapshot) else market_data.get("kc_upper", quote_val * 1.01))
            kc_low_val = float(market_data.kc_lower if isinstance(market_data, MarketDataSnapshot) else market_data.get("kc_lower", quote_val * 0.99))
            ema_f = float(market_data.ema_fast if isinstance(market_data, MarketDataSnapshot) else market_data.get("ema_fast", 0.0))
            ema_s = float(market_data.ema_slow if isinstance(market_data, MarketDataSnapshot) else market_data.get("ema_slow", 0.0))
            if quote_val > kc_up_val or (isinstance(market_data, dict) and market_data.get("side") == "LONG") or ema_f > ema_s:
                signal_side = "LONG"
            elif quote_val < kc_low_val or (isinstance(market_data, dict) and market_data.get("side") == "SHORT") or ema_f < ema_s:
                signal_side = "SHORT"
            else:
                signal_side = "UNKNOWN"
        else:
            signal_side = "UNKNOWN"

        if signal_side == "UNKNOWN":
            return False, f"VETO_INVALID_SIDE: 未知訊號方向 {signal_type}", {}

        if isinstance(market_data, MarketDataSnapshot):
            quote = market_data.current_price
            atr_val = market_data.atr
            slope = market_data.kc_middle_slope
            curr_k = market_data.curr_k
            prev_k = market_data.prev_k
            ma5 = market_data.ma5
            ma15 = market_data.ma15
            ema_fast = market_data.ema_fast
            ema_slow = market_data.ema_slow
            ma_spread_pct = market_data.ma_spread_pct
            kc_up = market_data.kc_upper
            kc_mid = market_data.kc_middle
            kc_low = market_data.kc_lower
            raw_df = market_data.raw_df
        else:
            quote = float(market_data.get("current_price", 0.0))
            atr_val = float(atr or market_data.get("atr", quote * 0.01))
            slope = float(kc_slope or market_data.get("kc_middle_slope", 0.0))
            curr_k = market_data.get("curr_k", {})
            prev_k = market_data.get("prev_k", {})
            ma5 = float(market_data.get("ma5", quote))
            ma15 = float(market_data.get("ma15", quote))
            kc_mid = float(market_data.get("kc_middle", quote))
            ema_fast = float(market_data.get("ema_fast", ma15))
            ema_slow = float(market_data.get("ema_slow", kc_mid))
            ma_spread_pct = float(market_data.get("ma_spread_pct", 0.1))
            kc_up = float(market_data.get("kc_upper", quote * 1.01))
            kc_low = float(market_data.get("kc_lower", quote * 0.99))
            raw_df = market_data.get("raw_df")

        meta = {
            "signal_type": signal_type,
            "side": signal_side,
            "quote": quote,
            "atr": atr_val,
            "kc_slope": slope,
            "ema_fast": ema_fast,
            "ema_slow": ema_slow,
            "ma_spread_pct": ma_spread_pct,
        }

        c_open = curr_k.get("open", quote)
        c_close = curr_k.get("close", quote)
        c_high = curr_k.get("high", max(c_open, c_close))
        c_low = curr_k.get("low", min(c_open, c_close))
        body = abs(c_close - c_open)
        # ══════════════════════════════════════════════════════════════════
        # 門禁 0：順勢交易鐵律（大趨勢一票否決）
        # 空頭趨勢嚴禁開多：當 KC 中軌向下 (slope < 0) 或 EMA空頭排列 (ema_fast < ema_slow)，100% 阻斷開多！
        # 多頭趨勢嚴禁開空：當 KC 中軌向上 (slope > 0) 或 EMA多頭排列 (ema_fast > ema_slow)，100% 阻斷開空！
        # ══════════════════════════════════════════════════════════════════
        if signal_side == "LONG" and (slope < 0 or ema_fast < ema_slow):
            return False, "BLOCKED_LONG_ON_BEARISH_TREND", {
                **meta,
                "cause": f"順勢交易鐵律一票否決: 大週期為空頭趨勢 (kc_slope={slope:.6f}, ema_fast={ema_fast:.4f} < ema_slow={ema_slow:.4f})，嚴禁在空頭主浪中開多接飛刀！",
            }
        if signal_side == "SHORT" and (slope > 0 or ema_fast > ema_slow):
            return False, "BLOCKED_SHORT_ON_BULLISH_TREND", {
                **meta,
                "cause": f"順勢交易鐵律一票否決: 大週期為多頭趨勢 (kc_slope={slope:.6f}, ema_fast={ema_fast:.4f} > ema_slow={ema_slow:.4f})，嚴禁在多頭主浪中摸頂開空！",
            }

        # ─── 均線斜率（EMA Slope）絕對順勢一票否決（Hard Veto） ───
        # 做多鐵律：若 EMA15 下拐或走平 (ema15[-1] <= ema15[-2])，一律 100% 阻斷開多接飛刀！
        # 做空鐵律：若 EMA15 上翹或走平 (ema15[-1] >= ema15[-2])，一律 100% 阻斷開空摸頂！
        prev_ma15_val = float(prev_k.get("ma15", prev_k.get("ema_fast", prev_k.get("ema15", 0.0)))) if prev_k else 0.0
        if prev_ma15_val > 0:
            ema15_slope = ema_fast - prev_ma15_val
            if signal_side == "LONG" and (ema_fast <= prev_ma15_val or ema15_slope <= 0):
                return False, "BLOCKED_LONG_ON_EMA_FALLING", {
                    **meta,
                    "cause": f"均線下拐嚴禁開多接刀: EMA15當前={ema_fast:.5f} <= 前根={prev_ma15_val:.5f} (斜率={ema15_slope:.6f} <= 0)！",
                }
            if signal_side == "SHORT" and (ema_fast >= prev_ma15_val or ema15_slope >= 0):
                return False, "BLOCKED_SHORT_ON_EMA_RISING", {
                    **meta,
                    "cause": f"均線上翹嚴禁開空摸頂: EMA15當前={ema_fast:.5f} >= 前根={prev_ma15_val:.5f} (斜率={ema15_slope:.6f} >= 0)！",
                }

        # ══════════════════════════════════════════════════════════════════
        # 門禁 1：禁止追急衝針（長影線一票否決，防 09:12 買在天花板 / 地板追空）
        # ══════════════════════════════════════════════════════════════════
        dist_to_ema_slow = abs(c_close - ema_slow)
        ma5_curr = float(curr_k["ma5"]) if curr_k.get("ma5") is not None else float(curr_k.get("close", quote))
        ma5_prev = float(prev_k["ma5"]) if prev_k.get("ma5") is not None else float(prev_k.get("close", ma5_curr))

        if signal_side == "LONG":
            upper_wick = c_high - max(c_open, c_close)
            if upper_wick >= body and upper_wick >= 0.25 * atr_val:
                return False, "VETO_WICK_REJECTION", {
                    **meta,
                    "cause": f"長上影線一票否決: 上影線({upper_wick:.4f}) >= 實體({body:.4f}) 且 >= 0.25*ATR({0.25*atr_val:.4f})，嚴防買在急衝頂部！",
                }
            # 距外軌過度延展防追高 (距 KC 上軌外超過 3.0 ATR 嚴禁追多，符合 AGENTS.md 規範)
            if c_close > (kc_up + 3.0 * atr_val):
                return False, "VETO_OVER_EXTENSION_FROM_SLOW_MA", {
                    **meta,
                    "cause": f"破軌乖離過大一票否決: 收盤={c_close:.4f} 超過上軌+3.0ATR({kc_up + 3.0*atr_val:.4f})，嚴禁追急衝天花板！",
                }
            # MA5 向上斜率防護：開多單時，MA5 必須呈現絕對向上斜率（curr_ma5 > prev_ma5）
            if ma5_curr <= ma5_prev:
                return False, "VETO_MA5_SLOPE_NOT_UPWARD", {
                    **meta,
                    "cause": f"MA5 走平或向下 (ma5_curr={ma5_curr:.6f} <= ma5_prev={ma5_prev:.6f})，均線未向上發散，嚴禁開多！",
                }
        elif signal_side == "SHORT":
            lower_wick = min(c_open, c_close) - c_low
            if lower_wick >= body and lower_wick >= 0.25 * atr_val:
                return False, "VETO_WICK_REJECTION", {
                    **meta,
                    "cause": f"長下影線一票否決: 下影線({lower_wick:.4f}) >= 實體({body:.4f}) 且 >= 0.25*ATR({0.25*atr_val:.4f})，嚴防空在地板針尖！",
                }
            # 距外軌過度延展防追低 (距 KC 下軌外超過 3.0 ATR 嚴禁追空，符合 AGENTS.md 規範)
            if c_close < (kc_low - 3.0 * atr_val):
                return False, "VETO_OVER_EXTENSION_FROM_SLOW_MA", {
                    **meta,
                    "cause": f"破軌乖離過大一票否決: 收盤={c_close:.4f} 超過下軌-3.0ATR({kc_low - 3.0*atr_val:.4f})，嚴禁追急跌地板！",
                }
            # MA5 向下斜率防護（鐵律：MA線並沒有往下嚴禁開空）：開空單時，MA5 必須呈現絕對向下斜率（curr_ma5 < prev_ma5）
            if ma5_curr >= ma5_prev:
                return False, "VETO_MA5_SLOPE_NOT_DOWNWARD", {
                    **meta,
                    "cause": f"MA5 走平或向上 (ma5_curr={ma5_curr:.6f} >= ma5_prev={ma5_prev:.6f})，均線未向下發散，嚴禁開空！",
                }

        # ─── 通道相對位置 (KC %B) 與破軌乖離門禁 ───
        bw = kc_up - kc_low
        kc_pos = (quote - kc_low) / bw if bw > 0 else 0.5
        is_breakout_mode = any(b in signal_type.upper() for b in ("BREAKOUT", "CONTINUATION", "OUTSIDE", "BURST", "BREAKDOWN"))
        is_late_chase = any(b in signal_type.upper() for b in ("CONTINUATION", "CONFIRMED_2BAR"))
        max_ext_allowed = 0.5 if is_late_chase else 3.0

        if not is_breakout_mode:
            # 僅通道內震盪或 MA_CROSSOVER 模式：檢查 KC %B 天花板與地板
            if signal_side == "LONG" and kc_pos > 0.80:
                return False, "VETO_KC_POS_CEILING", {
                    **meta,
                    "cause": f"價格貼近通道天花板 (KC_Pos={kc_pos:.2f} > 0.80)，通道內或交叉開多拒絕追高！",
                }
            elif signal_side == "SHORT" and kc_pos < 0.20:
                return False, "VETO_KC_POS_FLOOR", {
                    **meta,
                    "cause": f"價格貼近通道地板 (KC_Pos={kc_pos:.2f} < 0.20)，通道內或交叉開空拒絕追低！",
                }
            # 中軌平衡震盪區禁開門禁 (「且已在中軌不要再開空/開多」)
            if 0.35 <= kc_pos <= 0.65:
                return False, "VETO_AT_KC_MIDDLE", {
                    **meta,
                    "cause": f"價格位於KC中軌附近 (KC_Pos={kc_pos:.2f})，非破軌區且處通道中性區，嚴禁開倉！",
                }
        else:
            # 破軌模式：收盤自然在軌外 (KC_Pos >= 1.0)，改由「距同側外軌乖離」與「距 MA5 不得超過 1.0 ATR」獨立把關
            if signal_side == "LONG":
                band_extension = max(quote, c_close) - kc_up
                if band_extension > max_ext_allowed * atr_val:
                    return False, "LATE_CHASE_BAND_EXTENSION", {
                        **meta,
                        "cause": f"破軌距外軌過度延展一票否決: 乖離={band_extension:.4f} > {max_ext_allowed}*ATR({max_ext_allowed*atr_val:.4f})，拒絕追高！",
                    }
                if is_late_chase:
                    ma5_val = float(curr_k.get("ma5", c_close))
                    ma5_dist = abs(quote - ma5_val)
                    if ma5_dist > 1.0 * atr_val:
                        return False, "LATE_CHASE_MA5_DISTANCE", {
                            **meta,
                            "cause": f"破軌距MA5過度延展一票否決: 距MA5={ma5_dist:.4f} > 1.0*ATR({1.0*atr_val:.4f})，拒絕追高！",
                        }
            elif signal_side == "SHORT":
                band_extension = kc_low - min(quote, c_close)
                if band_extension > max_ext_allowed * atr_val:
                    return False, "LATE_CHASE_BAND_EXTENSION", {
                        **meta,
                        "cause": f"破軌距外軌過度延展一票否決: 乖離={band_extension:.4f} > {max_ext_allowed}*ATR({max_ext_allowed*atr_val:.4f})，拒絕追低！",
                    }
                if is_late_chase:
                    ma5_val = float(curr_k.get("ma5", c_close))
                    ma5_dist = abs(quote - ma5_val)
                    if ma5_dist > 1.0 * atr_val:
                        return False, "LATE_CHASE_MA5_DISTANCE", {
                            **meta,
                            "cause": f"破軌距MA5過度延展一票否決: 距MA5={ma5_dist:.4f} > 1.0*ATR({1.0*atr_val:.4f})，拒絕追低！",
                        }

        # ══════════════════════════════════════════════════════════════════
        # 門禁 2：抓準 09:45 盤整突破的「主升浪起點」/ 主跌浪起點
        # ══════════════════════════════════════════════════════════════════
        if signal_side == "LONG":
            is_breakout_candle = (c_close > kc_up) and (c_close > c_open)
            is_above_emas = (c_close > ema_fast) and (c_close > ema_slow)
            is_spreading = (ema_fast > ema_slow) and (slope >= 0) and ((ma_spread_pct >= 0.05) or (c_close > kc_up))
            if is_breakout_candle and is_above_emas and is_spreading:
                ret_reason = "CONFIRMED_2BAR_BREAKOUT" if "CONFIRMED_2BAR_BREAKOUT" in signal_type.upper() else "BREAKOUT_MAIN_WAVE_START"
                return True, ret_reason, {
                    **meta,
                    "cause": "主升浪起點突破放行: 實體紅K衝破KC上軌且站穩EMA15/40，盤整發散起漲點！",
                }

        elif signal_side == "SHORT":
            is_breakdown_candle = (c_close < kc_low) and (c_close < c_open)
            is_below_emas = (c_close < ema_fast) and (c_close < ema_slow)
            is_spreading = (ema_fast < ema_slow) and (slope <= 0) and ((ma_spread_pct >= 0.05) or (c_close < kc_low))
            if is_breakdown_candle and is_below_emas and is_spreading:
                ret_reason = "CONFIRMED_2BAR_BREAKOUT" if "CONFIRMED_2BAR_BREAKOUT" in signal_type.upper() else "BREAKOUT_MAIN_WAVE_START"
                return True, ret_reason, {
                    **meta,
                    "cause": "主跌浪起點突破放行: 實體黑K跌破KC下軌且壓制EMA15/40，盤整發散起跌點！",
                }

        # ══════════════════════════════════════════════════════════════════
        # 門禁 3：主升/主跌浪中途「回踩收腳 / 反彈遇阻延續開倉（模式三）」
        # ══════════════════════════════════════════════════════════════════
        if signal_side == "LONG":
            is_bullish_trend = (ema_fast > ema_slow) and (c_close > ema_slow) and (slope >= 0)
            recent_lows = [curr_k.get("low", quote), prev_k.get("low", quote)]
            touched_ema_pocket = any((l <= ema_fast * 1.005 and l >= ema_slow * 0.995) for l in recent_lows)
            reclaimed_ema_fast = (c_close > ema_fast) and (c_close > c_open)
            moderate_extension = dist_to_ema_slow <= 2.0 * atr_val

            if is_bullish_trend and (touched_ema_pocket or c_close >= kc_up) and reclaimed_ema_fast and moderate_extension:
                return True, "PULLBACK_RECLAIM_CONTINUATION", {
                    **meta,
                    "cause": "回踩收腳延續開倉放行: 回踩EMA15/40均線帶有守或強勢推進，收紅K重新站穩EMA15發動第二波！",
                }

        elif signal_side == "SHORT":
            is_bearish_trend = (ema_fast < ema_slow) and (c_close < ema_slow) and (slope <= 0)
            recent_highs = [curr_k.get("high", quote), prev_k.get("high", quote)]
            touched_ema_pocket = any((h >= ema_fast * 0.995 and h <= ema_slow * 1.005) for h in recent_highs)
            rejected_ema_fast = (c_close < ema_fast) and (c_close < c_open)
            moderate_extension = dist_to_ema_slow <= 2.0 * atr_val

            if is_bearish_trend and (touched_ema_pocket or c_close <= kc_low) and rejected_ema_fast and moderate_extension:
                return True, "PULLBACK_RECLAIM_CONTINUATION", {
                    **meta,
                    "cause": "反彈遇阻延續開空放行: 均線空頭排列，收黑K壓制在EMA15/40之下發動主跌段！",
                }

        # ══════════════════════════════════════════════════════════════════
        # 門禁 3-B：均線金叉/死叉 + 突破/跌破中軌提早開倉 (ENTRY_LONG_EMA_CROSS_MID / ENTRY_SHORT_EMA_CROSS_MID)
        # ══════════════════════════════════════════════════════════════════
        if signal_side == "LONG":
            is_ema_cross_mid = (ema_fast >= ema_slow) and (c_close >= kc_mid or quote >= kc_mid) and (c_close >= c_open)
            if is_ema_cross_mid:
                return True, "ENTRY_LONG_EMA_CROSS_MID", {
                    **meta,
                    "cause": "EMA15金叉EMA40且站上KC中軌提早放行開多，領先外軌追高天花板！",
                }
        elif signal_side == "SHORT":
            is_ema_cross_mid = (ema_fast <= ema_slow) and (c_close <= kc_mid or quote <= kc_mid) and (c_close <= c_open)
            if is_ema_cross_mid:
                return True, "ENTRY_SHORT_EMA_CROSS_MID", {
                    **meta,
                    "cause": "EMA15死叉EMA40且跌破KC中軌提早放行開空，領先外軌追低地板！",
                }

        # 門禁 4：非起點或延續開倉時，若距 EMA40 慢線過大一票否決
        if dist_to_ema_slow > 0.8 * atr_val:
            return False, "VETO_OVER_EXTENSION_FROM_SLOW_MA", {
                **meta,
                "cause": f"乖離過大一票否決: 距EMA40慢線={dist_to_ema_slow:.4f} > 0.8*ATR({0.8*atr_val:.4f})，嚴禁非起點追急衝！",
            }

        return False, "VETO_ENTRY_CONDITIONS_NOT_MET", {
            **meta,
            "cause": "未達有效主升浪/主跌浪突破或均線延續條件",
        }
