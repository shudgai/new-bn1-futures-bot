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

        # ── 1. 階梯鎖利 / 保本底線 (PROFIT_LOCK) ──
        # 達標 10% 鎖 7% (利潤達到 10% 以上，保證留存 70% 利潤)
        if peak_roe >= 0.10:
            min_retained_pnl = peak_roe * 0.70
            if unrealized_pct <= min_retained_pnl:
                return True, "PROFIT_LOCK", {
                    **meta,
                    "cause": f"10%鎖7%階梯利潤觸發: peak_roe={peak_roe:.2%}, curr={unrealized_pct:.2%} <= floor={min_retained_pnl:.2%}",
                }

        atr_pct = (atr_val / entry_p) if entry_p > 0 else 0.01

        # 帳戶硬止損 (3.0 ATR 防穿倉)
        hard_loss_threshold = -3.0 * atr_pct
        if unrealized_pct <= hard_loss_threshold:
            return True, "PROFIT_LOCK", {
                **meta,
                "cause": f"硬止損避險: loss={unrealized_pct:.2%} <= threshold={hard_loss_threshold:.2%}",
            }

        # ── 2. 純數學真峰谷力竭 (TRUE_PEAK / TRUE_BOTTOM) ──
        # 需防單棒毛刺洗盤：外軌大黑吞噬 或 三點確定性樞紐確立
        if raw_df is not None and len(raw_df) >= 3:
            c_curr = raw_df.iloc[-1]
            c_prev = raw_df.iloc[-2]
            c_prev2 = raw_df.iloc[-3] if len(raw_df) >= 3 else c_prev

            if side == "LONG":
                # 外軌大陰吞噬真峰頂：在高位（高於 KC 上軌或獲利中）收出實體巨黑棒跌破前棒低點
                is_bearish_engulfing = (c_curr["close"] < c_curr["open"]) and (c_curr["close"] < c_prev["low"]) and ((c_curr["open"] - c_curr["close"]) >= 0.5 * atr_val)
                # 三點真峰頂：中間棒 High 明確高於前後兩棒，且當前棒已跌破中間棒實體中點
                is_pivot_peak = (c_prev["high"] > c_prev2["high"]) and (c_prev["high"] > c_curr["high"])
                c_prev_mid = (c_prev["open"] + c_prev["close"]) / 2.0
                is_pivot_exhaustion = (c_curr["close"] < c_prev_mid) and (c_curr["close"] < c_curr["open"])

                if (is_bearish_engulfing or (is_pivot_peak and is_pivot_exhaustion)) and unrealized_pct > 0.005:
                    return True, "TRUE_PEAK", {
                        **meta,
                        "cause": f"純數學真峰頂確立: engulfing={is_bearish_engulfing}, pivot={is_pivot_peak}",
                    }

            elif side == "SHORT":
                # 外軌大陽吞噬真谷底：在低位收出實體巨紅棒衝破前棒高點
                is_bullish_engulfing = (c_curr["close"] > c_curr["open"]) and (c_curr["close"] > c_prev["high"]) and ((c_curr["close"] - c_curr["open"]) >= 0.5 * atr_val)
                # 三點真谷底：中間棒 Low 明確低於前後兩棒，且當前棒已衝破中間棒實體中點
                is_pivot_bottom = (c_prev["low"] < c_prev2["low"]) and (c_prev["low"] < c_curr["low"])
                c_prev_mid = (c_prev["open"] + c_prev["close"]) / 2.0
                is_pivot_exhaustion = (c_curr["close"] > c_prev_mid) and (c_curr["close"] > c_curr["open"])

                if (is_bullish_engulfing or (is_pivot_bottom and is_pivot_exhaustion)) and unrealized_pct > 0.005:
                    return True, "TRUE_BOTTOM", {
                        **meta,
                        "cause": f"純數學真谷底確立: engulfing={is_bullish_engulfing}, pivot={is_pivot_bottom}",
                    }

        # ── 3. 盤整黏合嚴禁平多（防 09:35 割肉）與主升浪一單到底 ──
        # 鐵律：只要價格沒有實質「連續兩根收破 EMA40（慢線）」，一律禁止平多（強制 VETO_HOLD）！
        if side == "LONG":
            two_bars_below_ema_slow = False
            if raw_df is not None and len(raw_df) >= 2:
                bar1_close = float(raw_df.iloc[-1]["close"])
                bar2_close = float(raw_df.iloc[-2]["close"])
                two_bars_below_ema_slow = (bar1_close < ema_slow) and (bar2_close < ema_slow)

            if not two_bars_below_ema_slow:
                return False, "VETO_HOLD", {
                    **meta,
                    "cause": "未達連續兩根收破EMA40慢線，盤整洗盤一律抱牢，嚴禁平多！",
                }

        elif side == "SHORT":
            two_bars_above_ema_slow = False
            if raw_df is not None and len(raw_df) >= 2:
                bar1_close = float(raw_df.iloc[-1]["close"])
                bar2_close = float(raw_df.iloc[-2]["close"])
                two_bars_above_ema_slow = (bar1_close > ema_slow) and (bar2_close > ema_slow)

            if not two_bars_above_ema_slow:
                return False, "VETO_HOLD", {
                    **meta,
                    "cause": "未達連續兩根收破EMA40慢線，反彈洗盤一律抱牢，嚴禁平空！",
                }

        # 滿足連續兩根實質跌破慢線，確認趨勢終結離場
        return True, "TREND_TERMINATED_SLOW_BREAK", {
            **meta,
            "cause": "連續兩根收盤實質破位EMA40慢線，多/空趨勢徹底告一段落平倉",
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

        signal_side = "LONG" if "LONG" in signal_type.upper() else "SHORT" if "SHORT" in signal_type.upper() else "UNKNOWN"
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
        # 門禁 1：禁止追急衝針（長影線一票否決，防 09:12 買在天花板 / 地板追空）
        # ══════════════════════════════════════════════════════════════════
        dist_to_ema_slow = abs(c_close - ema_slow)
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

        # ══════════════════════════════════════════════════════════════════
        # 門禁 2：抓準 09:45 盤整突破的「主升浪起點」/ 主跌浪起點
        # ══════════════════════════════════════════════════════════════════
        if signal_side == "LONG":
            is_breakout_candle = (c_close > kc_up) and (c_close > c_open)
            is_above_emas = (c_close > ema_fast) and (c_close > ema_slow)
            is_spreading = (ma_spread_pct >= 0.05) or (ema_fast > ema_slow and slope > 0)
            if is_breakout_candle and is_above_emas and is_spreading:
                return True, "BREAKOUT_MAIN_WAVE_START", {
                    **meta,
                    "cause": "主升浪起點突破放行: 實體紅K衝破KC上軌且站穩EMA15/40，盤整發散起漲點！",
                }

        elif signal_side == "SHORT":
            is_breakdown_candle = (c_close < kc_low) and (c_close < c_open)
            is_below_emas = (c_close < ema_fast) and (c_close < ema_slow)
            is_spreading = (ma_spread_pct >= 0.05) or (ema_fast < ema_slow and slope < 0)
            if is_breakdown_candle and is_below_emas and is_spreading:
                return True, "BREAKOUT_MAIN_WAVE_START", {
                    **meta,
                    "cause": "主跌浪起點突破放行: 實體黑K跌破KC下軌且壓制EMA15/40，盤整發散起跌點！",
                }

        # ══════════════════════════════════════════════════════════════════
        # 門禁 3：主升/主跌浪中途「回踩收腳 / 反彈遇阻延續開倉（模式三）」
        # ══════════════════════════════════════════════════════════════════
        if signal_side == "LONG":
            is_bullish_trend = (ema_fast > ema_slow) and (c_close > ema_slow)
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
            is_bearish_trend = (ema_fast < ema_slow) and (c_close < ema_slow)
            recent_highs = [curr_k.get("high", quote), prev_k.get("high", quote)]
            touched_ema_pocket = any((h >= ema_fast * 0.995 and h <= ema_slow * 1.005) for h in recent_highs)
            rejected_ema_fast = (c_close < ema_fast) and (c_close < c_open)
            moderate_extension = dist_to_ema_slow <= 2.0 * atr_val

            if is_bearish_trend and (touched_ema_pocket or c_close <= kc_low) and rejected_ema_fast and moderate_extension:
                return True, "PULLBACK_RECLAIM_CONTINUATION", {
                    **meta,
                    "cause": "反彈遇阻延續開空放行: 均線空頭排列，收黑K壓制在EMA15/40之下發動主跌段！",
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
