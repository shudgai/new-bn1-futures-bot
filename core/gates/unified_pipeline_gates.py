"""
core/gates/unified_pipeline_gates.py
====================================
三層流水線之【裁決層 (Pure Math Gates)】
依據 2026/10/21 盤面覆盤與最新防洗/防追急衝標準重構：
- 解決「買在急衝頂部、平在盤整底部、主升浪全程踏空」的問題。
"""
import math
import time
from dataclasses import dataclass, field
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
    def is_healthy_pullback(
        side: str,
        ma5_curr: float,
        ma5_prev: float,
        ma15: float,
        current_close: float,
        quote: float,
    ) -> Tuple[bool, str]:
        """
        健康回踩與健康反抽判定（強制一票否決平倉 VETO_HOLD）：
        - 多單：若 MA5 明顯向上 (ma5_curr > ma5_prev) 且 收盤價或最新價仍守穩於 MA15 之上 (current_close >= ma15 或 quote >= ma15)，
          此時價格跌破 MA5 或回踩 KC 中軌，一律視為「健康回踩」，強制一票否決平倉 (Veto Exit)！
        - 空單：若 MA5 明顯向下 (ma5_curr < ma5_prev) 且 收盤價或最新價仍壓制於 MA15 之下 (current_close <= ma15 或 quote <= ma15)，
          盤中反彈一律視為「健康反彈」，強制一票否決平空！
        """
        side = str(side).upper()
        if side == "LONG":
            if ma5_curr > ma5_prev and (current_close >= ma15 or quote >= ma15):
                return True, (
                    f"多單健康回踩續抱 (一票否決): MA5明顯向上 (curr={ma5_curr:.6f} > prev={ma5_prev:.6f}) "
                    f"且價格守穩MA15之上 (close={current_close:.6f} >= ma15={ma15:.6f})，"
                    f"跌破MA5或回踩中軌屬健康回踩，強制一票否決平倉，堅定抱牢主升段！"
                )
        elif side == "SHORT":
            if ma5_curr < ma5_prev and (current_close <= ma15 or quote <= ma15):
                return True, (
                    f"空單健康反抽續抱 (一票否決): MA5明顯向下 (curr={ma5_curr:.6f} < prev={ma5_prev:.6f}) "
                    f"且價格壓制於MA15之下 (close={current_close:.6f} <= ma15={ma15:.6f})，"
                    f"盤中反彈屬健康反抽，強制一票否決平空，堅定抱牢主跌段！"
                )
        return False, ""

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
        # ── 計算真實倉位合約 ROE (納入 5 倍槓桿或持倉配置槓桿) ──
        leverage = float(pos.get("leverage", 5.0))
        unrealized_roe = unrealized_pct * leverage

        # 價格波幅峰值與真實合約 ROE 峰值
        stored_peak_roe = float(pos.get("peak_roe") or 0.0)
        stored_peak_move = float(pos.get("peak_price_move") or pos.get("highest_pnl_pct") or 0.0)
        if stored_peak_roe <= 0 and stored_peak_move > 0:
            stored_peak_roe = stored_peak_move * leverage

        peak_roe = max(stored_peak_roe, unrealized_roe)
        peak_price_move = peak_roe / leverage

        pos["peak_roe"] = peak_roe
        pos["unrealized_roe"] = unrealized_roe
        pos["peak_price_move"] = peak_price_move

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

        # ── 1. 動態雙階梯鎖利 (真實合約 ROE 納入槓桿，達標 10% 鎖 70%、達標 15% 鎖 80%) ──
        if peak_roe >= 0.15:
            min_retained_roe = peak_roe * 0.80  # 達標 15% 鎖 80%
            if unrealized_roe <= min_retained_roe:
                return True, "PROFIT_LOCK", {
                    **meta,
                    "cause": f"15%鎖80%階梯觸發 (當前ROE: {unrealized_roe*100:.2f}%, 峰值ROE: {peak_roe*100:.2f}%, 底線: {min_retained_roe*100:.2f}%)",
                }
        elif peak_roe >= 0.10:
            min_retained_roe = peak_roe * 0.70  # 達標 10% 鎖 70%
            if unrealized_roe <= min_retained_roe:
                return True, "PROFIT_LOCK", {
                    **meta,
                    "cause": f"10%鎖70%階梯觸發 (當前ROE: {unrealized_roe*100:.2f}%, 峰值ROE: {peak_roe*100:.2f}%, 底線: {min_retained_roe*100:.2f}%)",
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

                # 1. 最新 3 根樞紐判定：中間棒衝頂累積利潤，第三棒收黑跌破中點，且具備動能
                is_3bar_pivot = (c_prev["high"] > c_prev2["high"]) and (c_prev["high"] >= c_curr["high"]) and ((c_prev["close"] > c_prev["open"]) or (c_prev["high"] >= kc_up))
                c_prev_mid = (c_prev["open"] + c_prev["close"]) / 2.0
                is_3bar_exhaust = (c_curr["close"] < c_prev_mid) and (c_curr["close"] < c_curr["open"])
                c_curr_body_long = abs(c_curr["close"] - c_curr["open"])
                had_gain_long = (c_prev["high"] - entry_p >= 0.4 * atr_val) or (peak_roe >= 0.003)
                drop_long = c_prev["high"] - quote
                is_real_drop_confirmed = ((c_curr["close"] < ma5_curr) and (ma5_curr <= ma5_prev)) or ((c_curr["close"] < c_prev["low"]) and (c_curr_body_long >= 0.35 * atr_val) and (drop_long >= 0.35 * atr_val))
                if is_3bar_pivot and is_3bar_exhaust and had_gain_long and is_real_drop_confirmed:
                    pivot_peak_found = True
                    pivot_peak_meta = {"peak_high": c_prev["high"], "conf_close": c_curr["close"], "peak_roe": peak_roe}

                # 2. 長序列實盤：回溯最近已收線棒 (排除未收線盤中棒，嚴防盤中毛刺誤判假峰頂！)
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

                # ── 核心鐵律：開倉後回踩絕對不平倉（第一重：開倉初期呼吸保護）──
                # 剛進場部位處於波段展開期 (未達實質展開 0.75% ROE 且衝高未達 0.5*ATR)
                # 任何回踩 MA5、回踩 KC 中軌或小陰線洗盤，100% 強制一票否決平倉，堅定死抱主升浪！
                is_early_pullback = (peak_roe < 0.0075) and ((c_prev["high"] - entry_p) < 0.5 * atr_val)
                if is_early_pullback and not is_bearish_engulfing:
                    return False, "VETO_HOLD", {
                        **meta,
                        "early_pullback_shield": True,
                        "cause": f"開倉後回踩不平倉鐵律: 剛開倉處於波段呼吸展開期 (峰值ROE={peak_roe*100:.2f}% < 0.75%, 衝高展開={max(0.0, c_prev['high']-entry_p)/atr_val:.2f}ATR < 0.5ATR)，開倉後回踩100%強制一票否決平倉，堅定死抱主升浪！",
                    }

                # ── 單邊強勢主升浪守穩MA5死抱走到最後保護 ──
                if not pivot_peak_found and not is_bearish_engulfing and not is_shooting_star:
                    if slope >= 0 and ema_fast >= ema_slow and quote >= ma5_curr:
                        return False, "VETO_HOLD", {
                            **meta,
                            "cause": f"多單單邊主升浪強勢延伸中，價格守穩於MA5防守線之上 (quote={quote:.6f} >= ma5={ma5_curr:.6f})，KC中軌依然向上 (slope={slope:.6f})，未破MA5前嚴禁平多，維持續抱！",
                        }

                # ── 核心鐵律：開倉後回踩絕對不平倉（第二重：波段推進中守穩中軌防洗保護）──
                # 守穩 KC 中軌且未現確定性三點真峰頂，任何盤中回踩給予充足空間，一票否決平多！
                if not pivot_peak_found and not is_bearish_engulfing:
                    if (c_curr["close"] >= kc_mid or quote >= kc_mid) and slope >= -0.000005:
                        return False, "VETO_HOLD", {
                            **meta,
                            "cause": f"開倉後回踩不平倉鐵律: 價格守穩KC中軌防守線 (close={c_curr['close']:.6f} >= mid={kc_mid:.6f}, slope={slope:.6f})，未見確定性已收線真峰頂，開倉後回踩給予充足呼吸空間，100% 一票否決平多，堅定死抱主升浪！",
                        }

                # ── 真峰頂平倉：已收線真峰頂確立且大週期轉弱，或大陰吞噬，立即平多落袋！──
                if (pivot_peak_found or is_bearish_engulfing or is_shooting_star) and (unrealized_pct > 0.001 or peak_roe >= 0.003):
                    return True, "EARLY_PIVOT_PEAK_EXIT", {
                        **meta,
                        "pivot_meta": pivot_peak_meta,
                        "cause": f"早期真峰頂樞紐確立(engulfing={is_bearish_engulfing}, pivot={pivot_peak_found}, star={is_shooting_star})，不必等待跌破中軌，立即平多落袋！",
                    }

                # ── 外軌鎖定機制：價格仍運行在KC上軌強勢區，絕對禁止提早平倉！──
                if quote >= kc_up or c_curr["close"] >= kc_up:
                    return False, "VETO_HOLD", {
                        **meta,
                        "cause": f"多單處於KC上軌外暴走段 (quote={quote:.6f} >= kc_up={kc_up:.6f})，外軌鎖定抱牢主升浪，嚴禁提早平多！",
                    }

                # ── 主升浪健康回踩保護（is_healthy_pullback，未達趨勢衰退強制續抱一票否決）──
                is_healthy_l, healthy_cause_l = ExitGate.is_healthy_pullback(
                    side="LONG",
                    ma5_curr=ma5_curr,
                    ma5_prev=ma5_prev,
                    ma15=ma15,
                    current_close=float(c_curr["close"]),
                    quote=quote,
                )
                if is_healthy_l:
                    return False, "VETO_HOLD", {
                        **meta,
                        "healthy_pullback": True,
                        "cause": healthy_cause_l,
                    }

                # ── 趨勢衰退確認平多：收線確認跌破 MA15 且 MA5 轉為走平或向下，且大週期均線死叉/轉弱 ──
                if (
                    float(c_curr["close"]) < ma15
                    and ma5_curr <= ma5_prev
                    and (ema_fast < ema_slow or macro_trend != "BULLISH")
                ):
                    return True, "LONG_EXIT_MA15_BREAK_AND_MA5_DECAY", {
                        **meta,
                        "cause": f"多單均線實質轉折平倉: 收線跌破MA15 (close={float(c_curr['close']):.6f} < ma15={ma15:.6f})，MA5走平或向下 (curr={ma5_curr:.6f} <= prev={ma5_prev:.6f}) 且大週期均線死叉轉弱，趨勢實質衰退平多離場！",
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

                # 1. 最新 3 根樞紐判定：中間棒探底累積利潤，第三棒收紅突破中點，且具備動能
                is_3bar_pivot_low = (c_prev["low"] < c_prev2["low"]) and (c_prev["low"] <= c_curr["low"]) and ((c_prev["close"] < c_prev["open"]) or (c_prev["low"] <= kc_low))
                c_prev_mid = (c_prev["open"] + c_prev["close"]) / 2.0
                is_3bar_exhaust_low = (c_curr["close"] > c_prev_mid) and (c_curr["close"] > c_curr["open"])
                c_curr_body_short = abs(c_curr["close"] - c_curr["open"])
                had_gain_short = (entry_p - c_prev["low"] >= 0.4 * atr_val) or (peak_roe >= 0.003)
                bounce_short = quote - c_prev["low"]
                is_real_bounce_confirmed = ((c_curr["close"] > ma5_curr) and (ma5_curr >= ma5_prev)) or ((c_curr["close"] > c_prev["high"]) and (c_curr_body_short >= 0.35 * atr_val) and (bounce_short >= 0.35 * atr_val))
                if is_3bar_pivot_low and is_3bar_exhaust_low and had_gain_short and is_real_bounce_confirmed:
                    pivot_valley_found = True
                    pivot_valley_meta = {"valley_low": c_prev["low"], "conf_close": c_curr["close"], "peak_roe": peak_roe}

                # 2. 長序列實盤：回溯最近已收線棒 (排除未收線盤中棒，嚴防盤中毛刺誤判假谷底！)
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

                # ── 核心鐵律：開倉後反抽絕對不平空（第一重：開倉初期呼吸保護）──
                # 剛進場部位處於波段展開期 (未達實質展開 0.75% ROE 且探底未達 0.5*ATR)
                # 任何反彈回抽 MA5、回踩 KC 中軌或小陽線洗盤，100% 強制一票否決平空，堅定死抱主跌浪！
                is_early_bounce = (peak_roe < 0.0075) and ((entry_p - c_prev["low"]) < 0.5 * atr_val)
                if is_early_bounce and not is_bullish_engulfing:
                    return False, "VETO_HOLD", {
                        **meta,
                        "early_pullback_shield": True,
                        "cause": f"開倉後反抽不平空鐵律: 剛開倉處於波段呼吸展開期 (峰值ROE={peak_roe*100:.2f}% < 0.75%, 探底展開={max(0.0, entry_p-c_prev['low'])/atr_val:.2f}ATR < 0.5ATR)，開倉後反抽100%強制一票否決平空，堅定死抱主跌浪！",
                    }

                # ── 持倉防洗鐵律：若尚未出現已收線真谷底，且價格受壓在 MA5 之下，一律視為空中換手死抱主跌浪！──
                is_below_ma5_safe = (quote <= ma5_curr) and (c_curr["close"] <= ma5_curr)
                if not pivot_valley_found and is_below_ma5_safe and not is_bullish_engulfing:
                    return False, "VETO_HOLD", {
                        **meta,
                        "cause": f"空單主跌浪強勢延伸中，未見已收線真谷底且價格受壓於MA5之下 (quote={quote:.6f} <= ma5={ma5_curr:.6f})，KC中軌依然向下 (slope={slope:.6f})，未到空頭趨勢末端，健康反抽給予呼吸空間，抱牢主跌浪嚴禁提早平空！堅定死抱走到最後！",
                    }

                # ── 單邊強勢主跌浪死抱走到最後保護 ──
                if not pivot_valley_found and not is_bullish_engulfing and not is_hammer:
                    if slope <= 0 and ema_fast <= ema_slow and quote <= ma5_curr:
                        return False, "VETO_HOLD", {
                            **meta,
                            "cause": f"空單單邊主跌浪強勢延伸中，價格受壓於MA5防守線之下 (quote={quote:.6f} <= ma5={ma5_curr:.6f})，KC中軌依然向下 (slope={slope:.6f})，未到空頭趨勢末端，未收復MA5前嚴禁平空！健康反抽中，維持死抱！堅定死抱走到最後！",
                        }

                # ── 核心鐵律：開倉後反抽絕對不平空（第二重：波段推進中受壓中軌防洗保護）──
                # 受壓 KC 中軌且未現確定性三點真谷底，任何盤中反彈給予充足空間，一票否決平空！
                if not pivot_valley_found and not is_bullish_engulfing:
                    if (c_curr["close"] <= kc_mid or quote <= kc_mid) and slope <= 0.000005:
                        return False, "VETO_HOLD", {
                            **meta,
                            "cause": f"開倉後反抽不平空鐵律: 價格受壓於KC中軌防守線 (close={c_curr['close']:.6f} <= mid={kc_mid:.6f}, slope={slope:.6f})，未見確定性已收線真谷底，開倉後反抽給予充足呼吸空間，100% 一票否決平空，堅定死抱主跌浪！",
                        }

                # ── 真谷底平倉：已收線真谷底確立或大陽吞噬，立即平空落袋！──
                if (pivot_valley_found or is_bullish_engulfing or is_hammer) and (unrealized_pct > 0.001 or peak_roe >= 0.003):
                    return True, "EARLY_PIVOT_VALLEY_EXIT", {
                        **meta,
                        "pivot_meta": pivot_valley_meta,
                        "cause": f"早期真谷底樞紐確立(engulfing={is_bullish_engulfing}, pivot={pivot_valley_found}, hammer={is_hammer})，立即平空落袋！",
                    }

                # ── 外軌鎖定機制：價格仍運行在KC下軌強勢區，絕對禁止提早平倉！──
                if quote <= kc_low or c_curr["close"] <= kc_low:
                    return False, "VETO_HOLD", {
                        **meta,
                        "cause": f"空單處於KC下軌外暴跌段 (quote={quote:.6f} <= kc_low={kc_low:.6f})，外軌鎖定抱牢主跌浪，嚴禁提早平空！",
                    }

                # ── 主跌浪健康反抽保護（is_healthy_pullback，未達趨勢衰退強制續抱一票否決）──
                is_healthy_s, healthy_cause_s = ExitGate.is_healthy_pullback(
                    side="SHORT",
                    ma5_curr=ma5_curr,
                    ma5_prev=ma5_prev,
                    ma15=ma15,
                    current_close=float(c_curr["close"]),
                    quote=quote,
                )
                if is_healthy_s:
                    return False, "VETO_HOLD", {
                        **meta,
                        "healthy_pullback": True,
                        "cause": healthy_cause_s,
                    }

                # ── 趨勢衰退確認平空：收線確認突破 MA15 且 MA5 轉為走平或向上，且大週期均線金叉/轉弱 ──
                if (
                    float(c_curr["close"]) > ma15
                    and ma5_curr >= ma5_prev
                    and (ema_fast > ema_slow or macro_trend != "BEARISH")
                ):
                    return True, "SHORT_EXIT_MA15_BREAK_AND_MA5_DECAY", {
                        **meta,
                        "cause": f"空單均線實質轉折平倉: 收線突破MA15 (close={float(c_curr['close']):.6f} > ma15={ma15:.6f})，MA5走平或向上 (curr={ma5_curr:.6f} >= prev={ma5_prev:.6f}) 且大週期均線金叉轉弱，趨勢實質衰退平空離場！",
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
    def evaluate_ma5_slope(
        ma5_curr: float,
        ma5_prev: float,
        atr_val: float,
        signal_side: str,
        threshold_atr_mult: float = 0.05,
    ) -> Tuple[bool, str, Dict[str, Any]]:
        """
        基於 MA5 運行方向（斜率 Slope）的量化硬性過濾規則（一票否決 VETO 機制）：
        - 斜率計算方式：ΔMA5 = MA5[-1] - MA5[-2]
        - 平行/走平門檻（防雜訊死水區）：波動閾值 ε = threshold_atr_mult * ATR（預設 0.05 * ATR）
        - 判定規則：
          1. 若 |ΔMA5| <= ε：判定為走平 (Flat) -> BLOCKED_MA5_FLAT（多空皆一票否決嚴禁開單）
          2. 若 ΔMA5 > ε：判定為向上 (Up) -> 允許做多，但若信號為做空則 VETO_MA5_SLOPE_NOT_DOWNWARD（嚴禁開空）
          3. 若 ΔMA5 < -ε：判定為向下 (Down) -> 允許做空，但若信號為做多則 VETO_MA5_SLOPE_NOT_UPWARD（嚴禁開多）
        """
        delta_ma5 = float(ma5_curr) - float(ma5_prev)
        epsilon = max(threshold_atr_mult * float(atr_val), 1e-6)
        details = {
            "ma5_curr": float(ma5_curr),
            "ma5_prev": float(ma5_prev),
            "delta_ma5": delta_ma5,
            "epsilon": epsilon,
        }

        if abs(delta_ma5) <= epsilon:
            return False, "BLOCKED_MA5_FLAT", {
                **details,
                "slope_status": "FLAT",
                "cause": f"MA5 平行走平無趨勢 (|ΔMA5|={abs(delta_ma5):.6f} <= ε={epsilon:.6f} [0.05*ATR])，橫盤死水區一票否決，多空皆禁止開倉！",
            }

        side = str(signal_side).upper()
        if delta_ma5 > epsilon:
            details["slope_status"] = "UP"
            if side == "SHORT":
                return False, "VETO_MA5_SLOPE_NOT_DOWNWARD", {
                    **details,
                    "cause": f"MA5 向上發散 (ΔMA5={delta_ma5:.6f} > ε={epsilon:.6f})，強制禁止開空單！",
                }
        elif delta_ma5 < -epsilon:
            details["slope_status"] = "DOWN"
            if side == "LONG":
                return False, "VETO_MA5_SLOPE_NOT_UPWARD", {
                    **details,
                    "cause": f"MA5 向下發散 (ΔMA5={delta_ma5:.6f} < -ε={-epsilon:.6f})，強制禁止開多單！",
                }

        return True, "PASSED", details

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
        # 門禁 0：順勢交易鐵律（大趨勢一票否決，AGENTS.md 最高準則）
        # 空頭趨勢嚴禁開多：大週期為 BEARISH，或 KC 中軌下行且受壓，100% 阻斷開多接飛刀！
        # 多頭趨勢嚴禁開空：大週期為 BULLISH，或 KC 中軌上行且站穩，100% 阻斷開空摸頂！
        # ══════════════════════════════════════════════════════════════════
        macro_trend = str(getattr(market_data, "macro_trend", None) or (market_data.get("macro_trend") if isinstance(market_data, dict) else "NEUTRAL") or "NEUTRAL").upper()
        if signal_side == "LONG":
            if macro_trend == "BEARISH":
                return False, "BLOCKED_LONG_ON_BEARISH_TREND", {
                    **meta,
                    "macro_trend": macro_trend,
                    "cause": "順勢交易鐵律一票否決: 市場大週期為空頭 (BEARISH)，哪怕盤中暴拉突破，100% 嚴禁開多接飛刀！",
                }
            if slope < -0.000005 and (c_close <= kc_mid or quote <= kc_mid):
                return False, "BLOCKED_LONG_ON_BEARISH_TREND", {
                    **meta,
                    "cause": f"順勢交易鐵律一票否決: 主跌浪KC中軌下行 (kc_slope={slope:.6f} < 0) 且價格在中軌之下 (close={c_close:.6f} <= mid={kc_mid:.6f})，嚴禁逆勢摸底開多！要一路開空！",
                }
            if ema_fast < ema_slow and slope < -0.00001:
                return False, "BLOCKED_LONG_ON_BEARISH_TREND", {
                    **meta,
                    "cause": f"順勢交易鐵律一票否決: 大週期均線空頭排列且中軌持續向下 (slope={slope:.6f})，嚴禁開多接飛刀！",
                }

        if signal_side == "SHORT":
            if macro_trend == "BULLISH":
                return False, "BLOCKED_SHORT_ON_BULLISH_TREND", {
                    **meta,
                    "macro_trend": macro_trend,
                    "cause": "順勢交易鐵律一票否決: 市場大週期為多頭 (BULLISH)，哪怕盤中暴跌破位，100% 嚴禁開空摸頂！要一路開多！",
                }
            if slope > 0.000005 and (c_close >= kc_mid or quote >= kc_mid):
                return False, "BLOCKED_SHORT_ON_BULLISH_TREND", {
                    **meta,
                    "cause": f"順勢交易鐵律一票否決: 主升浪中軌上行 (kc_slope={slope:.6f} > 0) 且價格在中軌之上 (close={c_close:.6f} >= mid={kc_mid:.6f})，多頭波段中100%嚴禁賣空！要一路開多！",
                }
            if ema_fast > ema_slow and slope > 0.00001:
                return False, "BLOCKED_SHORT_ON_BULLISH_TREND", {
                    **meta,
                    "cause": f"順勢交易鐵律一票否決: 大週期均線多頭排列且中軌持續向上 (slope={slope:.6f})，嚴禁開空摸頂！",
                }

        # ─── 延續開倉新鮮度防追高 (AGENTS.md 2026-10-20 最新授權) ───
        is_breakout_mode = any(b in signal_type.upper() for b in ("BREAKOUT", "CONTINUATION", "OUTSIDE", "BURST", "BREAKDOWN", "CROSS_MID"))
        is_late_chase = any(b in signal_type.upper() for b in ("CONTINUATION", "CONFIRMED_2BAR"))
        if is_late_chase:
            if signal_side == "LONG":
                band_ext = max(quote, c_close) - kc_up
                if band_ext > 0.5 * atr_val:
                    return False, "LATE_CHASE_BAND_EXTENSION", {
                        **meta,
                        "band_extension": band_ext,
                        "max_allowed": 0.5 * atr_val,
                        "cause": f"延續開倉距外軌過度延展一票否決: 乖離={band_ext:.6f} > 0.5*ATR({0.5*atr_val:.6f})，拒絕追高！",
                    }
            elif signal_side == "SHORT":
                band_ext = kc_low - min(quote, c_close)
                if band_ext > 0.5 * atr_val:
                    return False, "LATE_CHASE_BAND_EXTENSION", {
                        **meta,
                        "band_extension": band_ext,
                        "max_allowed": 0.5 * atr_val,
                        "cause": f"延續開倉距外軌過度延展一票否決: 乖離={band_ext:.6f} > 0.5*ATR({0.5*atr_val:.6f})，拒絕追低！",
                    }

            if isinstance(raw_df, pd.DataFrame) and len(raw_df) >= 3:
                closed_bars = raw_df.iloc[:-1]
                consecutive_outside = 0
                if signal_side == "LONG":
                    for idx in range(len(closed_bars) - 1, -1, -1):
                        b = closed_bars.iloc[idx]
                        b_kc_up = float(b.get("kc_upper", kc_up))
                        if float(b["close"]) > b_kc_up:
                            consecutive_outside += 1
                        else:
                            break
                elif signal_side == "SHORT":
                    for idx in range(len(closed_bars) - 1, -1, -1):
                        b = closed_bars.iloc[idx]
                        b_kc_low = float(b.get("kc_lower", kc_low))
                        if float(b["close"]) < b_kc_low:
                            consecutive_outside += 1
                        else:
                            break
                if consecutive_outside > 2:
                    return False, "LATE_CHASE_STALE_CONTINUATION", {
                        **meta,
                        "consecutive_outside": consecutive_outside,
                        "cause": f"軌外已連續收線 {consecutive_outside} 根 (>2 根)，破軌新鮮度已過，嚴防天花板追開第二個多單！",
                    }

        # ══════════════════════════════════════════════════════════════════
        # 前置門禁 A：平倉後強制冷卻與頻率上限防護 (Post-Exit Cooldown & Rate Limit)
        # 平倉後強制冷卻至少 3 根 1m K 棒 (180 秒)
        # 單一合約 1 小時內開倉上限為 3～4 次 (預設 3 次)
        # ══════════════════════════════════════════════════════════════════
        if last_exit_context:
            exit_ts = float(last_exit_context.get("timestamp") or 0.0)
            if exit_ts > 0:
                now_ts = float(last_exit_context.get("current_time") or time.time())
                elapsed = now_ts - exit_ts
                if elapsed < 180:
                    return False, "VETO_EXIT_COOLDOWN", {
                        **meta,
                        "elapsed_sec": elapsed,
                        "cooldown_required_sec": 180,
                        "cause": f"平倉強制冷卻期內 ({elapsed:.1f}s < 180s，不足 3 根 1m K 棒)，全域嚴禁任何開倉！",
                    }
            bars_since_exit = last_exit_context.get("bars_since_exit")
            if bars_since_exit is not None and int(bars_since_exit) < 3:
                return False, "VETO_EXIT_COOLDOWN", {
                    **meta,
                    "bars_since_exit": int(bars_since_exit),
                    "required_bars": 3,
                    "cause": f"平倉強制冷卻期內 (平倉後僅過 {bars_since_exit} 根 < 3 根已收線 K 棒)，全域嚴禁任何開倉！",
                }

            # ─── 禁止原地追單防反覆刷單 (Anti-Churning Re-entry Filter) ───
            # 平倉後若要再次開同向，價格必須實質突破前次平倉時的極值高點/低點，嚴禁同一區間反覆開平刷單
            last_exit_side = str(last_exit_context.get("side") or "").upper()
            if signal_side == last_exit_side:
                if signal_side == "LONG":
                    last_exit_high = float(
                        last_exit_context.get("exit_high")
                        or last_exit_context.get("peak_high")
                        or last_exit_context.get("highest_price")
                        or last_exit_context.get("price")
                        or last_exit_context.get("exit_price")
                        or 0.0
                    )
                    if last_exit_high > 0 and quote <= last_exit_high:
                        return False, "BLOCKED_REENTRY_PRICE_NOT_EXCEEDED", {
                            **meta,
                            "quote": quote,
                            "last_exit_high": last_exit_high,
                            "cause": f"禁止原地追多反覆刷單: 最新價 ({quote:.6f}) 未實質突破前次平倉最高價 ({last_exit_high:.6f})，嚴禁在原波段同一價格區間反覆開平倉！",
                        }
                elif signal_side == "SHORT":
                    last_exit_low = float(
                        last_exit_context.get("exit_low")
                        or last_exit_context.get("valley_low")
                        or last_exit_context.get("lowest_price")
                        or last_exit_context.get("price")
                        or last_exit_context.get("exit_price")
                        or 0.0
                    )
                    if last_exit_low > 0 and quote >= last_exit_low:
                        return False, "BLOCKED_REENTRY_PRICE_NOT_EXCEEDED", {
                            **meta,
                            "quote": quote,
                            "last_exit_low": last_exit_low,
                            "cause": f"禁止原地追空反覆刷單: 最新價 ({quote:.6f}) 未實質跌破前次平倉最低價 ({last_exit_low:.6f})，嚴禁在原波段同一價格區間反覆開平倉！",
                        }

            hourly_count = int(last_exit_context.get("hourly_entry_count", 0))
            if hourly_count >= 3:
                return False, "BLOCKED_HOURLY_ENTRY_LIMIT_EXCEEDED", {
                    **meta,
                    "hourly_count": hourly_count,
                    "cause": f"單一合約 1 小時內開倉已達上限 ({hourly_count} 次 >= 3 次)，嚴格限制下單頻率！",
                }

        # ══════════════════════════════════════════════════════════════════
        # 前置門禁 B：均線糾纏震盪過濾 (Chop Filter)
        # 8 根 K 棒內 MA5/MA15 交叉 >= 2 次，或間距 < 0.2 ATR 時，強制 VETO 禁止開倉！
        # ══════════════════════════════════════════════════════════════════
        if raw_df is not None and isinstance(raw_df, pd.DataFrame) and len(raw_df) >= 8:
            bars8 = raw_df.iloc[-8:]
            if "ma5" in bars8.columns and "ma15" in bars8.columns:
                spreads = [float(r.ma5) - float(r.ma15) for r in bars8.itertuples()]
                cross_count = 0
                prior_sign = 0
                for s in spreads:
                    curr_sign = 1 if s > 1e-9 else -1 if s < -1e-9 else 0
                    if curr_sign and prior_sign and curr_sign != prior_sign:
                        cross_count += 1
                    if curr_sign:
                        prior_sign = curr_sign
                if cross_count >= 2:
                    return False, "BLOCKED_MA5_MA15_CROSS_CHOP", {
                        **meta,
                        "cross_count_8": cross_count,
                        "cause": f"Chop Filter一票否決: 8 根 K 棒內 MA5/MA15 交叉 {cross_count} 次 (>=2次)，均線糾纏震盪，嚴禁開倉！",
                    }

        # 均線間距過窄判定 (< 0.2 ATR)
        ma_gap = abs(ma5 - ma15)
        if atr_val > 0 and ma_gap < 0.20 * atr_val and not (ma5 == quote and ma15 == quote):
            return False, "BLOCKED_MA5_MA15_GAP_TOO_NARROW", {
                **meta,
                "ma_gap": ma_gap,
                "min_gap_required": 0.20 * atr_val,
                "cause": f"Chop Filter一票否決: MA5/MA15 間距 ({ma_gap:.6f}) < 0.2 ATR ({0.20*atr_val:.6f})，均線黏合無空間，嚴禁開倉！",
            }

        # ══════════════════════════════════════════════════════════════════
        # 前置門禁 C：通道極度收斂防護 (KC Band Squeeze Filter)
        # 通道極度收縮（Squeeze）時禁止開倉（當前通道寬度小於過去 20 根 K 棒最大寬度的 25%）
        # ══════════════════════════════════════════════════════════════════
        if raw_df is not None and isinstance(raw_df, pd.DataFrame) and len(raw_df) >= 20:
            bars20 = raw_df.iloc[-20:]
            if "kc_upper" in bars20.columns and "kc_lower" in bars20.columns:
                hist_bw = bars20["kc_upper"] - bars20["kc_lower"]
                max_bw = float(hist_bw.max())
                current_bw = float(kc_up - kc_low)
                if current_bw <= 0:
                    current_bw = float(hist_bw.iloc[-1])
                if max_bw > 0 and 0 < current_bw <= 0.25 * max_bw:
                    return False, "BLOCKED_KC_SQUEEZE", {
                        **meta,
                        "current_bw": current_bw,
                        "max_bw_20": max_bw,
                        "bw_ratio": current_bw / max_bw,
                        "cause": f"通道極度收斂一票否決: 當前 KC 通道寬度比 ({current_bw/max_bw:.1%}) <= 25%，處於極度收縮死水區，嚴禁假突破開倉！",
                    }

        # ══════════════════════════════════════════════════════════════════
        # 前置門禁 D：大陰/大陽瀑布冷卻期 (Recent Giant Bar Breakdown Cooldown)
        # 若過去 15 根 K 棒內曾出現「實體長度 >= 2.0 ATR」的破壞性長黑柱/長紅柱，
        # 結構受重創，在未滿 10 根 K 棒內啟動冷卻期，嚴防假突破死貓跳誘多/誘空！
        # ══════════════════════════════════════════════════════════════════
        if raw_df is not None and isinstance(raw_df, pd.DataFrame) and len(raw_df) >= 3:
            lookback_bars = raw_df.iloc[-15:]
            n_lookback = len(lookback_bars)
            for i, r in enumerate(lookback_bars.itertuples()):
                bar_atr = float(getattr(r, "atr", atr_val)) if float(getattr(r, "atr", atr_val)) > 0 else atr_val
                bar_body = abs(float(r.close) - float(r.open))
                bars_ago = n_lookback - 1 - i
                if bars_ago <= 10 and bar_body >= 2.0 * bar_atr:
                    is_bear_giant = float(r.close) < float(r.open)
                    is_bull_giant = float(r.close) > float(r.open)
                    if signal_side == "LONG" and is_bear_giant:
                        return False, "BLOCKED_RECENT_WATERFALL_BREAKDOWN", {
                            **meta,
                            "giant_bars_ago": bars_ago,
                            "giant_body": bar_body,
                            "bar_atr": bar_atr,
                            "cause": f"結構受創一票否決: 過去 {bars_ago} 根 K 棒內曾出現實體長度 ({bar_body:.4f}) >= 2.0*ATR ({2.0*bar_atr:.4f}) 的巨型崩跌大黑柱，多頭結構瓦解，強制冷卻 10 根 K 棒嚴禁假反彈開多！",
                        }
                    elif signal_side == "SHORT" and is_bull_giant:
                        return False, "BLOCKED_RECENT_WATERFALL_BREAKDOWN", {
                            **meta,
                            "giant_bars_ago": bars_ago,
                            "giant_body": bar_body,
                            "bar_atr": bar_atr,
                            "cause": f"結構受創一票否決: 過去 {bars_ago} 根 K 棒內曾出現實體長度 ({bar_body:.4f}) >= 2.0*ATR ({2.0*bar_atr:.4f}) 的巨型垂直大陽柱，空頭結構瓦解，強制冷卻 10 根 K 棒嚴禁假回踩開空！",
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
            # MA5 向上斜率防護與走平一票否決
            ma5_ok, ma5_reason, ma5_meta = EntryGate.evaluate_ma5_slope(
                ma5_curr=ma5_curr,
                ma5_prev=ma5_prev,
                atr_val=atr_val,
                signal_side="LONG",
                threshold_atr_mult=0.05,
            )
            if not ma5_ok:
                return False, ma5_reason, {**meta, **ma5_meta}
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
            # MA5 向下斜率防護與走平一票否決
            ma5_ok, ma5_reason, ma5_meta = EntryGate.evaluate_ma5_slope(
                ma5_curr=ma5_curr,
                ma5_prev=ma5_prev,
                atr_val=atr_val,
                signal_side="SHORT",
                threshold_atr_mult=0.05,
            )
            if not ma5_ok:
                return False, ma5_reason, {**meta, **ma5_meta}

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
        # ─── 延續開倉新鮮度防追高 (AGENTS.md 2026-10-20 最新授權) ───
        # 延續開倉時，同側軌外已連續收線超過 2 根，一律 LATE_CHASE_STALE_CONTINUATION 拒絕開倉，嚴防天花板追第二個買多！
        if is_late_chase and isinstance(raw_df, pd.DataFrame) and len(raw_df) >= 3:
            closed_bars = raw_df.iloc[:-1]
            consecutive_outside = 0
            if signal_side == "LONG":
                for idx in range(len(closed_bars) - 1, -1, -1):
                    b = closed_bars.iloc[idx]
                    b_kc_up = float(b.get("kc_upper", kc_up))
                    if float(b["close"]) > b_kc_up:
                        consecutive_outside += 1
                    else:
                        break
            elif signal_side == "SHORT":
                for idx in range(len(closed_bars) - 1, -1, -1):
                    b = closed_bars.iloc[idx]
                    b_kc_low = float(b.get("kc_lower", kc_low))
                    if float(b["close"]) < b_kc_low:
                        consecutive_outside += 1
                    else:
                        break
            if consecutive_outside > 2:
                return False, "LATE_CHASE_STALE_CONTINUATION", {
                    **meta,
                    "consecutive_outside": consecutive_outside,
                    "cause": f"軌外已連續收線 {consecutive_outside} 根 (>2 根)，破軌新鮮度已過，嚴防天花板追開第二個多單！",
                }

        # ══════════════════════════════════════════════════════════════════
        # 門禁 2：妖幣/破軌「主升浪起點」/ 主跌浪起點（外軌突破放行）
        # ══════════════════════════════════════════════════════════════════
        if signal_side == "LONG":
            is_breakout_candle = (c_close > kc_up or quote > kc_up) and (c_close >= c_open)
            is_ma5_upward = ma5_curr > ma5_prev
            is_kc_mid_aligned = slope >= -0.000005

            # 兩根破軌動能確認 (高點突破確認)：針對兩根破軌確認訊號，第 2 根高點必須突破第 1 根高點
            if "CONFIRMED_2BAR" in signal_type.upper() and prev_k and prev_k.get("high") is not None:
                p_high = float(prev_k["high"])
                p_close = float(prev_k.get("close", p_high))
                p_up = float(prev_k.get("kc_upper", kc_up))
                if p_close > p_up and c_high <= p_high and c_close <= p_close:
                    return False, "BLOCKED_BREAKOUT_NO_NEW_HIGH", {
                        **meta,
                        "cause": f"兩根破軌確認動能衰竭一票否決: 第2根高點({c_high:.5f})未突破第1根高點({p_high:.5f})，頂部動能不足且帶上影線，拒絕追多！",
                    }

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

            # 兩根跌破動能確認：針對兩根跌破確認訊號，第 2 根低點必須跌破第 1 根低點
            if "CONFIRMED_2BAR" in signal_type.upper() and prev_k and prev_k.get("low") is not None:
                p_low = float(prev_k["low"])
                p_close = float(prev_k.get("close", p_low))
                p_low_band = float(prev_k.get("kc_lower", kc_low))
                if p_close < p_low_band and c_low >= p_low and c_close >= p_close:
                    return False, "BLOCKED_BREAKDOWN_NO_NEW_LOW", {
                        **meta,
                        "cause": f"兩根跌破確認動能衰竭一票否決: 第2根低點({c_low:.5f})未跌破第1根低點({p_low:.5f})，底部動能不足且帶下影線，拒絕追空！",
                    }

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


# 模組層級便捷函數導出
evaluate_ma5_slope = EntryGate.evaluate_ma5_slope
is_healthy_pullback = ExitGate.is_healthy_pullback


# ==============================================================================
# 一、 狀態追蹤器：冷卻與原地追單防護 (PositionStateMachine / ExitEventRecord)
# ==============================================================================
@dataclass
class ExitEventRecord:
    side: str               # "LONG" 或 "SHORT"
    exit_bar_index: int     # 平倉時的 K 棒索引
    exit_price: float       # 平倉價格
    peak_price: float       # 該波段持倉期間的最高價 (多單) / 最低價 (空單)
    exit_reason: str        # 平倉觸發原因


class PositionStateMachine:
    """維護交易狀態與進場冷卻/價格突破約束"""

    def __init__(self, cooldown_bars: int = 3):
        self.cooldown_bars = cooldown_bars
        self.last_exit: Optional[ExitEventRecord] = None

    def record_exit(self, side: str, bar_index: int, exit_price: float, peak_price: float, reason: str):
        """當 ExitGate 決定平倉時呼叫此處記錄"""
        self.last_exit = ExitEventRecord(
            side=side.upper(),
            exit_bar_index=bar_index,
            exit_price=exit_price,
            peak_price=peak_price,
            exit_reason=reason,
        )

    def validate_entry_cooldown(
        self,
        candidate_side: str,
        current_bar_index: int,
        current_price: float,
        is_strong: bool = False,
    ) -> Tuple[bool, str]:
        """
        開倉前置過濾：檢查冷卻 K 棒數與原地追單限制
        - 支援強訊號豁免冷卻 (is_strong=True 時豁免 K 棒冷卻期)
        返回: (是否放行, 原因說明)
        """
        if self.last_exit is None:
            return True, "無歷史平倉紀錄，允許進場"

        bars_passed = current_bar_index - self.last_exit.exit_bar_index
        candidate_side = candidate_side.upper()

        # 1. 強制 K 棒冷卻期檢查 (若為強勢破軌則豁免冷卻)
        if bars_passed < self.cooldown_bars:
            if is_strong:
                # 觸發強勢大確認棒，立刻豁免冷卻！
                pass
            else:
                return False, f"COOLDOWN_VETO: 平倉冷卻中 (已過 {bars_passed}/{self.cooldown_bars} 根 K 棒，非強勢大確認棒嚴禁開倉)"

        # 2. 禁止同向原地追單檢查
        if candidate_side == self.last_exit.side:
            if candidate_side == "LONG":
                # 多單：現價必須突破前一波段最高點，確認展開新一輪主升浪
                if current_price <= self.last_exit.peak_price:
                    return False, (
                        f"CHASING_VETO: 禁止多單原地追進 (現價 {current_price:.5f} "
                        f"尚未突破前高 {self.last_exit.peak_price:.5f})"
                    )
            elif candidate_side == "SHORT":
                # 空單：現價必須跌破前一波段最低點
                if current_price >= self.last_exit.peak_price:
                    return False, (
                        f"CHASING_VETO: 禁止空單地板追空 (現價 {current_price:.5f} "
                        f"尚未跌破前低 {self.last_exit.peak_price:.5f})"
                    )

        return True, "進場條件通過冷卻與追價審查"

    def get_exit_context(self, current_bar_index: Optional[int] = None) -> Dict[str, Any]:
        """提供給 EntryGate.evaluate 相容格式的平倉上下文資訊"""
        if self.last_exit is None:
            return {}
        ctx = {
            "side": self.last_exit.side,
            "exit_price": self.last_exit.exit_price,
            "exit_high": self.last_exit.peak_price if self.last_exit.side == "LONG" else None,
            "exit_low": self.last_exit.peak_price if self.last_exit.side == "SHORT" else None,
            "peak_price": self.last_exit.peak_price,
            "exit_reason": self.last_exit.exit_reason,
            "exit_bar_index": self.last_exit.exit_bar_index,
        }
        if current_bar_index is not None:
            ctx["bars_since_exit"] = max(0, current_bar_index - self.last_exit.exit_bar_index)
        return ctx


# ==============================================================================
# 二、 核心裁決官：優化版 UnifiedExitGate
# ==============================================================================
class UnifiedExitGate:
    def __init__(self, leverage: float = 5.0, flat_threshold_ratio: float = 0.05):
        self.leverage = leverage
        self.flat_ratio = flat_threshold_ratio

    def evaluate_exit(
        self,
        position: dict,
        df: pd.DataFrame,
        current_bar_index: int,
    ) -> Tuple[bool, str, str]:
        """
        ExitGate 決策入口
        返回: (是否執行平倉, 平倉類型代碼, 詳細原因說明)
        """
        side = position["side"].upper()
        entry_price = float(position["entry_price"])
        current_price = float(df["close"].iloc[-1])
        peak_price = float(position.get("peak_price", current_price))

        # 計算指標與斜率
        row_now = df.iloc[-1]
        row_prev = df.iloc[-2]

        ma5_now = float(row_now["ma5"])
        ma5_prev = float(row_prev["ma5"])
        ma15_now = float(row_now["ma15"])
        atr = float(row_now.get("atr", current_price * 0.005))

        # 1. 均線斜率計算 (Delta MA5)
        delta_ma5 = ma5_now - ma5_prev
        flat_epsilon = self.flat_ratio * atr
        is_ma5_up = delta_ma5 > flat_epsilon
        is_ma5_down = delta_ma5 < -flat_epsilon
        is_ma5_flat = abs(delta_ma5) <= flat_epsilon

        # 2. 計算合約真實 ROE
        if side == "LONG":
            unrealized_pct = (current_price - entry_price) / entry_price
            peak_pct = (peak_price - entry_price) / entry_price
        else:
            unrealized_pct = (entry_price - current_price) / entry_price
            peak_pct = (entry_price - peak_price) / entry_price

        unrealized_roe = unrealized_pct * self.leverage
        peak_roe = max(peak_pct * self.leverage, unrealized_roe)

        # ─────────────────────────────────────────────────────────────
        # 最高優先級：硬止損與階梯動態鎖利 (不接受任何「健康回踩」否決)
        # ─────────────────────────────────────────────────────────────
        # 止損線 (例如 ROE <= -15%)
        if unrealized_roe <= -0.15:
            return True, "HARD_STOP_LOSS", f"觸及硬止損線 (當前 ROE: {unrealized_roe*100:.2f}%)"

        # 雙階梯動態鎖利 (15% 鎖 80%, 10% 鎖 70%)
        if peak_roe >= 0.15:
            min_retained_roe = peak_roe * 0.80
            if unrealized_roe <= min_retained_roe:
                return True, "PROFIT_LOCK_T2", (
                    f"15%鎖80%階梯觸發 (峰值ROE: {peak_roe*100:.2f}%, "
                    f"底線: {min_retained_roe*100:.2f}%, 當前: {unrealized_roe*100:.2f}%)"
                )
        elif peak_roe >= 0.10:
            min_retained_roe = peak_roe * 0.70
            if unrealized_roe <= min_retained_roe:
                return True, "PROFIT_LOCK_T1", (
                    f"10%鎖70%階梯觸發 (峰值ROE: {peak_roe*100:.2f}%, "
                    f"底線: {min_retained_roe*100:.2f}%, 當前: {unrealized_roe*100:.2f}%)"
                )

        # ─────────────────────────────────────────────────────────────
        # 核心防禦：健康回踩續抱檢查 (Healthy Pullback Protection)
        # ─────────────────────────────────────────────────────────────
        if self._is_healthy_pullback(side, row_now, is_ma5_up, is_ma5_down, ma15_now):
            # 趨勢健康，一票否決日常平倉信號，直接拒絕平倉
            return False, "HOLD_HEALTHY_PULLBACK", "主趨勢完好且站穩MA15，判定為健康回踩，強制續抱"

        # ─────────────────────────────────────────────────────────────
        # 趨勢衰退驗證平倉 (Trend Exhaustion Exits)
        # ─────────────────────────────────────────────────────────────
        if side == "LONG":
            # 條件 1: 均線實質破位 (收盤跌破 MA15 且 MA5 不再陡峭向上)
            if (float(row_now["close"]) < ma15_now) and (not is_ma5_up):
                return True, "LONG_EXIT_MA15_LOST", "收線跌破 MA15 且 MA5 轉平或向下，趨勢正式衰退"

            # 條件 2: 頂部三點樞紐確認 (真峰頂)
            is_peak, reason = self._check_three_point_pivot_peak(df, unrealized_roe)
            if is_peak:
                return True, "EARLY_PIVOT_PEAK_EXIT", reason

        elif side == "SHORT":
            # 條件 1: 均線實質破位 (收盤衝破 MA15 且 MA5 不再陡峭向下)
            if (float(row_now["close"]) > ma15_now) and (not is_ma5_down):
                return True, "SHORT_EXIT_MA15_LOST", "收線突破 MA15 且 MA5 轉平或向上，趨勢正式衰退"

            # 條件 2: 底部三點樞紐確認 (真谷底)
            is_valley, reason = self._check_three_point_pivot_valley(df, unrealized_roe)
            if is_valley:
                return True, "EARLY_PIVOT_VALLEY_EXIT", reason

        return False, "NO_EXIT_SIGNAL", "未滿足趨勢衰退或離場條件，繼續持倉"

    def _is_healthy_pullback(
        self,
        side: str,
        current_bar: pd.Series,
        is_ma5_up: bool,
        is_ma5_down: bool,
        ma15: float,
    ) -> bool:
        """
        精確定義健康回踩：
        多單：MA5 向上且收盤價仍在 MA15 上方。
        空單：MA5 向下且收盤價仍在 MA15 下方。
        """
        close = float(current_bar["close"])
        if side == "LONG":
            return is_ma5_up and (close >= ma15)
        elif side == "SHORT":
            return is_ma5_down and (close <= ma15)
        return False

    def _check_three_point_pivot_peak(self, df: pd.DataFrame, unrealized_roe: float) -> Tuple[bool, str]:
        """頂部真峰頂判定：需三根 K 棒收線確認 + 實體跌破中點 + 具備實質浮盈"""
        if len(df) < 3 or unrealized_roe < 0.05:  # 未滿 5% ROE 浮盈不觸發樞紐搶跑
            return False, ""

        b_left = df.iloc[-3]
        b_peak = df.iloc[-2]
        b_confirm = df.iloc[-1]

        # 1. 峰值棒高點鶴立雞群
        is_highest = (float(b_peak["high"]) > float(b_left["high"])) and (float(b_peak["high"]) > float(b_confirm["high"]))
        if not is_highest:
            return False, ""

        # 2. 確認棒收黑且跌破峰值棒實體中點
        peak_body_mid = (float(b_peak["open"]) + float(b_peak["close"])) / 2.0
        is_confirmed = (float(b_confirm["close"]) < float(b_confirm["open"])) and (float(b_confirm["close"]) < peak_body_mid)

        if is_confirmed:
            return True, "三點樞紐真峰頂成型且確認棒跌破實體中點，獲利離場"

        return False, ""

    def _check_three_point_pivot_valley(self, df: pd.DataFrame, unrealized_roe: float) -> Tuple[bool, str]:
        """底部真谷底判定：需三根 K 棒收線確認 + 實體突破中點 + 具備實質浮盈"""
        if len(df) < 3 or unrealized_roe < 0.05:
            return False, ""

        b_left = df.iloc[-3]
        b_valley = df.iloc[-2]
        b_confirm = df.iloc[-1]

        # 1. 谷底棒低點鶴立雞群
        is_lowest = (float(b_valley["low"]) < float(b_left["low"])) and (float(b_valley["low"]) < float(b_confirm["low"]))
        if not is_lowest:
            return False, ""

        # 2. 確認棒收紅且漲破谷底棒實體中點
        valley_body_mid = (float(b_valley["open"]) + float(b_valley["close"])) / 2.0
        is_confirmed = (float(b_confirm["close"]) > float(b_confirm["open"])) and (float(b_confirm["close"]) > valley_body_mid)

        if is_confirmed:
            return True, "三點樞紐真谷底成型且確認棒衝破實體中點，空單獲利離場"

        return False, ""


# ==============================================================================
# 三、 強勢大確認棒判定與開倉綜合檢驗函數 (can_open_short / can_open_long)
# ==============================================================================
def is_strong_bearish_breakout(bar: Any, prev_bar: Any, avg_atr: float) -> bool:
    """
    判定是否為『極確定之強勢空方根線』：
    1. 必須是實體陰線
    2. 實體長度需達到強動能標準 (>= 1.2 * ATR)
    3. 收在相對低點 (下影線不能過長 <= 0.5 * 實體長度，避免遇到長針假跌破)
    4. 強勢跌破前一根的最低點
    5. 實體直接灌破下軌 (開在下軌之上/內，收在下軌外)
    """
    try:
        o = float(bar['open'])
        c = float(bar['close'])
        l = float(bar['low'])
        prev_l = float(prev_bar['low'])
        lower_band = float(bar.get('lower_band', bar.get('kc_lower', 0.0)))
    except (KeyError, TypeError, ValueError, IndexError):
        return False

    body_size = o - c
    lower_shadow = c - l

    # 1. 必須是實體陰線
    if body_size <= 0:
        return False

    # 2. 實體長度需達到強動能標準 (例如大於 1.2 倍 ATR)
    strong_momentum = body_size >= (1.2 * float(avg_atr))

    # 3. 收在相對低點 (下影線不能過長，避免遇到長針假跌破)
    clean_close = lower_shadow <= (0.5 * body_size)

    # 4. 強勢跌破前一根的最低點
    breaks_previous_low = c < prev_l

    # 5. 實體直接灌破下軌 (開在下軌內/上，收在下軌外)
    decisive_break_band = (c < lower_band) and (o > lower_band)

    return bool(strong_momentum and clean_close and breaks_previous_low and decisive_break_band)


def is_strong_bullish_breakout(bar: Any, prev_bar: Any, avg_atr: float) -> bool:
    """
    判定是否為『極確定之強勢多方根線』：
    1. 必須是實體陽線
    2. 實體長度需達到強動能標準 (>= 1.2 * ATR)
    3. 收在相對高點 (上影線不能過長 <= 0.5 * 實體長度，避免遇到長針假突破)
    4. 強勢突破前一根的最高點
    5. 實體直接衝破上軌 (開在上軌內/下，收在上軌外)
    """
    try:
        o = float(bar['open'])
        c = float(bar['close'])
        h = float(bar['high'])
        prev_h = float(prev_bar['high'])
        upper_band = float(bar.get('upper_band', bar.get('kc_upper', 0.0)))
    except (KeyError, TypeError, ValueError, IndexError):
        return False

    body_size = c - o
    upper_shadow = h - c

    # 1. 必須是實體陽線
    if body_size <= 0:
        return False

    # 2. 實體長度需達到強動能標準 (大於 1.2 倍 ATR)
    strong_momentum = body_size >= (1.2 * float(avg_atr))

    # 3. 收在相對高點 (上影線不能過長，避免長針假突破)
    clean_close = upper_shadow <= (0.5 * body_size)

    # 4. 強勢突破前一根的最高點
    breaks_previous_high = c > prev_h

    # 5. 實體直接衝破上軌 (開在上軌內/下，收在上軌外)
    decisive_break_band = (c > upper_band) and (o < upper_band)

    return bool(strong_momentum and clean_close and breaks_previous_high and decisive_break_band)


MIN_COOLDOWN_BARS = 3
last_exit_bar_index = -999


def can_open_short(
    df: pd.DataFrame,
    current_bar_index: int,
    avg_atr: Optional[float] = None,
    last_exit_idx: Optional[int] = None,
    min_cooldown: int = MIN_COOLDOWN_BARS,
) -> bool:
    """
    空單進場綜合檢驗：
    1. 平倉冷卻檢查 (強訊號豁免冷卻判定)
    2. 收盤定型與 K 棒顏色確認 (嚴禁未定型搶進，必須是實體陰線)
    3. MA5 短期均線方向與相對位置 (MA5 必須向下且收盤價壓制在 MA5 之下)
    4. 兩根破下軌確認 (或強勢大黑 K 灌破下軌)
    """
    global last_exit_bar_index
    exit_idx = last_exit_idx if last_exit_idx is not None else last_exit_bar_index

    if df is None or len(df) < 2:
        return False

    # 取得剛收盤定型的最後兩根 K 棒
    bar_t = df.iloc[-1]
    bar_prev = df.iloc[-2]

    # --- 基礎方向過濾 (任何情況都必須符合) ---
    # 剛收完必須是黑 K
    if float(bar_t['close']) >= float(bar_t['open']):
        return False

    # MA5 必須向下，且收盤價壓制在 MA5 之下 (絕不逆勢)
    ma5_trending_down = float(bar_t['ma5']) < float(bar_prev['ma5'])
    price_below_ma5 = float(bar_t['close']) < float(bar_t['ma5'])
    if not (ma5_trending_down and price_below_ma5):
        return False

    atr_val = float(avg_atr if avg_atr is not None else bar_t.get('atr', abs(float(bar_t['high']) - float(bar_t['low']))))
    lower_band = float(bar_t.get('lower_band', bar_t.get('kc_lower', 0.0)))
    prev_lower_band = float(bar_prev.get('lower_band', bar_prev.get('kc_lower', lower_band)))

    # --- 核心：強訊號豁免冷卻判定 ---
    is_strong = is_strong_bearish_breakout(bar_t, bar_prev, atr_val)

    # 檢查是否在冷卻期內
    in_cooldown = (current_bar_index - exit_idx) <= min_cooldown

    if in_cooldown:
        if is_strong:
            # 觸發強勢大黑 K，立刻豁免冷卻，允許直接開空！
            pass
        else:
            # 一般小碎步、反彈後弱跌，維持冷卻，禁止進場
            return False
    else:
        # 已過冷卻期：採一般常規進場標準 (例如兩根破下軌) 或強勢大黑 K
        two_bars_break = (float(bar_t['close']) < lower_band) and (float(bar_prev['close']) < prev_lower_band)
        if not two_bars_break and not is_strong:
            return False

    return True


def can_open_long(
    df: pd.DataFrame,
    current_bar_index: int,
    avg_atr: Optional[float] = None,
    last_exit_idx: Optional[int] = None,
    min_cooldown: int = MIN_COOLDOWN_BARS,
) -> bool:
    """
    多單進場綜合檢驗：
    1. 平倉冷卻檢查 (強訊號豁免冷卻判定)
    2. 收盤定型與 K 棒顏色確認 (必須是實體陽線)
    3. MA5 短期均線方向與相對位置 (MA5 必須向上且收盤價穩踩在 MA5 之上)
    4. 兩根破上軌確認 (或強勢大陽 K 衝破上軌)
    """
    global last_exit_bar_index
    exit_idx = last_exit_idx if last_exit_idx is not None else last_exit_bar_index

    if df is None or len(df) < 2:
        return False

    bar_t = df.iloc[-1]
    bar_prev = df.iloc[-2]

    # --- 基礎方向過濾 ---
    if float(bar_t['close']) <= float(bar_t['open']):
        return False

    ma5_trending_up = float(bar_t['ma5']) > float(bar_prev['ma5'])
    price_above_ma5 = float(bar_t['close']) > float(bar_t['ma5'])
    if not (ma5_trending_up and price_above_ma5):
        return False

    atr_val = float(avg_atr if avg_atr is not None else bar_t.get('atr', abs(float(bar_t['high']) - float(bar_t['low']))))
    upper_band = float(bar_t.get('upper_band', bar_t.get('kc_upper', 0.0)))
    prev_upper_band = float(bar_prev.get('upper_band', bar_prev.get('kc_upper', upper_band)))

    is_strong = is_strong_bullish_breakout(bar_t, bar_prev, atr_val)

    in_cooldown = (current_bar_index - exit_idx) <= min_cooldown

    if in_cooldown:
        if is_strong:
            pass
        else:
            return False
    else:
        two_bars_break = (float(bar_t['close']) > upper_band) and (float(bar_prev['close']) > prev_upper_band)
        if not two_bars_break and not is_strong:
            return False

    return True


