"""
core/gates/unified_pipeline_gates.py
====================================
三層流水線之【裁決層 (Pure Math Gates)】
職責：
- 唯一決策仲裁官。
- 純輸入純輸出，完全不與任何 API / 網路 / Broker 溝通。
- 回傳格式標準化為 Tuple[bool, str, Dict[str, Any]]。
"""
from typing import Dict, Any, Tuple, Optional, Union
import pandas as pd
from core.services.data_pipeline import MarketDataSnapshot


class ExitGate:
    """
    出場裁決唯一裁決官：
    只有三種裁決可能：
    1. 階梯鎖利/保本觸發 -> True (PROFIT_LOCK)
    2. 純數學真峰谷力竭 (三點樞紐確立，且防單棒毛刺洗盤) -> True (TRUE_PEAK / TRUE_BOTTOM)
    3. 趨勢結構未破 -> 強制 False (VETO_HOLD)
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
            kc_mid = market_data.kc_middle
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
        }

        # ── 1. 階梯鎖利 / 保本觸發 (PROFIT_LOCK) ──
        # 若曾獲利超過 1.5 ATR 等值比例 (約 >= 1.5%)，自最高峰回撤超過 30% 鎖定利潤
        atr_pct = (atr_val / entry_p) if entry_p > 0 else 0.01
        high_profit_threshold = max(0.012, 1.5 * atr_pct)
        if peak_roe >= high_profit_threshold:
            giveback = peak_roe - unrealized_pct
            max_tolerated_giveback = peak_roe * 0.30
            if giveback >= max_tolerated_giveback:
                return True, "PROFIT_LOCK", {
                    **meta,
                    "cause": f"階梯鎖利觸發: peak_roe={peak_roe:.2%}, curr={unrealized_pct:.2%}, giveback={giveback:.2%}",
                }

        # 帳戶級保命止損 (3.0 ATR 硬防守)
        hard_loss_threshold = -3.0 * atr_pct
        if unrealized_pct <= hard_loss_threshold:
            return True, "PROFIT_LOCK", {
                **meta,
                "cause": f"硬止損避險: loss={unrealized_pct:.2%} <= threshold={hard_loss_threshold:.2%}",
            }

        # ── 2. 純數學真峰谷力竭 (TRUE_PEAK / TRUE_BOTTOM) ──
        # 需防單棒毛刺洗盤：三點確定性樞紐確認 (Three-Point Pivot)
        if raw_df is not None and len(raw_df) >= 4:
            c3 = raw_df.iloc[-1]  # 最新收線棒
            c2 = raw_df.iloc[-2]  # 峰/谷棒
            c1 = raw_df.iloc[-3]  # 前棒

            if side == "LONG":
                # 多單真峰頂：中間棒 High 嚴格高於前後兩棒 High，且最新棒收陰或跌破中間棒實體中點
                is_pivot_peak = (c2["high"] > c1["high"]) and (c2["high"] > c3["high"])
                c2_mid = (c2["open"] + c2["close"]) / 2.0
                is_exhaustion = (c3["close"] < c2_mid) and (c3["close"] < c3["open"])
                if is_pivot_peak and is_exhaustion and unrealized_pct > 0:
                    return True, "TRUE_PEAK", {
                        **meta,
                        "cause": f"純數學真峰頂確立: c2_high={c2['high']} > {c1['high']}, c3_close={c3['close']} < mid={c2_mid}",
                    }
            elif side == "SHORT":
                # 空單真谷底：中間棒 Low 嚴格低於前後兩棒 Low，且最新棒收陽或突破中間棒實體中點
                is_pivot_bottom = (c2["low"] < c1["low"]) and (c2["low"] < c3["low"])
                c2_mid = (c2["open"] + c2["close"]) / 2.0
                is_exhaustion = (c3["close"] > c2_mid) and (c3["close"] > c3["open"])
                if is_pivot_bottom and is_exhaustion and unrealized_pct > 0:
                    return True, "TRUE_BOTTOM", {
                        **meta,
                        "cause": f"純數學真谷底確立: c2_low={c2['low']} < {c1['low']}, c3_close={c3['close']} > mid={c2_mid}",
                    }

        # ── 3. 趨勢結構未破 -> 強制抱牢 (VETO_HOLD) ──
        # 多單只要收盤在 KC 中軌安全側或中軌未轉空，嚴禁因單根洗盤或微幅回踩平倉
        if side == "LONG":
            if quote >= kc_mid or slope >= 0:
                return False, "VETO_HOLD", {
                    **meta,
                    "cause": "多頭趨勢結構未破 (穩居KC中軌或中軌向上)，強制抱牢！",
                }
        elif side == "SHORT":
            if quote <= kc_mid or slope <= 0:
                return False, "VETO_HOLD", {
                    **meta,
                    "cause": "空頭趨勢結構未破 (受制KC中軌或中軌向下)，強制抱牢！",
                }

        # 預設強制抱牢，避免游離分支偷平倉
        return False, "VETO_HOLD", {**meta, "cause": "趨勢結構未確認終結，強制抱牢"}


class EntryGate:
    """
    進場裁決唯一裁決官：
    1. 快車道（鎖利延續 / 破軌放行）：同色K破軌且均線同向直接放行
    2. 避震門禁：長影線一票否決延後下一根
    3. 結構門禁：均線走平/黏合一票否決禁止開單
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
            kc_up = market_data.kc_upper
            kc_mid = market_data.kc_middle
            kc_low = market_data.kc_lower
        else:
            quote = float(market_data.get("current_price", 0.0))
            atr_val = float(atr or market_data.get("atr", quote * 0.01))
            slope = float(kc_slope or market_data.get("kc_middle_slope", 0.0))
            curr_k = market_data.get("curr_k", {})
            prev_k = market_data.get("prev_k", {})
            ma5 = float(market_data.get("ma5", quote))
            ma15 = float(market_data.get("ma15", quote))
            kc_up = float(market_data.get("kc_upper", quote * 1.01))
            kc_mid = float(market_data.get("kc_middle", quote))
            kc_low = float(market_data.get("kc_lower", quote * 0.99))

        meta = {
            "signal_type": signal_type,
            "side": signal_side,
            "quote": quote,
            "atr": atr_val,
            "kc_slope": slope,
        }

        # ── 門禁 3：結構門禁（均線走平/黏合一票否決禁止開單）──
        # MA5 與 MA15 間距過小 (黏合 < 0.15% 且中軌走平) 視為死水震盪
        ma_spread_pct = abs(ma5 - ma15) / quote if quote > 0 else 0.0
        if ma_spread_pct < 0.0015 and abs(slope) < (0.05 * atr_val):
            return False, "VETO_CHOP_OR_CONVERGENCE", {
                **meta,
                "cause": f"結構門禁否決: 均線過度黏合 (spread={ma_spread_pct:.3%}) 且中軌走平",
            }

        # 方向一致性檢查：逆大趨勢一票否決
        if signal_side == "LONG" and slope < -0.05 * atr_val:
            return False, "VETO_CHOP_OR_CONVERGENCE", {
                **meta,
                "cause": "結構門禁否決: KC 中軌明確向下，嚴禁逆勢開多！",
            }
        if signal_side == "SHORT" and slope > 0.05 * atr_val:
            return False, "VETO_CHOP_OR_CONVERGENCE", {
                **meta,
                "cause": "結構門禁否決: KC 中軌明確向上，嚴禁逆勢開空！",
            }

        # ── 門禁 2：避震門禁（長影線一票否決延後下一根）──
        c_open = curr_k.get("open", quote)
        c_close = curr_k.get("close", quote)
        c_high = curr_k.get("high", max(c_open, c_close))
        c_low = curr_k.get("low", min(c_open, c_close))
        body = abs(c_close - c_open)

        if signal_side == "LONG":
            upper_wick = c_high - max(c_open, c_close)
            # 做多時：上影線超過實體的 1.2 倍且大於 0.4 ATR，代表衝高嚴重受阻
            if upper_wick > max(body * 1.2, 0.4 * atr_val):
                return False, "VETO_WICK_REJECTION", {
                    **meta,
                    "cause": f"避震門禁否決: 上影線過長 (wick={upper_wick:.4f} > 1.2*body)，衝高受阻延後下一根",
                }
        elif signal_side == "SHORT":
            lower_wick = min(c_open, c_close) - c_low
            # 做空時：下影線超過實體的 1.2 倍且大於 0.4 ATR，代表探底強烈反彈
            if lower_wick > max(body * 1.2, 0.4 * atr_val):
                return False, "VETO_WICK_REJECTION", {
                    **meta,
                    "cause": f"避震門禁否決: 下影線過長 (wick={lower_wick:.4f} > 1.2*body)，探底受阻延後下一根",
                }

        # ── 門禁 1：快車道（鎖利延續 / 破軌放行）──
        # 做多：最新價突破 KC 上軌或當根收紅陽線破軌，且 MA5 向上發散
        if signal_side == "LONG":
            is_breakout = (quote > kc_up) or (c_close > kc_up and c_close > c_open)
            is_ma_aligned = (ma5 >= ma15) and (ma5 >= curr_k.get("open", quote))
            if is_breakout and is_ma_aligned:
                return True, "FAST_TRACK_CONTINUATION", {
                    **meta,
                    "cause": "快車道放行: 同色K強勢破軌且均線多頭發散",
                }
            # 普通趨勢回踩入場
            if c_close > kc_mid and ma5 > ma15 and c_close > c_open:
                return True, "STANDARD_TREND_ENTRY", {
                    **meta,
                    "cause": "標準順勢進場: 價格在中軌上方且均線多頭排列",
                }

        elif signal_side == "SHORT":
            is_breakdown = (quote < kc_low) or (c_close < kc_low and c_close < c_open)
            is_ma_aligned = (ma5 <= ma15) and (ma5 <= curr_k.get("open", quote))
            if is_breakdown and is_ma_aligned:
                return True, "FAST_TRACK_CONTINUATION", {
                    **meta,
                    "cause": "快車道放行: 同色K強勢破軌且均線空頭發散",
                }
            # 普通趨勢反彈做空
            if c_close < kc_mid and ma5 < ma15 and c_close < c_open:
                return True, "STANDARD_TREND_ENTRY", {
                    **meta,
                    "cause": "標準順勢進場: 價格在中軌下方且均線空頭排列",
                }

        return False, "VETO_ENTRY_CONDITIONS_NOT_MET", {**meta, "cause": "未達開倉條件"}
