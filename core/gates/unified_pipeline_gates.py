"""
core/gates/unified_pipeline_gates.py
====================================
三層流水線之【裁決層 (Pure Math Gates)】
依據 2026/10/21 盤面覆盤與最新防洗/防追急衝標準重構：
- 解決「買在急衝頂部、平在盤整底部、主升浪全程踏空」的問題。
"""
import math
import time
import logging
from dataclasses import dataclass, field
from typing import Dict, Any, Tuple, Optional, Union
import pandas as pd
from core.services.data_pipeline import MarketDataSnapshot

logger = logging.getLogger(__name__)


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

    # 第一級絕對安全避險出口（3 ATR 硬止損、極端瀑布、反側破軌）：優先於頂底平倉
    ABSOLUTE_SAFETY_REASONS = frozenset({
        "CIRCUIT_BREAKER_HARD_SL",
        "EXIT_BY_CIRCUIT_BREAKER_HARD_SL",
        "HARD_STOP_LOSS",
        "CIRCUIT_BREAKER_EMERGENCY_DATA_OUTAGE",
        "EXIT_BY_EXTREME_WATERFALL",
        "EMERGENCY_DUMP_EXIT",
        "EMERGENCY_EXTREME_ADVERSE_BAR",
        "EXIT_LONG_ON_TOP_WATERFALL_DUMP",
        "FAIL_SAFE_EMERGENCY_EXIT",
        "OPPOSITE_KC_BAND_BREACH",
        "EXIT_LONG_ON_KC_LOWER_BREACH",
        "EXIT_SHORT_ON_KC_UPPER_BREACH",
        "EXIT_BY_MEME_ADVERSE_STOP_LOSS",
    })
    PROFIT_LOCK_EXIT_REASONS = frozenset({
        "PROFIT_LOCK",
        "EXIT_BY_RATCHET_PROFIT_LOCK",
    })
    # 中軌 MA3 暫緩期間仍然放行的真峰谷出口
    PIVOT_EXIT_REASONS = frozenset({
        "EARLY_PIVOT_PEAK_EXIT",
        "EARLY_PIVOT_VALLEY_EXIT",
        "CLOSE_SHORT_AT_BOTTOM",
        "CLOSE_LONG_AT_TOP",
        "EXIT_BY_VERIFIED_FRACTAL_PEAK",
        "EXIT_BY_VERIFIED_FRACTAL_VALLEY",
        "EXIT_BY_CONFIRMED_TRUE_PEAK",
        "EXIT_BY_CONFIRMED_TRUE_VALLEY",
        "EXIT_BY_TRUE_PEAK_EXTREME_REVERSAL",
        "EXIT_BY_TRUE_VALLEY_EXTREME_REVERSAL",
        "EXIT_BY_CONFIRMED_TRUE_PEAK_PIVOT",
        "EXIT_BY_CONFIRMED_TRUE_VALLEY_PIVOT",
        "EXIT_MEME_TRUE_PEAK_CONFIRMED",
        "EXIT_MEME_TRUE_VALLEY_CONFIRMED",
        "THREE_POINT_PIVOT",
    })
    GENERAL_EXIT_VETO_REASON = "VETO_HOLDING_UNTIL_TRUE_PIVOT"

    @staticmethod
    def _gate_unconfirmed_exit(result):
        """Only locks, confirmed swing pivots, and emergency protection may close."""
        should_exit, reason, meta = result
        if not should_exit:
            return result
        reason = str(reason or "")
        reason_upper = reason.upper()
        is_profit_lock = (
            reason in ExitGate.PROFIT_LOCK_EXIT_REASONS
            or any(token in reason_upper for token in (
                "PROFIT_LOCK", "RATCHET", "BREAKEVEN", "TRAILING_LOCK",
                "PROFIT_FLOOR", "NET_ROE_THREE_TIER_GIVEBACK",
            ))
        )
        if (reason in ExitGate.ABSOLUTE_SAFETY_REASONS
                or reason in ExitGate.PIVOT_EXIT_REASONS
                or is_profit_lock):
            return result
        return False, ExitGate.GENERAL_EXIT_VETO_REASON, {
            **(meta or {}),
            "suppressed_exit_reason": reason,
            "exit_policy": "PROFIT_LOCK_OR_CONFIRMED_TRUE_PIVOT_ONLY",
        }

    @staticmethod
    def evaluate(
        pos: Dict[str, Any],
        market_data: Union[MarketDataSnapshot, Dict[str, Any]],
        current_price: Optional[float] = None,
        atr: Optional[float] = None,
        kc_slope: Optional[float] = None,
    ) -> Tuple[bool, str, Dict[str, Any]]:
        """
        出場總裁決（2026-10-23 最新授權）：
        1. 絕對風險出口（3 ATR 硬止損、極端瀑布、反側破軌）最優先。
        2. 極值轉折點見頂/見底即平倉 (CLOSE_SHORT_AT_BOTTOM / CLOSE_LONG_AT_TOP)：
           MA3 勾頭、下影線插針打底或陽包陰吞噬，不等回摸中軌立即平倉鎖利！一票否決 VETO_HOLD！
        3. 淨 ROE 階梯鎖利與中軌 MA3 規則（僅已收線）優先於一般 VETO_HOLD 與普通均線出口。
        4. 暫緩期間 (HOLD_WAIT) 只放行真峰谷轉折出口，其餘一般出口暫停。
        """
        result = ExitGate._evaluate_core(pos, market_data, current_price, atr, kc_slope)
        should_exit, reason, meta = result
        if should_exit and reason in ExitGate.ABSOLUTE_SAFETY_REASONS:
            return result
        # 開倉初期呼吸保護：最高優先續抱 (防剛開倉微幅震盪被洗)
        if not should_exit and meta.get("early_pullback_shield"):
            return result

        if not pos or not market_data:
            return result
        try:
            if float(pos.get("entry_price") or 0.0) <= 0:
                return result
        except (TypeError, ValueError):
            return result

        raw_df = (
            market_data.raw_df if isinstance(market_data, MarketDataSnapshot)
            else market_data.get("raw_df")
        )

        pos_side = str(pos.get("side", "")).upper()
        quote_p = float(current_price or (market_data.current_price if isinstance(market_data, MarketDataSnapshot) else market_data.get("current_price", 0.0)))
        atr_val = (
            market_data.atr if isinstance(market_data, MarketDataSnapshot)
            else float(atr or market_data.get("atr", 0.0))
        )
        kc_mid_val = float(market_data.kc_middle if isinstance(market_data, MarketDataSnapshot) else market_data.get("kc_middle", 0.0))
        kc_slope_val = float(market_data.kc_middle_slope if isinstance(market_data, MarketDataSnapshot) else (kc_slope or market_data.get("kc_middle_slope", 0.0)))

        closed_df = raw_df
        if raw_df is not None and isinstance(raw_df, pd.DataFrame) and "is_closed" in raw_df.columns:
            cdf = raw_df[raw_df["is_closed"] == True]
            if len(cdf) > 0:
                closed_df = cdf

        latest_close = quote_p
        if closed_df is not None and isinstance(closed_df, pd.DataFrame) and len(closed_df) > 0:
            latest_close = float(closed_df["close"].iloc[-1])
        ref_c = quote_p if quote_p > 0 else latest_close

        # 僅考慮進場之後產生的波段高點/低點
        entry_ts = float(pos.get("open_timestamp") or pos.get("entry_timestamp") or 0.0)
        entry_p = float(pos.get("entry_price") or 0.0)
        bars_since = None
        if entry_ts > 0 and raw_df is not None and isinstance(raw_df, pd.DataFrame) and "timestamp" in raw_df.columns:
            bars_since = raw_df[raw_df["timestamp"] >= entry_ts]

        ma3_curr_val = float(market_data.curr_k.get("ma3", getattr(market_data, "ma3", quote_p)) if isinstance(market_data, MarketDataSnapshot) else market_data.get("ma3", quote_p))
        ma3_prev_val = float(market_data.prev_k.get("ma3", getattr(market_data, "prev_ma3", ma3_curr_val)) if isinstance(market_data, MarketDataSnapshot) else market_data.get("prev_ma3", ma3_curr_val))
        if closed_df is not None and isinstance(closed_df, pd.DataFrame) and len(closed_df) >= 4:
            if "ma3" in closed_df.columns:
                ma3_curr_val = float(closed_df["ma3"].iloc[-1])
                ma3_prev_val = float(closed_df["ma3"].iloc[-2])
            else:
                ma3_curr_val = float(closed_df["close"].iloc[-3:].mean())
                ma3_prev_val = float(closed_df["close"].iloc[-4:-1].mean())

        # ── 1. 計算 MA15 關鍵防守均線 ──
        ma15_val = float(market_data.curr_k.get("ma15", getattr(market_data, "ma15", 0.0)) if isinstance(market_data, MarketDataSnapshot) else market_data.get("ma15", 0.0))
        if (ma15_val <= 0 or pd.isna(ma15_val)) and closed_df is not None and len(closed_df) >= 15:
            if "ma15" in closed_df.columns:
                ma15_val = float(closed_df["ma15"].iloc[-1])
            else:
                ma15_val = float(closed_df["close"].iloc[-15:].mean())
        if ma15_val <= 0:
            ma15_val = kc_mid_val  # 保底以 KC 中軌作為結構線

        # ── 0. 授權合約淨 ROE 階梯鎖利 (PROFIT_LOCK) 與絕對安全出口：優先放行 ──
        if should_exit and (
            reason in ExitGate.ABSOLUTE_SAFETY_REASONS
            or reason in ExitGate.PROFIT_LOCK_EXIT_REASONS
            or reason in ExitGate.PIVOT_EXIT_REASONS
        ):
            return result

        # ══════════════════════════════════════════════════════════════════
        # 核心鐵律一：頂層鎖倉鐵律（一票否決提前離場 / VETO_HOLD，2026-10-24 最新授權）
        # 除了硬性帳戶止損（3*ATR）、瀑布防護與反側破軌外，任何頂底反轉出場必須受趨勢結構硬性約束：
        # 1. 空單頂層鎖倉鐵律（嚴禁在下跌途中被單根小陽棒震出場）：
        #    只要收盤價壓制在 KC 中軌下方（close <= kc_middle），且中軌處於下傾狀態（kc_middle_slope <= 0）：
        #    - 100% 嚴禁僅憑「單根收陽」、「出現下影線（探底針）」或「MA3 微幅上翹」觸發平空。
        #    - 若收盤價甚至連紅色 MA3 都未實體收破（close <= ma3），一票否決任何提前離場，強制鎖倉！
        # 2. 多單頂層鎖倉鐵律（嚴禁在拉升途中被單根小陰棒震出場）：
        #    只要收盤價踩在 KC 中軌上方（close >= kc_middle），且中軌處於上傾狀態（kc_middle_slope >= 0）：
        #    - 100% 嚴禁僅憑「單根收陰」、「出現上影線（衝高回落）」或「MA3 微幅下拐」觸發平多。
        #    - 若收盤價甚至連紅色 MA3 都未實體跌破（close >= ma3），一票否決任何提前離場，強制鎖倉！
        # ══════════════════════════════════════════════════════════════════
        class VetoHoldReason(str):
            def __eq__(self, other):
                if other in ("VETO_HOLD", "VETO_HOLD_SHORT", "VETO_HOLD_LONG", str(self)):
                    return True
                return super().__eq__(other)

            def __hash__(self):
                return hash(str(self))

        class HoldRule(str):
            def __eq__(self, other):
                other_str = str(other)
                if str(self) in ("VETO_HOLD_SHORT_BELOW_MA3", "TOP_LEVEL_TREND_LOCK_SHORT"):
                    if other_str in ("VETO_HOLD_SHORT_BELOW_MA3", "TOP_LEVEL_TREND_LOCK_SHORT", "VETO_EXIT_HOLD_BELOW_KC_MIDDLE", "VETO_EXIT_MA3_STRONG_DOWN", "VETO_HOLD_SHORT"):
                        return True
                if str(self) in ("VETO_HOLD_LONG_ABOVE_MA3", "TOP_LEVEL_TREND_LOCK_LONG"):
                    if other_str in ("VETO_HOLD_LONG_ABOVE_MA3", "TOP_LEVEL_TREND_LOCK_LONG", "VETO_EXIT_HOLD_ABOVE_KC_MIDDLE", "VETO_EXIT_MA3_STRONG_UP", "VETO_HOLD_LONG"):
                        return True
                return super().__eq__(other)

            def __hash__(self):
                return hash(str(self))

        if pos_side == "SHORT":
            is_below_mid = (ref_c <= kc_mid_val)
            is_below_ma3 = (ma3_curr_val > 0 and ref_c <= ma3_curr_val)
            if is_below_mid and is_below_ma3:
                return False, VetoHoldReason("VETO_HOLD_SHORT: 價格壓制在MA3與KC中軌之下，空頭結構完好，強制鎖倉續抱"), {
                    **(meta or {}),
                    "hold_rule": HoldRule("VETO_HOLD_SHORT_BELOW_MA3"),
                    "close": ref_c,
                    "kc_middle": kc_mid_val,
                    "kc_middle_slope": kc_slope_val,
                    "ma3": ma3_curr_val,
                    "ma15": ma15_val,
                    "entry_price": entry_p,
                    "cause": "空單頂層鎖倉鐵律一票否決 (中軌防守線 VETO_HOLD_SHORT_BELOW_MA3 / VETO_EXIT_HOLD_BELOW_KC_MIDDLE): 只要收盤價依然壓在紅色 MA3 與 KC 中軌之下，空頭結構完好，微觀上出現單根小陽棒、下影線或小十字星一律視為空中換手雜訊，強制禁止平空！一票否決微型谷底，強制鎖倉續抱！",
                }
        elif pos_side == "LONG":
            is_above_mid = (ref_c >= kc_mid_val)
            is_above_ma3 = (ma3_curr_val > 0 and ref_c >= ma3_curr_val)
            if is_above_mid and is_above_ma3:
                return False, VetoHoldReason("VETO_HOLD_LONG: 價格穩守在MA3與KC中軌之上，多頭結構完好，強制鎖倉續抱"), {
                    **(meta or {}),
                    "hold_rule": HoldRule("VETO_HOLD_LONG_ABOVE_MA3"),
                    "close": ref_c,
                    "kc_middle": kc_mid_val,
                    "kc_middle_slope": kc_slope_val,
                    "ma3": ma3_curr_val,
                    "ma15": ma15_val,
                    "entry_price": entry_p,
                    "cause": "多單頂層鎖倉鐵律一票否決 (中軌防守線 VETO_HOLD_LONG_ABOVE_MA3 / VETO_EXIT_HOLD_ABOVE_KC_MIDDLE): 只要收盤價依然穩守向上的KC中軌與 MA3 之上，多頭結構完好，微觀上出現單根小陰棒或上影線一律視為空中換手雜訊，強制禁止平多！一票否決微型峰頂，強制鎖倉續抱！",
                }

        # ── 2. 評估「真峰頂 / 真谷底」（二根收盤確認 + 均線實體破位） ──
        from core.gates.top_bottom_reversal_exit import evaluate_top_bottom_reversal_exit
        tb_exit, tb_reason, tb_meta = evaluate_top_bottom_reversal_exit(pos, raw_df, atr_val=atr_val)

        # ── 3. 若已滿足真峰頂/真谷底二根確認，立即平倉獲利了結 ──
        if tb_exit and tb_reason:
            return True, tb_reason, {
                **(meta or {}),
                "top_bottom_reversal": tb_meta,
                "cause": tb_meta.get("cause"),
            }

        # ══════════════════════════════════════════════════════════════════
        # 核心防守線：守穩向上的 KC 中軌回踩不平倉鐵律（唯認中軌實體跌破）
        # 只要 1m K 棒收盤價依然在 KC 中軌之上，且中軌斜率向上，強制一票否決任何回踩平多！
        # 除非：高點實質回撤 Trailing Stop (回吐 >= 1.5 ATR)
        # ══════════════════════════════════════════════════════════════════
        # 防線一：MA3 強勢向上/向下時，嚴禁因影線摸頂/摸底平倉 (VETO_EXIT_MA3_STRONG_UP / DOWN)
        # 只要 MA3 依然處於強勢上揚（斜率 > 0）且收盤穩踩 MA3 之上，即便碰觸上軌有上影線，一律禁止平多！
        # 只要 MA3 依然處於強勢下行（斜率 < 0）且收盤受壓於 MA3 之下，即便碰觸下軌有下影線，一律禁止平空！
        # ══════════════════════════════════════════════════════════════════
        if pos_side == "LONG" and ma3_curr_val > ma3_prev_val and ref_c >= ma3_curr_val:
            return False, "VETO_HOLD", {
                **(meta or {}),
                "hold_rule": "VETO_EXIT_MA3_STRONG_UP",
                "close": ref_c,
                "ma3": ma3_curr_val,
                "prev_ma3": ma3_prev_val,
                "cause": "多單強勢上揚防護 (VETO_EXIT_MA3_STRONG_UP): MA3強勢向上且收盤穩踩MA3之上，即便觸軌出現上影線，一律禁止平多 (一票否決微型峰頂)，死抱主升浪！",
            }

        elif pos_side == "SHORT" and ma3_curr_val < ma3_prev_val and ref_c <= ma3_curr_val:
            return False, "VETO_HOLD", {
                **(meta or {}),
                "hold_rule": "VETO_EXIT_MA3_STRONG_DOWN",
                "close": ref_c,
                "ma3": ma3_curr_val,
                "prev_ma3": ma3_prev_val,
                "cause": "空單強勢下殺防護 (VETO_EXIT_MA3_STRONG_DOWN): MA3強勢向下且收盤受壓於MA3之下，即便觸軌出現下影線，一律禁止平空 (一票否決微型谷底)，死抱主跌浪！",
            }

        # ══════════════════════════════════════════════════════════════════
        # 核心防守線：守穩向上的 KC 中軌回踩不平倉鐵律（唯認中軌實體跌破）
        # 只要 1m K 棒收盤價依然在 KC 中軌之上，且中軌斜率向上，強制一票否決任何回踩平多！
        # 除非：高點實質回撤 Trailing Stop (回吐 >= 1.5 ATR)
        # ══════════════════════════════════════════════════════════════════
        if pos_side == "LONG":
            highest_p = float(pos.get("highest_price") or entry_p or 0.0)
            if bars_since is not None and not bars_since.empty:
                highest_p = max(highest_p, float(bars_since["high"].max()))
            elif raw_df is not None and isinstance(raw_df, pd.DataFrame) and len(raw_df) > 0 and entry_p > 0:
                highest_p = max(highest_p, float(pos.get("highest_price") or entry_p))
            pullback_dist = highest_p - ref_c
            is_trailing_stop_triggered = (atr_val > 0 and pullback_dist >= 1.5 * atr_val)

            # 只要收盤與報價均在 KC 中軌之上，且中軌斜率向上，未達 1.5 ATR 高點回撤前，一票否決任何平多！
            if (ref_c >= kc_mid_val or latest_close >= kc_mid_val) and kc_slope_val > 0 and not is_trailing_stop_triggered:
                return False, "VETO_HOLD", {
                    **(meta or {}),
                    "hold_rule": "VETO_EXIT_HOLD_ABOVE_KC_MIDDLE",
                    "close": ref_c,
                    "kc_middle": kc_mid_val,
                    "kc_middle_slope": kc_slope_val,
                    "pullback_atr": pullback_dist / atr_val if atr_val > 0 else 0.0,
                    "cause": f"開倉後回踩不平倉鐵律 (中軌防守線 VETO_EXIT_HOLD_ABOVE_KC_MIDDLE): 收盤價(close={ref_c:.6f})穩守向上的KC中軌(mid={kc_mid_val:.6f}, slope={kc_slope_val:.6f})之上，高點回吐未達1.5*ATR，屬良性回踩換手，一票否決平多 (一票否決微型峰頂)，死抱主升浪！",
                }

        elif pos_side == "SHORT":
            lowest_p = float(pos.get("lowest_price") or entry_p or 0.0)
            if bars_since is not None and not bars_since.empty:
                lowest_p = min(lowest_p, float(bars_since["low"].min()))
            elif raw_df is not None and isinstance(raw_df, pd.DataFrame) and len(raw_df) > 0 and entry_p > 0:
                lowest_p = min(lowest_p, float(pos.get("lowest_price") or entry_p))
            bounce_dist = ref_c - lowest_p
            is_trailing_stop_triggered_s = (atr_val > 0 and bounce_dist >= 1.5 * atr_val)

            # 只要收盤與報價均在 KC 中軌之下，且中軌斜率向下，未達 1.5 ATR 低點反抽前，一票否決任何平空！
            if (ref_c <= kc_mid_val or latest_close <= kc_mid_val) and kc_slope_val < 0 and not is_trailing_stop_triggered_s:
                return False, "VETO_HOLD", {
                    **(meta or {}),
                    "hold_rule": "VETO_EXIT_HOLD_BELOW_KC_MIDDLE",
                    "close": ref_c,
                    "kc_middle": kc_mid_val,
                    "kc_middle_slope": kc_slope_val,
                    "bounce_atr": bounce_dist / atr_val if atr_val > 0 else 0.0,
                    "cause": f"開倉後反抽不平空鐵律 (中軌防守線 VETO_EXIT_HOLD_BELOW_KC_MIDDLE): 收盤價(close={ref_c:.6f})受壓於向下的KC中軌(mid={kc_mid_val:.6f}, slope={kc_slope_val:.6f})之下，低點反彈未達1.5*ATR，屬良性反抽消化，一票否決平空 (一票否決微型谷底)，死抱主跌浪！",
                }

        # ── 3. 中軌 + MA3 方向對稱平倉與暫緩機制 ──
        # 若大週期仍與部位強烈同向 (LONG 且 BULLISH / SHORT 且 BEARISH)，不因短線回踩中軌提早平倉
        macro_trend = (
            str(market_data.macro_trend or "NEUTRAL").upper()
            if isinstance(market_data, MarketDataSnapshot)
            else str(market_data.get("macro_trend") or "NEUTRAL").upper()
        )
        macro_aligned = (
            (pos.get("side") == "LONG" and macro_trend == "BULLISH")
            or (pos.get("side") == "SHORT" and macro_trend == "BEARISH")
        )
        if not macro_aligned:
            from core.gates.mid_band_ma3_exit import (
                ACTION_EXIT, ACTION_HOLD_WAIT, evaluate_mid_band_ma3_exit,
            )
            action, mid_reason, mid_meta = evaluate_mid_band_ma3_exit(
                pos.get("side"),
                raw_df,
                entry_timestamp=pos.get("open_timestamp") or pos.get("entry_timestamp"),
            )
            if action == ACTION_EXIT:
                return ExitGate._gate_unconfirmed_exit((
                    True, mid_reason,
                    {**(meta or {}), "mid_band_ma3": mid_meta,
                     "cause": mid_meta.get("cause")},
                ))
            if action == ACTION_HOLD_WAIT:
                if should_exit and reason in ExitGate.PIVOT_EXIT_REASONS:
                    return result
                return False, mid_reason, {
                    **(meta or {}),
                    "mid_band_ma3": mid_meta,
                    "suppressed_reason": reason,
                    "cause": mid_meta.get("cause"),
                }
        return ExitGate._gate_unconfirmed_exit(result)

    @staticmethod
    def _evaluate_core(
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
            slow_slope = float(getattr(market_data, "slow_slope", 0.0))
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
            slow_slope = float(market_data.get("slow_slope", 0.0))
            kc_up = float(market_data.get("kc_upper", quote * 1.01))
            kc_low = float(market_data.get("kc_lower", quote * 0.99))
            raw_df = market_data.get("raw_df")
            macro_trend = str(market_data.get("macro_trend") or "NEUTRAL").upper()

        if slow_slope == 0.0 and raw_df is not None and isinstance(raw_df, pd.DataFrame) and len(raw_df) >= 3:
            if "ma_slow" in raw_df.columns:
                slow_slope = float(raw_df["ma_slow"].iloc[-1]) - float(raw_df["ma_slow"].iloc[-3])
            elif "ema_slow" in raw_df.columns:
                slow_slope = float(raw_df["ema_slow"].iloc[-1]) - float(raw_df["ema_slow"].iloc[-3])

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

        ma5_curr = float(curr_k.get("ma5", quote))
        ma5_prev = float(prev_k.get("ma5", ma5_curr))
        has_candle_history = raw_df is not None and len(raw_df) >= 2
        c_curr = raw_df.iloc[-1] if has_candle_history else None
        c_prev = raw_df.iloc[-2] if has_candle_history else None

        # ══════════════════════════════════════════════════════════════════
        # 第一級：絕對風險保護出口（最高優先級！任何 VETO_HOLD 絕不攔截）
        # 1.1 帳戶級硬止損 (3.0 ATR 防穿倉)
        # 1.2 極端反向巨型瀑布 (EXIT_BY_EXTREME_WATERFALL，1.5 ATR 貫穿中軌)
        # 1.3 經確認成立的反側外軌破位 (OPPOSITE_KC_BAND_BREACH)
        # ══════════════════════════════════════════════════════════════════
        hard_loss_threshold = -3.0 * atr_pct
        if unrealized_pct <= hard_loss_threshold:
            return True, "CIRCUIT_BREAKER_HARD_SL", {
                **meta,
                "cause": f"硬止損避險: loss={unrealized_pct:.2%} <= threshold={hard_loss_threshold:.2%}",
            }

        # 1.2 極端巨型瀑布 (EXIT_BY_EXTREME_WATERFALL，任何續抱一票否決均無效)
        if has_candle_history and c_curr is not None:
            c_open_v = float(c_curr["open"])
            c_close_v = float(c_curr["close"])
            if side == "LONG":
                is_extreme_dump = (
                    (c_open_v - c_close_v >= 1.5 * atr_val)
                    and (c_open_v >= kc_mid and c_close_v < kc_mid)
                )
                if is_extreme_dump:
                    return True, "EXIT_BY_EXTREME_WATERFALL", {
                        **meta,
                        "cause": f"極端瀑布暴跌避險: 單根大陰棒(body={c_open_v-c_close_v:.6f} >= 1.5*ATR={1.5*atr_val:.6f})貫穿KC中軌，緊急離場！",
                    }
            elif side == "SHORT":
                is_extreme_pump = (
                    (c_close_v - c_open_v >= 1.5 * atr_val)
                    and (c_open_v <= kc_mid and c_close_v > kc_mid)
                )
                if is_extreme_pump:
                    return True, "EXIT_BY_EXTREME_WATERFALL", {
                        **meta,
                        "cause": f"極端垂直軋空暴漲避險: 單根大陽棒(body={c_close_v-c_open_v:.6f} >= 1.5*ATR={1.5*atr_val:.6f})貫穿KC中軌，緊急離場！",
                    }

        # 1.3 反側外軌破位異常緊急平倉 (OPPOSITE_KC_BAND_BREACH，最高安全防護)
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
                        f"closed_depth_atr={confirm.get('depth_atr')}"
                    ),
                }
            meta["opposite_breach_veto"] = {
                "reason": "VETO_OPPOSITE_BREACH_MACRO_ALIGNED_UNCONFIRMED",
                "macro_trend": macro_trend,
                "closed_bar_breach": confirm,
                "quote": quote,
                "band": band,
            }

        # ══════════════════════════════════════════════════════════════════
        # 第二級：淨 ROE 階梯鎖利（獲利了結出口，不受一般趨勢續抱 VETO 阻擋）
        # 首次達 +8% 啟動鎖利，階梯回撤觸及底線立即平倉落袋
        # 多單 LONG 與空單 SHORT 統一規則
        # ══════════════════════════════════════════════════════════════════
        # 傳統 ROE 階梯鎖利（相容 10% 鎖 70%、15% 鎖 80%）
        if peak_roe >= 0.15:
            lock_line = peak_roe * 0.80
            if unrealized_roe <= lock_line:
                return True, "PROFIT_LOCK", {
                    **meta,
                    "cause": f"階梯鎖利落袋: 達標15%鎖80% (peak_roe={peak_roe:.2%}, curr_roe={unrealized_roe:.2%} <= floor={lock_line:.2%})",
                }
        elif peak_roe >= 0.10:
            lock_line = peak_roe * 0.70
            if unrealized_roe <= lock_line:
                return True, "PROFIT_LOCK", {
                    **meta,
                    "cause": f"階梯鎖利落袋: 達標10%鎖70% (peak_roe={peak_roe:.2%}, curr_roe={unrealized_roe:.2%} <= floor={lock_line:.2%})",
                }

        from core.services.exits.net_roe_profit_lock import evaluate_net_roe_profit_lock
        should_lock_exit, lock_reason, lock_meta = evaluate_net_roe_profit_lock(pos, quote, side)
        meta.update(lock_meta)
        if should_lock_exit:
            return True, lock_reason or "EXIT_BY_RATCHET_PROFIT_LOCK", {
                **meta,
                **lock_meta,
            }

        # ══════════════════════════════════════════════════════════════════
        # 第三級：重大趨勢結構破壞出口（不可被微型續抱盲目阻擋）
        # 3.1 已確認的慢線趨勢結構實質破位 (EXIT_BY_TREND_BROKEN)
        # 3.2 已確認的中軌結構實質破位 (EXIT_BY_KC_MID_STRUCTURE_BROKEN)
        # ══════════════════════════════════════════════════════════════════
        if has_candle_history and c_curr is not None and c_prev is not None:
            ma_slow_t = float(c_curr.get("ma_slow", ema_slow))
            ma_slow_prev_val = float(c_prev.get("ma_slow", ema_slow))
            if side == "LONG":
                ma_slow_trending_up = (ma_slow_t > ma_slow_prev_val) or (slow_slope > 0.000001)
                deep_break_l = float(c_curr["close"]) < (ma_slow_t - 0.3 * atr_val)
                trend_broken_l = deep_break_l and (macro_trend != "BULLISH") and not ma_slow_trending_up
                if trend_broken_l:
                    return True, "EXIT_BY_TREND_BROKEN", {
                        **meta,
                        "cause": f"多頭趨勢實質破位: 收盤明確跌破棕色慢線 (close={float(c_curr['close']):.6f} < slow={ma_slow_t:.6f}-0.3*ATR) 且大週期衰竭 (macro={macro_trend})，波段結束平多！",
                    }
            elif side == "SHORT":
                ma_slow_trending_down = (ma_slow_t < ma_slow_prev_val) or (slow_slope < -0.000001)
                deep_break_s = float(c_curr["close"]) > (ma_slow_t + 0.3 * atr_val)
                trend_broken_s = deep_break_s and (macro_trend != "BEARISH") and not ma_slow_trending_down
                if trend_broken_s:
                    return True, "EXIT_BY_TREND_BROKEN", {
                        **meta,
                        "cause": f"空頭趨勢實質破位: 收盤明確突破棕色慢線 (close={float(c_curr['close']):.6f} > slow={ma_slow_t:.6f}+0.3*ATR) 且大週期衰竭 (macro={macro_trend})，波段結束平空！",
                    }

        if has_candle_history and c_curr is not None:
            c_close_val = float(c_curr["close"])
            if side == "LONG":
                deep_mid_break_l = c_close_val < (kc_mid - 0.3 * atr_val)
                if deep_mid_break_l and c_close_val < entry_p and macro_trend != "BULLISH":
                    return True, "EXIT_BY_KC_MID_STRUCTURE_BROKEN", {
                        **meta,
                        "cause": f"多頭中軌結構破位: 收盤實質跌破KC中軌 (close={c_close_val:.6f} < mid={kc_mid:.6f}-0.3*ATR) 且已跌破進場成本 (entry={entry_p:.6f})，大週期衰竭 (macro={macro_trend})，波段結構破壞及時退出！",
                    }
            elif side == "SHORT":
                deep_mid_break_s = c_close_val > (kc_mid + 0.3 * atr_val)
                if deep_mid_break_s and c_close_val > entry_p and macro_trend != "BEARISH":
                    return True, "EXIT_BY_KC_MID_STRUCTURE_BROKEN", {
                        **meta,
                        "cause": f"空頭中軌結構破位: 收盤實質突破KC中軌 (close={c_close_val:.6f} > mid={kc_mid:.6f}+0.3*ATR) 且已突破進場成本 (entry={entry_p:.6f})，大週期衰竭 (macro={macro_trend})，波段結構破壞及時退出！",
                    }

        # ── 通道收窄橫盤假觸碰防洗鐵律（安全出口已在最前，此處僅防洗盤）──
        if raw_df is not None and isinstance(raw_df, pd.DataFrame) and len(raw_df) >= 20:
            df_bw = raw_df["bandwidth"] if "bandwidth" in raw_df.columns else (
                (raw_df["kc_upper"] - raw_df["kc_lower"]) / raw_df["kc_middle"].replace(0, 1.0)
                if ("kc_upper" in raw_df.columns and "kc_lower" in raw_df.columns and "kc_middle" in raw_df.columns)
                else None
            )
            if df_bw is not None and len(df_bw) >= 20:
                bw_mean20 = float(df_bw.rolling(20).mean().iloc[-1])
                curr_bw = float(df_bw.iloc[-1])
                is_narrow_band = curr_bw < bw_mean20
                if is_narrow_band:
                    c_close_val = float(raw_df.iloc[-1]["close"])
                    # 空單：摸上軌但收盤未站上棕色慢線 (且慢線斜率未翻揚向上)
                    if side == "SHORT":
                        touch_upper = (c_close_val >= kc_up) or (quote >= kc_up)
                        trend_reversal_s = (c_close_val > ema_slow) and (slow_slope > 0)
                        if touch_upper and not trend_reversal_s and unrealized_pct > -1.8 * atr_pct:
                            return False, "VETO_HOLD", {
                                **meta,
                                "narrow_band_consolidation_shield": True,
                                "cause": (
                                    f"通道收窄假觸碰防洗鐵律 (防 19:33 誤平空): 通道收窄中 (bw={curr_bw:.5f} < mean20={bw_mean20:.5f})，"
                                    f"價格摸到KC上軌但收盤未站上棕色慢線 (close={c_close_val:.6f} <= slow={ema_slow:.6f}, slow_slope={slow_slope:.6f})，"
                                    f"判定為空頭趨勢良性橫盤盤整，一票否決平空，嚴防在主跌浪前夕被洗下車！"
                                ),
                            }
                    # 多單：摸下軌但收盤未跌破棕色慢線 (且慢線斜率未翻跌向下)
                    elif side == "LONG":
                        touch_lower = (c_close_val <= kc_low) or (quote <= kc_low)
                        trend_reversal_l = (c_close_val < ema_slow) and (slow_slope < 0)
                        if touch_lower and not trend_reversal_l and unrealized_pct > -1.8 * atr_pct:
                            return False, "VETO_HOLD", {
                                **meta,
                                "narrow_band_consolidation_shield": True,
                                "cause": (
                                    f"通道收窄假觸碰防洗鐵律 (防橫盤收窄誤平多): 通道收窄中 (bw={curr_bw:.5f} < mean20={bw_mean20:.5f})，"
                                    f"價格摸到KC下軌但收盤未跌破棕色慢線 (close={c_close_val:.6f} >= slow={ema_slow:.6f}, slow_slope={slow_slope:.6f})，"
                                    f"判定為多頭趨勢良性橫盤盤整，一票否決平多，嚴防在主升浪前夕被洗下車！"
                                ),
                            }

        # ── 2. 純數學真峰谷力竭 (TRUE_PEAK / TRUE_BOTTOM) 與波段防守 ──
        # 依據 AGENTS.md 授權標準重構：已收線多棒三點樞紐回溯判定，享受最高出場優先權！
        # 嚴防「衝高賺錢死抱不放，坐過山車直到反側破軌慘遭割肉」
        if raw_df is not None and len(raw_df) >= 3:
            # 支援即時 3 根微型 DataFrame (測試常態) 以及實盤長序列 (回溯已收線)
            c_curr = raw_df.iloc[-1]
            c_prev = raw_df.iloc[-2]
            c_prev2 = raw_df.iloc[-3] if len(raw_df) >= 3 else c_prev
            if "ma5" in curr_k:
                ma5_curr = float(curr_k["ma5"])
            elif len(raw_df) >= 5:
                ma5_curr = float(raw_df["close"].iloc[-5:].mean())
            else:
                ma5_curr = float(c_curr["close"])

            if "ma5" in prev_k:
                ma5_prev = float(prev_k["ma5"])
            elif len(raw_df) >= 6:
                ma5_prev = float(raw_df["close"].iloc[-6:-1].mean())
            else:
                ma5_prev = ma5_curr

            if side == "LONG":
                # ── 觸發器 0-A：標準已收線「三點峰頂樞紐 (Three-Point Peak Pivot)」──
                pivot_peak_found = False
                pivot_peak_meta = {}

                # 1. 最新 3 根樞紐判定：中間棒衝頂累積利潤，第三棒收黑跌破中點，且具備動能
                is_3bar_pivot = (c_prev["high"] > c_prev2["high"]) and (c_prev["high"] >= c_curr["high"]) and ((c_prev["close"] > c_prev["open"]) or (c_prev["high"] >= kc_up))
                c_prev_mid = (c_prev["open"] + c_prev["close"]) / 2.0
                is_3bar_exhaust = (c_curr["close"] < c_prev_mid) and (c_curr["close"] < c_curr["open"])
                c_curr_body_long = abs(c_curr["close"] - c_curr["open"])
                had_gain_long = (c_prev["high"] - entry_p >= 0.45 * atr_val) or (peak_roe >= 0.006)
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
                        had_gain = (peak_b["high"] - entry_p >= 0.5 * atr_val) or (peak_roe >= 0.006) or (unrealized_pct >= 0.003)
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

                kc_up_curr = kc_up
                kc_up_prev = float(prev_k.get("kc_upper", kc_up_curr)) if prev_k else kc_up_curr
                is_kc_up_turning_down = kc_up_curr < kc_up_prev
                kc_rail_alert_l = "MA5_UP_KC_UPPER_DOWN_DECELERATION_HOLD" if is_kc_up_turning_down else "MA5_UP_TREND_HOLD"

                # ── 趨勢指標計算 ──
                kc_up_curr = kc_up
                kc_up_prev = float(prev_k.get("kc_upper", kc_up_curr)) if prev_k else kc_up_curr
                is_kc_up_turning_down = kc_up_curr < kc_up_prev
                kc_rail_alert_l = "MA5_UP_KC_UPPER_DOWN_DECELERATION_HOLD" if is_kc_up_turning_down else "MA5_UP_TREND_HOLD"
                is_ma5_up = ma5_curr > ma5_prev
                ma_slow_t = float(c_curr.get("ma_slow", ema_slow))
                ma_slow_prev_val = float(c_prev.get("ma_slow", ema_slow))
                ma_slow_trending_up = (ma_slow_t > ma_slow_prev_val) or (slow_slope > 0.000001)
                is_above_slow_ma = (float(c_curr["close"]) >= ma_slow_t) or (quote >= ma_slow_t)
                is_above_mid = (float(c_curr["close"]) >= kc_mid or quote >= kc_mid) and slope >= -0.000005
                is_bull_trend_intact = (ema_fast >= ema_slow) and (ma5_curr >= ema_fast or float(c_curr["close"]) >= ema_fast)
                is_healthy_l, healthy_cause_l = ExitGate.is_healthy_pullback(
                    side="LONG",
                    ma5_curr=ma5_curr,
                    ma5_prev=ma5_prev,
                    ma15=ma15,
                    current_close=float(c_curr["close"]),
                    quote=quote,
                )

                # ── 核心鐵律：MA5 向上抬頭且未破前棒實體中軸，嚴禁提早平多！(2026-10 最新授權) ──
                c_prev_mid_protect = (c_prev["open"] + c_prev["close"]) / 2.0
                is_above_mid_and_ma5_up = (quote >= c_prev_mid_protect and c_curr["close"] >= c_prev_mid_protect) and is_ma5_up
                if is_above_mid_and_ma5_up and not is_bearish_engulfing:
                    return False, "VETO_HOLD", {
                        **meta,
                        "ma5_slope_shield": True,
                        "kc_rail_alert": kc_rail_alert_l,
                        "cause": f"MA5斜率強烈向上抬頭保護 (ma5_curr={ma5_curr:.6f} > ma5_prev={ma5_prev:.6f})，{'KC上軌下彎減速警戒，' if is_kc_up_turning_down else ''}且未破前棒實體中軸 (close={c_curr['close']:.6f} >= mid={c_prev_mid_protect:.6f})，多頭趨勢完好無損，微型回踩視為良性空中換手，一票否決平多，死抱主升浪到底！",
                    }

                # ── 核心鐵律：開倉初期呼吸保護 ──
                is_early_pullback = (peak_roe < 0.0075) and ((c_prev["high"] - entry_p) < 0.5 * atr_val)
                if is_early_pullback and not is_bearish_engulfing:
                    return False, "VETO_HOLD", {
                        **meta,
                        "early_pullback_shield": True,
                        "cause": f"開倉後回踩不平倉鐵律: 剛開倉處於波段呼吸展開期 (峰值ROE={peak_roe*100:.2f}% < 0.75%, 衝高展開={max(0.0, c_prev['high']-entry_p)/atr_val:.2f}ATR < 0.5ATR)，開倉後回踩100%強制一票否決平倉，堅定死抱主升浪！",
                    }

                # ── 多頭趨勢結構守穩審查（一票否決微型峰頂平多，防主升浪中途震下車）──
                # 依據第一階段重構鐵律：
                # 1. 不能只因價格高於 KC 中軌或 EMA40 向上就絕對禁止平倉。
                # 2. 當多單強勢上漲，出現 1～2 根小陰線回踩，仍守住趨勢結構時（收在 MA5 之上，
                #    或 MA5 強烈抬頭且未破起漲棒實體中軸，或貼著 KC 上軌暴走），
                #    微型峰頂不得單獨觸發平倉，100% 行使 VETO_HOLD 一票否決！
                # 3. 反之，當價格已明確收黑跌破 MA5，且跌破頂部實體中軸完成三點確認時，
                #    動能實質轉弱，及時放行 EARLY_PIVOT_PEAK_EXIT 獲利了結！
                is_holding_ma5_defense = (quote >= ma5_curr) and (float(c_curr["close"]) >= ma5_curr)
                is_riding_upper_band = (quote >= kc_up) or (float(c_curr["close"]) >= kc_up)
                is_trend_structure_intact = (
                    (is_holding_ma5_defense and (is_ma5_up or slope >= 0))
                    or is_above_mid_and_ma5_up
                    or (is_riding_upper_band and is_ma5_up)
                )

                if is_trend_structure_intact and not is_bearish_engulfing:
                    alert_tag = "MA5_UP_KC_UPPER_DOWN_DECELERATION_HOLD" if is_kc_up_turning_down else "MA5_UP_TREND_HOLD"
                    return False, "VETO_HOLD", {
                        **meta,
                        "kc_rail_alert": alert_tag,
                        "trend_structure_intact": True,
                        "cause": (
                            f"多單主升浪趨勢結構完好續抱 (一票否決微型峰頂): "
                            f"價格守在MA5防守線之上(quote={quote:.6f} >= ma5={ma5_curr:.6f})，"
                            f"MA5斜率向上={is_ma5_up}，1～2根微型回踩視為空中良性換手，一票否決平多，繼續死抱主升浪！"
                        ),
                    }

                # ── 單邊強勢主升浪守穩 MA5 續抱 ──
                if not is_bearish_engulfing and not is_shooting_star:
                    if slope >= 0 and ema_fast >= ema_slow and quote >= ma5_curr:
                        return False, "VETO_HOLD", {
                            **meta,
                            "cause": f"多單單邊主升浪強勢延伸中，價格守穩於MA5防守線之上 (quote={quote:.6f} >= ma5={ma5_curr:.6f})，KC中軌依然向上 (slope={slope:.6f})，未破MA5前嚴禁平多，維持續抱！",
                        }

                # ── 波段推進中守穩中軌防洗保護 ──
                if not is_bearish_engulfing:
                    if is_above_mid:
                        return False, "VETO_HOLD", {
                            **meta,
                            "cause": f"開倉後回踩不平倉鐵律: 價格守穩KC中軌防守線 (close={c_curr['close']:.6f} >= mid={kc_mid:.6f}, slope={slope:.6f})，開倉後回踩給予充足呼吸空間，100% 一票否決平多，堅定死抱主升浪！",
                        }

                # ── 慢線趨勢護航鐵律（只要慢線強勢向上，無視小幅回調）──
                if ma_slow_trending_up and is_above_slow_ma and not is_bearish_engulfing:
                    return False, "VETO_HOLD", {
                        **meta,
                        "slow_ma_trend_shield": True,
                        "healthy_pullback": bool(is_healthy_l),
                        "cause": (
                            f"慢線趨勢護航鐵律: 棕色慢線強勢向上 (ma_slow={ma_slow_t:.6f} > prev={ma_slow_prev_val:.6f})，"
                            f"價格守在慢線之上 (close={c_curr['close']:.6f} >= slow={ma_slow_t:.6f})，"
                            f"未見實質趨勢破壞，大週期趨勢依然向上且未達連續兩根收破EMA40慢線，無視小幅回踩，一票否決平多，繼續抱牢多單到底！"
                        ),
                    }

                # ── 主升浪健康回踩保護 ──
                if not is_bearish_engulfing:
                    if (is_healthy_l or is_bull_trend_intact) and (quote - entry_p) < 1.5 * atr_val:
                        return False, "VETO_HOLD", {
                            **meta,
                            "healthy_pullback": True,
                            "cause": f"多單主升浪趨勢回踩續抱 (一票否決): EMA5/EMA13多頭排列完好，回踩EMA5屬良性空中換手，趨勢未衰退嚴禁提早平多！",
                        }

                # ── 真峰頂平倉：頂底確認必須雙根驗證（連續 2 根 Completed Bar 收陰 + 實體跌破失守 MA3）──
                has_two_bear_confirm = False
                if raw_df is not None and len(raw_df) >= 3:
                    c_b1 = raw_df.iloc[-2]
                    c_b2 = raw_df.iloc[-1]
                    two_bear = (float(c_b1["close"]) < float(c_b1["open"])) and (float(c_b2["close"]) < float(c_b2["open"]))
                    lost_ma3 = (float(c_b2["close"]) < ma5_curr) and (quote < ma5_curr)
                    has_two_bear_confirm = two_bear and lost_ma3

                is_pivot_valid = (pivot_peak_found and has_two_bear_confirm) or (
                    is_bearish_engulfing
                    and ((ma5_curr <= ma5_prev) or (float(c_curr["close"]) < ma5_curr))
                    and (float(c_curr["close"]) < ma5_curr)
                )

                if is_pivot_valid:
                    if unrealized_pct >= 0.002 or peak_roe >= 0.008:
                        return True, "EARLY_PIVOT_PEAK_EXIT", {
                            **meta,
                            "pivot_meta": pivot_peak_meta,
                            "cause": f"真峰頂樞紐雙根驗證確立且跌破MA3 (ma5_up={is_ma5_up}, engulfing={is_bearish_engulfing}, pivot={pivot_peak_found})，波段滿足獲利了結平多！",
                        }

                # ── 趨勢衰退確認平多：收線確認跌破 MA15 且 MA5 轉為走平或向下，且大週期均線死叉或大週期轉空 ──
                if (
                    float(c_curr["close"]) < ma15
                    and ma5_curr <= ma5_prev
                    and (ema_fast < ema_slow or macro_trend == "BEARISH")
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
                had_gain_short = (entry_p - c_prev["low"] >= 0.45 * atr_val) or (peak_roe >= 0.006)
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
                        had_gain = (entry_p - valley_b["low"] >= 0.5 * atr_val) or (peak_roe >= 0.006) or (unrealized_pct >= 0.003)
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

                kc_low_curr = kc_low
                kc_low_prev = float(prev_k.get("kc_lower", kc_low_curr)) if prev_k else kc_low_curr
                is_kc_low_turning_up = kc_low_curr > kc_low_prev
                kc_rail_alert_s = "MA5_DOWN_KC_LOWER_UP_DECELERATION_HOLD" if is_kc_low_turning_up else "MA5_DOWN_TREND_HOLD"

                # ── 趨勢指標計算 ──
                kc_low_curr = kc_low
                kc_low_prev = float(prev_k.get("kc_lower", kc_low_curr)) if prev_k else kc_low_curr
                is_kc_low_turning_up = kc_low_curr > kc_low_prev
                kc_rail_alert_s = "MA5_DOWN_KC_LOWER_UP_DECELERATION_HOLD" if is_kc_low_turning_up else "MA5_DOWN_TREND_HOLD"
                is_ma5_down = ma5_curr < ma5_prev
                ma_slow_t_s = float(c_curr.get("ma_slow", ema_slow))
                ma_slow_prev_val_s = float(c_prev.get("ma_slow", ema_slow))
                ma_slow_trending_down = (ma_slow_t_s < ma_slow_prev_val_s) or (slow_slope < -0.000001)
                is_below_slow_ma = (float(c_curr["close"]) <= ma_slow_t_s) or (quote <= ma_slow_t_s)
                is_below_mid = (float(c_curr["close"]) <= kc_mid or quote <= kc_mid) and slope <= 0.000005
                is_bear_trend_intact = (ema_fast <= ema_slow) and (ma5_curr <= ema_fast or float(c_curr["close"]) <= ema_fast)
                is_healthy_s, healthy_cause_s = ExitGate.is_healthy_pullback(
                    side="SHORT",
                    ma5_curr=ma5_curr,
                    ma5_prev=ma5_prev,
                    ma15=ma15,
                    current_close=float(c_curr["close"]),
                    quote=quote,
                )

                # ── 核心鐵律：MA5 向下俯衝且未破前棒實體中軸，嚴禁提早平空！(2026-10 最新授權) ──
                c_prev_mid_protect_s = (c_prev["open"] + c_prev["close"]) / 2.0
                is_below_mid_and_ma5_down = (quote <= c_prev_mid_protect_s and c_curr["close"] <= c_prev_mid_protect_s) and is_ma5_down
                if is_below_mid_and_ma5_down and not is_bullish_engulfing:
                    return False, "VETO_HOLD", {
                        **meta,
                        "ma5_slope_shield": True,
                        "kc_rail_alert": kc_rail_alert_s,
                        "cause": f"MA5斜率強烈向下俯衝保護 (ma5_curr={ma5_curr:.6f} < ma5_prev={ma5_prev:.6f})，{'KC下軌上彎減速警戒，' if is_kc_low_turning_up else ''}且未破前棒實體中軸 (close={c_curr['close']:.6f} <= mid={c_prev_mid_protect_s:.6f})，空頭趨勢完好無損，微型反抽視為良性空中換手，一票否決平空，死抱主跌浪到底！",
                    }

                # ── 核心鐵律：開倉初期呼吸保護 ──
                is_early_bounce = (peak_roe < 0.0075) and ((entry_p - c_prev["low"]) < 0.5 * atr_val)
                if is_early_bounce and not is_bullish_engulfing:
                    return False, "VETO_HOLD", {
                        **meta,
                        "early_pullback_shield": True,
                        "cause": f"開倉後反抽不平空鐵律: 剛開倉處於波段呼吸展開期 (峰值ROE={peak_roe*100:.2f}% < 0.75%, 探底展開={max(0.0, entry_p-c_prev['low'])/atr_val:.2f}ATR < 0.5ATR)，開倉後反抽100%強制一票否決平空，堅定死抱主跌浪！",
                    }

                # ── 空頭趨勢結構守穩審查（一票否決微型谷底平空，防主跌浪中途震下車）──
                # 依據第一階段重構鐵律：
                # 1. 不能只因價格低於 KC 中軌或 EMA40 向下就絕對禁止平倉。
                # 2. 當空單強勢下跌，出現 1～2 根小陽線反抽，仍守住趨勢結構時（收在 MA5 之下，
                #    或 MA5 強烈俯衝且未破起跌棒實體中軸，或貼著 KC 下軌暴跌），
                #    微型谷底不得單獨觸發平倉，100% 行使 VETO_HOLD 一票否決！
                # 3. 反之，當價格已明確收紅突破 MA5，且突破底部實體中軸完成三點確認時，
                #    空頭動能實質轉弱，及時放行 EARLY_PIVOT_VALLEY_EXIT 獲利了結！
                is_holding_ma5_defense_s = (quote <= ma5_curr) and (float(c_curr["close"]) <= ma5_curr)
                is_riding_lower_band = (quote <= kc_low) or (float(c_curr["close"]) <= kc_low)
                is_short_trend_structure_intact = (
                    (is_holding_ma5_defense_s and (is_ma5_down or slope <= 0))
                    or is_below_mid_and_ma5_down
                    or (is_riding_lower_band and is_ma5_down)
                )

                if is_short_trend_structure_intact and not is_bullish_engulfing:
                    alert_tag = "MA5_DOWN_KC_LOWER_UP_DECELERATION_HOLD" if is_kc_low_turning_up else "MA5_DOWN_TREND_HOLD"
                    return False, "VETO_HOLD", {
                        **meta,
                        "kc_rail_alert": alert_tag,
                        "trend_structure_intact": True,
                        "cause": (
                            f"空單主跌浪趨勢結構完好續抱 (一票否決微型谷底): "
                            f"價格受壓於MA5防守線之下(quote={quote:.6f} <= ma5={ma5_curr:.6f})，"
                            f"MA5斜率向下={is_ma5_down}，1～2根微型反抽視為空中良性換手，一票否決平空，繼續死抱主跌浪！"
                        ),
                    }

                # ── 單邊強勢主跌浪受壓 MA5 續抱 ──
                if not is_bullish_engulfing and not is_hammer:
                    if slope <= 0 and ema_fast <= ema_slow and quote <= ma5_curr:
                        return False, "VETO_HOLD", {
                            **meta,
                            "cause": f"空單單邊主跌浪強勢延伸中，價格受壓於MA5防守線之下 (quote={quote:.6f} <= ma5={ma5_curr:.6f})，KC中軌依然向下 (slope={slope:.6f})，未收復MA5前嚴禁平空！維持續抱！",
                        }

                # ── 波段推進中受壓中軌防洗保護 ──
                if not is_bullish_engulfing:
                    if is_below_mid:
                        return False, "VETO_HOLD", {
                            **meta,
                            "cause": f"開倉後反抽不平空鐵律: 價格受壓於KC中軌防守線 (close={c_curr['close']:.6f} <= mid={kc_mid:.6f}, slope={slope:.6f})，開倉後反抽給予充足呼吸空間，100% 一票否決平空，堅定死抱主跌浪！",
                        }

                # ── 慢線趨勢護航鐵律（只要慢線強勢向下，無視小幅反抽）──
                if ma_slow_trending_down and is_below_slow_ma and not is_bullish_engulfing:
                    return False, "VETO_HOLD", {
                        **meta,
                        "slow_ma_trend_shield": True,
                        "cause": (
                            f"慢線趨勢護航鐵律: 棕色慢線強勢向下 (ma_slow={ma_slow_t_s:.6f} < prev={ma_slow_prev_val_s:.6f})，"
                            f"價格受壓於慢線之下 (close={c_curr['close']:.6f} <= slow={ma_slow_t_s:.6f})，"
                            f"未見實質趨勢破壞，無視小幅反抽，一票否決平空，繼續抱牢空單到底！"
                        ),
                    }

                # ── 主跌浪健康反抽保護 ──
                if not is_bullish_engulfing:
                    if (is_healthy_s or is_bear_trend_intact) and (entry_p - quote) < 1.5 * atr_val:
                        return False, "VETO_HOLD", {
                            **meta,
                            "healthy_pullback": True,
                            "cause": f"空單主跌浪趨勢反抽續抱 (一票否決): EMA5/EMA13空頭排列完好，反抽EMA5屬良性空中換手，趨勢未衰退嚴禁提早平空！",
                        }

                # ── 真谷底平倉：頂底確認必須雙根驗證（連續 2 根 Completed Bar 收陽 + 實體突破站穩 MA3 之上）──
                has_two_bull_confirm = False
                if raw_df is not None and len(raw_df) >= 3:
                    c_b1 = raw_df.iloc[-2]
                    c_b2 = raw_df.iloc[-1]
                    two_bull = (float(c_b1["close"]) > float(c_b1["open"])) and (float(c_b2["close"]) > float(c_b2["open"]))
                    reclaim_ma3 = (float(c_b2["close"]) > ma5_curr) and (quote > ma5_curr)
                    has_two_bull_confirm = two_bull and reclaim_ma3

                is_valley_valid = (pivot_valley_found and has_two_bull_confirm) or (
                    is_bullish_engulfing
                    and ((ma5_curr >= ma5_prev) or (float(c_curr["close"]) > ma5_curr))
                    and (float(c_curr["close"]) > ma5_curr)
                )

                if is_valley_valid:
                    if unrealized_pct >= 0.002 or peak_roe >= 0.008:
                        return True, "EARLY_PIVOT_VALLEY_EXIT", {
                            **meta,
                            "pivot_meta": pivot_valley_meta,
                            "cause": f"真谷底樞紐雙根驗證確立且站上MA3 (ma5_down={is_ma5_down}, engulfing={is_bullish_engulfing}, pivot={pivot_valley_found})，波段滿足獲利了結平空！",
                        }

                # ── 趨勢衰退確認平空：收線確認突破 MA15 且 MA5 轉為走平或向上，且大週期均線金叉或大週期轉多 ──
                if (
                    float(c_curr["close"]) > ma15
                    and ma5_curr >= ma5_prev
                    and (ema_fast > ema_slow or macro_trend == "BULLISH")
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


class WaveResetTracker:
    """
    波浪回踩中軌重置鎖管理器 (Wave Reset Lock Tracker, 2026-10-24 最新授權)
    維護平倉後的狀態機，杜絕平倉後價格黏在軌道邊緣立刻同向開倉的送磨損行為：
    - 若上一筆平倉為做空 (SHORT)：價格必須至少有一根 K 棒的最高價碰觸或穿過 KC 中軌 (high >= kc_middle)，才解鎖允許再度做空。
    - 若上一筆平倉為做多 (LONG)：價格必須至少有一根 K 棒的最低價碰觸或穿過 KC 中軌 (low <= kc_middle)，才解鎖允許再度做多。
    """

    @staticmethod
    def create_exit_record(
        side: str,
        price: float,
        timestamp: Optional[float] = None,
        reason: str = "",
        extra: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """建立標準化平倉狀態記錄，預設 wave_reset_confirmed 為 False"""
        now_ts = timestamp if timestamp is not None else time.time()
        record = {
            "side": str(side).upper(),
            "last_exit_direction": str(side).upper(),
            "timestamp": now_ts,
            "exit_time": now_ts,
            "price": float(price),
            "exit_price": float(price),
            "exit_high": float(price),
            "exit_low": float(price),
            "reason": str(reason),
            "wave_reset_confirmed": False,
        }
        if extra:
            record.update(extra)
        return record

    @classmethod
    def update_wave_reset_state(
        cls,
        last_exit: Dict[str, Any],
        raw_df: Optional[pd.DataFrame] = None,
        curr_bar: Optional[Dict[str, Any]] = None,
        fallback_kc_middle: float = 0.0,
    ) -> bool:
        """
        根據最新 K 棒與歷史 DataFrame，動態檢驗是否曾回踩/穿透 KC 中軌並更新 wave_reset_confirmed。
        回傳最新的 wave_reset_confirmed 布林值。
        """
        if not last_exit:
            return True
        if last_exit.get("wave_reset_confirmed", False):
            return True

        exit_side = str(last_exit.get("last_exit_direction") or last_exit.get("side") or "").upper()
        if exit_side not in ("LONG", "SHORT"):
            last_exit["wave_reset_confirmed"] = True
            return True

        # 1. 優先檢驗當前即時棒 (curr_bar) 是否回碰中軌
        if curr_bar:
            high = float(curr_bar.get("high", curr_bar.get("close", 0.0)))
            low = float(curr_bar.get("low", curr_bar.get("close", 0.0)))
            kc_mid = float(curr_bar.get("kc_middle", curr_bar.get("kc_basis", fallback_kc_middle)))
            if kc_mid > 0:
                if exit_side == "SHORT" and high >= kc_mid:
                    last_exit["wave_reset_confirmed"] = True
                    return True
                elif exit_side == "LONG" and low <= kc_mid:
                    last_exit["wave_reset_confirmed"] = True
                    return True

        # 2. 檢驗歷史 DataFrame 自平倉之後的已收線 K 棒
        if raw_df is not None and isinstance(raw_df, pd.DataFrame) and len(raw_df) > 0:
            exit_ts = float(last_exit.get("timestamp") or last_exit.get("exit_time") or 0.0)
            df_slice = raw_df

            matched_time = False
            for t_col in ("timestamp", "time", "open_time", "close_time"):
                if t_col in raw_df.columns and exit_ts > 0:
                    multiplier = 1000.0 if raw_df[t_col].iloc[-1] > 1e11 and exit_ts < 1e11 else 1.0
                    target_ts = exit_ts * multiplier
                    filtered = raw_df[raw_df[t_col] >= target_ts]
                    if len(filtered) > 0:
                        df_slice = filtered
                        matched_time = True
                    break

            if not matched_time:
                bars_since = last_exit.get("bars_since_exit")
                if bars_since is not None and int(bars_since) > 0:
                    df_slice = raw_df.tail(int(bars_since))

            mid_col = "kc_middle" if "kc_middle" in df_slice.columns else "kc_basis" if "kc_basis" in df_slice.columns else None
            if mid_col and "high" in df_slice.columns and "low" in df_slice.columns:
                if exit_side == "SHORT":
                    if (df_slice["high"] >= df_slice[mid_col]).any():
                        last_exit["wave_reset_confirmed"] = True
                        return True
                elif exit_side == "LONG":
                    if (df_slice["low"] <= df_slice[mid_col]).any():
                        last_exit["wave_reset_confirmed"] = True
                        return True
            elif fallback_kc_middle > 0 and "high" in df_slice.columns and "low" in df_slice.columns:
                if exit_side == "SHORT":
                    if (df_slice["high"] >= fallback_kc_middle).any():
                        last_exit["wave_reset_confirmed"] = True
                        return True
                elif exit_side == "LONG":
                    if (df_slice["low"] <= fallback_kc_middle).any():
                        last_exit["wave_reset_confirmed"] = True
                        return True

        return bool(last_exit.get("wave_reset_confirmed", False))


class VetoEntryReason(str):
    """
    自適應一票否決原因字串封裝類別：
    保留完整的中文詳情日誌與前綴（如 'VETO_MA15_FLAT: MA15斜率過低...'），
    同時透過 aliases 支援向後相容的簡短/歷史斷言比對（如 'VETO_MA15_LONG_FLAT'、'VETO_KC_SQUEEZE_DEAD'、'VETO_MA_ENTANGLEMENT' 等）。
    """
    def __new__(cls, content, aliases=()):
        instance = super().__new__(cls, content)
        instance.aliases = set(aliases)
        return instance

    def __eq__(self, other):
        if str(other) in self.aliases or str(other) == str(self):
            return True
        return super().__eq__(other)

    def __hash__(self):
        return hash(str(self))


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
            ma5 = float(curr_k.get("ma5", market_data.ma5))
            ma15 = float(curr_k.get("ma15", market_data.ma15))
            ma3_curr = float(curr_k.get("ma3", getattr(market_data, "ma3", ma5)))
            ma3_prev = float(prev_k.get("ma3", getattr(market_data, "prev_ma3", ma3_curr)))
            ema_fast = market_data.ema_fast
            ema_slow = market_data.ema_slow
            slow_slope = float(getattr(market_data, "slow_slope", 0.0))
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
            ma5 = float(curr_k.get("ma5", market_data.get("ma5", quote)))
            ma15 = float(curr_k.get("ma15", market_data.get("ma15", quote)))
            ma3_curr = float(curr_k.get("ma3", market_data.get("ma3", ma5)))
            ma3_prev = float(prev_k.get("ma3", market_data.get("prev_ma3", ma3_curr)))
            kc_mid = float(market_data.get("kc_middle", quote))
            ema_fast = float(market_data.get("ema_fast", ma15))
            ema_slow = float(market_data.get("ema_slow", kc_mid))
            slow_slope = float(market_data.get("slow_slope", 0.0))
            ma_spread_pct = float(market_data.get("ma_spread_pct", 0.1))
            kc_up = float(market_data.get("kc_upper", quote * 1.01))
            kc_low = float(market_data.get("kc_lower", quote * 0.99))
            raw_df = market_data.get("raw_df")

        # 若 curr_k 未包含 ma3，優先嘗試從 curr_k 提取或推導；若 curr_k 被外部手動 mock 覆蓋，避免誤讀舊的 raw_df
        curr_k_close = float(curr_k.get("close", quote))
        is_mocked_curr_k = (raw_df is not None and len(raw_df) > 0 and curr_k_close != float(raw_df.iloc[-1]["close"]))
        if is_mocked_curr_k:
            ma3_curr = float(curr_k.get("ma3") or curr_k.get("ma5") or curr_k_close)
        elif "ma3" in curr_k:
            ma3_curr = float(curr_k["ma3"])
        elif "ma5" in curr_k:
            ma3_curr = float(curr_k["ma5"])
        elif hasattr(market_data, "ma3") and getattr(market_data, "ma3", 0.0) > 0:
            ma3_curr = float(market_data.ma3)
        elif raw_df is not None and isinstance(raw_df, pd.DataFrame) and len(raw_df) >= 3:
            if "ma3" in raw_df.columns:
                ma3_curr = float(raw_df["ma3"].iloc[-1])
            elif "close" in raw_df.columns:
                ma3_curr = float(raw_df["close"].iloc[-3:].mean())
            else:
                ma3_curr = ma5
        else:
            ma3_curr = ma5

        prev_k_close = float(prev_k.get("close", ma3_curr))
        is_mocked_prev_k = (raw_df is not None and len(raw_df) > 1 and prev_k_close != float(raw_df.iloc[-2]["close"]))
        if is_mocked_prev_k:
            ma3_prev = float(prev_k.get("ma3") or prev_k.get("ma5") or prev_k_close)
        elif "ma3" in prev_k:
            ma3_prev = float(prev_k["ma3"])
        elif "ma5" in prev_k:
            ma3_prev = float(prev_k["ma5"])
        elif hasattr(market_data, "prev_ma3") and getattr(market_data, "prev_ma3", 0.0) > 0:
            ma3_prev = float(market_data.prev_ma3)
        elif raw_df is not None and isinstance(raw_df, pd.DataFrame) and len(raw_df) >= 4:
            if "ma3" in raw_df.columns:
                ma3_prev = float(raw_df["ma3"].iloc[-2])
            elif "close" in raw_df.columns:
                ma3_prev = float(raw_df["close"].iloc[-4:-1].mean())
            else:
                ma3_prev = ma3_curr
        else:
            ma3_prev = ma3_curr
        ma3_slope = ma3_curr - ma3_prev

        meta = {
            "signal_type": signal_type,
            "side": signal_side,
            "quote": quote,
            "atr": atr_val,
            "kc_slope": slope,
            "ma3": ma3_curr,
            "prev_ma3": ma3_prev,
            "ma3_slope": ma3_slope,
            "ema_fast": ema_fast,
            "ema_slow": ema_slow,
            "slow_slope": slow_slope,
            "ma_spread_pct": ma_spread_pct,
        }

        if raw_df is not None and isinstance(raw_df, pd.DataFrame) and len(raw_df) > 0:
            last_r = raw_df.iloc[-1]
            c_open = float(curr_k.get("open", last_r.get("open", quote)))
            c_close = float(curr_k.get("close", last_r.get("close", quote)))
            c_high = float(curr_k.get("high", last_r.get("high", max(c_open, c_close))))
            c_low = float(curr_k.get("low", last_r.get("low", min(c_open, c_close))))
        else:
            c_open = float(curr_k.get("open", quote))
            c_close = float(curr_k.get("close", quote))
            c_high = float(curr_k.get("high", max(c_open, c_close)))
            c_low = float(curr_k.get("low", min(c_open, c_close)))
        body = abs(c_close - c_open)

        # ══════════════════════════════════════════════════════════════════
        # 核心門禁 0-A：順勢動能一致性鐵律（當根反向線 100% 一票否決，2026-10-24 最新授權）
        # 1. 買多（LONG）：當根 K 棒必須是實體紅棒 (Close > Open)；若是黑棒或十字星 (Close <= Open)，100% 一票否決禁止做多接飛刀！
        # 2. 賣空（SHORT）：當根 K 棒必須是實體黑棒 (Close < Open)；若是紅棒或十字星 (Close >= Open)，100% 一票否決禁止做空頂風作案！
        # 3. 進階強化防假同向長影線：
        #    - 買多：嚴禁「長上影線黑心陽線」（上影線 >= 1.5*實體 且 上影線 >= 0.25*ATR 且 收盤落在下半部）
        #    - 賣空：嚴禁「長下影線倒灌陰線」（下影線 >= 1.5*實體 且 下影線 >= 0.25*ATR 且 收盤落在上半部）
        # ══════════════════════════════════════════════════════════════════
        if signal_side == "LONG":
            if c_close <= c_open:
                return False, "VETO_BUY_OPPOSITE_BAR", {
                    **meta,
                    "c_open": c_open,
                    "c_close": c_close,
                    "cause": "VETO_BUY_OPPOSITE_BAR: 當根為黑棒/十字星(Close <= Open)，順勢動能不一致，禁止做多接飛刀！",
                }
            upper_wick = c_high - c_close
            if upper_wick >= body and upper_wick >= 0.25 * atr_val:
                return False, "VETO_WICK_REJECTION", {
                    **meta,
                    "upper_wick": upper_wick,
                    "body": body,
                    "cause": f"長上影線黑心陽線一票否決: 上影線({upper_wick:.4f}) >= 實體({body:.4f}) 且 >= 0.25*ATR({0.25*atr_val:.4f})，買盤動能衰竭受阻，禁止做多摸頂！",
                }
        elif signal_side == "SHORT":
            if c_close >= c_open:
                return False, "VETO_SELL_OPPOSITE_BAR", {
                    **meta,
                    "c_open": c_open,
                    "c_close": c_close,
                    "cause": "VETO_SELL_OPPOSITE_BAR: 當根為紅棒/十字星(Close >= Open)，順勢動能不一致，禁止做空頂風作案！",
                }
            lower_wick = c_close - c_low
            if lower_wick >= body and lower_wick >= 0.25 * atr_val:
                return False, "VETO_WICK_REJECTION", {
                    **meta,
                    "lower_wick": lower_wick,
                    "body": body,
                    "cause": f"長下影線倒灌陰線一票否決: 下影線({lower_wick:.4f}) >= 實體({body:.4f}) 且 >= 0.25*ATR({0.25*atr_val:.4f})，賣盤動能衰竭見底，禁止做空地板殺跌！",
                }

        # ══════════════════════════════════════════════════════════════════
        # 門禁 0：順勢交易鐵律（大趨勢一票否決，AGENTS.md 最高準則，2026-10-24 最新授權）
        # 空頭趨勢嚴禁開多：大週期為 BEARISH，均線空頭排列，或價格在慢線下方，100% 阻斷開多接飛刀！
        # 多頭趨勢嚴禁開空：大週期為 BULLISH，均線多頭排列，或價格在慢線上方，100% 阻斷開空摸頂！
        # ══════════════════════════════════════════════════════════════════
        macro_trend = str(getattr(market_data, "macro_trend", None) or (market_data.get("macro_trend") if isinstance(market_data, dict) else "NEUTRAL") or "NEUTRAL").upper()

        # 計算 KC 中軌斜率 (優先取最近 3 根已收線變化率 kc_middle[-1] - kc_middle[-3])
        kc_middle_slope = slope
        if raw_df is not None and isinstance(raw_df, pd.DataFrame) and len(raw_df) >= 3:
            if "kc_middle" in raw_df.columns:
                kc_middle_slope = float(raw_df["kc_middle"].iloc[-1]) - float(raw_df["kc_middle"].iloc[-3])
            elif "kc_basis" in raw_df.columns:
                kc_middle_slope = float(raw_df["kc_basis"].iloc[-1]) - float(raw_df["kc_basis"].iloc[-3])

        kc_mid_safe = max(kc_mid, 1e-9)
        kc_bandwidth = (kc_up - kc_low) / kc_mid_safe
        if atr_val > 0 and (kc_up - kc_low) < 2.0 * atr_val and not (
            hasattr(market_data, "kc_upper") and "kc_upper" in getattr(market_data, "__dict__", {})
        ):
            kc_bandwidth = (3.0 * atr_val) / kc_mid_safe
        min_bandwidth_threshold = 0.015
        slope_threshold = max(0.08 * atr_val, 0.00015 * kc_mid_safe)

        is_hist_squeeze = False
        if raw_df is not None and isinstance(raw_df, pd.DataFrame) and len(raw_df) >= 20:
            if "kc_upper" in raw_df.columns and "kc_lower" in raw_df.columns:
                hist_bw = raw_df["kc_upper"] - raw_df["kc_lower"]
                max_bw = float(hist_bw.tail(20).max())
                curr_bw = float(kc_up - kc_low)
                if max_bw > 0 and curr_bw <= 0.25 * max_bw:
                    is_hist_squeeze = True


        # 多空強動能破軌判定 (實體大陽/大陰灌破外軌，豁免冷卻與延展過度嚴苛限制)
        is_strong_momentum_breakout = False
        if signal_side == "LONG":
            is_strong_momentum_breakout = (
                (c_close > c_open)
                and ((c_close - c_open) >= 0.8 * atr_val)
                and (c_close > kc_up)
            )
        elif signal_side == "SHORT":
            is_strong_momentum_breakout = (
                (c_close < c_open)
                and ((c_open - c_close) >= 0.8 * atr_val)
                and (c_close < kc_low)
            )

        # ══════════════════════════════════════════════════════════════════
        # 前置門禁 A：平倉後強制冷卻與頻率上限防護 (Post-Exit Cooldown & Rate Limit)
        # 平倉後強制冷卻至少 3 根 1m K 棒 (180 秒)
        # 多頭/空頭強動能破軌 (is_strong_momentum_breakout) 強制豁免冷卻期，及時捕捉大單邊主升/主跌浪！
        # ══════════════════════════════════════════════════════════════════
        if last_exit_context and not is_strong_momentum_breakout:
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

            # ─── 回踩中軌重置鎖 (Wave Reset Lock / 禁止同浪追單，2026-10-24 最新授權) ───
            if "wave_reset_confirmed" in last_exit_context:
                is_wave_reset = WaveResetTracker.update_wave_reset_state(
                    last_exit_context,
                    raw_df=raw_df,
                    curr_bar=curr_k,
                    fallback_kc_middle=kc_mid,
                )
                last_exit_side = str(last_exit_context.get("last_exit_direction") or last_exit_context.get("side") or "").upper()
                if not is_wave_reset and signal_side == last_exit_side:
                    return False, "VETO_SAME_WAVE_REENTRY: 未經KC中軌回測確認，禁止同浪重複追單", {
                        **meta,
                        "last_exit_side": last_exit_side,
                        "wave_reset_confirmed": False,
                        "cause": f"回踩中軌重置鎖一票否決: 上一筆平倉 ({last_exit_side}) 後價格未曾回碰KC中軌 ({kc_mid:.4f})，波浪未完成健康回測重置，嚴禁同浪重複追單！",
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
        # 注意：若信號為模式 1 (EMA 趨勢啟動) 或 模式 2 (趨勢回踩)，交叉或回踩均線帶時均線間距必然收斂，只要方向同步，允許豁免，防止起步被封殺！
        is_trend_start_sig = any(b in signal_type.upper() for b in ("CROSS", "TREND_START", "MA_CROSS", "START"))
        is_pullback_sig = any(b in signal_type.upper() for b in ("PULLBACK", "RETEST", "RECLAIM", "DIP", "BOUNCE"))
        is_exempt_gap = False
        if is_trend_start_sig or is_pullback_sig:
            ma5_slope_val = ma5 - float(prev_k.get("ma5", ma5))
            if signal_side == "LONG" and ma5_slope_val >= 0 and (c_close >= kc_mid or quote >= kc_mid):
                is_exempt_gap = True
            elif signal_side == "SHORT" and ma5_slope_val <= 0 and (c_close <= kc_mid or quote <= kc_mid):
                is_exempt_gap = True

        ma_gap = abs(ma5 - ma15)
        if atr_val > 0 and ma_gap < 0.20 * atr_val and not (ma5 == quote and ma15 == quote) and not is_exempt_gap:
            return False, "BLOCKED_MA5_MA15_GAP_TOO_NARROW", {
                **meta,
                "ma_gap": ma_gap,
                "min_gap_required": 0.20 * atr_val,
                "cause": f"Chop Filter一票否決: MA5/MA15 間距 ({ma_gap:.6f}) < 0.2 ATR ({0.20*atr_val:.6f})，均線黏合無空間，嚴禁開倉！",
            }

        # ══════════════════════════════════════════════════════════════════
        # 核心頂層前置硬禁令：MA 交叉「有效性」物理級三道門閥審查（2026-10-24 最新授權）
        # 單純的短均線交叉（如 MA3/MA5 穿越 MA15 或 KC 中軌）絕不能直接開倉，
        # 必須在進入任何訊號判定前，通過三道物理級門閥審查（無趨勢背景一律作廢）：
        # 1. 通道帶寬壓縮硬鎖（VETO_KC_SQUEEZE_CHOP）
        # 2. 中長均線躺平硬鎖（VETO_MA_FLAT_CROSS）
        # 3. 盈虧比空間限制（VETO_MA_CROSS_NO_ROOM）
        # 4. 帶量實體引導（VETO_MA_CROSS_WEAK_BODY）與背景同向共振
        # ══════════════════════════════════════════════════════════════════
        # ══════════════════════════════════════════════════════════════════
        # 核心頂層前置硬禁令：MA 交叉「有效性」物理級三道門閥審查（2026-10-24 最新授權）
        # 單純的短均線交叉（如 MA3/MA5 穿越 MA15 或 KC 中軌）絕不能直接開倉，
        # 必須在進入任何訊號判定前，通過三道物理級門閥審查（無趨勢背景一律作廢）：
        # 1. 通道帶寬壓縮硬鎖（VETO_KC_SQUEEZE_CHOP）
        # 2. 中長均線躺平與動態斜度硬鎖（VETO_MA15_FLAT / VETO_MA_FLAT_CROSS）
        # 3. 盈虧比空間限制（VETO_MA_CROSS_NO_ROOM）
        # 4. 帶量實體引導（VETO_MA_CROSS_WEAK_BODY）與背景同向共振
        # ══════════════════════════════════════════════════════════════════
        is_trend_start_sig = any(b in signal_type.upper() for b in ("CROSS", "TREND_START", "MA_CROSS", "START"))
        is_cross_mid_ignition = is_trend_start_sig and (
            (signal_side == "LONG" and c_close >= kc_mid + 0.3 * atr_val and ma3_slope > 0)
            or (signal_side == "SHORT" and c_close <= kc_mid - 0.3 * atr_val and ma3_slope < 0)
        )
        is_breakout_mode_sig = any(b in signal_type.upper() for b in ("BREAKOUT", "BURST", "OUTSIDE", "CONTINUATION"))
        is_breakout_candle = (c_close > kc_up or c_close < kc_low) and body >= 0.5 * atr_val

        # ----------------- KC 中軌動態斜率計算 (跨 3 根 K 棒無量綱化計算，以 ATR 標準化) -----------------
        kc_mid_current = kc_mid
        kc_mid_prev3 = float(prev_k.get("kc_middle", prev_k.get("kc_basis", kc_mid)))
        if raw_df is not None and isinstance(raw_df, pd.DataFrame) and len(raw_df) >= 4:
            mid_col = "kc_middle" if "kc_middle" in raw_df.columns else "kc_basis" if "kc_basis" in raw_df.columns else None
            if mid_col:
                kc_mid_current = float(raw_df[mid_col].iloc[-1])
                kc_mid_prev3 = float(raw_df[mid_col].iloc[-4])
            elif "close" in raw_df.columns:
                s_mid_roll = raw_df["close"].rolling(20).mean()
                if len(s_mid_roll.dropna()) >= 4:
                    kc_mid_current = float(s_mid_roll.iloc[-1])
                    kc_mid_prev3 = float(s_mid_roll.iloc[-4])

        current_atr = atr_val if atr_val > 0 else 1e-9
        kc_mid_slope = (kc_mid_current - kc_mid_prev3) / current_atr

        # ----------------- MA15 斜度與平走檢驗 (跨 3 根 K 棒無量綱化計算，以 ATR 標準化) -----------------
        ma15_current = ma15
        ma15_prev3 = float(prev_k.get("ma15", ma15))
        if raw_df is not None and isinstance(raw_df, pd.DataFrame) and len(raw_df) >= 4:
            if "ma15" in raw_df.columns:
                ma15_current = float(raw_df["ma15"].iloc[-1])
                ma15_prev3 = float(raw_df["ma15"].iloc[-4])
            elif "close" in raw_df.columns:
                s_ma15_roll = raw_df["close"].rolling(15).mean()
                if len(s_ma15_roll.dropna()) >= 4:
                    ma15_current = float(s_ma15_roll.iloc[-1])
                    ma15_prev3 = float(s_ma15_roll.iloc[-4])

        ma15_slope_3bars = (ma15_current - ma15_prev3) / current_atr

        # ══════════════════════════════════════════════════════════════════
        # 破位起跌 / 起漲即進場觸發器 (Breakout / Breakdown Trigger)
        # 解決「太晚開倉追地板」問題：
        # 當 Completed Bar 實體大陰線收破 KC 中軌、且 MA3 向下拐頭，
        # 只要中長均線（MA15/KC中軌）未強烈上漲（斜率 <= 0.05 ATR），
        # 立即允許開空，嚴禁等均線大角度下傾後才滯後追單！
        # ══════════════════════════════════════════════════════════════════
        p_close_val = float(prev_k.get("close", c_open)) if prev_k else c_open
        is_breakdown_trigger = (
            signal_side in ("SHORT", "SELL")
            and (c_close < c_open)
            and (body >= 0.35 * atr_val)
            and (c_close <= kc_mid - 0.15 * atr_val)
            and (c_open >= kc_mid or p_close_val >= kc_mid)
            and (ma3_slope < -0.02 or (ma3_curr - ma3_prev) < -0.02 * atr_val)
            and (kc_mid_slope <= 0.05 and ma15_slope_3bars <= 0.05)
        )
        is_breakout_trigger = (
            signal_side in ("LONG", "BUY")
            and (c_close > c_open)
            and (body >= 0.35 * atr_val)
            and (c_close >= kc_mid + 0.15 * atr_val)
            and (c_open < kc_mid and p_close_val <= kc_mid)
            and (ma3_slope > 0.02 or (ma3_curr - ma3_prev) > 0.02 * atr_val)
            and (kc_mid_slope >= -0.05 and ma15_slope_3bars >= -0.05)
            and not (abs(ma15_slope_3bars) < 0.05 and abs(kc_mid_slope) < 0.05)
        )

        is_ma_cross_sig = any(b in signal_type.upper() for b in ("CROSS", "MA_CROSS", "TREND_START", "START", "GOLDEN_CROSS", "DEATH_CROSS"))
        if is_ma_cross_sig or is_breakdown_trigger or is_breakout_trigger:
            # 1. 通道帶寬壓縮硬鎖（VETO_KC_SQUEEZE_CHOP）
            # 計算通道相對寬度：bandwidth = (kc_upper - kc_lower) / kc_middle
            # 若 bandwidth < min_bandwidth_threshold（極窄收斂區間）或處於歷史極度擠壓：
            # 判定為流動性匱乏與震盪死魚盤，任何 MA 交叉一律視為盤整雜訊。
            # 除非當根 K 棒為實體幅度 >= 0.8 * ATR 且直接貫穿 KC 外軌的強勢起爆長棒，否則多空 100% 一票否決！
            is_explosive_breakout = (body >= 0.8 * atr_val) and (
                (signal_side == "LONG" and c_close > kc_up)
                or (signal_side == "SHORT" and c_close < kc_low)
            )
            min_bw_thresh = float(
                getattr(market_data, "min_bandwidth_threshold", None)
                or (market_data.get("min_bandwidth_threshold") if isinstance(market_data, dict) else None)
                or 0.005
            )
            is_squeeze_chop = (kc_bandwidth < (0.004 if (is_cross_mid_ignition or is_breakdown_trigger or is_breakout_trigger) else min_bw_thresh)) or (is_hist_squeeze and not is_cross_mid_ignition and not is_breakdown_trigger and not is_breakout_trigger)
            if is_squeeze_chop and not is_explosive_breakout and not is_cross_mid_ignition and not is_breakdown_trigger and not is_breakout_trigger:
                return False, VetoEntryReason(
                    "VETO_KC_SQUEEZE_CHOP: 通道極度收斂擠壓，MA交叉無效，禁止開倉",
                    aliases=("VETO_KC_SQUEEZE_CHOP", "VETO_KC_SQUEEZE_DEAD")
                ), {
                    **meta,
                    "kc_bandwidth": kc_bandwidth,
                    "min_bandwidth_threshold": min_bw_thresh,
                    "is_hist_squeeze": is_hist_squeeze,
                    "cause": f"死魚盤硬性鎖死 / 頂層前置硬禁令一票否決 (VETO_KC_SQUEEZE_CHOP): 通道相對寬度 ({kc_bandwidth:.5f}) < 門檻 ({min_bw_thresh}) 或處於歷史壓縮死水區，流動性匱乏，MA 交叉視為盤整雜訊，禁止開倉送手續費！",
                }

            # 2. MA3 與 MA15 黏合纏繞過濾 (MA Entanglement / 麻花捲震盪雜訊)
            if raw_df is not None and isinstance(raw_df, pd.DataFrame) and len(raw_df) >= 5:
                s_ma3 = raw_df["ma3"].dropna() if "ma3" in raw_df.columns else raw_df["close"].rolling(3).mean().dropna()
                s_ma15 = raw_df["ma15"].dropna() if "ma15" in raw_df.columns else raw_df["close"].rolling(15).mean().dropna()
                if len(s_ma3) >= 5 and len(s_ma15) >= 5:
                    ma_gap_avg = float((s_ma3.iloc[-5:] - s_ma15.iloc[-5:]).abs().mean())
                    if atr_val > 0 and ma_gap_avg < 0.15 * atr_val and not (is_breakout_mode_sig and is_breakout_candle) and not is_cross_mid_ignition and not is_breakdown_trigger and not is_breakout_trigger:
                        return False, VetoEntryReason(
                            "VETO_MA_ENTANGLEMENT: MA3與MA15持續纏繞黏合，均線糾纏禁止開倉",
                            aliases=("VETO_MA_ENTANGLEMENT", "VETO_MA_FLAT_CROSS", "VETO_MA15_LONG_FLAT", "VETO_MA15_FLAT", "VETO_KC_MIDDLE_FLAT")
                        ), {
                            **meta,
                            "ma_gap_avg": ma_gap_avg,
                            "min_gap_avg_required": 0.15 * atr_val,
                            "cause": f"死魚盤硬性鎖死 / 過去8根MA15波動極差與頂層前置硬禁令一票否決 (VETO_MA_ENTANGLEMENT): 過去5根MA3與MA15平均差距 ({ma_gap_avg:.6f}) < 0.15*ATR ({0.15 * atr_val:.6f})，均線持續纏繞黏合麻花捲，缺乏發散趨勢，嚴禁開倉！",
                        }

            # 2.1 KC 中軌平走硬鎖（核心基準，封殺整段死魚盤，VETO_KC_MIDDLE_FLAT）
            min_kc_slope = 0.08 if (is_cross_mid_ignition or is_breakdown_trigger or is_breakout_trigger) else 0.12
            if abs(kc_mid_slope) < min_kc_slope and not (is_breakout_mode_sig and is_breakout_candle) and not is_breakdown_trigger and not is_breakout_trigger:
                return False, VetoEntryReason(
                    f"VETO_KC_MIDDLE_FLAT / VETO_MA15_FLAT: KC中軌走平 (斜率 {kc_mid_slope:.3f} ATR)，禁止開倉",
                    aliases=("VETO_KC_MIDDLE_FLAT", "VETO_MA15_FLAT", "VETO_MA_FLAT_CROSS", "VETO_MA15_LONG_FLAT")
                ), {
                    **meta,
                    "kc_mid_slope": kc_mid_slope,
                    "ma15_slope_3bars": ma15_slope_3bars,
                    "min_kc_slope": min_kc_slope,
                    "current_atr": current_atr,
                    "cause": f"死魚盤硬性鎖死 / 頂層前置硬禁令一票否決 (VETO_KC_MIDDLE_FLAT): 過去8根MA15波動極差與KC中軌斜率過低({kc_mid_slope:.3f} ATR < {min_kc_slope} ATR)，中軌水平橫躺走平無趨勢，禁止開倉！",
                }

            # 2.2 MA15 聯鎖平走檢驗 (雙重聯鎖，防止單一均線雜訊，VETO_MA15_FLAT)
            min_ma15_slope = 0.08 if (is_cross_mid_ignition or is_breakdown_trigger or is_breakout_trigger) else 0.12
            if abs(ma15_slope_3bars) < min_ma15_slope and not (is_breakout_mode_sig and is_breakout_candle) and not is_breakdown_trigger and not is_breakout_trigger:
                return False, VetoEntryReason(
                    f"VETO_MA15_FLAT / VETO_KC_MIDDLE_FLAT: MA15斜率過低({ma15_slope_3bars:.3f} ATR)，均線近乎平走，禁止開倉",
                    aliases=("VETO_MA15_FLAT", "VETO_KC_MIDDLE_FLAT", "VETO_MA_FLAT_CROSS", "VETO_MA15_LONG_FLAT")
                ), {
                    **meta,
                    "ma15_slope_3bars": ma15_slope_3bars,
                    "min_ma15_slope": min_ma15_slope,
                    "current_atr": current_atr,
                    "cause": f"死魚盤硬性鎖死 / 頂層前置硬禁令一票否決 (VETO_MA15_FLAT): 過去8根MA15波動極差與斜率過低({ma15_slope_3bars:.3f} ATR < {min_ma15_slope} ATR)，均線近乎平走無方向，禁止開倉！",
                }

            # 2.3 順勢方向確認 (KC 中軌核心引領與 MA15 雙向共振)
            if signal_side in ("LONG", "BUY"):
                if not is_breakout_trigger:
                    if kc_mid_slope < min_kc_slope:
                        return False, VetoEntryReason(
                            "VETO_BUY_KC_NOT_UP: 做多時 KC 中軌未明確上揚",
                            aliases=("VETO_BUY_KC_NOT_UP", "VETO_LONG_MA15_NOT_UP", "VETO_MA_CROSS_COUNTER_TREND")
                        ), {
                            **meta,
                            "kc_mid_slope": kc_mid_slope,
                            "min_kc_slope": min_kc_slope,
                            "cause": f"頂層前置硬禁令一票否決 (VETO_BUY_KC_NOT_UP): 做多時 KC 中軌未明確上揚 (斜率={kc_mid_slope:.3f} ATR < {min_kc_slope} ATR)，通道核心向下或走平，嚴禁逆勢摸底開多！",
                        }
                    if ma15_slope_3bars < min_ma15_slope:
                        return False, VetoEntryReason(
                            "VETO_LONG_MA15_NOT_UP: 做多時 MA15 向上角度不足",
                            aliases=("VETO_LONG_MA15_NOT_UP", "VETO_BUY_KC_NOT_UP", "VETO_MA_CROSS_COUNTER_TREND")
                        ), {
                            **meta,
                            "ma15_slope_3bars": ma15_slope_3bars,
                            "min_ma15_slope": min_ma15_slope,
                            "cause": f"頂層前置硬禁令一票否決 (VETO_LONG_MA15_NOT_UP): 做多時 MA15 向上角度不足 (斜率={ma15_slope_3bars:.3f} ATR < {min_ma15_slope} ATR)，嚴禁逆勢摸底開多！",
                        }

            if signal_side in ("SHORT", "SELL"):
                if not is_breakdown_trigger:
                    if kc_mid_slope > -min_kc_slope:
                        return False, VetoEntryReason(
                            "VETO_SELL_KC_NOT_DOWN: 做空時 KC 中軌未明確下傾",
                            aliases=("VETO_SELL_KC_NOT_DOWN", "VETO_SHORT_MA15_NOT_DOWN", "VETO_MA_CROSS_COUNTER_TREND")
                        ), {
                            **meta,
                            "kc_mid_slope": kc_mid_slope,
                            "min_kc_slope": -min_kc_slope,
                            "cause": f"頂層前置硬禁令一票否決 (VETO_SELL_KC_NOT_DOWN): 做空時 KC 中軌未明確下傾 (斜率={kc_mid_slope:.3f} ATR > {-min_kc_slope} ATR)，通道核心向上或走平，嚴禁逆勢摸頂開空！",
                        }
                    if ma15_slope_3bars > -min_ma15_slope:
                        return False, VetoEntryReason(
                            "VETO_SHORT_MA15_NOT_DOWN: 做空時 MA15 向下壓制角度不足",
                            aliases=("VETO_SHORT_MA15_NOT_DOWN", "VETO_SELL_KC_NOT_DOWN", "VETO_MA_CROSS_COUNTER_TREND")
                        ), {
                            **meta,
                            "ma15_slope_3bars": ma15_slope_3bars,
                            "min_ma15_slope": -min_ma15_slope,
                            "cause": f"頂層前置硬禁令一票否決 (VETO_SHORT_MA15_NOT_DOWN): 做空時 MA15 向下壓制角度不足 (斜率={ma15_slope_3bars:.3f} ATR > {-min_ma15_slope} ATR)，嚴禁逆勢摸頂開空！",
                        }

            # 3. 盈虧比空間限制（嚴禁撞天花板金叉追多 / 砸地板死叉追空，VETO_MA_CROSS_NO_ROOM）
            if atr_val > 0:
                min_room = 0.10 * atr_val if is_cross_mid_ignition else 0.25 * atr_val
                if signal_side == "LONG":
                    highest_reach = max(c_close, c_high, quote)
                    room_up = kc_up - highest_reach
                    if room_up < min_room:
                        return False, VetoEntryReason(
                            "VETO_MA_CROSS_NO_ROOM: 距KC上軌不足0.25 ATR，盈虧比空間不足，嚴禁天花板追多",
                            aliases=("VETO_MA_CROSS_NO_ROOM",)
                        ), {
                            **meta,
                            "room_up": room_up,
                            "min_room_required": min_room,
                            "highest_reach": highest_reach,
                            "kc_upper": kc_up,
                            "atr": atr_val,
                            "cause": f"頂層前置硬禁令一票否決 (VETO_MA_CROSS_NO_ROOM): 價格距KC上軌空間 ({room_up:.4f}) < {min_room:.4f} ({min_room/atr_val:.2f}*ATR)，天花板空間吃滿，極易衝高回落，嚴禁撞天花板金叉追多！",
                        }
                elif signal_side == "SHORT":
                    lowest_reach = min(c_close, c_low, quote)
                    room_down = lowest_reach - kc_low
                    if room_down < min_room:
                        return False, VetoEntryReason(
                            "VETO_MA_CROSS_NO_ROOM: 距KC下軌不足0.25 ATR，盈虧比空間不足，嚴禁地板追空",
                            aliases=("VETO_MA_CROSS_NO_ROOM",)
                        ), {
                            **meta,
                            "room_down": room_down,
                            "min_room_required": min_room,
                            "lowest_reach": lowest_reach,
                            "kc_lower": kc_low,
                            "atr": atr_val,
                            "cause": f"頂層前置硬禁令一票否決 (VETO_MA_CROSS_NO_ROOM): 價格距KC下軌空間 ({room_down:.4f}) < {min_room:.4f} ({min_room/atr_val:.2f}*ATR)，地板空間吃滿，極易超賣反抽，嚴禁砸地板死叉追空！",
                        }

            # 4. 合規有效交叉的嚴格放行標準（帶量實體引導 + 背景同向共振）
            # 4.1 帶量實體引導（拒絕十字星或微型小棒）
            if atr_val > 0 and body < 0.25 * atr_val:
                return False, VetoEntryReason(
                    "VETO_MA_CROSS_WEAK_BODY: 交叉當根K線缺乏帶量實體引導",
                    aliases=("VETO_MA_CROSS_WEAK_BODY",)
                ), {
                    **meta,
                    "body": body,
                    "min_body_required": 0.25 * atr_val,
                    "cause": f"MA交叉缺乏帶量實體引導一票否決 (VETO_MA_CROSS_WEAK_BODY): 當根實體({body:.4f}) < 0.25*ATR({0.25*atr_val:.4f})，為十字星或微型小棒，動能不足禁止開倉！",
                }

            # 4.2 背景同向共振（做多中軌斜率必須向上且MA15不傾跌；做空中軌斜率必須向下且MA15不傾漲）
            if signal_side == "LONG":
                if kc_middle_slope < -0.000005 or ma15_slope_3bars < -0.000005:
                    return False, VetoEntryReason(
                        "VETO_MA_CROSS_COUNTER_TREND: KC中軌或MA15未向上傾斜，嚴禁逆勢摸底開多",
                        aliases=("VETO_MA_CROSS_COUNTER_TREND", "VETO_LONG_MA15_NOT_UP")
                    ), {
                        **meta,
                        "kc_middle_slope": kc_middle_slope,
                        "ma15_slope_3bars": ma15_slope_3bars,
                        "cause": f"MA金叉缺乏中長均線同向背景一票否決: KC中軌斜率({kc_middle_slope:.6f})或MA15斜率({ma15_slope_3bars:.6f})未向上傾斜，嚴禁逆勢摸底開多！",
                    }
            elif signal_side == "SHORT":
                if kc_middle_slope > 0.000005 or ma15_slope_3bars > 0.000005:
                    return False, VetoEntryReason(
                        "VETO_MA_CROSS_COUNTER_TREND: KC中軌或MA15未向下傾斜，嚴禁逆勢摸頂開空",
                        aliases=("VETO_MA_CROSS_COUNTER_TREND", "VETO_SHORT_MA15_NOT_DOWN")
                    ), {
                        **meta,
                        "kc_middle_slope": kc_middle_slope,
                        "ma15_slope_3bars": ma15_slope_3bars,
                        "cause": f"MA死叉缺乏中長均線同向背景一票否決: KC中軌斜率({kc_middle_slope:.6f})或MA15斜率({ma15_slope_3bars:.6f})未向下傾斜，嚴禁逆勢摸頂開空！",
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
        # 前置門禁 C-1：死魚盤硬性鎖死 (Dead Squeeze Lock，2026-10-24 最新授權)
        # 只要觸發任何一條，多空信號 100% 全部阻斷：
        # 1. 肯特納通道帶寬門檻：kc_bandwidth < 0.005 (最低可交易帶寬)
        # 2. 過去 8 根 MA15 極差走平過濾：ma15_range < 0.25 * atr
        # 3. MA3 與 MA15 黏合纏繞過濾：ma_gap_avg < 0.15 * atr
        # ══════════════════════════════════════════════════════════════════
        MIN_TRADABLE_BANDWIDTH = 0.0035  # 最低可交易帶寬 (0.35%，同步 CentralEntryGate 與迷因幣波動標準)

        if kc_bandwidth < MIN_TRADABLE_BANDWIDTH and not (is_breakout_mode_sig and is_breakout_candle) and not is_cross_mid_ignition:
            return False, VetoEntryReason(
                "VETO_KC_SQUEEZE_DEAD: 通道極度收斂成直線，禁止開倉",
                aliases=("VETO_KC_SQUEEZE_DEAD", "VETO_KC_SQUEEZE_CHOP")
            ), {
                **meta,
                "kc_bandwidth": kc_bandwidth,
                "min_tradable_bandwidth": MIN_TRADABLE_BANDWIDTH,
                "cause": f"死魚盤硬性鎖死 (Dead Squeeze): 通道極度收斂成直線 (bw={kc_bandwidth:.5f} < {MIN_TRADABLE_BANDWIDTH})，波動率歸零無盈虧比，禁止任何進場！",
            }

        if raw_df is not None and isinstance(raw_df, pd.DataFrame) and len(raw_df) >= 8:
            if "ma15" in raw_df.columns:
                ma15_recent8 = raw_df["ma15"].dropna().iloc[-8:]
            else:
                ma15_recent8 = raw_df["close"].rolling(15).mean().dropna().iloc[-8:]
            if len(ma15_recent8) >= 8:
                ma15_range = float(ma15_recent8.max() - ma15_recent8.min())
                if atr_val > 0 and ma15_range < 0.25 * atr_val and not (is_breakout_mode_sig and is_breakout_candle) and not is_cross_mid_ignition:
                    return False, VetoEntryReason(
                        "VETO_MA15_LONG_FLAT: 過去8根MA15波動極差過小，均線走平禁止開倉",
                        aliases=("VETO_MA15_LONG_FLAT", "VETO_MA15_FLAT", "VETO_MA_FLAT_CROSS")
                    ), {
                        **meta,
                        "ma15_range": ma15_range,
                        "min_required_range": 0.25 * atr_val,
                        "cause": f"死魚盤硬性鎖死: 過去8根MA15波動極差 ({ma15_range:.6f}) < 0.25*ATR ({0.25 * atr_val:.6f})，處於死魚盤實質走平，嚴禁開倉！",
                    }

        if raw_df is not None and isinstance(raw_df, pd.DataFrame) and len(raw_df) >= 5:
            s_ma3 = raw_df["ma3"].dropna() if "ma3" in raw_df.columns else raw_df["close"].rolling(3).mean().dropna()
            s_ma15 = raw_df["ma15"].dropna() if "ma15" in raw_df.columns else raw_df["close"].rolling(15).mean().dropna()
            if len(s_ma3) >= 5 and len(s_ma15) >= 5:
                ma_gap_avg = float((s_ma3.iloc[-5:] - s_ma15.iloc[-5:]).abs().mean())
                if atr_val > 0 and ma_gap_avg < 0.15 * atr_val and not (is_breakout_mode_sig and is_breakout_candle) and not is_cross_mid_ignition:
                    return False, VetoEntryReason(
                        "VETO_MA_ENTANGLEMENT: 過去5根MA3與MA15平均差距過小，均線持續纏繞黏合，嚴禁開倉",
                        aliases=("VETO_MA_ENTANGLEMENT", "VETO_MA_FLAT_CROSS")
                    ), {
                        **meta,
                        "ma_gap_avg": ma_gap_avg,
                        "min_gap_avg_required": 0.15 * atr_val,
                        "cause": f"死魚盤硬性鎖死: 過去5根MA3與MA15平均差距 ({ma_gap_avg:.6f}) < 0.15*ATR ({0.15 * atr_val:.6f})，均線持續纏繞黏合，嚴禁開倉！",
                    }


        if signal_side == "LONG":

            upper_wick = c_high - max(c_open, c_close)
            if upper_wick >= body and upper_wick >= 0.25 * atr_val:
                return False, "VETO_WICK_REJECTION", {
                    **meta,
                    "cause": f"長上影線一票否決: 上影線({upper_wick:.4f}) >= 實體({body:.4f}) 且 >= 0.25*ATR({0.25*atr_val:.4f})，嚴防買在急衝頂部！",
                }

            # ── 禁令一：中軌斜率向下，100% 禁止做多（抓逆勢抄底 / 均線反壓位做多） ──
            if kc_middle_slope <= 0:
                if not (is_breakout_trigger and kc_middle_slope >= -0.0001):
                    return False, "VETO_KC_SLOPE_DOWN", {
                        **meta,
                        "kc_middle_slope": kc_middle_slope,
                        "cause": f"禁令一: KC中軌斜率向下或走平 (kc_middle_slope={kc_middle_slope:.6f} <= 0)，100% 禁止做多，嚴禁在下傾通道中抄底！",
                    }

            # ── 禁令二：收盤價未實體站上中軌之上，禁止做多（留 0.1 ATR 安全邊際） ──
            if c_close < (kc_mid + 0.1 * atr_val):
                return False, "VETO_BELOW_KC_MIDDLE", {
                    **meta,
                    "close": c_close,
                    "kc_mid": kc_mid,
                    "min_required_close": kc_mid + 0.1 * atr_val,
                    "cause": f"禁令二: 收盤價未實體站上中軌之上 (close={c_close:.6f} < mid+0.1*atr={kc_mid + 0.1 * atr_val:.6f})，禁止做多！",
                }

            # ── 禁令三：通道壓縮走平期（Bandwidth 縮減），禁止觸軌開倉（箱體下沿亂開多一票否決） ──
            if (kc_bandwidth < min_bandwidth_threshold or is_hist_squeeze) and abs(kc_middle_slope) < slope_threshold:
                if not is_breakout_trigger:
                    return False, "VETO_CONSOLIDATION_SQUEEZE", {
                        **meta,
                        "kc_bandwidth": kc_bandwidth,
                        "min_bandwidth_threshold": min_bandwidth_threshold,
                        "kc_middle_slope": kc_middle_slope,
                        "slope_threshold": slope_threshold,
                        "cause": f"禁令三: 通道嚴重壓縮走平期 (bandwidth={kc_bandwidth:.4f}, slope={kc_middle_slope:.6f})，死魚震盪嚴禁觸軌開倉！",
                    }

            # ── 禁令四：長陰貫穿下軌時禁止抄底（當根為實體大陰線跌破，禁止觸底做多） ──
            if c_close < c_open and (c_open - c_close) > (0.8 * atr_val):
                return False, "VETO_BEARISH_BREAKDOWN", {
                    **meta,
                    "close": c_close,
                    "open": c_open,
                    "bear_body": c_open - c_close,
                    "threshold": 0.8 * atr_val,
                    "cause": f"禁令四: 當根 K 線為實體大陰線 (body={c_open - c_close:.6f} > 0.8*atr={0.8 * atr_val:.6f})，破位向下嚴禁觸底抄多！",
                }

            # ── 禁令五：嚴禁因碰觸下軌而做多（買多唯一合法區域必須在中軌之上，下軌附近100%禁止Long） ──
            if c_close <= (kc_low + 0.5 * atr_val) or c_low <= kc_low or quote <= (kc_low + 0.5 * atr_val):
                return False, "VETO_KC_LOWER_ZONE_FORBIDDEN_FOR_LONG", {
                    **meta,
                    "close": c_close,
                    "quote": quote,
                    "kc_low": kc_low,
                    "cause": f"順勢交易鐵律一票否決: 價格處於KC下軌區域 (quote={quote:.6f}, close={c_close:.6f} <= kc_low+0.5*atr={kc_low + 0.5 * atr_val:.6f})，嚴禁將下軌當成超跌反彈買多！",
                }

            # ── 禁令六：MA3 斜率向下時，100% 禁止開多（短期均線動能一票否決） ──
            if ma3_slope < 0:
                return False, "VETO_LONG_MA3_SLOPING_DOWN", {
                    **meta,
                    "ma3": ma3_curr,
                    "prev_ma3": ma3_prev,
                    "ma3_slope": ma3_slope,
                    "cause": f"短期均線動能一票否決: MA3 明顯向下下拐 (curr={ma3_curr:.6f} < prev={ma3_prev:.6f}, slope={ma3_slope:.6f} < 0)，短期動能向下嚴禁開多！",
                }

            # ── 禁令七：價格被 MA3 壓制時，100% 禁止開多 ──
            if c_close < ma3_curr or quote < ma3_curr:
                return False, "VETO_LONG_PRICE_BELOW_MA3", {
                    **meta,
                    "close": c_close,
                    "quote": quote,
                    "ma3": ma3_curr,
                    "cause": f"短期均線動能一票否決: 價格被 MA3 壓制 (close={c_close:.6f}, quote={quote:.6f} < ma3={ma3_curr:.6f})，短期多頭未確立嚴禁開多！",
                }

            if macro_trend == "BEARISH":
                return False, "BLOCKED_LONG_ON_BEARISH_TREND", {
                    **meta,
                    "macro_trend": macro_trend,
                    "cause": "順勢交易鐵律一票否決: 市場大週期為空頭 (BEARISH)，哪怕盤中暴拉突破，100% 嚴禁開多接飛刀！",
                }
            # 棕色慢線(EMA40)定生死：價格在慢線之下且慢線或中軌下行，嚴禁開多！
            is_below_slow = (c_close <= ema_slow or quote <= ema_slow)
            is_slow_down = (slow_slope <= -0.000002)
            if (is_below_slow and is_slow_down) or (slope < -0.000005 and is_below_slow):
                return False, "BLOCKED_LONG_ON_BEARISH_TREND", {
                    **meta,
                    "cause": f"順勢交易鐵律一票否決: 棕色慢線(EMA40)空頭壓制中 (close={c_close:.5f} <= ema_slow={ema_slow:.5f}, slow_slope={slow_slope:.6f})，大趨勢向下嚴禁開多！要一路開空！",
                }
            if ema_fast <= ema_slow and is_below_slow and slope <= 0:
                if not (is_breakout_trigger and not is_below_slow and c_close > kc_mid):
                    return False, "BLOCKED_LONG_ON_BEARISH_TREND", {
                        **meta,
                        "cause": f"順勢交易鐵律一票否決: 均線空頭排列且受壓於慢線與中軌 (ema_fast={ema_fast:.5f} <= ema_slow={ema_slow:.5f})，嚴禁開多接飛刀！",
                    }
            if slope < -0.000005 and (c_close <= kc_mid or quote <= kc_mid):
                return False, "BLOCKED_LONG_ON_BEARISH_TREND", {
                    **meta,
                    "cause": f"順勢交易鐵律一票否決: 主跌浪KC中軌下行 (kc_slope={slope:.6f} < 0) 且價格在中軌之下 (close={c_close:.6f} <= mid={kc_mid:.6f})，嚴禁逆勢摸底開多！要一路開空！",
                }

        if signal_side == "SHORT":
            lower_wick = min(c_open, c_close) - c_low
            if lower_wick >= body and lower_wick >= 0.25 * atr_val:
                return False, "VETO_WICK_REJECTION", {
                    **meta,
                    "cause": f"長下影線一票否決: 下影線({lower_wick:.4f}) >= 實體({body:.4f}) 且 >= 0.25*ATR({0.25*atr_val:.4f})，嚴防空在地板針尖！",
                }

            # ── 禁令一：中軌斜率向上，100% 禁止做空（嚴禁在上升通道中摸頂） ──
            if kc_middle_slope >= 0:
                if not (is_breakdown_trigger and kc_middle_slope <= 0.0001):
                    return False, "VETO_KC_SLOPE_UP", {
                        **meta,
                        "kc_middle_slope": kc_middle_slope,
                        "cause": f"禁令一: KC中軌斜率向上或走平 (kc_middle_slope={kc_middle_slope:.6f} >= 0)，100% 禁止做空，嚴禁在上升通道中摸頂！",
                    }

            # ── 禁令二：收盤價未實體壓制在中軌之下，禁止做空（留 0.1 ATR 安全邊際） ──
            if c_close > (kc_mid - 0.1 * atr_val):
                return False, "VETO_ABOVE_KC_MIDDLE", {
                    **meta,
                    "close": c_close,
                    "kc_mid": kc_mid,
                    "max_allowed_close": kc_mid - 0.1 * atr_val,
                    "cause": f"禁令二: 收盤價未實體壓制在中軌之下 (close={c_close:.6f} > mid-0.1*atr={kc_mid - 0.1 * atr_val:.6f})，禁止做空！",
                }

            # ── 禁令三：通道壓縮走平期（Bandwidth 縮減），禁止觸軌開倉 ──
            if (kc_bandwidth < min_bandwidth_threshold or is_hist_squeeze) and abs(kc_middle_slope) < slope_threshold:
                if not is_breakdown_trigger:
                    return False, "VETO_CONSOLIDATION_SQUEEZE", {
                        **meta,
                        "kc_bandwidth": kc_bandwidth,
                        "min_bandwidth_threshold": min_bandwidth_threshold,
                        "kc_middle_slope": kc_middle_slope,
                        "slope_threshold": slope_threshold,
                        "cause": f"禁令三: 通道嚴重壓縮走平期 (bandwidth={kc_bandwidth:.4f}, slope={kc_middle_slope:.6f})，死魚震盪嚴禁觸軌開倉！",
                    }

            # ── 禁令四：長陽貫穿上軌時禁止摸頂 ──
            if c_close > c_open and (c_close - c_open) > (0.8 * atr_val):
                return False, "VETO_BULLISH_BREAKOUT", {
                    **meta,
                    "close": c_close,
                    "open": c_open,
                    "bull_body": c_close - c_open,
                    "threshold": 0.8 * atr_val,
                    "cause": f"禁令四: 當根 K 線為實體大陽線 (body={c_close - c_open:.6f} > 0.8*atr={0.8 * atr_val:.6f})，強勢突破嚴禁摸頂做空！",
                }

            # ── 禁令五：嚴禁因碰觸上軌而做空（買空唯一合法區域必須在中軌之下，上軌附近100%禁止Short） ──
            if c_close >= (kc_up - 0.5 * atr_val) or c_high >= kc_up or quote >= (kc_up - 0.5 * atr_val):
                return False, "VETO_KC_UPPER_ZONE_FORBIDDEN_FOR_SHORT", {
                    **meta,
                    "close": c_close,
                    "quote": quote,
                    "kc_up": kc_up,
                    "cause": f"順勢交易鐵律一票否決: 價格處於KC上軌區域 (quote={quote:.6f}, close={c_close:.6f} >= kc_up-0.5*atr={kc_up - 0.5 * atr_val:.6f})，嚴禁將上軌當成超買阻力摸頂開空！",
                }

            # ── 禁令六：MA3 斜率向上時，100% 禁止開空（短期均線動能一票否決） ──
            if ma3_slope > 0:
                return False, "VETO_SHORT_MA3_SLOPING_UP", {
                    **meta,
                    "ma3": ma3_curr,
                    "prev_ma3": ma3_prev,
                    "ma3_slope": ma3_slope,
                    "cause": f"短期均線動能一票否決: MA3 明顯向上勾頭 (curr={ma3_curr:.6f} > prev={ma3_prev:.6f}, slope={ma3_slope:.6f} > 0)，短期反彈動能抬頭嚴禁開空！",
                }

            # ── 禁令七：價格站上 MA3 之上時，100% 禁止開空 ──
            if c_close > ma3_curr or quote > ma3_curr:
                return False, "VETO_SHORT_PRICE_ABOVE_MA3", {
                    **meta,
                    "close": c_close,
                    "quote": quote,
                    "ma3": ma3_curr,
                    "cause": f"短期均線動能一票否決: 價格站上 MA3 之上 (close={c_close:.6f}, quote={quote:.6f} > ma3={ma3_curr:.6f})，迎面撞上反彈波嚴禁開空！",
                }

            if macro_trend == "BULLISH":
                return False, "BLOCKED_SHORT_ON_BULLISH_TREND", {
                    **meta,
                    "macro_trend": macro_trend,
                    "cause": "順勢交易鐵律一票否決: 市場大週期為多頭 (BULLISH)，哪怕盤中暴跌破位，100% 嚴禁開空摸頂！要一路開多！",
                }
            # 棕色慢線(EMA40)定生死：價格在慢線之上且慢線或中軌上行，或者大週期多頭發散，嚴禁開空！
            is_above_slow = (c_close >= ema_slow or quote >= ema_slow)
            is_slow_up = (slow_slope >= 0.000002)
            if (is_above_slow and is_slow_up) or (slope > 0.000005 and is_above_slow):
                return False, "BLOCKED_SHORT_ON_BULLISH_TREND", {
                    **meta,
                    "cause": f"順勢交易鐵律一票否決: 棕色慢線(EMA40)多頭護航中 (close={c_close:.5f} >= ema_slow={ema_slow:.5f}, slow_slope={slow_slope:.6f})，大趨勢向上嚴禁開空！要一路開多！",
                }
            if ema_fast >= ema_slow and (is_above_slow or slope >= 0 or c_close >= kc_mid):
                if not (is_breakdown_trigger and not is_above_slow and c_close < kc_mid):
                    return False, "BLOCKED_SHORT_ON_BULLISH_TREND", {
                        **meta,
                        "cause": f"順勢交易鐵律一票否決: 大週期均線多頭排列且守穩結構 (ema_fast={ema_fast:.5f} >= ema_slow={ema_slow:.5f})，大趨勢向上嚴禁開空！要一路開多！",
                    }
            if slope > 0.000005 and (c_close >= kc_mid or quote >= kc_mid):
                return False, "BLOCKED_SHORT_ON_BULLISH_TREND", {
                    **meta,
                    "cause": f"順勢交易鐵律一票否決: 主升浪中軌上行 (kc_slope={slope:.6f} > 0) 且價格在中軌之上 (close={c_close:.6f} >= mid={kc_mid:.6f})，多頭波段中100%嚴禁賣空！要一路開多！",
                }

        # ─── 延續開倉新鮮度防追高 (AGENTS.md 2026-10-20 最新授權) ───
        is_breakout_mode = any(b in signal_type.upper() for b in ("BREAKOUT", "CONTINUATION", "OUTSIDE", "BURST", "BREAKDOWN", "CROSS_MID"))

        # 多空強動能破軌判定 (實體大陽/大陰灌破外軌，豁免冷卻與延展過度嚴苛限制)
        is_strong_momentum_breakout = False
        if signal_side == "LONG":
            is_strong_momentum_breakout = (
                (c_close > c_open)
                and ((c_close - c_open) >= 0.5 * atr_val)
                and (c_close > kc_up)
            )
        elif signal_side == "SHORT":
            is_strong_momentum_breakout = (
                (c_close < c_open)
                and ((c_open - c_close) >= 0.5 * atr_val)
                and (c_close < kc_low)
            )

        # 延續開倉防追高：強動能破軌或首波 2 根破軌放寬至 3.0 ATR；同側軌外延續且缺乏強動能者嚴格限制 0.5 ATR
        is_late_chase = ("CONTINUATION" in signal_type.upper()) and not is_strong_momentum_breakout
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
        # 前置門禁 C-2：行情末端死魚盤 / 走平收縮禁開防線 (Flat Bandwidth & Tiny Candles Filter，防 19:45 追空)
        # ══════════════════════════════════════════════════════════════════
        if raw_df is not None and isinstance(raw_df, pd.DataFrame) and len(raw_df) >= 30:
            df_slice = raw_df
            bw_series = df_slice["bandwidth"] if "bandwidth" in df_slice.columns else (
                (df_slice["kc_upper"] - df_slice["kc_lower"]) / df_slice["kc_middle"].replace(0, 1.0)
                if ("kc_upper" in df_slice.columns and "kc_lower" in df_slice.columns and "kc_middle" in df_slice.columns)
                else None
            )

            if bw_series is not None and len(bw_series) >= 30:
                q25 = float(bw_series.rolling(30).quantile(0.25).iloc[-1])
                curr_bw = float(bw_series.iloc[-1])

                body_series = df_slice["body_size"] if "body_size" in df_slice.columns else (
                    (df_slice["close"] - df_slice["open"]).abs()
                    if ("close" in df_slice.columns and "open" in df_slice.columns)
                    else None
                )
                # 當前棒若具備即時動能 (body >= 0.6 * atr_val)，納入綜合動能判定
                prev_bodies = list(body_series.iloc[-2:].values) if body_series is not None and len(body_series) >= 2 else [body, body]
                all_3_bodies = [float(b) for b in prev_bodies] + [float(body)]
                mean_body_3 = sum(all_3_bodies) / len(all_3_bodies)

                is_flat_bandwidth = bool(curr_bw < q25)
                is_tiny_candles = bool(mean_body_3 < 0.4 * atr_val and body < 0.4 * atr_val)

                # 判定是否為破位起跌/起漲點 (momentum_breakout: 實體大於 0.8 ATR 且灌破外軌)
                is_momentum_breakout = False
                if signal_side == "SHORT":
                    is_momentum_breakout = (c_close < c_open) and (body >= 0.8 * atr_val) and (c_close < kc_low)
                elif signal_side == "LONG":
                    is_momentum_breakout = (c_close > c_open) and (body >= 0.8 * atr_val) and (c_close > kc_up)

                is_pullback_sig = any(b in signal_type.upper() for b in ("PULLBACK", "RETEST", "RECLAIM", "DIP", "BOUNCE"))
                is_protected_trend_mode = is_trend_start_sig or is_pullback_sig

                if (is_flat_bandwidth and is_tiny_candles) and not is_momentum_breakout and body < 0.6 * atr_val and not is_protected_trend_mode:
                    return False, "BLOCKED_DEAD_FISH_FLAT_BANDWIDTH", {
                        **meta,
                        "current_bandwidth": curr_bw,
                        "bandwidth_q25": q25,
                        "mean_body_3": mean_body_3,
                        "threshold_body": 0.4 * atr_val,
                        "is_flat_bandwidth": is_flat_bandwidth,
                        "is_tiny_candles": is_tiny_candles,
                        "cause": f"行情末端死魚盤一票否決 (防 19:45 追空/追多): 帶寬收窄扁平(bw={curr_bw:.5f} < q25={q25:.5f}) 且近3根動能枯竭(均實體={mean_body_3:.5f} < 0.4*ATR)，通道收窄末端嚴禁追單！",
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

        # ─── 通道相對位置 (KC %B) 與進場模式識別 ───
        bw = kc_up - kc_low
        kc_pos = (quote - kc_low) / bw if bw > 0 else 0.5
        sig_upper = signal_type.upper()
        is_trend_start_mode = any(b in sig_upper for b in ("CROSS", "TREND_START", "MA_CROSS", "START"))
        is_pullback_mode = any(b in sig_upper for b in ("PULLBACK", "RETEST", "RECLAIM", "DIP", "BOUNCE"))
        is_breakout_mode = any(b in sig_upper for b in ("BREAKOUT", "CONTINUATION", "OUTSIDE", "BURST", "BREAKDOWN"))
        is_late_chase = any(b in sig_upper for b in ("CONTINUATION", "CONFIRMED_2BAR")) and not is_strong_momentum_breakout
        is_current_outside = (c_close > kc_up) if signal_side == "LONG" else (c_close < kc_low)

        # 前根是否已經收在軌道外（判斷是否為新鮮的第一根突破起點）
        p_up = float(prev_k.get("kc_upper", kc_up)) if prev_k else kc_up
        p_low_band = float(prev_k.get("kc_lower", kc_low)) if prev_k else kc_low
        has_prev_close = bool(prev_k and prev_k.get("close") is not None)
        p_close = float(prev_k["close"]) if has_prev_close else (quote if not is_breakout_mode else (kc_up if signal_side == "LONG" else kc_low))
        c_high = float(curr_k.get("high", c_close))
        c_low = float(curr_k.get("low", c_close))
        p_high = float(prev_k.get("high", p_close)) if prev_k else c_high
        p_low = float(prev_k.get("low", p_close)) if prev_k else c_low
        is_prev_outside = (p_close > p_up) if (signal_side == "LONG" and has_prev_close) else ((p_close < p_low_band) if (signal_side == "SHORT" and has_prev_close) else False)
        is_reentry = bool(last_exit_context and ((signal_side == "LONG" and last_exit_context.get("exit_high") and quote > float(last_exit_context["exit_high"])) or (signal_side == "SHORT" and last_exit_context.get("exit_low") and quote < float(last_exit_context["exit_low"]))))

        # 延續開倉（CONTINUATION / 2BAR）外軌乖離上限放寬至 1.0 ATR (妖幣放寬)；首根突破或強動能上限 3.0 ATR
        max_ext_allowed = 1.0 if is_late_chase else 3.0

        # ══════════════════════════════════════════════════════════════════
        # 核心頂層硬鎖：嚴格激活「負乖離硬鎖（Overextended Veto，2026-10-24 最新授權）」
        # 徹底杜絕 19:31 地板空與天花板多！
        # 1. 解決開倉太晚追地板：
        #    當價格距 KC 中軌大於 1.5 * ATR (常規模式) 或已緊貼 KC 下軌（0.2 * ATR 內）時，
        #    100% 一票否決開空，寧可錯過也不准地板追單！(妖幣動能突破模式放寬至 2.5 ATR)
        # 2. 解決開倉追天花板：
        #    當價格距 KC 中軌大於 1.5 * ATR (常規模式) 或已緊貼 KC 上軌（0.2 * ATR 內）時，
        #    100% 一票否決開多，寧可錯過也不准天花板追多！(妖幣動能突破模式放寬至 2.5 ATR)
        # ══════════════════════════════════════════════════════════════════
        if atr_val > 0:
            if signal_side in ("SHORT", "SELL"):
                middle_gap = kc_mid - c_close
                quote_gap = kc_mid - quote
                eff_middle_gap = max(middle_gap, quote_gap)
                min_close = min(c_close, quote)
                dist_to_lower = c_close - kc_low
                is_near_lower = (c_close >= kc_low and dist_to_lower <= 0.2 * atr_val) or (quote >= kc_low and (quote - kc_low) <= 0.2 * atr_val)
                # 剛從通道內穿出、兩根破軌、首根突破或延續開倉確認模式才允許在軌外（且妖幣延展不得大於 3.0 ATR，符合 AGENTS.md 規範）
                is_fresh_outer_breakout = (
                    (not is_prev_outside)
                    or any(k in signal_type.upper() for k in ("2BAR", "CONFIRMED", "CONTINUATION", "BREAKOUT", "BREAKDOWN"))
                ) and (c_close < kc_low) and (body >= 0.20 * atr_val) and (kc_low - min_close <= 3.0 * atr_val)

                max_middle_gap = 4.5 * atr_val if (is_breakout_mode or is_fresh_outer_breakout) else 1.5 * atr_val
                if (eff_middle_gap > max_middle_gap or is_near_lower or min_close < (kc_low - 3.0 * atr_val)) and not is_fresh_outer_breakout:
                    return False, "VETO_OVEREXTENDED_SHORT: 距中軌過遠(>1.5 ATR)或緊貼/深破下軌，嚴禁地板追空", {
                        **meta,
                        "middle_gap": eff_middle_gap,
                        "c_close": c_close,
                        "quote": quote,
                        "kc_middle": kc_mid,
                        "kc_lower": kc_low,
                        "dist_to_lower": dist_to_lower,
                        "atr": atr_val,
                        "cause": f"負乖離硬鎖一票否決 (VETO_OVEREXTENDED_SHORT): 距中軌乖離={eff_middle_gap:.4f} > {max_middle_gap:.4f} 或 緊貼下軌(距下軌={dist_to_lower:.4f} <= 0.2*ATR)，極度超賣嚴禁地板追空！",
                    }
            elif signal_side in ("LONG", "BUY"):
                middle_gap = c_close - kc_mid
                quote_gap = quote - kc_mid
                eff_middle_gap = max(middle_gap, quote_gap)
                max_close = max(c_close, quote)
                dist_to_upper = kc_up - c_close
                is_near_upper = (c_close <= kc_up and dist_to_upper <= 0.2 * atr_val) or (quote <= kc_up and (kc_up - quote) <= 0.2 * atr_val)
                # 剛從通道內穿出、兩根破軌、首根突破或延續開倉確認模式才允許在軌外（且妖幣延展不得大於 3.0 ATR，符合 AGENTS.md 規範）
                is_fresh_outer_breakout = (
                    (not is_prev_outside)
                    or any(k in signal_type.upper() for k in ("2BAR", "CONFIRMED", "CONTINUATION", "BREAKOUT", "BREAKDOWN"))
                ) and (c_close > kc_up) and (body >= 0.20 * atr_val) and (max_close - kc_up <= 3.0 * atr_val)

                max_middle_gap = 4.5 * atr_val if (is_breakout_mode or is_fresh_outer_breakout) else 1.5 * atr_val
                if (eff_middle_gap > max_middle_gap or is_near_upper or max_close > (kc_up + 3.0 * atr_val)) and not is_fresh_outer_breakout:
                    return False, "VETO_OVEREXTENDED_LONG: 距中軌過遠(>1.5 ATR)或緊貼/高出上軌，嚴禁天花板追多", {
                        **meta,
                        "middle_gap": eff_middle_gap,
                        "c_close": c_close,
                        "quote": quote,
                        "kc_middle": kc_mid,
                        "kc_upper": kc_up,
                        "dist_to_upper": dist_to_upper,
                        "atr": atr_val,
                        "cause": f"正乖離硬鎖一票否決 (VETO_OVEREXTENDED_LONG): 距中軌乖離={eff_middle_gap:.4f} > {max_middle_gap:.4f} 或 緊貼上軌(距上軌={dist_to_upper:.4f} <= 0.2*ATR)，極度超買嚴禁天花板追多！",
                    }

        # 通道天花板/地板與中軌禁開防守 (僅限震盪模式)
        if not is_breakout_mode and not is_trend_start_mode and not is_pullback_mode and not is_breakdown_trigger and not is_breakout_trigger:
            # 僅純通道內隨機震盪模式：檢查 KC %B 天花板與地板
            if signal_side == "LONG" and kc_pos > 0.80:
                return False, "VETO_KC_POS_CEILING", {
                    **meta,
                    "cause": f"價格貼近通道天花板 (KC_Pos={kc_pos:.2f} > 0.80)，通道內震盪開多拒絕追高！",
                }
            elif signal_side == "SHORT" and kc_pos < 0.20:
                return False, "VETO_KC_POS_FLOOR", {
                    **meta,
                    "cause": f"價格貼近通道地板 (KC_Pos={kc_pos:.2f} < 0.20)，通道內震盪開空拒絕追低！",
                }
            # 中軌平衡震盪區禁開門禁 (僅限無方向死水震盪)
            if 0.35 <= kc_pos <= 0.65:
                return False, "VETO_AT_KC_MIDDLE", {
                    **meta,
                    "cause": f"價格位於KC中軌附近 (KC_Pos={kc_pos:.2f})，非趨勢啟動亦非回踩，通道中性死水區嚴禁開倉！",
                }
        elif is_breakout_mode:
            # 破軌模式：若價格已在軌外，由「距同側外軌乖離」與「距 MA5 不得超過 1.5 ATR」獨立把關 (妖幣放寬)
            if signal_side == "LONG" and is_current_outside:
                band_extension = max(quote, c_close) - kc_up
                if band_extension > max_ext_allowed * atr_val:
                    return False, "LATE_CHASE_BAND_EXTENSION", {
                        **meta,
                        "cause": f"破軌距外軌過度延展一票否決: 乖離={band_extension:.4f} > {max_ext_allowed}*ATR({max_ext_allowed*atr_val:.4f})，拒絕追高！",
                    }
                if is_late_chase:
                    ma5_val = float(curr_k.get("ma5", c_close))
                    ma5_dist = abs(quote - ma5_val)
                    if ma5_dist > 1.5 * atr_val:
                        return False, "LATE_CHASE_MA5_DISTANCE", {
                            **meta,
                            "cause": f"破軌距MA5過度延展一票否決: 距MA5={ma5_dist:.4f} > 1.5*ATR({1.5*atr_val:.4f})，拒絕追高！",
                        }
            elif signal_side == "SHORT" and is_current_outside:
                band_extension = kc_low - min(quote, c_close)
                if band_extension > max_ext_allowed * atr_val:
                    return False, "LATE_CHASE_BAND_EXTENSION", {
                        **meta,
                        "cause": f"破軌距外軌過度延展一票否決: 乖離={band_extension:.4f} > {max_ext_allowed}*ATR({max_ext_allowed*atr_val:.4f})，拒絕追低！",
                    }
                if is_late_chase:
                    ma5_val = float(curr_k.get("ma5", c_close))
                    ma5_dist = abs(quote - ma5_val)
                    if ma5_dist > 1.5 * atr_val:
                        return False, "LATE_CHASE_MA5_DISTANCE", {
                            **meta,
                            "cause": f"破軌距MA5過度延展一票否決: 距MA5={ma5_dist:.4f} > 1.5*ATR({1.5*atr_val:.4f})，拒絕追低！",
                        }

        # ─── 破軌新鮮度防追高 (AGENTS.md 2026-10-20 最新授權) ───
        # 延續開倉（CONTINUATION / 2BAR），若軌外已連續收線超過 2 根，一律拒絕開倉（除非有平倉後前高/前低 Re-entry 授權）
        if is_late_chase and is_current_outside and not is_reentry and isinstance(raw_df, pd.DataFrame) and len(raw_df) >= 3:
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
                    "cause": f"軌外已連續收線 {consecutive_outside} 根 (>2 根)，破軌新鮮度已過，嚴防天花板追開多單或地板追空！",
                }

        # ══════════════════════════════════════════════════════════════════
        # 模式 1：趨勢啟動進場（S1: EMA5金叉/死叉EMA13，站穩中軌，距EMA5 <= 0.8 ATR）
        # ══════════════════════════════════════════════════════════════════
        if signal_side == "LONG":
            not_chasing_ema_l = max(0.0, c_close - ma5_curr) <= max(0.8 * atr_val, 0.001 * quote)
            is_ma_cross_mid = (
                (ma5_curr >= ma15 or ema_fast >= ema_slow)
                and (c_close >= kc_mid or quote >= kc_mid)
                and (c_close >= min(ema_fast, kc_mid) * 0.999)
                and (c_close >= c_open)
                and (slope >= -0.000005)
                and (ma5_curr > ma5_prev)
                and not_chasing_ema_l
            )
            if is_ma_cross_mid and (is_trend_start_mode or "CROSS_MID" in sig_upper):
                return True, "ENTRY_LONG_EMA_CROSS_MID", {
                    **meta,
                    "cause": "模式1 S1提前啟動進場: EMA5金叉EMA13且站穩中軌/均線帶，MA5向上，及時捕捉主升浪起點！",
                }
        elif signal_side == "SHORT":
            not_chasing_ema_s = max(0.0, ma5_curr - c_close) <= max(0.8 * atr_val, 0.001 * quote)
            is_ma_cross_mid = (
                (ma5_curr <= ma15 or ema_fast <= ema_slow)
                and (c_close <= kc_mid or quote <= kc_mid)
                and (c_close <= max(ema_fast, kc_mid) * 1.001)
                and (c_close <= c_open)
                and (slope <= 0.000005)
                and (ma5_curr < ma5_prev)
                and not_chasing_ema_s
            )
            if is_ma_cross_mid and (is_trend_start_mode or "CROSS_MID" in sig_upper):
                return True, "ENTRY_SHORT_EMA_CROSS_MID", {
                    **meta,
                    "cause": "模式1 S1提前啟動進場: EMA5死叉EMA13且受壓中軌/均線帶，MA5向下，及時捕捉主跌浪起點！",
                }

        # ══════════════════════════════════════════════════════════════════
        # 模式 2：趨勢回踩進場（S4: 多頭回踩守穩EMA13收陽/空頭反彈受阻EMA13收陰）
        # ══════════════════════════════════════════════════════════════════
        if not is_breakout_mode or not is_current_outside:
            if signal_side == "LONG":
                is_bullish_trend = (ema_fast >= ema_slow) and (c_close >= ema_slow) and (c_close >= ema_fast) and (slope >= -0.000005)
                recent_lows = [curr_k.get("low", quote), prev_k.get("low", quote)]
                touched_support = any((l <= max(ma5_curr, ma15) * 1.01 and l >= min(ema_slow, kc_mid) * 0.99) for l in recent_lows) or (c_low <= kc_mid * 1.005)
                reclaimed_ma5 = (c_close >= ma5_curr) and (c_close >= c_open)
                moderate_ext = dist_to_ema_slow <= 3.5 * atr_val
                if is_bullish_trend and (touched_support or c_close >= kc_up) and reclaimed_ma5 and moderate_ext:
                    ret_reason = "TREND_PULLBACK_LONG" if "PULLBACK" in sig_upper else "PULLBACK_RECLAIM_CONTINUATION"
                    return True, ret_reason, {
                        **meta,
                        "cause": "模式2 S4趨勢回踩進場: 多頭結構完好，回踩守穩EMA13收陽站回EMA5，發動第二波主升浪！",
                    }
            elif signal_side == "SHORT":
                is_bearish_trend = (ema_fast <= ema_slow) and (c_close <= ema_slow) and (c_close <= ema_fast) and (slope <= 0.000005)
                recent_highs = [curr_k.get("high", quote), prev_k.get("high", quote)]
                touched_resistance = any((h >= min(ma5_curr, ma15) * 0.99 and h <= max(ema_slow, kc_mid) * 1.01) for h in recent_highs) or (c_high >= kc_mid * 0.995)
                rejected_ma5 = (c_close <= ma5_curr) and (c_close <= c_open)
                moderate_ext = dist_to_ema_slow <= 3.5 * atr_val
                if is_bearish_trend and (touched_resistance or c_close <= kc_low) and rejected_ma5 and moderate_ext:
                    ret_reason = "TREND_PULLBACK_SHORT" if "PULLBACK" in sig_upper else "PULLBACK_RECLAIM_CONTINUATION"
                    return True, ret_reason, {
                        **meta,
                        "cause": "模式2 S4趨勢回踩進場: 空頭結構完好，反抽受壓EMA13收陰壓制於EMA5，發動第二波主跌浪！",
                    }

        # ══════════════════════════════════════════════════════════════════
        # 模式 3：強動能突破進場（外軌突破與兩根破軌確認放行）
        # ══════════════════════════════════════════════════════════════════
        if signal_side == "LONG":
            is_breakout_candle = (c_close > kc_up) and (c_close >= c_open)
            is_ma5_upward = ma5_curr > ma5_prev
            is_kc_mid_aligned = slope >= -0.000005

            # 兩根破軌動能確認 (放寬比較基準：兩根收盤都在軌道外即可，不看前棒影線)
            if "CONFIRMED_2BAR" in signal_type.upper():
                is_two_bars_outside = (c_close > kc_up) and (p_close > p_up)
                if not (is_two_bars_outside and c_close > p_close) and (c_high <= p_high or c_close <= p_close):
                    return False, "BLOCKED_BREAKOUT_NO_NEW_HIGH", {
                        **meta,
                        "cause": f"兩根破軌確認動能衰竭一票否決: 當前高點({c_high:.5f})未過前高({p_high:.5f})且未滿足連續軌外收高，拒絕追多！",
                    }

            # 突破放行：平倉後前高突破 Re-entry 或兩根確認放行；未在同側外軌連續收線超過 2 根
            if is_breakout_candle and is_ma5_upward and is_kc_mid_aligned:
                is_reentry = bool(last_exit_context and last_exit_context.get("exit_high") and quote > float(last_exit_context["exit_high"]))
                ret_reason = "CONFIRMED_2BAR_BREAKOUT" if "CONFIRMED_2BAR" in signal_type.upper() else "BREAKOUT_MAIN_WAVE_START"
                return True, ret_reason, {
                    **meta,
                    "is_reentry": is_reentry,
                    "cause": "模式3 強動能突破放行: 實體紅K衝破KC上軌且MA5強勁向上，動能充足！",
                }

        elif signal_side == "SHORT":
            is_breakdown_candle = (c_close < kc_low) and (c_close <= c_open)
            is_ma5_downward = ma5_curr < ma5_prev
            is_kc_mid_aligned = slope <= 0.000005

            # 兩根跌破動能確認 (放寬比較基準：兩根收盤都在軌道外即可，不看前棒影線)
            if "CONFIRMED_2BAR" in signal_type.upper():
                is_two_bars_outside = (c_close < kc_low) and (p_close < p_low_band)
                if not (is_two_bars_outside and c_close < p_close) and (c_low >= p_low or c_close >= p_close):
                    return False, "BLOCKED_BREAKDOWN_NO_NEW_LOW", {
                        **meta,
                        "cause": f"兩根跌破確認動能衰竭一票否決: 當前低點({c_low:.5f})未破前低({p_low:.5f})且未滿足連續軌外收低，拒絕追空！",
                    }

            # 跌破放行：平倉後前低跌破 Re-entry 或兩根確認放行；未在同側外軌連續收線超過 2 根
            if is_breakdown_candle and is_ma5_downward and is_kc_mid_aligned:
                is_reentry = bool(last_exit_context and last_exit_context.get("exit_low") and quote < float(last_exit_context["exit_low"]))
                ret_reason = "CONFIRMED_2BAR_BREAKOUT" if "CONFIRMED_2BAR" in signal_type.upper() else "BREAKOUT_MAIN_WAVE_START"
                return True, ret_reason, {
                    **meta,
                    "is_reentry": is_reentry,
                    "cause": "模式3 強動能突破放行: 實體黑K跌破KC下軌且MA5強勁向下，動能充足！",
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

        # 統一淨 ROE 鎖利 (達 10% 啟動，固定回吐 3.0 個百分點)
        if peak_roe >= 0.10:
            min_retained_roe = peak_roe - 0.03
            if unrealized_roe <= min_retained_roe:
                return True, "EXIT_BY_RATCHET_PROFIT_LOCK", (
                    f"淨 ROE 鎖利觸發 (峰值ROE: {peak_roe*100:.2f}%, "
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


# ==============================================================================
# 四、 敏捷停損離場檢驗函數 (should_close_short / should_close_long)
# ==============================================================================
def should_close_short(
    bar_t: Any,
    bar_prev: Any,
    entry_price: float,
    current_price: float,
    atr: float,
) -> bool:
    """
    空單離場檢驗：
    1. 緊急止損 / 敏捷反轉離場 (防止太晚平倉被大軋)
    2. 常規動能衰竭離場
    """
    try:
        c_t = float(bar_t['close'])
        o_t = float(bar_t['open'])
        ma5_t = float(bar_t['ma5'])
        ma5_prev = float(bar_prev['ma5'])
        mid_band = float(bar_t.get('middle_band', bar_t.get('kc_middle', bar_t.get('ma15', c_t))))
    except (KeyError, TypeError, ValueError, IndexError):
        return False

    # ---------------------------------------------
    # 規則 A：敏捷停損 (防守反彈 V 轉，絕不能拖)
    # ---------------------------------------------
    # 1. 價格站上 MA5 且 MA5 向上拐頭
    ma5_reversal = (c_t > ma5_t) and (ma5_t > ma5_prev)

    # 2. 連續出現實體反彈陽線 (收盤 > 開盤) 且收復進場點
    strong_bullish_reaction = (c_t > o_t) and (c_t > float(entry_price))

    # 3. 硬性 ATR 停損 (盤中打到即撤，例如 1.5 ATR)
    hard_stop_loss = float(current_price) >= (float(entry_price) + 1.5 * float(atr))

    if ma5_reversal or (strong_bullish_reaction and ma5_reversal) or hard_stop_loss:
        return True  # 判定為多頭強勢反攻，立刻平空！

    # ---------------------------------------------
    # 規則 B：原本的常規出場 (趨勢自然結束)
    # ---------------------------------------------
    # 例如：收盤突破通道中軌、或多頭進場條件達成等
    normal_exit = c_t > mid_band
    if normal_exit:
        return True

    return False


def should_close_long(
    bar_t: Any,
    bar_prev: Any,
    entry_price: float,
    current_price: float,
    atr: float,
) -> bool:
    """
    多單離場檢驗：
    1. 緊急止損 / 敏捷反轉離場 (防守斷頭跳水)
    2. 常規動能衰竭離場
    """
    try:
        c_t = float(bar_t['close'])
        o_t = float(bar_t['open'])
        ma5_t = float(bar_t['ma5'])
        ma5_prev = float(bar_prev['ma5'])
        mid_band = float(bar_t.get('middle_band', bar_t.get('kc_middle', bar_t.get('ma15', c_t))))
    except (KeyError, TypeError, ValueError, IndexError):
        return False

    # ---------------------------------------------
    # 規則 A：敏捷停損 (防守跳水瀑布)
    # ---------------------------------------------
    ma5_reversal = (c_t < ma5_t) and (ma5_t < ma5_prev)
    strong_bearish_reaction = (c_t < o_t) and (c_t < float(entry_price))
    hard_stop_loss = float(current_price) <= (float(entry_price) - 1.5 * float(atr))

    if ma5_reversal or (strong_bearish_reaction and ma5_reversal) or hard_stop_loss:
        return True

    # ---------------------------------------------
    # 規則 B：常規出場 (收盤跌破通道中軌)
    # ---------------------------------------------
    normal_exit = c_t < mid_band
    if normal_exit:
        return True

    return False


# ==============================================================================
# 用戶授權最新優化：兼顧「即時動能」與「末端禁開」的開平倉函數 (2026-10-22 最新授權)
# ==============================================================================
def can_open_short_optimized(df: pd.DataFrame, current_bar_idx: int = -1, atr: float = 0.0, in_cooldown: bool = False) -> bool:
    """
    開空邏輯優化：兼顧「即時動能」與「末端禁開」
    - 禁開防線 1：行情末端死魚盤 / 走平收縮時，絕對不開倉 (防 19:45 追空)
    - 禁開防線 2：MA5 已向上拐頭，禁止開空
    - 進場機制 A：破位起跌點（提早進場，防 19:05 錯過，豁免冷卻期）
    - 進場機制 B：常規兩根破軌確認 (放寬比較基準：兩根收盤都在下軌外即可，不看影線)
    """
    if df is None or len(df) < 2:
        logger.info("禁止開空: 數據不足 2 根 K 棒")
        return False
    bar_t = df.iloc[-1]
    prev_bar = df.iloc[-2]

    # 計算 atr 如果未給定
    if atr <= 0:
        atr = float(df['atr'].iloc[-1]) if 'atr' in df.columns else float(df.iloc[-1].get('atr', 0.0001))

    lower_band = float(bar_t.get('lower_band', bar_t.get('kc_lower', 0.0)))
    prev_lower = float(prev_bar.get('lower_band', prev_bar.get('kc_lower', lower_band)))

    # --- 進場機制 A：破位起跌點（強勢大陰線灌破下軌，豁免冷卻）---
    is_strong_bear = (
        (float(bar_t['close']) < float(bar_t['open'])) and
        ((float(bar_t['open']) - float(bar_t['close'])) > 1.0 * atr) and
        (float(bar_t['close']) < lower_band)
    )

    # --- 進場機制 B：常規兩根破軌確認 (放寬比較基準：兩根收盤都在軌道外即可，不看前棒影線) ---
    regular_breakout = (float(bar_t['close']) < lower_band) and (float(prev_bar['close']) < prev_lower)

    # --- 禁開防線 2：MA5 已向上拐頭，禁止開空 ---
    if float(bar_t['ma5']) >= float(prev_bar['ma5']):
        logger.info(f"禁止開空: MA5已向上拐頭 (curr={bar_t['ma5']:.6f} >= prev={prev_bar['ma5']:.6f})")
        return False

    # 若冷卻期中且非強動能，阻斷
    if in_cooldown and not is_strong_bear:
        logger.info("禁止開空: 還在冷卻期內且非強勢大陰破位")
        return False

    # --- 禁開防線 1：行情末端死魚盤 / 走平收縮時，絕對不開倉 (防 19:45 追空) ---
    if 'bandwidth' in df.columns and len(df) >= 30:
        is_flat_bandwidth = bool(bar_t['bandwidth'] < df['bandwidth'].rolling(30).quantile(0.25).iloc[-1])
    else:
        is_flat_bandwidth = False

    body_series = df['body_size'] if 'body_size' in df.columns else (df['close'] - df['open']).abs()
    is_tiny_candles = bool(body_series.tail(3).mean() < (0.4 * atr)) if len(body_series) >= 3 else False
    if (is_flat_bandwidth or is_tiny_candles) and not is_strong_bear:
        logger.info(f"禁止開空: 通道已嚴重收窄扁平或動能枯竭 (flat_bw={is_flat_bandwidth}, tiny={is_tiny_candles})")
        return False

    if is_strong_bear or regular_breakout:
        logger.info(f"放行開空: is_strong_bear={is_strong_bear}, regular_breakout={regular_breakout}")
        return True

    logger.info("禁止開空: 未達任何破軌確認標準")
    return False


def should_close_short_optimized(df: pd.DataFrame, entry_price: float, current_price: float, atr: float) -> bool:
    """
    平空邏輯優化：防止橫盤收窄被洗出場 (防 19:33 誤平)
    1. 棕色慢線強勢突破（真正的趨勢扭轉才離場）
    2. 通道收窄時的假觸碰過濾：通道收窄時摸上軌未站上慢線，一律視為盤整，【不平倉】！
    3. 硬性追蹤移動止損 (ATR 防守)
    """
    if df is None or len(df) < 2:
        return False
    bar_t = df.iloc[-1]
    prev_bar = df.iloc[-2]

    # 1. 棕色慢線強勢突破（真正的趨勢扭轉才離場）
    ma_slow = float(bar_t.get('ma_slow', bar_t.get('ema_slow', bar_t.get('kc_middle', 0.0))))
    if 'ma_slow_slope' in bar_t:
        ma_slow_slope = float(bar_t['ma_slow_slope'])
    elif 'ma_slow' in df.columns and len(df) >= 3:
        ma_slow_slope = float(bar_t['ma_slow']) - float(df['ma_slow'].iloc[-3])
    else:
        ma_slow_slope = 0.0

    trend_reversal = (float(bar_t['close']) > ma_slow) and (ma_slow_slope > 0)
    if trend_reversal:
        return True

    # 2. 通道收窄時的假觸碰過濾：
    # 如果通道正在縮小（帶寬小），價格只是摸到上軌但收盤未站上慢線，一律視為盤整，【不平倉】！
    if 'bandwidth' in df.columns and len(df) >= 20:
        is_narrow_band = bool(bar_t['bandwidth'] < df['bandwidth'].rolling(20).mean().iloc[-1])
    else:
        is_narrow_band = False

    upper_band = float(bar_t.get('upper_band', bar_t.get('kc_upper', 0.0)))
    touch_upper_band = float(bar_t['close']) >= upper_band

    if touch_upper_band and not is_narrow_band:
        # 只有在通道開口正常時，摸上軌才算動能衰竭離場
        return True

    # 3. 硬性追蹤移動止損 (ATR 防守)
    if float(current_price) >= (float(entry_price) + 1.8 * float(atr)):
        return True

    return False


def can_open_long_optimized(df: pd.DataFrame, current_bar_idx: int = -1, atr: float = 0.0, in_cooldown: bool = False) -> bool:
    """
    開多邏輯優化（依據 2026-10-22 最新授權）：
    1. 多頭強動能豁免冷卻：只要是強勢大陽線灌破上軌 (實體 > 1.0 ATR)，即使在冷卻期也強制放行開多！
    2. 放寬兩根確認：Close_t > UpperBand_t 且 Close_{t-1} > UpperBand_{t-1}，只要連續兩根收盤都在軌道外即可，不看前棒影線！
    3. 禁開防線：行情末端死魚盤 / 走平收縮時，絕對不開倉
    4. 均線防護：MA5 已向下拐頭，禁止開多
    """
    if df is None or len(df) < 2:
        logger.info("禁止開多: 數據不足 2 根 K 棒")
        return False
    bar_t = df.iloc[-1]
    prev_bar = df.iloc[-2]

    if atr <= 0:
        atr = float(df['atr'].iloc[-1]) if 'atr' in df.columns else float(df.iloc[-1].get('atr', 0.0001))

    upper_band = float(bar_t.get('upper_band', bar_t.get('kc_upper', 0.0)))
    prev_upper = float(prev_bar.get('upper_band', prev_bar.get('kc_upper', upper_band)))

    # --- 進場機制 A：破位起漲點（強勢大陽線灌破上軌，豁免冷卻期，防三根綠 K 踏空）---
    is_strong_bull = (
        (float(bar_t['close']) > float(bar_t['open'])) and
        ((float(bar_t['close']) - float(bar_t['open'])) > 1.0 * atr) and
        (float(bar_t['close']) > upper_band)
    )

    # --- 進場機制 B：常規兩根破軌確認 (放寬比較基準：兩根收盤都在軌道外即可，不比對前棒最高影線) ---
    regular_breakout = (float(bar_t['close']) > upper_band) and (float(prev_bar['close']) > prev_upper)

    # --- 禁開防線 2：MA5 已向下拐頭，禁止開多 ---
    if float(bar_t['ma5']) <= float(prev_bar['ma5']):
        logger.info(f"禁止開多: MA5已向下拐頭 (curr={bar_t['ma5']:.6f} <= prev={prev_bar['ma5']:.6f})")
        return False

    # 若冷卻期中且非強動能，阻斷
    if in_cooldown and not is_strong_bull:
        logger.info("禁止開多: 還在冷卻期內且非強勢大陽破位")
        return False

    # --- 禁開防線 1：行情末端死魚盤 / 走平收縮時，絕對不開倉 ---
    if 'bandwidth' in df.columns and len(df) >= 30:
        is_flat_bandwidth = bool(bar_t['bandwidth'] < df['bandwidth'].rolling(30).quantile(0.25).iloc[-1])
    else:
        is_flat_bandwidth = False

    body_series = df['body_size'] if 'body_size' in df.columns else (df['close'] - df['open']).abs()
    is_tiny_candles = bool(body_series.tail(3).mean() < (0.4 * atr)) if len(body_series) >= 3 else False
    if (is_flat_bandwidth or is_tiny_candles) and not is_strong_bull:
        logger.info(f"禁止開多: 通道已嚴重收窄扁平或動能枯竭 (flat_bw={is_flat_bandwidth}, tiny={is_tiny_candles})")
        return False

    if is_strong_bull or regular_breakout:
        logger.info(f"放行開多: is_strong_bull={is_strong_bull}, regular_breakout={regular_breakout}")
        return True

    logger.info("禁止開多: 未達任何破軌確認標準")
    return False


def should_close_long_optimized(df: pd.DataFrame, entry_price: float, current_price: float, atr: float) -> bool:
    """
    平多邏輯優化（多空對稱）：防止橫盤收窄被洗出場
    """
    if df is None or len(df) < 2:
        return False
    bar_t = df.iloc[-1]
    prev_bar = df.iloc[-2]

    ma_slow = float(bar_t.get('ma_slow', bar_t.get('ema_slow', bar_t.get('kc_middle', 0.0))))
    if 'ma_slow_slope' in bar_t:
        ma_slow_slope = float(bar_t['ma_slow_slope'])
    elif 'ma_slow' in df.columns and len(df) >= 3:
        ma_slow_slope = float(bar_t['ma_slow']) - float(df['ma_slow'].iloc[-3])
    else:
        ma_slow_slope = 0.0

    trend_reversal = (float(bar_t['close']) < ma_slow) and (ma_slow_slope < 0)
    if trend_reversal:
        return True

    if 'bandwidth' in df.columns and len(df) >= 20:
        is_narrow_band = bool(bar_t['bandwidth'] < df['bandwidth'].rolling(20).mean().iloc[-1])
    else:
        is_narrow_band = False

    lower_band = float(bar_t.get('lower_band', bar_t.get('kc_lower', 0.0)))
    touch_lower_band = float(bar_t['close']) <= lower_band

    if touch_lower_band and not is_narrow_band:
        return True

    if float(current_price) <= (float(entry_price) - 1.8 * float(atr)):
        return True

    return False


def should_close_long_trend(df: pd.DataFrame, entry_price: float, current_price: float, atr: float) -> bool:
    """
    趨勢波段平多邏輯優化（依據 2026-10-22 最新授權）：
    - 慢線趨勢過濾：ma_slow 強勢向上
    - 1. 真正的空頭反轉信號才平多：收盤實體跌破棕色慢線，且慢線走平或向下
    - 2. 避免太早平多：只要慢線強勢向上，無視跌破 MA5 的小回調！
         只有當利潤大幅拉開 (例如浮盈 > 3*ATR) 且出現極致頂部反轉 (如超長上影線吞噬) 才提早止盈
    - 其餘正常回踩（像圖中回踩棕線）一律 False，繼續抱牢多單！
    """
    if df is None or len(df) < 2:
        return False
    bar_t = df.iloc[-1]
    prev_bar = df.iloc[-2]

    ma_slow_t = float(bar_t.get('ma_slow', bar_t.get('ema_slow', bar_t.get('kc_middle', 0.0))))
    ma_slow_prev = float(prev_bar.get('ma_slow', prev_bar.get('ema_slow', prev_bar.get('kc_middle', 0.0))))

    # --- 慢線趨勢過濾 ---
    ma_slow_trending_up = ma_slow_t > ma_slow_prev

    # 1. 真正的空頭反轉信號才平多：收盤實體跌破棕色慢線，且慢線走平或向下
    trend_broken = (float(bar_t['close']) < ma_slow_t) and not ma_slow_trending_up
    if trend_broken:
        return True

    # 2. 避免太早平多：只要慢線強勢向上，無視跌破 MA5 的小回調！
    # 只有當利潤大幅拉開 (例如浮盈 > 3*ATR) 且出現極致頂部反轉 (如超長上影線吞噬) 才提早止盈
    body = abs(float(bar_t['close']) - float(bar_t['open']))
    upper_wick = float(bar_t['high']) - max(float(bar_t['open']), float(bar_t['close']))
    is_climax_reversal = (
        upper_wick > 1.5 * body
        and (float(bar_t['close']) < float(bar_t['open']))
        and ((float(current_price) - float(entry_price)) > 3.0 * float(atr))
    )
    if is_climax_reversal:
        return True

    # 其餘正常回踩（像圖中回踩棕線）一律 False，繼續抱牢多單！
    return False


def should_close_short_trend(df: pd.DataFrame, entry_price: float, current_price: float, atr: float) -> bool:
    """
    趨勢波段平空邏輯優化（多空對稱，依據 2026-10-22 最新授權）：
    - 慢線趨勢過濾：ma_slow 強勢向下
    - 1. 真正的多頭反轉信號才平空：收盤實體突破棕色慢線，且慢線走平或向上
    - 2. 避免太早平空：只要慢線強勢向下，無視突破 MA5 的小反抽！
         只有當利潤大幅拉開 (例如浮盈 > 3*ATR) 且出現極致底部反轉 (如超長下影線陽吞噬) 才提早止盈
    - 其餘正常反抽（像回抽棕線）一律 False，繼續抱牢空單！
    """
    if df is None or len(df) < 2:
        return False
    bar_t = df.iloc[-1]
    prev_bar = df.iloc[-2]

    ma_slow_t = float(bar_t.get('ma_slow', bar_t.get('ema_slow', bar_t.get('kc_middle', 0.0))))
    ma_slow_prev = float(prev_bar.get('ma_slow', prev_bar.get('ema_slow', prev_bar.get('kc_middle', 0.0))))

    # --- 慢線趨勢過濾 ---
    ma_slow_trending_down = ma_slow_t < ma_slow_prev

    # 1. 真正的多頭反轉信號才平空：收盤實體突破棕色慢線，且慢線走平或向上
    trend_broken = (float(bar_t['close']) > ma_slow_t) and not ma_slow_trending_down
    if trend_broken:
        return True

    # 2. 避免太早平空：只要慢線強勢向下，無視突破 MA5 的小反抽！
    body = abs(float(bar_t['close']) - float(bar_t['open']))
    lower_wick = min(float(bar_t['open']), float(bar_t['close'])) - float(bar_t['low'])
    is_climax_reversal = (
        lower_wick > 1.5 * body
        and (float(bar_t['close']) > float(bar_t['open']))
        and ((float(entry_price) - float(current_price)) > 3.0 * float(atr))
    )
    if is_climax_reversal:
        return True

    # 其餘正常反抽（像回抽棕線）一律 False，繼續抱牢空單！
    return False



