"""
EMA Cross Entry Service (EMA5 / EMA13 交叉獨立開倉服務)

【規則體系】
1. 必要條件：收盤確認交叉、前後位置相反、空手或持反向倉 (先平倉再反手)、同一根 K 棒只開一次、指標預熱完成。
2. 最低限度過濾：EMA 距離 >= 0.07 * ATR；同方向止損後冷卻 3 根；多單收盤須在 EMA13 之上 (空單在之下)。
3. 輔助指標只調整倉位 (不得禁止開倉)：大週期 15M 方向、ADX、KC 通道擴張、EMA13 斜率只調整倉位，最低保底 30% 倉位。
4. 補開機制：交叉當根被最低限度過濾擋時，允許 2 根內補開，價格距交叉點 <= 0.5 * ATR。
5. 優先序架構：EMA 交叉獨立評估且第一優先，破軌開倉次之。
6. 可觀測性：被擋掉時即時 logging 原因，交易記錄注入 entry_source。
"""

from __future__ import annotations

import logging
import math
from typing import Any, Dict, Optional, Tuple
import pandas as pd
from core.services.candle_data import closed_entry_candles

logger = logging.getLogger("ema_cross_entry")

# 記憶體狀態：追蹤交叉點與補開、冷卻記錄
_ema_cross_tracker: Dict[str, Dict[str, Any]] = {}


class EmaCrossEntryService:
    """獨立的 EMA5 / EMA13 交叉開倉評估服務"""

    @classmethod
    def evaluate_entry(
        cls,
        symbol: str,
        frame: pd.DataFrame,
        side: str,
        *,
        position: Optional[Dict[str, Any]] = None,
        last_closed_trades: Optional[list] = None,
        htf_15m_trend: str = "",
    ) -> Tuple[Optional[Dict[str, Any]], str]:
        """
        評估 EMA5 / EMA13 交叉開倉訊號
        :return: (signal_dict, status_or_blocked_reason)
        """
        side = str(side).upper()
        if side not in ("LONG", "SHORT"):
            return None, "INVALID_SIDE"

        # ── 1. 必要條件：指標預熱完成 ──
        if frame is None or frame.empty:
            return None, "WARMUP_NOT_READY"

        # EMA crosses are candle-close events. Do not let the changing live
        # tail create or erase a cross on every quote poll.
        bars = (
            closed_entry_candles(frame)
            if "is_closed" in frame.columns
            else frame.copy()
        )
        if len(bars) < 35:
            return None, "WARMUP_NOT_READY"
        timeframe_ms = frame.attrs.get("timeframe_ms")
        if timeframe_ms is not None and float(timeframe_ms) != 180_000.0:
            return None, "WRONG_TIMEFRAME_EXPECTED_3M"
        current_bar = bars.iloc[-1].to_dict()
        prev_bar = bars.iloc[-2].to_dict()

        # 確保收盤確認數值
        c_close = float(current_bar.get("close", 0.0) or 0.0)
        c_open = float(current_bar.get("open", 0.0) or 0.0)
        p_close = float(prev_bar.get("close", 0.0) or 0.0)
        bar_ts = float(current_bar.get("timestamp", 0.0) or current_bar.get("time", 0.0) or 0.0)

        # 優先讀取真實 EMA5 與 EMA13，若均不存在則自動計算 ewm
        c_ma5 = float(current_bar.get("ema5") or current_bar.get("ma5") or current_bar.get("_wild_ma5") or 0.0)
        c_ma13 = float(current_bar.get("ema13") or current_bar.get("ma15") or current_bar.get("kc_middle") or 0.0)
        p_ma5 = float(prev_bar.get("ema5") or prev_bar.get("ma5") or prev_bar.get("_wild_ma5") or 0.0)
        p_ma13 = float(prev_bar.get("ema13") or prev_bar.get("ma15") or prev_bar.get("kc_middle") or 0.0)

        if c_ma5 <= 0 or c_ma13 <= 0 or p_ma5 <= 0 or p_ma13 <= 0:
            if "close" in bars.columns and len(bars) >= 13:
                calc_ema5 = bars["close"].ewm(span=5, adjust=False).mean()
                calc_ema13 = bars["close"].ewm(span=13, adjust=False).mean()
                if c_ma5 <= 0:
                    c_ma5 = float(calc_ema5.iloc[-1])
                if p_ma5 <= 0:
                    p_ma5 = float(calc_ema5.iloc[-2])
                if c_ma13 <= 0:
                    c_ma13 = float(calc_ema13.iloc[-1])
                if p_ma13 <= 0:
                    p_ma13 = float(calc_ema13.iloc[-2])
        atr = float(current_bar.get("atr", 0.0001) or 0.0001)

        if min(c_close, c_ma5, c_ma13, p_ma5, p_ma13) <= 0:
            return None, "INVALID_INDICATOR_VALUES"

        # ── 1. 必要條件：同一根 K 棒只開一次 ──
        tracker = _ema_cross_tracker.setdefault(symbol, {
            "last_entry_bar_ts": 0.0,
            "pending_cross": None,
            "last_evaluated_bar_ts": {},
        })
        if tracker["last_entry_bar_ts"] > 0 and tracker["last_entry_bar_ts"] == bar_ts:
            return None, "BLOCKED_SAME_BAR_DUPLICATE"
        evaluated_bars = tracker.setdefault("last_evaluated_bar_ts", {})
        if evaluated_bars.get(side) == bar_ts:
            return None, "NO_NEW_CLOSED_BAR"
        # Delayed-cross age is measured in distinct candles, not quote polls.
        evaluated_bars[side] = bar_ts

        # ── 1. 必要條件：前後位置相反判定交叉 (或 2 根內補開) ──
        is_fresh_cross = False
        is_delayed_cross = False
        cross_price = c_close

        if side == "LONG":
            is_fresh_cross = (p_ma5 <= p_ma13 and c_ma5 > c_ma13)
        else:
            is_fresh_cross = (p_ma5 >= p_ma13 and c_ma5 < c_ma13)

        if is_fresh_cross:
            # 記錄交叉點供補開使用
            tracker["pending_cross"] = {
                "side": side,
                "bar_ts": bar_ts,
                "cross_price": c_close,
                "bars_passed": 0,
            }
        else:
            # 檢查 2 根內補開機會
            pending = tracker.get("pending_cross")
            if pending and pending.get("side") == side:
                pending["bars_passed"] = pending.get("bars_passed", 0) + 1
                if pending["bars_passed"] <= 2:
                    cross_p = float(pending.get("cross_price", c_close))
                    # 價格距交叉點 <= 0.5 * ATR
                    price_dist = abs(c_close - cross_p)
                    if price_dist <= 0.5 * atr:
                        # 均線依然保持順向
                        if (side == "LONG" and c_ma5 > c_ma13) or (side == "SHORT" and c_ma5 < c_ma13):
                            is_delayed_cross = True
                            cross_price = cross_p
                    else:
                        logger.info("[EMA交叉補開失效] %s %s 價格距交叉點已超過 0.5*ATR (dist=%.4g > %.4g)", symbol, side, price_dist, 0.5 * atr)
                        tracker["pending_cross"] = None
                else:
                    tracker["pending_cross"] = None

        if not is_fresh_cross and not is_delayed_cross:
            return None, "NO_CROSS"

        # ── 2. 最低限度過濾 (Minimum Viable Filters) ──
        # (a) EMA 距離 >= 0.07 * ATR
        ema_dist = abs(c_ma5 - c_ma13)
        if ema_dist < 0.07 * atr:
            reason = f"EMA_SPREAD_TOO_SMALL({ema_dist:.5g} < {0.07 * atr:.5g})"
            logger.info("[EMA交叉攔截] %s %s 條件未達: %s", symbol, side, reason)
            return None, reason

        # (b) 同方向止損後冷卻 3 根
        if last_closed_trades and isinstance(last_closed_trades, list):
            recent_trades = [t for t in reversed(last_closed_trades) if isinstance(t, dict) and t.get("symbol") == symbol]
            if recent_trades:
                last_t = recent_trades[0]
                t_side = str(last_t.get("side") or "").upper()
                t_reason = str(last_t.get("reason") or "").upper()
                is_sl = any(kw in t_reason for kw in ("STOP", "SL", "LOSS", "DEFENSE", "FAIL"))
                if is_sl and t_side == side:
                    t_ts = float(last_t.get("timestamp") or last_t.get("time") or 0.0)
                    tf_ms = float(frame.attrs.get("timeframe_ms", 180_000.0) or 180_000.0)
                    if tf_ms > 0 and t_ts > 0 and bar_ts > t_ts:
                        bars_since_sl = (bar_ts - t_ts) / tf_ms
                        if bars_since_sl < 3.0:
                            reason = f"COOLDOWN_ACTIVE({bars_since_sl:.1f}/3_bars)"
                            logger.info("[EMA交叉攔截] %s %s 同向止損冷卻中: %s", symbol, side, reason)
                            return None, reason

        # (c) 收盤價必須在 EMA13 順向側
        if side == "LONG" and c_close <= c_ma13:
            reason = "PRICE_BELOW_EMA13"
            logger.info("[EMA交叉攔截] %s 多單收盤價未在 EMA13 之上 (close=%.6g <= ema13=%.6g)", symbol, c_close, c_ma13)
            return None, reason
        elif side == "SHORT" and c_close >= c_ma13:
            reason = "PRICE_ABOVE_EMA13"
            logger.info("[EMA交叉攔截] %s 空單收盤價未在 EMA13 之下 (close=%.6g >= ema13=%.6g)", symbol, c_close, c_ma13)
            return None, reason

        # (d) 當根 K 棒必須同向收線（做多收陽線，做空收陰線，嚴禁反向 K 棒開倉）
        if side == "LONG" and c_close <= c_open:
            reason = "VETO_LONG_BEARISH_CANDLE"
            logger.info("[EMA交叉攔截] %s 多單當根收出陰線 (close=%.6g <= open=%.6g)，嚴禁開多", symbol, c_close, c_open)
            return None, reason
        elif side == "SHORT" and c_close >= c_open:
            reason = "VETO_SHORT_BULLISH_CANDLE"
            logger.info("[EMA交叉攔截] %s 空單當根收出陽線 (close=%.6g >= open=%.6g)，嚴禁開空", symbol, c_close, c_open)
            return None, reason

        # (e) 破軌第二根檢查：若前一根破軌，次根仍在軌外，同色放行，反色且小實體 (<=0.6*avg_body) 放行，大實體或收回軌內才否決
        p_kc_upper = float(prev_bar.get("kc_upper", 0.0) or 0.0)
        p_kc_lower = float(prev_bar.get("kc_lower", 0.0) or 0.0)
        c_kc_upper = float(current_bar.get("kc_upper", 0.0) or 0.0)
        c_kc_lower = float(current_bar.get("kc_lower", 0.0) or 0.0)
        c_body = abs(c_close - c_open)
        recent_bodies = (frame["close"].iloc[-11:-1] - frame["open"].iloc[-11:-1]).abs()
        avg_body = float(recent_bodies.mean()) if len(recent_bodies) > 0 else max(c_body, 1e-9)

        if side == "LONG" and p_kc_upper > 0 and p_close > p_kc_upper:
            if c_close <= c_kc_upper:
                reason = "BLOCKED_BREAKOUT_SECOND_BAR_RETURN_INSIDE_KC"
                logger.warning("[破軌第二根攔截] %s 首根破上軌，次根收盤已跌回KC軌內，嚴禁開多", symbol)
                return None, reason
            if c_close <= c_open and c_body > 0.60 * avg_body:
                reason = "BLOCKED_BREAKOUT_SECOND_BAR_OPPOSITE_LARGE_BODY"
                logger.warning("[破軌第二根攔截] %s 首根破上軌，次根收陰且實體較大 (%.6g > 0.6*%.6g)，嚴禁開多", symbol, c_body, avg_body)
                return None, reason
        elif side == "SHORT" and p_kc_lower > 0 and p_close < p_kc_lower:
            if c_close >= c_kc_lower:
                reason = "BLOCKED_BREAKOUT_SECOND_BAR_RETURN_INSIDE_KC"
                logger.warning("[破軌第二根攔截] %s 首根破下軌，次根收盤已漲回KC軌內，嚴禁開空", symbol)
                return None, reason
            if c_close >= c_open and c_body > 0.60 * avg_body:
                reason = "BLOCKED_BREAKOUT_SECOND_BAR_OPPOSITE_LARGE_BODY"
                logger.warning("[破軌第二根攔截] %s 首根破下軌，次根收陽且實體較大 (%.6g > 0.6*%.6g)，嚴禁開空", symbol, c_body, avg_body)
                return None, reason

        # ── 3. 輔助指標體系：只調整倉位，不得禁止開倉 (最低 30%，最高 100%) ──
        size_ratio = 0.30  # 保底 30%

        # (1) 大週期 15M 方向加成 (+20%)
        if (side == "LONG" and htf_15m_trend in ("UP", "向上", "BULLISH")) or (side == "SHORT" and htf_15m_trend in ("DOWN", "向下", "BEARISH")):
            size_ratio += 0.20

        # (2) ADX 趨勢強度加成 (+20%)
        adx_val = float(current_bar.get("adx", 0.0) or 0.0)
        if adx_val >= 20.0:
            size_ratio += 0.20

        # (3) KC 通道開口擴張加成 (+15%)
        c_kc_w = float(current_bar.get("kc_upper", 0.0)) - float(current_bar.get("kc_lower", 0.0))
        p_kc_w = float(prev_bar.get("kc_upper", 0.0)) - float(prev_bar.get("kc_lower", 0.0))
        if c_kc_w > p_kc_w:
            size_ratio += 0.15

        # (4) EMA13 斜率方向加成 (+15%)
        if (side == "LONG" and c_ma13 > p_ma13) or (side == "SHORT" and c_ma13 < p_ma13):
            size_ratio += 0.15

        size_ratio = min(1.0, max(0.30, size_ratio))

        # ── 1. 必要條件：持反向倉時標註先平再反手 ──
        cur_pos_side = str((position or {}).get("side", "")).upper()
        cur_pos_amt = abs(float((position or {}).get("amount", 0.0) or 0.0))
        needs_flip = bool(cur_pos_amt > 0 and cur_pos_side and cur_pos_side != side)

        # 訊號來源標籤
        entry_source = "EMA_CROSS_FLIP" if needs_flip else ("EMA_CROSS_DELAYED" if is_delayed_cross else "EMA_CROSS")

        # 成功開倉，清除補開暫存並記錄開倉 K 棒 timestamp
        tracker["pending_cross"] = None
        tracker["last_entry_bar_ts"] = bar_ts

        signal = {
            "action": f"OPEN_{side}",
            "side": side,
            "type": "MA_CROSSOVER",
            "entry_source": entry_source,
            "entry_pattern": "MA_CROSSOVER",
            "structure_confirmation": "MA_CROSSOVER",
            "structure_confirmed": True,
            "size_ratio": round(size_ratio, 2),
            "price": c_close,
            "cross_price": cross_price,
            "bar_id": bar_ts,
            "is_intrabar": False,
            "needs_flip": needs_flip,
            "reason": f"[{entry_source}] EMA5/EMA13交叉成立 (倉位比例: {size_ratio*100:.0f}%)",
            "entry_label": f"[{entry_source}] 均線交叉",
            "channel_expanding": (c_kc_w > p_kc_w),
            "ma5_current": c_ma5,
            "ma5_previous": p_ma5,
            "ma15_current": c_ma13,
            "ma15_previous": p_ma13,
        }
        logger.info("🎯 [EMA交叉發出訊號] %s %s 成功生成訊號: %s", symbol, side, signal["reason"])
        return signal, "PASSED"
