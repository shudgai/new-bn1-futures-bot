"""Pure mathematical authorization for final entry and exit execution."""

from __future__ import annotations

import math
import time
from collections.abc import Mapping
from typing import Any


def _number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return result if math.isfinite(result) else None


def _rows(frame: Any) -> list[dict[str, Any]]:
    if frame is None:
        return []
    if isinstance(frame, Mapping):
        candles = frame.get("candles")
        if isinstance(candles, (list, tuple)):
            return [dict(row) for row in candles if isinstance(row, Mapping)]
        return []
    if hasattr(frame, "to_dict") and hasattr(frame, "iloc"):
        try:
            return [dict(row) for row in frame.to_dict("records")]
        except (TypeError, ValueError):
            return []
    if isinstance(frame, (list, tuple)):
        return [dict(row) for row in frame if isinstance(row, Mapping)]
    return []


def validate_entry_math(
    frame: Any,
    side: str,
    *,
    profit_lock_fast_track: bool = False,
    is_outer_entry: bool = False,
    is_dip_sniper: bool = False,
    is_meme_hunter: bool = False,
    is_swing_pivot: bool = False,
    now_ms: float | None = None,
    max_staleness_ms: float = 180_000.0,
) -> tuple[bool, str]:
    """Authorize an entry only when closed-bar HTF trend agrees with its side."""
    side = str(side or "").upper()
    if side not in {"LONG", "SHORT"}:
        return False, "BLOCKED_ENTRY_MATH_INVALID_SIDE"
    bars = _rows(frame)
    if is_swing_pivot:
        from core.services.candle_data import closed_entry_candles
        closed_frame = closed_entry_candles(frame)
        bars = _rows(closed_frame)
    if len(bars) < 2:
        return False, "BLOCKED_ENTRY_MATH_INSUFFICIENT_BARS"
    current, previous = bars[-1], bars[-2]

    # 數據層防禦：過期舊資料攔截（Stale Data Veto）
    # 比對最後一根 K 棒的時間戳（timestamp）與當前系統時間（UTC/毫秒）：
    # 若時間落差超過 3 分鐘（180,000 毫秒），Fail-Closed 一票否決並返回 BLOCKED_STALE_DATA
    raw_ts = _number(current.get("timestamp"))
    if raw_ts is not None:
        ts_ms = raw_ts * 1000.0 if (1e8 < raw_ts < 1e11) else raw_ts
        if ts_ms >= 1e11 or now_ms is not None:
            current_ms = now_ms if now_ms is not None else (time.time() * 1000.0)
            if (current_ms - ts_ms) > max_staleness_ms:
                return False, "BLOCKED_STALE_DATA"
    values = {
        key: _number(current.get(key))
        for key in (
            "open", "high", "low", "close", "ma5", "ma15", "atr",
            "kc_upper", "kc_middle", "kc_lower",
        )
    }
    previous_ma5 = _number(previous.get("ma5"))
    previous_ma15 = _number(previous.get("ma15"))
    prev_opened = _number(previous.get("open"))
    prev_closed = _number(previous.get("close"))
    closed_bars = bars
    if any("is_closed" in row for row in bars):
        closed_bars = [row for row in bars if row.get("is_closed") == True]
    if len(closed_bars) < 2:
        return False, "BLOCKED_ENTRY_MATH_KC_TREND_UNKNOWN"
    if is_swing_pivot:
        from core.services.swing_pivot_rules import evaluate_swing_pivot_entry
        evidence = evaluate_swing_pivot_entry(frame)
        if evidence is None or evidence.get("side") != side:
            return False, "BLOCKED_SWING_PIVOT_EVIDENCE_INVALID"
    prior_middle = _number(closed_bars[-2].get("kc_middle"))
    latest_middle = _number(closed_bars[-1].get("kc_middle"))
    if (any(value is None for value in values.values()) or previous_ma5 is None
            or prior_middle is None or latest_middle is None):
        return False, "BLOCKED_ENTRY_MATH_INVALID_DATA"
    opened, high, low, close, ma5, ma15, atr, upper, lower = (
        values[key]
        for key in ("open", "high", "low", "close", "ma5", "ma15", "atr", "kc_upper", "kc_lower")
    )
    if min(opened, close, ma5, ma15, atr, upper, lower) <= 0 or high < low:
        return False, "BLOCKED_ENTRY_MATH_INVALID_DATA"
    if high < max(opened, close) or low > min(opened, close):
        return False, "BLOCKED_ENTRY_MATH_INVALID_OHLC"

    # The final account gate must enforce MA5 direction for every entry class.
    # A breakout, continuation, manual flag, or dip/hunter label cannot turn an
    # adverse/flat MA5 slope into an authorized entry.
    if side == "LONG" and ma5 <= previous_ma5 and not is_swing_pivot:
        return False, "BLOCKED_BY_MA5_NOT_UPWARD"
    if side == "SHORT" and ma5 >= previous_ma5 and not is_swing_pivot:
        return False, "BLOCKED_BY_MA5_NOT_DOWNWARD"

    # Final execution gate: numeric KC midline slope is authoritative. No
    # continuation, oversold-reversal, or profit-lock fast track may override it.
    kc_slope = latest_middle - prior_middle
    if kc_slope == 0 and not is_swing_pivot:
        return False, "BLOCKED_ENTRY_MATH_KC_TREND_FLAT"
    if side == "LONG" and kc_slope < 0 and not is_swing_pivot:
        return False, "BLOCKED_LONG_ON_BEARISH_TREND"
    if side == "SHORT" and kc_slope > 0 and not is_swing_pivot:
        return False, "BLOCKED_SHORT_ON_BULLISH_TREND"

    # The final order gate independently rebuilds the macro regime from closed
    # candles. Breakouts and MA crosses cannot override an opposing or unknown
    # higher-timeframe direction.
    try:
        import pandas as pd
        from core.services.data_pipeline import DataPipeline

        macro_frame = pd.DataFrame(closed_bars)
        macro_close = _number(closed_bars[-1].get("close"))
        snapshot = (
            DataPipeline.calculate_snapshot(
                "", macro_frame, current_price=macro_close,
            )
            if macro_close is not None and macro_close > 0
            else None
        )
        macro_trend = str(
            getattr(snapshot, "macro_trend", "NEUTRAL") or "NEUTRAL"
        ).upper()
    except (AttributeError, KeyError, TypeError, ValueError, IndexError, OverflowError):
        macro_trend = "NEUTRAL"
    if macro_trend == "NEUTRAL" and not is_swing_pivot:
        return False, "BLOCKED_ENTRY_MACRO_TREND_NEUTRAL_OR_UNAVAILABLE"
    if side == "LONG" and macro_trend != "BULLISH" and not is_swing_pivot:
        return False, "BLOCKED_LONG_ON_BEARISH_MACRO_TREND"
    if side == "SHORT" and macro_trend != "BEARISH" and not is_swing_pivot:
        return False, "BLOCKED_SHORT_ON_BULLISH_MACRO_TREND"

    # ─── 資金費率尖峰一票否決 (Funding Rate Spike Veto) ───
    funding_rate = _number(values.get("funding_rate") or current.get("funding_rate") or 0.0)
    if funding_rate is not None and abs(funding_rate) >= 0.015:
        now_ts = time.time()
        time_to_settle = 3600 - (int(now_ts) % 3600)
        if time_to_settle <= 300:
            if (side == "LONG" and funding_rate > 0) or (side == "SHORT" and funding_rate < 0):
                return False, "BLOCKED_FUNDING_RATE_SPIKE"

    # ─── 均線斜率（EMA Slope）絕對順勢一票否決（Hard Veto） ───
    # 做多鐵律：若 EMA15 下拐或走平 (ema15[-1] <= ema15[-2] 或 ema15_slope <= 0)，一律 100% 阻斷開多接飛刀！
    # 做空鐵律：若 EMA15 上翹或走平 (ema15[-1] >= ema15[-2] 或 ema15_slope >= 0)，一律 100% 阻斷開空摸頂！
    curr_ema15 = _number(current.get("ema15")) or _number(current.get("ema_fast")) or values.get("ma15")
    prev_ema15 = _number(previous.get("ema15")) or _number(previous.get("ema_fast")) or previous_ma15

    if curr_ema15 is not None and prev_ema15 is not None and not is_swing_pivot:
        ema15_slope = curr_ema15 - prev_ema15
        if side == "LONG" and (curr_ema15 <= prev_ema15 or ema15_slope <= 0):
            return False, "BLOCKED_LONG_ON_EMA_FALLING"
        if side == "SHORT" and (curr_ema15 >= prev_ema15 or ema15_slope >= 0):
            return False, "BLOCKED_SHORT_ON_EMA_RISING"

    is_outer = is_swing_pivot or is_outer_entry or (side == "LONG" and close > upper) or (side == "SHORT" and close < lower)

    # In-channel entries: reject squeeze chop
    if not is_outer:
        kc_width = upper - lower
        if kc_width < (atr * 1.5):
            return False, "BLOCKED_ENTRY_KC_CHANNEL_SQUEEZE_CHOP"

    # 做多必須站在中軌與 MA15 之上，做空必須在下方
    # 放寬中軌/MA15 緩衝區（0.15 ATR）：允許反抽假刺穿或短暫回測中軌/MA15 時，不被直接 1 票秒殺
    retest_buffer = 0.15 * atr
    if side == "LONG" and not is_swing_pivot and (close < latest_middle - retest_buffer or close < ma15 - retest_buffer):
        return False, "BLOCKED_LONG_BELOW_MIDLINE_OR_MA15"
    if side == "SHORT" and not is_swing_pivot and (close > latest_middle + retest_buffer or close > ma15 + retest_buffer):
        return False, "BLOCKED_SHORT_ABOVE_MIDLINE_OR_MA15"

    if is_dip_sniper or is_meme_hunter:
        if side == "LONG":
            if ma5 < ma15 - (0.15 * atr):
                return False, "BLOCKED_ENTRY_MATH_LONG_MA_ALIGNMENT"
            return True, "ENTRY_MATH_MEME_HUNTER_APPROVED" if is_meme_hunter else "ENTRY_MATH_DIP_SNIPER_APPROVED"
        else:
            if ma5 > ma15 + (0.15 * atr):
                return False, "BLOCKED_ENTRY_MATH_SHORT_MA_ALIGNMENT"
            return True, "ENTRY_MATH_MEME_HUNTER_APPROVED" if is_meme_hunter else "ENTRY_MATH_DIP_SNIPER_APPROVED"

    body = abs(close - opened)
    wick = high - max(opened, close) if side == "LONG" else min(opened, close) - low
    if wick >= body and wick >= 0.25 * atr:
        return False, "BLOCKED_ENTRY_MATH_LONG_WICK"

    if profit_lock_fast_track:
        if side == "LONG" and close > opened and close > upper:
            return True, "ENTRY_MATH_PROFIT_LOCK_FAST_TRACK"
        if side == "SHORT" and close < opened and close < lower:
            return True, "ENTRY_MATH_PROFIT_LOCK_FAST_TRACK"
        return False, "BLOCKED_ENTRY_MATH_FAST_TRACK_NOT_CONFIRMED"

    # ─── 規則 1：假突破 / 長黑吞噬反轉提早開空 (Bearish Engulfing Reversal) ───
    if (
        not is_outer
        and side == "SHORT"
        and prev_opened is not None
        and prev_closed is not None
        and (prev_closed > prev_opened)
        and (close < opened)
        and (close < prev_opened)
        and (close < ma15)
        and (close <= latest_middle)
    ):
        if body >= 0.3 * atr and (previous_ma5 is None or ma5 <= previous_ma5 + 1e-6):
            return True, "ENTRY_SHORT_ENGULFING_REVERSAL"

    # ─── 規則 1-LONG：假跌破 / 長紅吞噬反轉提早開多 (Bullish Engulfing Reversal) ───
    if (
        not is_outer
        and side == "LONG"
        and prev_opened is not None
        and prev_closed is not None
        and (prev_closed < prev_opened)
        and (close > opened)
        and (close > prev_opened)
        and (close > ma15)
        and (close >= latest_middle - retest_buffer)
    ):
        if body >= 0.3 * atr and (previous_ma5 is None or ma5 >= previous_ma5 - 1e-6):
            return True, "ENTRY_LONG_ENGULFING_REVERSAL"

    # ─── 規則 3：EMA15 金叉 EMA40 + 站上 KC 中軌提早開多 (ENTRY_LONG_EMA_CROSS_MID) ───
    ema_slow = _number(current.get("ema_slow")) or _number(current.get("ema40")) or latest_middle
    if (
        not is_outer
        and side == "LONG"
        and (close >= latest_middle - retest_buffer)
        and (close - ma15 <= 1.2 * atr)
        and (ma15 >= ema_slow - 0.05 * atr)
        and previous_ma15 is not None
        and (ma15 >= previous_ma15 - 1e-6)
    ):
        upper_wick = high - max(opened, close)
        if not (upper_wick >= body and upper_wick >= 0.25 * atr):
            return True, "ENTRY_LONG_EMA_CROSS_MID"

    # ─── 規則 4：EMA15 死叉 EMA40 + 跌破 KC 中軌提早開空 (ENTRY_SHORT_EMA_CROSS_MID) ───
    if (
        not is_outer
        and side == "SHORT"
        and (close <= latest_middle + retest_buffer)
        and (ma15 - close <= 1.2 * atr)
        and (ma15 <= ema_slow + 0.05 * atr)
        and previous_ma15 is not None
        and (ma15 <= previous_ma15 + 1e-6)
    ):
        lower_wick = min(opened, close) - low
        if not (lower_wick >= body and lower_wick >= 0.25 * atr):
            return True, "ENTRY_SHORT_EMA_CROSS_MID"

    # ─── 規則 2：破中軌 + 均線下拐提早進場開空 (Mid Band Break) ───
    # 支援在跌破中軌後、回踩中軌遇阻收黑或弱勢盤整時提早開出主跌浪起點空單
    prev_c_val = _number(previous.get("close"))
    prev_mid_val = _number(previous.get("kc_middle"))
    prev_below_mid = (prev_c_val is not None and prev_mid_val is not None and prev_c_val < prev_mid_val)
    if (
        not is_outer
        and side == "SHORT"
        and (close < latest_middle or (prev_below_mid and close <= latest_middle + retest_buffer))
        and (ma15 - close <= 1.2 * atr)
        and (close < opened or close <= ma5)
        and previous_ma15 is not None
        and (ma15 <= previous_ma15 + 1e-6)
    ):
        return True, "ENTRY_SHORT_MID_BAND_BREAK"

    # ─── 規則 2-LONG：站上中軌 + 均線上拐提早進場開多 (Mid Band Break / Bounce) ───
    # 支援在突破中軌後、回踩中軌有守收紅或強勢盤整時提早開出主升浪起點多單
    prev_above_mid = (prev_c_val is not None and prev_mid_val is not None and prev_c_val > prev_mid_val)
    if (
        not is_outer
        and side == "LONG"
        and (close > latest_middle or (prev_above_mid and close >= latest_middle - retest_buffer))
        and (close - ma15 <= 1.2 * atr)
        and (close > opened or close >= ma5)
        and previous_ma15 is not None
        and (ma15 >= previous_ma15 - 1e-6)
    ):
        return True, "ENTRY_LONG_MID_BAND_BREAK"

    # Outer-rail breakout and continuation entries per AGENTS.md:
    # When price is outside KC bands (or explicitly signaled as an outer entry),
    # price naturally exceeds MA15 by the channel half-width.
    # Enforce:
    # 1. MA5 slope direction (LONG: ma5 > previous_ma5, SHORT: ma5 < previous_ma5)
    # 2. Outer chase limit: quote within 3 ATR from the outer rail.
    is_outer = is_swing_pivot or is_outer_entry or (side == "LONG" and close > upper) or (side == "SHORT" and close < lower)
    if is_outer:
        if not is_swing_pivot and abs(ma5 - ma15) < 0.05 * atr:
            return False, "BLOCKED_ENTRY_MATH_MA_TOO_CLOSE"
        if side == "LONG":
            if not is_swing_pivot and not (ma5 > previous_ma5 and ma5 >= ma15 - 0.15 * atr):
                return False, "BLOCKED_ENTRY_MATH_LONG_MA_ALIGNMENT"
            if close - upper > 3.0 * atr:
                return False, "BLOCKED_ENTRY_MATH_LONG_CHASE"
        else:
            if not is_swing_pivot and not (ma5 < previous_ma5 and ma5 <= ma15 + 0.15 * atr):
                return False, "BLOCKED_ENTRY_MATH_SHORT_MA_ALIGNMENT"
            if lower - close > 3.0 * atr:
                return False, "BLOCKED_ENTRY_MATH_SHORT_CHASE"
        
        funding_rate = _number(values.get("funding_rate") or current.get("funding_rate") or 0.0)
        if funding_rate is not None and abs(funding_rate) >= 0.015:
            now_ts = time.time()
            time_to_settle = 3600 - (int(now_ts) % 3600)
            if time_to_settle <= 300:
                if (side == "LONG" and funding_rate > 0) or (side == "SHORT" and funding_rate < 0):
                    return False, "BLOCKED_FUNDING_RATE_SPIKE"

        return True, "ENTRY_MATH_APPROVED"

    if side == "LONG":
        # 開多允許：MA5 > MA15，且 MA5 向上發散；或者當前價已站上 MA15 且 MA15 處於上行狀態
        if not (ma5 >= ma15 - 0.05 * atr and (ma5 > previous_ma5 or (close > ma15 and ma15 > previous_ma15))):
            return False, "BLOCKED_ENTRY_MATH_LONG_MA_ALIGNMENT"
        if close - ma15 > 1.2 * atr:
            return False, "BLOCKED_ENTRY_MATH_LONG_CHASE"
    else:
        # 開空允許：MA5 < MA15，且 MA5 向下發散；或者當前價已跌破 MA15 且 MA15 處於下行狀態
        if not (ma5 <= ma15 + 0.05 * atr and (ma5 < previous_ma5 or (close < ma15 and ma15 < previous_ma15))):
            return False, "BLOCKED_ENTRY_MATH_SHORT_MA_ALIGNMENT"
        if ma15 - close > 1.2 * atr:
            return False, "BLOCKED_ENTRY_MATH_SHORT_CHASE"

    funding_rate = _number(values.get("funding_rate") or current.get("funding_rate") or 0.0)
    if funding_rate is not None and abs(funding_rate) >= 0.015:
        now_ts = time.time()
        time_to_settle = 3600 - (int(now_ts) % 3600)
        if time_to_settle <= 300:
            if (side == "LONG" and funding_rate > 0) or (side == "SHORT" and funding_rate < 0):
                return False, "BLOCKED_FUNDING_RATE_SPIKE"

    return True, "ENTRY_MATH_APPROVED"


def _net_roe_pct(position: Mapping[str, Any], price: float, sign: int, fee: float, slippage: float) -> float | None:
    entry = _number(position.get("entry_price"))
    qty = _number(position.get("qty") or position.get("quantity"))
    margin = _number(position.get("margin"))
    leverage = _number(position.get("leverage")) or 1.0
    if entry is None or qty is None or entry <= 0 or qty <= 0:
        return None
    if margin is None or margin <= 0:
        margin = entry * qty / leverage
    if margin <= 0:
        return None
    gross = sign * (price - entry) * qty
    costs = (entry + price) * qty * fee + price * qty * slippage
    return (gross - costs) / margin * 100.0


def is_emergency_exit(close_reason: Any) -> bool:
    reason = str(close_reason or "").upper().replace(" ", "_")
    return any(token in reason for token in (
        "CIRCUIT_BREAKER", "HARD_STOP_LOSS", "HARD_STOP", "LIQUIDATION",
        "MARGIN_LOSS", "MAXIMUM_LOSS", "OPPOSITE_KC_BAND_BREACH", "EXTREME_WATERFALL",
        "TWO_CLOSED_ADVERSE_ABNORMAL", "ADVERSE_STOP_LOSS",
        "EXIT_LONG_AT_PEAK", "EXIT_SHORT_AT_BOTTOM",
        "WATERFALL", "CATASTROPHIC_DUMP", "CATASTROPHIC_PUMP", "閃崩", "瀑布",
        "帳戶硬止損", "最大虧損", "熔斷", "反側KC",
    ))


def validate_exit_math(
    position: Mapping[str, Any],
    frame: Any,
    price: float,
    metadata: Mapping[str, Any] | None = None,
    *,
    fee_rate: float = 0.0004,
    slippage: float = 0.0002,
    close_reason: str = "",
    now_ms: float | None = None,
    max_staleness_ms: float = 180_000.0,
) -> tuple[bool, str, dict[str, Any]]:
    """Return the only mathematical authorization for a full position exit."""
    details: dict[str, Any] = {}
    norm_reason = str(close_reason or "").upper().replace(" ", "_")
    emergency = is_emergency_exit(close_reason)
    side = str(position.get("side") or "").upper()
    quote = _number(price)
    bars = _rows(frame)

    # Safety labels require matching evidence. Only exchange/account failure
    # exits that cannot be price-validated retain an unconditional override.
    if emergency:
        if any(token in norm_reason for token in (
            "DATA_OUTAGE", "LIQUIDATION", "MARGIN_LOSS", "FAIL_SAFE",
        )):
            return True, "EMERGENCY_EXIT_OVERRIDE", {"exit_type": "EMERGENCY"}
        if quote is None or quote <= 0 or side not in ("LONG", "SHORT"):
            return False, "BLOCKED_EXIT_EMERGENCY_EVIDENCE_INVALID", details

        if "TWO_CLOSED_ADVERSE_ABNORMAL" in norm_reason:
            from core.services.exits.abnormal_candle_exit import (
                two_closed_adverse_abnormal_evidence,
            )
            evidence = two_closed_adverse_abnormal_evidence(position, frame)
            if evidence is None:
                return False, "BLOCKED_EXIT_TWO_CLOSED_ABNORMAL_NOT_CONFIRMED", details
            return True, "EMERGENCY_EXIT_TWO_CLOSED_ABNORMAL_CONFIRMED", {
                "exit_type": "TWO_CLOSED_ADVERSE_ABNORMAL",
                **evidence,
            }

        if "EXIT_LONG_AT_PEAK" in norm_reason or "EXIT_SHORT_AT_BOTTOM" in norm_reason:
            from core.services.swing_pivot_rules import evaluate_outer_rail_reversal_exit
            evidence = evaluate_outer_rail_reversal_exit(position, frame)
            expected_side = "LONG" if "EXIT_LONG_AT_PEAK" in norm_reason else "SHORT"
            if evidence is None or evidence.get("side") != expected_side or evidence.get("reason") not in norm_reason:
                return False, "BLOCKED_EXIT_OUTER_RAIL_REVERSAL_NOT_CONFIRMED", details
            return True, "EMERGENCY_EXIT_OUTER_RAIL_REVERSAL_CONFIRMED", evidence

        if any(token in norm_reason for token in (
            "WATERFALL", "CATASTROPHIC_DUMP", "CATASTROPHIC_PUMP", "閃崩", "瀑布",
        )):
            if len(bars) < 2:
                return False, "BLOCKED_EXIT_ABNORMAL_CANDLE_DATA_UNAVAILABLE", details
            live, previous = bars[-1], bars[-2]
            opening = _number(live.get("open"))
            middle = _number(live.get("kc_middle"))
            atr = _number(previous.get("atr")) or _number(live.get("atr"))
            if None in (opening, middle, atr) or atr <= 0:
                return False, "BLOCKED_EXIT_ABNORMAL_CANDLE_DATA_INVALID", details
            body = opening - quote if side == "LONG" else quote - opening
            crossed = quote < middle if side == "LONG" else quote > middle
            if body < 1.5 * atr or not crossed:
                return False, "BLOCKED_EXIT_ABNORMAL_CANDLE_NOT_CONFIRMED", details
            return True, "EMERGENCY_EXIT_ABNORMAL_CANDLE_CONFIRMED", {
                "exit_type": "ABNORMAL_CANDLE", "body": body,
                "atr": atr, "kc_middle": middle, "quote": quote,
            }

        if "OPPOSITE_KC_BAND_BREACH" in norm_reason or "反側KC" in norm_reason:
            if len(bars) < 2:
                return False, "BLOCKED_EXIT_OPPOSITE_BREACH_DATA_UNAVAILABLE", details
            live, closed = bars[-1], bars[-2]
            band_key = "kc_lower" if side == "LONG" else "kc_upper"
            live_band = _number(live.get(band_key))
            closed_band = _number(closed.get(band_key))
            closed_price = _number(closed.get("close"))
            atr = _number(closed.get("atr")) or _number(live.get("atr"))
            if None in (live_band, closed_band, closed_price, atr) or atr <= 0:
                return False, "BLOCKED_EXIT_OPPOSITE_BREACH_DATA_INVALID", details
            outside = quote < live_band if side == "LONG" else quote > live_band
            if not outside:
                return False, "BLOCKED_EXIT_OPPOSITE_BREACH_RECLAIMED", details
            try:
                macro_frame = getattr(frame, "attrs", {}).get("macro_frame")
                if macro_frame is None:
                    macro_frame = frame
                import pandas as pd
                from core.services.data_pipeline import DataPipeline
                closed_macro = pd.DataFrame(_rows(macro_frame))
                macro = str(DataPipeline.calculate_snapshot(
                    "", closed_macro,
                    current_price=_number(closed_macro.iloc[-1].get("close"))
                    if not closed_macro.empty else None,
                ).macro_trend or "NEUTRAL").upper()
            except (AttributeError, KeyError, TypeError, ValueError, IndexError, OverflowError):
                macro = "NEUTRAL"
            aligned = (side == "LONG" and macro == "BULLISH") or (
                side == "SHORT" and macro == "BEARISH"
            )
            depth = (closed_band - closed_price) if side == "LONG" else (closed_price - closed_band)
            if aligned and depth < 0.5 * atr:
                return False, "BLOCKED_EXIT_OPPOSITE_BREACH_UNCONFIRMED", {
                    "macro_trend": macro, "closed_depth_atr": depth / atr,
                }
            return True, "EMERGENCY_EXIT_OPPOSITE_BREACH_CONFIRMED", {
                "exit_type": "OPPOSITE_KC_BAND_BREACH", "macro_trend": macro,
                "closed_depth_atr": depth / atr,
            }

        if "MEME_ADVERSE_STOP_LOSS" in norm_reason:
            entry = _number(position.get("entry_price"))
            atr = _number(bars[-2].get("atr")) if len(bars) >= 2 else None
            adverse_move = (
                entry - quote if side == "LONG" else quote - entry
            ) if entry is not None else None
            if (atr is not None and atr > 0 and adverse_move is not None
                    and adverse_move >= 1.5 * atr):
                return True, "EMERGENCY_EXIT_MEME_ADVERSE_STOP_CONFIRMED", {
                    "exit_type": "MEME_ADVERSE_STOP", "adverse_move": adverse_move,
                    "atr": atr, "adverse_atr": adverse_move / atr,
                }
            return False, "BLOCKED_EXIT_MEME_ADVERSE_STOP_NOT_CONFIRMED", details

        if any(token in norm_reason for token in (
            "HARD_STOP", "CIRCUIT_BREAKER", "STOP_LOSS", "STOP-LOSS", "停損",
        )):
            entry = _number(position.get("entry_price"))
            stop = _number(position.get("sl")) or _number(position.get("initial_sl"))
            if stop is None:
                entry_atr = _number(position.get("entry_atr"))
                if entry_atr is None and len(bars) >= 2:
                    entry_atr = _number(bars[-2].get("atr"))
                if entry is not None and entry_atr is not None:
                    stop = entry - 3.0 * entry_atr if side == "LONG" else entry + 3.0 * entry_atr
            breached = (
                stop is not None and (quote <= stop if side == "LONG" else quote >= stop)
            )
            if entry is not None and len(bars) >= 2:
                atr = _number(bars[-2].get("atr"))
                breached = breached or bool(
                    atr is not None and atr > 0 and
                    ((entry - quote >= 3.0 * atr) if side == "LONG"
                     else (quote - entry >= 3.0 * atr))
                )
            if breached:
                return True, "EMERGENCY_EXIT_HARD_STOP_CONFIRMED", {
                    "exit_type": "HARD_STOP", "stop_price": stop,
                    "quote": quote,
                }
            return False, "BLOCKED_EXIT_HARD_STOP_NOT_BREACHED", details

        return False, "BLOCKED_EXIT_EMERGENCY_REASON_UNVERIFIED", details

    sign = 1 if side == "LONG" else -1 if side == "SHORT" else 0
    if not sign or quote is None or quote <= 0 or len(bars) < 2:
        return False, "BLOCKED_EXIT_MATH_DATA_UNAVAILABLE", details
    entry_time = _number(position.get("open_timestamp") or position.get("entry_timestamp"))
    if entry_time is not None:
        entry_ms = entry_time * 1000.0 if entry_time < 1e11 else entry_time
        in_trade = []
        for row in bars:
            timestamp = _number(row.get("timestamp"))
            timestamp_ms = timestamp * 1000.0 if timestamp is not None and timestamp < 1e11 else timestamp
            if timestamp_ms is None or timestamp_ms >= entry_ms:
                in_trade.append(row)
        bars = in_trade
        if len(bars) < 2:
            return False, "BLOCKED_EXIT_MATH_DATA_UNAVAILABLE", details
    current, previous = bars[-1], bars[-2]

    # 數據層防禦：過期舊資料攔截（Stale Data Veto）
    raw_ts = _number(current.get("timestamp"))
    if raw_ts is not None:
        ts_ms = raw_ts * 1000.0 if (1e8 < raw_ts < 1e11) else raw_ts
        if ts_ms >= 1e11 or now_ms is not None:
            current_ms = now_ms if now_ms is not None else (time.time() * 1000.0)
            if (current_ms - ts_ms) > max_staleness_ms:
                return False, "BLOCKED_STALE_DATA", {"exit_type": "BLOCKED", "reason": "BLOCKED_STALE_DATA"}
    keys = ("open", "high", "low", "ma5", "atr", "kc_upper", "kc_middle", "kc_lower")
    current_values = {key: _number(current.get(key)) for key in keys}
    previous_upper = _number(previous.get("kc_upper"))
    previous_lower = _number(previous.get("kc_lower"))
    previous_middle = _number(previous.get("kc_middle"))
    if any(value is None for value in current_values.values()) or any(
        value is None for value in (previous_upper, previous_lower, previous_middle)
    ):
        return False, "BLOCKED_EXIT_MATH_DATA_UNAVAILABLE", details
    opened, high, low, ma5, atr, upper, middle, lower = (
        current_values[key] for key in keys
    )
    if min(opened, ma5, atr, upper, middle, lower, previous_upper, previous_lower, previous_middle) <= 0:
        return False, "BLOCKED_EXIT_MATH_DATA_INVALID", details
    close = quote
    previous_close = _number(previous.get("close"))
    previous_high = _number(previous.get("high"))
    previous_low = _number(previous.get("low"))
    if any(value is None for value in (previous_close, previous_high, previous_low)):
        return False, "BLOCKED_EXIT_MATH_DATA_INVALID", details

    # Path 0: Structural KC midline reversal, EMA reversal, and confirmed pivot exits
    norm_reason = str(close_reason or "").upper().replace(" ", "_")
    is_midline_reversal = any(token in norm_reason for token in (
        "CONFIRMED_KC_MIDLINE_REVERSAL", "KC_MIDLINE_RECLAIM_CLOSE",
        "KC_MIDLINE_RETRACE", "KC_MIDLINE_BREAK", "MIDLINE_REVERSAL",
        "WRONG_DIRECTION_CORRECTION", "SHORT_EXIT_KC_MID_BREACH",
        "MID_BREACH", "MID_BAND_EXIT",
    ))
    if is_midline_reversal:
        if side == "SHORT" and (close >= middle or previous_close >= previous_middle or middle > previous_middle):
            details.update(exit_type="MIDLINE_REVERSAL", close=close, middle=middle, previous_middle=previous_middle)
            return True, "EXIT_MATH_SHORT_MIDLINE_REVERSAL", details
        elif side == "LONG" and (close <= middle or previous_close <= previous_middle or middle < previous_middle):
            details.update(exit_type="MIDLINE_REVERSAL", close=close, middle=middle, previous_middle=previous_middle)
            return True, "EXIT_MATH_LONG_MIDLINE_REVERSAL", details

    curr_ma15 = _number(current.get("ma15")) or _number(current.get("ema15"))
    prev_ma15 = _number(previous.get("ma15")) or _number(previous.get("ema15"))
    is_ema_reversal = any(token in norm_reason for token in (
        "EMA_REVERSAL", "SHORT_EXIT_EMA_REVERSAL", "EMA_BULLISH_CROSS"
    ))
    if side == "SHORT" and (
        is_ema_reversal
        or (curr_ma15 is not None and prev_ma15 is not None and curr_ma15 > prev_ma15 and close > curr_ma15 and previous_close > prev_ma15)
    ):
        details.update(exit_type="EMA_REVERSAL", close=close, ma15=curr_ma15)
        return True, "EXIT_MATH_EMA_REVERSAL", details

    # ── Path 0.5: 早期真峰頂 / 真谷底樞紐識別 (Early Pivot Exit) ──
    is_early_pivot = any(token in norm_reason for token in (
        "EARLY_PIVOT", "EARLY_PIVOT_VALLEY", "EARLY_PIVOT_PEAK",
        "PIVOT_VALLEY", "PIVOT_PEAK", "EARLY_VALLEY", "EARLY_PEAK",
        "CLOSE_SHORT_AT_BOTTOM", "CLOSE_LONG_AT_TOP", "AT_BOTTOM", "AT_TOP",
        "TOP_BOTTOM_EXIT",
    ))
    if len(bars) >= 3:
        b1, b2, b3 = bars[-3], bars[-2], bars[-1]
        l1, l2, l3 = _number(b1.get("low")), _number(b2.get("low")), _number(b3.get("low"))
        h1, h2, h3 = _number(b1.get("high")), _number(b2.get("high")), _number(b3.get("high"))
        o2, c2 = _number(b2.get("open")), _number(b2.get("close"))
        c3_val, o3_val = _number(b3.get("close")), _number(b3.get("open"))
        if side == "SHORT":
            is_pivot_low = (l2 is not None and l1 is not None and l3 is not None and l2 < l1 and l2 < l3)
            mid2 = ((o2 or 0) + (c2 or 0)) / 2.0 if o2 and c2 else None
            is_valley_confirmed = is_pivot_low and (
                (c3_val is not None and mid2 is not None and c3_val > mid2 and c3_val > (o3_val or 0))
                or (c3_val is not None and c2 is not None and c3_val > c2)
            )
            if is_early_pivot or is_valley_confirmed:
                details.update(exit_type="EARLY_PIVOT", close=close, pivot_low=l2)
                return True, "EXIT_MATH_EARLY_PIVOT", details
        elif side == "LONG":
            is_pivot_high = (h2 is not None and h1 is not None and h3 is not None and h2 > h1 and h2 > h3)
            mid2 = ((o2 or 0) + (c2 or 0)) / 2.0 if o2 and c2 else None
            is_peak_confirmed = is_pivot_high and (
                (c3_val is not None and mid2 is not None and c3_val < mid2 and c3_val < (o3_val or 0))
                or (c3_val is not None and c2 is not None and c3_val < c2)
            )
            if is_early_pivot or is_peak_confirmed:
                details.update(exit_type="EARLY_PIVOT", close=close, pivot_high=h2)
                return True, "EXIT_MATH_EARLY_PIVOT", details
    elif is_early_pivot:
        details.update(exit_type="EARLY_PIVOT", close=close)
        return True, "EXIT_MATH_EARLY_PIVOT", details

    is_confirmed_pivot = any(token in norm_reason for token in (
        "TRUE_VALLEY_EXTREME_REVERSAL", "TRUE_PEAK_EXTREME_REVERSAL",
        "CONFIRMED_TRUE_PEAK_PIVOT", "CONFIRMED_TRUE_VALLEY_PIVOT",
        "EXIT_MEME_TRUE_PEAK_CONFIRMED", "EXIT_MEME_TRUE_VALLEY_CONFIRMED",
    ))
    if is_confirmed_pivot:
        details.update(exit_type="CONFIRMED_PIVOT", close=close)
        return True, "EXIT_MATH_CONFIRMED_PIVOT", details


    # Path A: outer-rail extension followed by a mathematical exhaustion signal.
    hold_veto: tuple[str, dict[str, Any]] | None = None
    if side == "SHORT":
        historical_low = min(
            _number(position.get("lowest_price")) or quote,
            *(_number(row.get("low")) or quote for row in bars[-120:]),
            quote,
        )
        was_below_lower = (
            low < lower or previous_low < previous_lower or previous_close < previous_lower
            or (historical_low < lower)
            or any((_number(r.get("low")) or quote) < (_number(r.get("kc_lower")) or quote) for r in bars[-30:])
            or bool((position.get("kc_true_extreme_exit_state") or {}).get("armed"))
        )
        bullish_ma_reclaim = close > opened and close > ma5
        rebound = close - historical_low >= 0.3 * atr
        if was_below_lower and (bullish_ma_reclaim or rebound):
            details.update(exit_type="TRUE_BOTTOM", historical_low=historical_low, atr=atr)
            return True, "EXIT_MATH_TRUE_BOTTOM", details
        middle_slope = middle - previous_middle
        if middle_slope <= 0 and close < middle:
            hold_veto = ("VETO_HOLD", {"exit_type": "VETO_HOLD", "kc_middle_slope": middle_slope})
    else:
        historical_high = max(
            _number(position.get("highest_price")) or quote,
            *(_number(row.get("high")) or quote for row in bars[-120:]),
            quote,
        )
        was_above_upper = (
            high > upper or previous_high > previous_upper or previous_close > previous_upper
            or (historical_high > upper)
            or any((_number(r.get("high")) or quote) > (_number(r.get("kc_upper")) or quote) for r in bars[-30:])
            or bool((position.get("kc_true_extreme_exit_state") or {}).get("armed"))
        )
        bearish_ma_break = close < opened and close < ma5
        pullback = historical_high - close >= 0.3 * atr
        if was_above_upper and (bearish_ma_break or pullback):
            details.update(exit_type="TRUE_PEAK", historical_high=historical_high, atr=atr)
            return True, "EXIT_MATH_TRUE_PEAK", details
        middle_slope = middle - previous_middle
        if middle_slope >= 0 and close > middle:
            hold_veto = ("VETO_HOLD", {"exit_type": "VETO_HOLD", "kc_middle_slope": middle_slope})

    # Path B: fee-aware net-ROE breakeven and ratchet floors.
    meta = metadata if isinstance(metadata, Mapping) else {}
    state = (
        position.get("three_tier_net_roe_lock_state")
        or meta.get("three_tier_net_roe_lock_state")
        or position.get("profit_lock_state")
        or meta.get("profit_lock_state")
        or {}
    )
    state = state if isinstance(state, Mapping) else {}
    fee = max(0.0, _number(fee_rate) or 0.0)
    slip = max(0.0, _number(slippage) or 0.0)
    current_roe = _net_roe_pct(position, quote, sign, fee, slip)
    entry = _number(position.get("entry_price"))
    if current_roe is not None and entry is not None:
        favored = max(
            _number(position.get("highest_price")) or entry,
            *(_number(row.get("high")) or entry for row in bars[-120:]),
        ) if side == "LONG" else min(
            _number(position.get("lowest_price")) or entry,
            *(_number(row.get("low")) or entry for row in bars[-120:]),
        )
        peak_roe = _number(state.get("peak_net_roe_pct"))
        if peak_roe is None:
            peak_roe = _net_roe_pct(position, favored, sign, fee, slip)
        favorable_atr = (favored - entry) / atr if side == "LONG" else (entry - favored) / atr
        if peak_roe is not None and peak_roe >= 10.0 and favorable_atr >= 0.5:
            be_line = entry * (1.0005 if side == "LONG" else 0.9995)
            if (side == "LONG" and quote <= be_line) or (side == "SHORT" and quote >= be_line):
                details.update(exit_type="PROFIT_LOCK", lock_type="BREAKEVEN", peak_roe=peak_roe)
                return True, "EXIT_MATH_PROFIT_LOCK", details
        floor = None
        if peak_roe is not None:
            from core.services.exits.net_roe_profit_lock import tiered_net_roe_floor
            floor, _tier = tiered_net_roe_floor(peak_roe)
        if floor is not None and current_roe <= floor + 1e-12:
            details.update(exit_type="PROFIT_LOCK", floor_net_roe_pct=floor, current_net_roe_pct=current_roe)
            return True, "EXIT_MATH_PROFIT_LOCK", details

    if hold_veto is not None:
        return False, hold_veto[0], hold_veto[1]
    return False, "BLOCKED_EXIT_NO_MATH_TRIGGER", details


async def validate_entry_at_execution(
    account: Any, symbol: str, side: str, context: Any = None,
    *, execution_price: float | None = None,
) -> tuple[bool, str]:
    """Load the latest permitted snapshot, then run the pure entry calculator."""
    payload = context if isinstance(context, Mapping) else {}
    # A cached signal snapshot may be stale by the time a direct, continuation,
    # or passive order reaches execution. Always prefer a fresh account snapshot.
    # open_position() supplies one freshly verified execution snapshot for all
    # account-side gates. Re-fetching here and again in entry_firewall both
    # wastes REST calls and can hold the global submit lock across settlement
    # retries.
    frame = payload.get("entry_gate_frame")
    provider = getattr(account, "entry_frame_provider", None)
    if frame is None and callable(provider):
        frame = provider(symbol)
        if hasattr(frame, "__await__"):
            frame = await frame
    if frame is None:
        frame = payload.get("entry_gate_frame")
    if frame is None:
        frame = payload.get("frame")
    if frame is None:
        snapshot = payload.get("snapshot")
        if isinstance(snapshot, Mapping):
            frame = snapshot
    if frame is None:
        return False, "BLOCKED_ENTRY_MATH_SNAPSHOT_UNAVAILABLE"

    # Re-run the shared structural gate at the account execution boundary.
    # Earlier scan authorization and manual/UI flags cannot replace this check.
    from core.gates.central_entry_gate import CentralEntryGate

    signal_type = str(
        payload.get("candidate_signal_type")
        or payload.get("entry_signal_code")
        or payload.get("signal_code")
        or ""
    )
    source = " ".join(str(value or "") for value in (
        payload.get("source"), payload.get("reason"), signal_type,
    ))
    is_swing_pivot = (
        str(payload.get("entry_type") or "").upper() == "SWING_PIVOT"
        or "SWING_PIVOT" in signal_type.upper()
    )
    if is_swing_pivot:
        from core.services.swing_pivot_rules import evaluate_swing_pivot_entry
        pivot = evaluate_swing_pivot_entry(frame)
        requested_stop = _number(payload.get("stop_loss_price"))
        if (
            pivot is None or pivot.get("side") != str(side).upper()
            or requested_stop is None
            or abs(requested_stop - float(pivot["stop_loss_price"])) > max(1e-12, abs(requested_stop) * 1e-10)
        ):
            return False, "BLOCKED_SWING_PIVOT_STOP_OR_EVIDENCE_MISMATCH"
    authorized_intrabar_types = {
        "BREAKOUT_LONG", "BREAKDOWN_SHORT",
        "CONTINUATION_OUTSIDE_KC_LONG", "CONTINUATION_OUTSIDE_KC_SHORT",
        "ENTRY_LONG_MA3_MA15_CROSS_MID", "ENTRY_SHORT_MA3_MA15_CROSS_MID",
        "AUTHORIZED_REALTIME_BREAKOUT", "KC_REALTIME_RAIL_BREACH_SHORT",
        "KC_LIVE_BODY_BREAKOUT_LONG", "KC_LIVE_BODY_BREAKOUT_SHORT",
        "CLOSED_BODY_BREAKOUT_LONG", "CLOSED_BODY_BREAKOUT_SHORT",
        "KC_LIVE_PATTERN_BREAKOUT_LONG", "KC_LIVE_PATTERN_BREAKOUT_SHORT",
    }
    allow_intrabar = bool(
        payload.get("intrabar_authorized") is True
        and payload.get("upstream_entry_gate_authorized") is True
        and payload.get("source") == "UNIFIED_PIPELINE"
        and signal_type in authorized_intrabar_types
    )
    rows = _rows(frame)
    quote = _number(
        execution_price or payload.get("entry_gate_quote")
        or (rows[-1].get("close") if rows else None)
    )
    if quote is None or quote <= 0:
        return False, "BLOCKED_ENTRY_MATH_EXECUTION_QUOTE_INVALID"
    central_result = CentralEntryGate.verify_and_authorize(
        symbol=symbol, side=side, price=quote, klines_or_snapshot=frame,
        signal_id=str(payload.get("signal_id") or "") or None,
        source=source or "EXECUTION_REVALIDATION",
        allow_intrabar=allow_intrabar,
    )
    if not central_result.passed:
        return False, f"BLOCKED_FINAL_CENTRAL_ENTRY_GATE:{central_result.reason}"

    fast_track = False
    bars = _rows(frame)
    if bars:
        raw_time = _number(bars[-1].get("timestamp"))
        if raw_time is not None:
            bar_ms = raw_time * 1000.0 if raw_time < 1e11 else raw_time
            timeframe_ms = 60_000
            if hasattr(frame, "attrs"):
                timeframe_ms = int(frame.attrs.get("timeframe_ms") or timeframe_ms)
            for trade in reversed(getattr(account, "trades", []) or []):
                if trade.get("symbol") != symbol or not str(trade.get("action", "")).startswith("CLOSE_"):
                    continue
                reason = str(trade.get("reason") or "").upper()
                close_ms = _number(trade.get("id") or trade.get("timestamp_ms"))
                if close_ms is not None:
                    if close_ms < 1e11:
                        close_ms *= 1000.0
                    elapsed_bars = max(0, int((bar_ms - close_ms) // timeframe_ms))
                    fast_track = "PROFIT_LOCK" in reason and elapsed_bars <= 3
                break
    is_outer = bool(
        payload.get("is_outer_entry")
        or payload.get("outer_rail")
        or "OUTSIDE_KC" in str(payload.get("entry_signal_code") or "")
        or "BREAKOUT" in str(payload.get("entry_signal_code") or "")
        or payload.get("entry_authority") in {"KC2BAR", "CONTINUATION"}
    )
    is_dip = bool(
        payload.get("entry_authority") in {"DIP_SNIPER", "KEY_LEVEL_DIP_SNIPER"}
        or "DIP_SNIPER" in str(payload.get("entry_signal_code") or "")
        or "DIP_SNIPER" in str(payload.get("reason") or "")
        or payload.get("structure_confirmation") in {"DIP_SNIPER", "MA_CROSSOVER"}
        or payload.get("entry_pattern") in {"DIP_SNIPER", "MA_CROSSOVER"}
    )
    is_meme = bool(
        payload.get("entry_authority") == "MEME_PEAK_HUNTER"
        or "MEME_PEAK_HUNTER" in str(payload.get("entry_signal_code") or "")
        or "MEME_PEAK_HUNTER" in str(payload.get("reason") or "")
        or payload.get("structure_confirmation") == "MEME_PEAK_HUNTER"
        or payload.get("entry_pattern") == "MEME_PEAK_HUNTER"
    )
    math_frame = frame
    if is_swing_pivot:
        from core.services.candle_data import closed_entry_candles
        math_frame = closed_entry_candles(frame)
        math_frame.attrs.update(getattr(frame, "attrs", {}))
    return validate_entry_math(
        math_frame,
        side,
        profit_lock_fast_track=fast_track,
        is_outer_entry=is_outer,
        is_dip_sniper=is_dip,
        is_meme_hunter=is_meme,
        is_swing_pivot=is_swing_pivot,
    )


def validate_exit_at_execution(
    account: Any,
    symbol: str,
    position: Mapping[str, Any],
    price: float,
    close_reason: str,
) -> tuple[bool, str, dict[str, Any]]:
    provider = getattr(account, "channel_exit_frame_provider", None)
    frame = provider(symbol) if callable(provider) else None
    if frame is None:
        frame = getattr(account, "_channel_exit_frames", {}).get(symbol)
    if frame is None:
        engine = getattr(account, "engine", None)
        frame = getattr(engine, "_channel_exit_frames", {}).get(symbol)
    from core.config import SLIPPAGE_PCT, TAKER_FEE_RATE
    return validate_exit_math(
        position,
        frame,
        price,
        getattr(account, "position_meta", {}).get(symbol, {}),
        fee_rate=TAKER_FEE_RATE,
        slippage=SLIPPAGE_PCT,
        close_reason=close_reason,
    )


def is_entry_bar(position: Mapping[str, Any], frame: Any) -> bool:
    bars = _rows(frame)
    if not bars:
        return False
    bar_ms = _number(bars[-1].get("timestamp"))
    opened = _number(position.get("open_timestamp") or position.get("entry_timestamp"))
    if bar_ms is None or opened is None:
        return False
    if opened < 1e11:
        opened *= 1000.0
    if bar_ms < 1e11:
        bar_ms *= 1000.0
    timeframe_ms = 60_000
    frame_obj = frame
    if hasattr(frame_obj, "attrs"):
        timeframe_ms = int(frame_obj.attrs.get("timeframe_ms") or timeframe_ms)
    return math.floor(opened / timeframe_ms) == math.floor(bar_ms / timeframe_ms)
