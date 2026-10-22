"""
core/gates/unified_pipeline_gates.py
====================================
三層流水線之【裁決層 (Pure Math Gates)】
依據 2026/10/21 盤面覆盤與最新防洗/防追急衝標準重構：
- 解決「買在急衝頂部、平在盤整底部、主升浪全程踏空」的問題。
"""
import math
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

    OPPOSITE_BREACH_MIN_DEPTH_ATR = 0.5

    @staticmethod
    def _closed_bar_opposite_breach(raw_df: Any, side: str) -> Dict[str, Any]:
        """以最近一根已收線 K（raw_df.iloc[-2]，最後一根為盤中棒）驗證反側外軌收線擊穿深度。"""
        result: Dict[str, Any] = {"valid": False, "confirmed": False, "depth_atr": None}
        try:
            if raw_df is None or len(raw_df) < 2:
                return result
            bar = raw_df.iloc[-2]
            close = float(bar["close"])
            atr_v = float(bar["atr"])
            band = float(bar["kc_lower"] if side == "LONG" else bar["kc_upper"])
        except (KeyError, IndexError, TypeError, ValueError, AttributeError):
            return result
        if not all(math.isfinite(v) and v > 0 for v in (close, atr_v, band)):
            return result
        depth = (band - close) if side == "LONG" else (close - band)
        depth_atr = depth / atr_v
        result.update(
            valid=True,
            depth_atr=round(depth_atr, 4),
            closed_close=close,
            closed_band=band,
            confirmed=depth_atr >= ExitGate.OPPOSITE_BREACH_MIN_DEPTH_ATR,
        )
        return result

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
            macro_trend = str(market_data.macro_trend or "NEUTRAL").upper()
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
            macro_trend = str(market_data.get("macro_trend") or "NEUTRAL").upper()

        side = str(pos.get("side", "")).upper()
        entry_p = float(pos.get("entry_price") or 0.0)
        if entry_p <= 0 or quote <= 0:
            return False, "VETO_HOLD: 報價無效", {}

        # 損益計算 (價格波幅與真實合約 ROE)
        unrealized_pct = ((quote - entry_p) / entry_p) if side == "LONG" else ((entry_p - quote) / entry_p)
        # ── 計算真實倉位合約 ROE ──
        # leverage 可自持倉配置讀取，預設 5.0
        leverage = float(pos.get("leverage", 5.0))

        # 價格波幅峰值 (peak_price_move)
        peak_price_move = float(pos.get("peak_price_move") or pos.get("highest_pnl_pct") or 0.0)
        if peak_price_move <= 0 and pos.get("peak_roe"):
            peak_price_move = float(pos["peak_roe"]) / leverage
        peak_price_move = max(peak_price_move, unrealized_pct)
        pos["peak_price_move"] = peak_price_move

        unrealized_roe = unrealized_pct * leverage
        peak_roe = peak_price_move * leverage
        pos["peak_roe"] = peak_roe
        pos["unrealized_roe"] = unrealized_roe

        meta = {
            "unrealized_pct": unrealized_pct,
            "unrealized_roe": unrealized_roe,
            "peak_roe": peak_roe,
            "peak_price_move": peak_price_move,
            "leverage": leverage,
            "quote": quote,
            "atr": atr_val,
            "kc_slope": slope,
            "ema_fast": ema_fast,
            "ema_slow": ema_slow,
        }

        atr_pct = (atr_val / entry_p) if entry_p > 0 else 0.01

        # ── 1. 動態階梯鎖利 (PROFIT_LOCK) ──
        if peak_roe >= 0.15:
            min_retained_roe = peak_roe * 0.80  # 達標 15% 鎖 80%
            if unrealized_roe <= min_retained_roe:
                return True, "PROFIT_LOCK", {
                    **meta,
                    "cause": f"15%鎖80%階梯觸發 (當前ROE: {unrealized_roe*100:.2f}%, 底線: {min_retained_roe*100:.2f}%)",
                }
        elif peak_roe >= 0.10:
            min_retained_roe = peak_roe * 0.70  # 達標 10% 鎖 70%
            if unrealized_roe <= min_retained_roe:
                return True, "PROFIT_LOCK", {
                    **meta,
                    "cause": f"10%鎖70%階梯觸發 (當前ROE: {unrealized_roe*100:.2f}%, 底線: {min_retained_roe*100:.2f}%)",
                }

        # ── 0. 反側外軌破位異常緊急平倉 (OPPOSITE_KC_BAND_BREACH，AGENTS.md 最優先防護) ──
        # 2026-10-22 使用者授權（方案 A）：大週期仍與持倉同向時，盤中毛刺擊穿反側外軌不平倉；
        # 須最近一根已收線 1m 收在反側外軌外且深度 >= 0.5 ATR，且最新報價仍在外軌外才放行。
        # 大週期已不同向（中性/反向）或資料無效 → 維持即時平倉。硬止損與其他出口照常往下評估。
        breached = (side == "LONG" and quote < kc_low) or (side == "SHORT" and quote > kc_up)
        if breached:
            macro_aligned = (
                (side == "LONG" and macro_trend == "BULLISH")
                or (side == "SHORT" and macro_trend == "BEARISH")
            )
            confirm = ExitGate._closed_bar_opposite_breach(raw_df, side)
            band = kc_low if side == "LONG" else kc_up
            if not macro_aligned or confirm["confirmed"] or not confirm["valid"]:
                return True, "OPPOSITE_KC_BAND_BREACH", {
                    **meta,
                    "macro_trend": macro_trend,
                    "closed_bar_breach": confirm,
                    "cause": (
                        f"{'多單跌破反側KC下軌' if side == 'LONG' else '空單突破反側KC上軌'}異常逃命: "
                        f"quote={quote:.6f} band={band:.6f} macro={macro_trend} "
                        f"closed_depth_atr={confirm['depth_atr']}"
                    ),
                }
            meta["opposite_breach_veto"] = {
                "reason": "VETO_OPPOSITE_BREACH_MACRO_ALIGNED_UNCONFIRMED",
                "macro_trend": macro_trend,
                "closed_bar_breach": confirm,
                "quote": quote,
                "band": band,
            }

        # 帳戶硬止損 (3.0 ATR 防穿倉)
        hard_loss_threshold = -3.0 * atr_pct
        if unrealized_pct <= hard_loss_threshold:
            return True, "PROFIT_LOCK", {
                **meta,
                "cause": f"硬止損避險: loss={unrealized_pct:.2%} <= threshold={hard_loss_threshold:.2%}",
            }

        # ── 2. 純數學真峰谷力竭 (TRUE_PEAK / TRUE_BOTTOM) 與波段防守 ──
        # 依據 AGENTS.md 授權標準重構：已收線多棒三點樞紐回溯判定，享受最高出場優先權！
        # 嚴防「衝高賺錢死抱不放，坐過山車直到反側破軌慘遭割肉」
        if raw_df is not None and len(raw_df) >= 3:
            # 支援即時 3 根微型 DataFrame (測試常態) 以及實盤長序列 (回溯已收線)
            c_curr = raw_df.iloc[-1]
            c_prev = raw_df.iloc[-2]
            c_prev2 = raw_df.iloc[-3] if len(raw_df) >= 3 else c_prev
            ma5_curr = float(curr_k.get("ma5", c_curr["close"]))
            ma5_prev = float(prev_k.get("ma5", ma5_curr))

            if side == "LONG":
                # ── 觸發器 0-A：標準已收線「三點峰頂樞紐 (Three-Point Peak Pivot)」──
                pivot_peak_found = False
                pivot_peak_meta = {}

                # 1. 短序列 3 根判定：中間棒衝頂累積利潤，第三棒收黑跌破中點，且具備動能跌破前低或跌破 MA5
                is_3bar_pivot = (c_prev["high"] > c_prev2["high"]) and (c_prev["high"] >= c_curr["high"]) and ((c_prev["close"] > c_prev["open"]) or (c_prev["high"] >= kc_up))
                c_prev_mid = (c_prev["open"] + c_prev["close"]) / 2.0
                is_3bar_exhaust = (c_curr["close"] < c_prev_mid) and (c_curr["close"] < c_curr["open"])
                c_curr_body_long = abs(c_curr["close"] - c_curr["open"])
                had_gain_long = (c_prev["high"] - entry_p >= 0.4 * atr_val) or (peak_roe >= 0.003)
                drop_long = c_prev["high"] - quote
                is_real_drop_confirmed = (quote < ma5_curr) or (c_curr["close"] < ma5_curr) or ((c_curr["close"] < c_prev["low"]) and (c_curr_body_long >= 0.35 * atr_val) and (drop_long >= 0.35 * atr_val))
                if is_3bar_pivot and is_3bar_exhaust and had_gain_long and is_real_drop_confirmed:
                    pivot_peak_found = True
                    pivot_peak_meta = {"peak_high": c_prev["high"], "conf_close": c_curr["close"], "peak_roe": peak_roe}

                # 2. 若為長序列，回溯最近 3 根已收線棒 (排除最後一根盤中棒)
                if not pivot_peak_found and len(raw_df) >= 4:
                    closed_df = raw_df.iloc[:-1]
                    n_closed = len(closed_df)
                    for i in range(n_closed - 2, max(0, n_closed - 4), -1):
                        left_b = closed_df.iloc[i - 1]
                        peak_b = closed_df.iloc[i]
                        conf_b = closed_df.iloc[i + 1]

                        # 中間棒 High 鶴立雞群，且必須為陽棒衝高或碰上軌
                        if not ((peak_b["high"] > left_b["high"]) and (peak_b["high"] >= conf_b["high"]) and ((peak_b["close"] > peak_b["open"]) or (peak_b["high"] >= kc_up))):
                            continue
                        p_mid = (peak_b["open"] + peak_b["close"]) / 2.0
                        c_exh = (conf_b["close"] < conf_b["open"]) and (conf_b["close"] < p_mid)
                        if not c_exh:
                            continue
                        had_gain = (peak_b["high"] - entry_p >= 0.6 * atr_val) or (peak_roe >= 0.003) or (unrealized_pct >= 0.002)
                        if not had_gain:
                            continue
                        if (quote < peak_b["high"]) and (quote <= ma5_curr or quote <= conf_b["close"] or quote < p_mid):
                            pivot_peak_found = True
                            pivot_peak_meta = {"peak_high": peak_b["high"], "conf_close": conf_b["close"], "peak_roe": peak_roe}
                            break

                # 頂部大陰吞噬或射擊之星
                is_bearish_engulfing = (c_curr["close"] < c_curr["open"]) and (c_curr["close"] < c_prev["low"]) and ((c_curr["open"] - c_curr["close"]) >= 1.2 * atr_val)
                upper_wick_prev = c_prev["high"] - max(c_prev["open"], c_prev["close"])
                body_prev = abs(c_prev["open"] - c_prev["close"])
                is_shooting_star = (c_prev["high"] > c_prev2["high"]) and (upper_wick_prev >= 1.5 * max(body_prev, 0.2 * atr_val)) and (upper_wick_prev >= 0.4 * atr_val) and (c_curr["close"] < c_curr["open"])

                # ── 單邊強勢主升浪死抱走到最後保護 ──
                # 若大趨勢強烈多頭 (slope >= 0 且 ema_fast >= ema_slow)，且價格守穩在 MA5 之上且未確認峰頂回落
                if not pivot_peak_found and not is_bearish_engulfing and not is_shooting_star:
                    if slope >= 0 and ema_fast >= ema_slow and quote >= ma5_curr:
                        return False, "VETO_HOLD", {
                            **meta,
                            "cause": f"多單單邊主升浪強勢延伸中，價格守穩於MA5防守線之上 (quote={quote:.6f} >= ma5={ma5_curr:.6f})，KC中軌依然向上 (slope={slope:.6f})，未到多頭趨勢末端，未破MA5前嚴禁平多！健康回踩中，維持死抱！堅定死抱走到最後！",
                        }

                if (pivot_peak_found or is_bearish_engulfing or is_shooting_star) and (unrealized_pct > 0.001 or peak_roe >= 0.003):
                    return True, "EARLY_PIVOT_PEAK_EXIT", {
                        **meta,
                        "pivot_meta": pivot_peak_meta,
                        "cause": f"早期真峰頂樞紐確立(engulfing={is_bearish_engulfing}, pivot={pivot_peak_found}, star={is_shooting_star})，不必等待跌破中軌，立即平多落袋！",
                    }

                # 主升浪健康回踩防洗保護 (Healthy Pullback Invariant)
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

                # ── 外軌鎖定機制：價格仍運行在KC上軌強勢區，絕對禁止提早平倉！──
                if quote >= kc_up or c_curr["close"] >= kc_up:
                    return False, "VETO_HOLD", {
                        **meta,
                        "cause": f"多單處於KC上軌外暴走段 (quote={quote:.6f} >= kc_up={kc_up:.6f})，外軌鎖定抱牢主升浪，嚴禁提早平多！",
                    }

                # ── 觸發器 0-C：均線死叉且實質跌破KC中軌平多 ──
                if ema_fast < ema_slow and (c_curr["close"] < kc_mid or quote < kc_mid):
                    return True, "LONG_EXIT_ON_EMA_CROSS", {
                        **meta,
                        "cause": f"EMA15向下死叉EMA40且跌破KC中軌 (quote={quote:.6f} < kc_mid={kc_mid:.6f})，波段反轉立即平多！",
                    }

                # ── 觸發器 0-D：跌破 KC 中軌平多 (LONG_EXIT_KC_MID_BREACH) ──
                if ema_fast < ema_slow and (c_curr["close"] < kc_mid or quote < kc_mid) and (peak_roe >= max(0.6 * atr_pct, 0.004) and unrealized_pct <= peak_roe * 0.60):
                    return True, "LONG_EXIT_KC_MID_BREACH", {
                        **meta,
                        "cause": f"多單均線轉弱且跌破KC中軌 (close={c_curr['close']:.4f} < kc_mid={kc_mid:.4f})，波段回吐利潤鎖定平多！",
                    }

                # 趨勢不變續抱：大週期多頭 (ema_fast >= ema_slow) 維持續抱！
                if ema_fast >= ema_slow:
                    return False, "VETO_HOLD", {
                        **meta,
                        "cause": f"多單大週期趨勢依然向上且守穩結構 (kc_middle_slope={slope:.6f}, ema_fast={ema_fast:.5f} >= ema_slow={ema_slow:.5f})，維持續抱！",
                    }

            elif side == "SHORT":
                # ── 觸發器 2-A：標準已收線「三點谷底樞紐 (Three-Point Valley Pivot)」──
                pivot_valley_found = False
                pivot_valley_meta = {}

                # 1. 短序列 3 根判定：中間棒探底累積利潤，第三棒收紅突破中點，且具備動能突破前高或收復 MA5
                is_3bar_pivot_low = (c_prev["low"] < c_prev2["low"]) and (c_prev["low"] <= c_curr["low"]) and ((c_prev["close"] < c_prev["open"]) or (c_prev["low"] <= kc_low))
                c_prev_mid = (c_prev["open"] + c_prev["close"]) / 2.0
                is_3bar_exhaust_low = (c_curr["close"] > c_prev_mid) and (c_curr["close"] > c_curr["open"])
                c_curr_body_short = abs(c_curr["close"] - c_curr["open"])
                had_gain_short = (entry_p - c_prev["low"] >= 0.4 * atr_val) or (peak_roe >= 0.003)
                bounce_short = quote - c_prev["low"]
                is_real_bounce_confirmed = (quote > ma5_curr) or (c_curr["close"] > ma5_curr) or ((c_curr["close"] > c_prev["high"]) and (c_curr_body_short >= 0.35 * atr_val) and (bounce_short >= 0.35 * atr_val))
                if is_3bar_pivot_low and is_3bar_exhaust_low and had_gain_short and is_real_bounce_confirmed:
                    pivot_valley_found = True
                    pivot_valley_meta = {"valley_low": c_prev["low"], "conf_close": c_curr["close"], "peak_roe": peak_roe}

                # 2. 若為長序列，回溯已收線棒
                if not pivot_valley_found and len(raw_df) >= 4:
                    closed_df = raw_df.iloc[:-1]
                    n_closed = len(closed_df)
                    for i in range(n_closed - 2, max(0, n_closed - 4), -1):
                        left_b = closed_df.iloc[i - 1]
                        valley_b = closed_df.iloc[i]
                        conf_b = closed_df.iloc[i + 1]

                        # 中間棒 Low 鶴立雞群，且必須為陰棒探底或碰下軌
                        if not ((valley_b["low"] < left_b["low"]) and (valley_b["low"] <= conf_b["low"]) and ((valley_b["close"] < valley_b["open"]) or (valley_b["low"] <= kc_low))):
                            continue
                        v_mid = (valley_b["open"] + valley_b["close"]) / 2.0
                        c_exh = (conf_b["close"] > conf_b["open"]) and (conf_b["close"] > v_mid)
                        if not c_exh:
                            continue
                        had_gain = (entry_p - valley_b["low"] >= 0.6 * atr_val) or (peak_roe >= 0.003) or (unrealized_pct >= 0.002)
                        if not had_gain:
                            continue
                        if (quote > valley_b["low"]) and (quote >= ma5_curr or quote >= conf_b["close"] or quote > v_mid):
                            pivot_valley_found = True
                            pivot_valley_meta = {"valley_low": valley_b["low"], "conf_close": conf_b["close"], "peak_roe": peak_roe}
                            break

                # 底部大陽吞噬或錘頭線
                is_bullish_engulfing = (c_prev["close"] < c_prev["open"]) and (c_curr["close"] > c_curr["open"]) and (c_curr["close"] > c_prev["high"]) and ((c_curr["close"] - c_curr["open"]) >= 1.2 * atr_val)
                lower_wick_prev = min(c_prev["open"], c_prev["close"]) - c_prev["low"]
                body_prev = abs(c_prev["open"] - c_prev["close"])
                is_hammer = (c_prev["low"] < c_prev2["low"]) and (lower_wick_prev >= 1.5 * max(body_prev, 0.2 * atr_val)) and (lower_wick_prev >= 0.4 * atr_val) and (c_curr["close"] > c_curr["open"])

                # ── 單邊強勢主跌浪死抱走到最後保護 ──
                # 若大趨勢強烈空頭 (slope <= 0 且 ema_fast <= ema_slow)，且價格受壓在 MA5 之下且未確認谷底反彈
                if not pivot_valley_found and not is_bullish_engulfing and not is_hammer:
                    if slope <= 0 and ema_fast <= ema_slow and quote <= ma5_curr:
                        return False, "VETO_HOLD", {
                            **meta,
                            "cause": f"空單單邊主跌浪強勢延伸中，價格受壓於MA5防守線之下 (quote={quote:.6f} <= ma5={ma5_curr:.6f})，KC中軌依然向下 (slope={slope:.6f})，未到空頭趨勢末端，未收復MA5前嚴禁平空！健康反抽中，維持死抱！堅定死抱走到最後！",
                        }

                if (pivot_valley_found or is_bullish_engulfing or is_hammer) and (unrealized_pct > 0.001 or peak_roe >= 0.003):
                    return True, "EARLY_PIVOT_VALLEY_EXIT", {
                        **meta,
                        "pivot_meta": pivot_valley_meta,
                        "cause": f"早期真谷底樞紐確立(engulfing={is_bullish_engulfing}, pivot={pivot_valley_found}, hammer={is_hammer})，不必等待中軌即刻平空！",
                    }

                # 主跌浪健康反抽防洗保護 (Healthy Pullback Invariant)
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

                # ── 外軌鎖定機制：價格仍運行在KC下軌強勢區，絕對禁止提早平倉！──
                if quote <= kc_low or c_curr["close"] <= kc_low:
                    return False, "VETO_HOLD", {
                        **meta,
                        "cause": f"空單處於KC下軌外暴跌段 (quote={quote:.6f} <= kc_low={kc_low:.6f})，外軌鎖定抱牢主跌浪，嚴禁提早平空！",
                    }

                # ── 觸發器 2-C：均線金叉且實質突破KC中軌平空 ──
                if ema_fast > ema_slow and (c_curr["close"] > kc_mid or quote > kc_mid):
                    return True, "SHORT_EXIT_ON_EMA_CROSS", {
                        **meta,
                        "cause": f"EMA15向上金叉EMA40且突破收復KC中軌 (quote={quote:.6f} > kc_mid={kc_mid:.6f})，波段反轉立即平空！",
                    }

                # ── 觸發器 2-D：收復突破 KC 中軌平空 (SHORT_EXIT_KC_MID_BREACH) ──
                if ema_fast > ema_slow and (c_curr["close"] > kc_mid or quote > kc_mid) and (peak_roe >= max(0.6 * atr_pct, 0.004) and unrealized_pct <= peak_roe * 0.60):
                    return True, "SHORT_EXIT_KC_MID_BREACH", {
                        **meta,
                        "cause": f"空單均線轉弱且突破收復KC中軌 (close={c_curr['close']:.4f} > kc_mid={kc_mid:.4f})，波段回吐利潤鎖定平空！",
                    }

                # 趨勢不變續抱：大週期空頭 (ema_fast <= ema_slow) 維持續抱！
                if ema_fast <= ema_slow:
                    return False, "VETO_HOLD", {
                        **meta,
                        "cause": f"空單大週期趨勢依然向下且受制於中軌 (kc_middle_slope={slope:.6f}, ema_fast={ema_fast:.5f} <= ema_slow={ema_slow:.5f})，未到空頭趨勢末端，維持死抱！維持續抱！健康反抽中！",
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
        # 空頭趨勢嚴禁開多：KC中軌下行 (slope < -0.000005) 且價格受壓在中軌之下，100% 阻斷開多接飛刀！
        # 多頭趨勢嚴禁開空：KC中軌上行 (slope > 0.000005) 且價格站穩在中軌之上，100% 阻斷開空摸頂！
        # 破軌衝擊與MA5/MA15交叉：若價格站穩中軌之上且中軌走平向上，允許順勢啟動新主浪！
        # ══════════════════════════════════════════════════════════════════
        if signal_side == "LONG":
            is_breakout_mode = any(b in signal_type.upper() for b in ("BREAKOUT", "CONTINUATION", "CROSS"))
            if slope < -0.000005 and (c_close < kc_mid or not is_breakout_mode):
                return False, "BLOCKED_LONG_ON_BEARISH_TREND", {
                    **meta,
                    "cause": f"順勢交易鐵律一票否決: 主通道KC中軌下行 (kc_slope={slope:.6f} < 0) 且在通道內，嚴禁逆勢摸底開多！",
                }
            if ema_fast < ema_slow and slope < -0.00001:
                return False, "BLOCKED_LONG_ON_BEARISH_TREND", {
                    **meta,
                    "cause": f"順勢交易鐵律一票否決: 大週期均線空頭排列且中軌持續向下 (slope={slope:.6f})，嚴禁開多接飛刀！",
                }

        if signal_side == "SHORT":
            is_breakout_mode = any(b in signal_type.upper() for b in ("BREAKDOWN", "CONTINUATION", "CROSS"))
            if slope > 0.000005 and (c_close > kc_mid or not is_breakout_mode):
                return False, "BLOCKED_SHORT_ON_BULLISH_TREND", {
                    **meta,
                    "cause": f"順勢交易鐵律一票否決: 主通道KC中軌上行 (kc_slope={slope:.6f} > 0) 且在通道內，嚴禁逆勢摸頂開空！",
                }
            if ema_fast > ema_slow and slope > 0.00001:
                return False, "BLOCKED_SHORT_ON_BULLISH_TREND", {
                    **meta,
                    "cause": f"順勢交易鐵律一票否決: 大週期均線多頭排列且中軌持續向上 (slope={slope:.6f})，嚴禁開空摸頂！",
                }

        # ─── 均線斜率（EMA Slope）絕對順勢一票否決（Hard Veto） ───
        # 做多鐵律：若 EMA15 下拐或走平 (ema15[-1] <= ema15[-2])，一律 100% 阻斷開多接飛刀！
        # 做空鐵律：若 EMA15 上翹或走平 (ema15[-1] >= ema15[-2])，一律 100% 阻斷開空摸頂！
        prev_ema_val = float(prev_k.get("ema_fast", prev_k.get("ema15", 0.0))) if prev_k else 0.0
        is_breakout_or_cross = any(b in signal_type.upper() for b in ("BREAKOUT", "CONTINUATION", "CROSS"))
        if prev_ema_val > 0 and not is_breakout_or_cross:
            ema15_slope = ema_fast - prev_ema_val
            if signal_side == "LONG" and (ema_fast < prev_ema_val or ema15_slope < -0.000005):
                return False, "BLOCKED_LONG_ON_EMA_FALLING", {
                    **meta,
                    "cause": f"均線下拐嚴禁開多接刀: EMA15當前={ema_fast:.5f} < 前根={prev_ema_val:.5f} (斜率={ema15_slope:.6f} < 0)！",
                }
            if signal_side == "SHORT" and (ema_fast > prev_ema_val or ema15_slope > 0.000005):
                return False, "BLOCKED_SHORT_ON_EMA_RISING", {
                    **meta,
                    "cause": f"均線上翹嚴禁開空摸頂: EMA15當前={ema_fast:.5f} > 前根={prev_ema_val:.5f} (斜率={ema15_slope:.6f} > 0)！",
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
        is_breakout_mode = any(b in signal_type.upper() for b in ("BREAKOUT", "CONTINUATION", "OUTSIDE", "BURST", "BREAKDOWN", "CROSS_MID"))
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
        # 門禁 2：妖幣/破軌「主升浪起點」/ 主跌浪起點（外軌突破放行）
        # ══════════════════════════════════════════════════════════════════
        if signal_side == "LONG":
            is_breakout_candle = (c_close > kc_up or quote > kc_up) and (c_close >= c_open)
            is_ma5_upward = ma5_curr > ma5_prev
            is_kc_mid_aligned = slope >= -0.000005
            if is_breakout_candle and is_ma5_upward and is_kc_mid_aligned:
                ret_reason = "CONFIRMED_2BAR_BREAKOUT" if "CONFIRMED_2BAR_BREAKOUT" in signal_type.upper() else "BREAKOUT_MAIN_WAVE_START"
                return True, ret_reason, {
                    **meta,
                    "cause": "主升浪起點突破放行: 實體紅K衝破KC上軌且MA5強勁向上，破軌動能發起點！",
                }

        elif signal_side == "SHORT":
            is_breakdown_candle = (c_close < kc_low or quote < kc_low) and (c_close <= c_open)
            is_ma5_downward = ma5_curr < ma5_prev
            is_kc_mid_aligned = slope <= 0.000005
            if is_breakdown_candle and is_ma5_downward and is_kc_mid_aligned:
                ret_reason = "CONFIRMED_2BAR_BREAKOUT" if "CONFIRMED_2BAR_BREAKOUT" in signal_type.upper() else "BREAKOUT_MAIN_WAVE_START"
                return True, ret_reason, {
                    **meta,
                    "cause": "主跌浪起點突破放行: 實體黑K跌破KC下軌且MA5強勁向下，破軌動能發跌點！",
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
            is_ma_cross_mid = (ma5_curr >= ma15 or ema_fast >= ema_slow) and (c_close >= kc_mid or quote >= kc_mid) and (c_close >= c_open) and (slope >= -0.000005) and (ma5_curr > ma5_prev)
            if is_ma_cross_mid:
                return True, "ENTRY_LONG_EMA_CROSS_MID", {
                    **meta,
                    "cause": "MA5金叉MA15且收紅站上KC中軌提早放行開多，領先外軌追高天花板！",
                }
        elif signal_side == "SHORT":
            is_ma_cross_mid = (ma5_curr <= ma15 or ema_fast <= ema_slow) and (c_close <= kc_mid or quote <= kc_mid) and (c_close <= c_open) and (slope <= 0.000005) and (ma5_curr < ma5_prev)
            if is_ma_cross_mid:
                return True, "ENTRY_SHORT_EMA_CROSS_MID", {
                    **meta,
                    "cause": "MA5死叉MA15且收黑跌破KC中軌提早放行開空，領先外軌追低地板！",
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
