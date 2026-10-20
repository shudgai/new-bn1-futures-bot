"""Fail-closed completed-bar MA5/MA15 crossover entry authorization."""

import math
from typing import Any, Dict, Tuple

import pandas as pd

from core.services.candle_data import closed_entry_candles


def _kc_direction(row: pd.Series) -> str:
    value = str(row.get("kc_trend") or row.get("channel_state") or "").strip().upper()
    if value in {"UP", "KC向上", "KC_TREND_UP", "UPTREND", "BULLISH", "LONG"}:
        return "UP"
    if value in {"DOWN", "KC向下", "KC_TREND_DOWN", "DOWNTREND", "BEARISH", "SHORT"}:
        return "DOWN"
    return "RANGE"


def ma_cross_gate_exempt_for_entry(
    entry_type: Any = None,
    entry_phase: Any = None,
    strategy_type: Any = None,
    entry_pattern: Any = None,
    structure_confirmation: Any = None,
) -> bool:
    """Identify independently confirmed breakout and continuation authorities."""
    from core.gates.pipeline import FLAT_LINE_BREAKOUT_TYPES, entry_strategy_type

    normalized_type = str(entry_type or "").upper()
    normalized_phase = str(entry_phase or "").upper()
    normalized_strategy = str(strategy_type or "").upper()
    normalized_pattern = str(entry_pattern or "").upper()
    normalized_confirmation = str(structure_confirmation or "").upper()
    cross_types = {
        "KC_GOLDEN_CROSS_FAST_LONG",
        "KC_DEATH_CROSS_FAST_SHORT",
        "TRIGGER_B_MA_CROSS",
    }
    if normalized_type in cross_types:
        return False
    if (
        normalized_strategy in {"BREAKOUT", "TREND_CONTINUATION"}
        or entry_strategy_type(normalized_phase, normalized_strategy) is not None
        or normalized_type in FLAT_LINE_BREAKOUT_TYPES
        or normalized_type in {
            "BEARISH_INSTANT_BREAKOUT",
            "BULLISH_INSTANT_BREAKOUT",
            "KC_LIVE_OUTER_LONG",
            "KC_LIVE_OUTER_SHORT",
            "KC_MIDDLE_TREND_LONG",
            "KC_MIDDLE_TREND_SHORT",
            "KC_OUTSIDE_LONG",
            "KC_OUTSIDE_SHORT",
            "KC_TREND_LONG",
            "KC_TREND_SHORT",
        }
        or normalized_phase in {
            "KC_2BAR_BREAKOUT",
            "KC_2BAR_CLOSED_CONFIRM",
            "KC_LIVE_OUTER_BREAKOUT",
            "KC_LIVE_PATTERN_BREAKOUT",
            "KC_THREE_BAR_BREAKOUT",
            "POST_CLOSE_CONTINUATION_ENTRY",
            "REALTIME_RAIL_BREACH",
        }
        or normalized_pattern in {
            "BREAKOUT",
            "CONFIRMED_2BAR_BREAKOUT",
            "CONTINUATION",
            "CONTINUATION_OUTSIDE_KC",
            "DIP_SNIPER",
            "MID_LINE_BREAKDOWN",
            "MID_LINE_BREAKOUT",
            "MA_CROSSOVER",
            "MEME_PEAK_HUNTER",
            "INSTANT_BREAKOUT_INSIDE",
            "INSTANT_BURST_BREAKOUT",
        }
        or normalized_confirmation in {
            "BREAKOUT",
            "CONFIRMED_2BAR_BREAKOUT",
            "CONTINUATION",
            "CONTINUATION_OUTSIDE_KC",
            "DIP_SNIPER",
            "MID_LINE_BREAKDOWN",
            "MID_LINE_BREAKOUT",
            "MA_CROSSOVER",
            "MEME_PEAK_HUNTER",
            "INSTANT_BREAKOUT_INSIDE",
            "INSTANT_BURST_BREAKOUT",
        }
    ):
        return True
    return False


def validate_ma5_ma15_cross_entry(
    frame: Any,
    side: str,
    price: Any = None,
) -> Tuple[bool, str, Dict[str, Any]]:
    """Validate fresh MA crosses and veto late, extended long crossover entries."""
    side = str(side).upper()
    if side not in {"LONG", "SHORT"}:
        return False, "BLOCKED_INVALID_SIDE", {}
    if not isinstance(frame, pd.DataFrame) or frame.empty:
        return False, "BLOCKED_MA_CROSS_MARKET_DATA_INVALID", {}

    try:
        closed = closed_entry_candles(frame)
    except (AttributeError, KeyError, TypeError, ValueError):
        return False, "BLOCKED_MA_CROSS_MARKET_DATA_INVALID", {}
    if len(closed) < 3:
        return False, "BLOCKED_MA_CROSS_INSUFFICIENT_CLOSED_BARS", {}
    previous, current = closed.iloc[-2], closed.iloc[-1]
    try:
        ma5_prev = float(previous.get("ma5", previous.get("_wild_ma5")))
        ma15_prev = float(previous["ma15"])
        ma5_curr = float(current.get("ma5", current.get("_wild_ma5")))
        ma15_curr = float(current["ma15"])
        opening = float(current["open"])
        close = float(current["close"])
        values = (ma5_prev, ma15_prev, ma5_curr, ma15_curr, opening, close)
    except (KeyError, TypeError, ValueError, OverflowError):
        return False, "BLOCKED_MA_CROSS_MARKET_DATA_INVALID", {}
    if not all(math.isfinite(value) and value > 0 for value in values):
        return False, "BLOCKED_MA_CROSS_MARKET_DATA_INVALID", {}

    try:
        observed = frame.iloc[-1]
        quote = float(price if price is not None else observed["close"])
    except (KeyError, TypeError, ValueError, OverflowError, IndexError):
        return False, "BLOCKED_MA_CROSS_MARKET_DATA_INVALID", {}
    if not math.isfinite(quote) or quote <= 0:
        return False, "BLOCKED_MA_CROSS_MARKET_DATA_INVALID", {}

    direction = _kc_direction(current)
    explicit_direction = str(
        current.get("kc_trend") or current.get("channel_state") or ""
    ).strip()
    try:
        previous_middle = float(previous["kc_middle"])
        current_middle = float(current["kc_middle"])
        middle_direction = (
            "UP" if current_middle > previous_middle
            else "DOWN" if current_middle < previous_middle
            else "RANGE"
        )
        if explicit_direction:
            if direction != middle_direction:
                return False, "BLOCKED_KC_TREND_DATA_CONFLICT", {}
        else:
            direction = middle_direction
    except (KeyError, TypeError, ValueError, OverflowError):
        if not explicit_direction:
            direction = "RANGE"
    details = {
        "side": side,
        "previous_ma5": ma5_prev,
        "previous_ma15": ma15_prev,
        "current_ma5": ma5_curr,
        "current_ma15": ma15_curr,
        "kc_trend": direction,
        "open": opening,
        "close": close,
        "confirmation_bar_id": current.get("timestamp"),
        "quote": quote,
    }
    if side == "LONG":
        cross_events = []
        try:
            ma_pairs = [
                (
                    float(row.get("ma5", row.get("_wild_ma5"))),
                    float(row["ma15"]),
                )
                for _, row in closed.iterrows()
            ]
        except (KeyError, TypeError, ValueError, OverflowError):
            return False, "BLOCKED_MA_CROSS_MARKET_DATA_INVALID", details
        if not all(
            math.isfinite(ma5) and ma5 > 0
            and math.isfinite(ma15) and ma15 > 0
            for ma5, ma15 in ma_pairs
        ):
            return False, "BLOCKED_MA_CROSS_MARKET_DATA_INVALID", details
        for index in range(1, len(ma_pairs)):
            prior_spread = ma_pairs[index - 1][0] - ma_pairs[index - 1][1]
            spread = ma_pairs[index][0] - ma_pairs[index][1]
            if prior_spread <= 0 < spread:
                cross_events.append((index, "LONG"))
            elif prior_spread >= 0 > spread:
                cross_events.append((index, "SHORT"))
        last_cross = cross_events[-1] if cross_events else None
        cross_age = (
            len(ma_pairs) - 1 - last_cross[0]
            if last_cross is not None else None
        )
        details["cross_age_bars"] = cross_age
        cross = (
            last_cross is not None
            and last_cross[1] == "LONG"
            and cross_age in (1, 2)
        )
        if not cross:
            return False, (
                "BLOCKED_MA5_MA15_CROSS_TOO_OLD"
                if last_cross is not None and last_cross[1] == "LONG"
                else "BLOCKED_MA5_MA15_GOLDEN_CROSS_REQUIRED"
            ), details
        try:
            kc_upper = float(observed["kc_upper"])
            current_ma15 = float(
                observed.get("ma15", observed.get("_wild_ma15"))
            )
            reference_atr = float(current["atr"])
            observed_close = float(observed["close"])
            observed_is_closed = observed.get("is_closed", False)
            if not (
                observed_is_closed is True
                or (
                    type(observed_is_closed).__name__ == "bool_"
                    and bool(observed_is_closed)
                )
            ):
                current_ma15 += (quote - observed_close) / 15.0
            if not all(math.isfinite(value) and value > 0 for value in (
                kc_upper, current_ma15, reference_atr,
            )):
                raise ValueError("invalid anti-FOMO indicator")
        except (KeyError, TypeError, ValueError, OverflowError):
            return False, "BLOCKED_ANTI_FOMO_MARKET_DATA_INVALID", details
        long_bias_atr = abs(quote - current_ma15) / reference_atr
        details.update({
            "kc_upper": kc_upper,
            "current_ma15": current_ma15,
            "reference_atr": reference_atr,
            "ma15_bias_atr": long_bias_atr,
        })
        if quote > kc_upper:
            return False, "BLOCKED_LONG_ABOVE_KC_UPPER", details
        if long_bias_atr > 0.5:
            return False, "BLOCKED_LONG_MA15_BIAS", details
        valid = (
            cross and direction == "UP" and close > opening
        )
        failure_reason = "BLOCKED_MA5_MA15_GOLDEN_CROSS_REQUIRED"
    else:
        try:
            kc_lower = float(observed["kc_lower"])
            current_ma15 = float(
                observed.get("ma15", observed.get("_wild_ma15"))
            )
            reference_atr = float(current["atr"])
            observed_close = float(observed["close"])
            observed_is_closed = observed.get("is_closed", False)
            if not (
                observed_is_closed is True
                or (
                    type(observed_is_closed).__name__ == "bool_"
                    and bool(observed_is_closed)
                )
            ):
                current_ma15 += (quote - observed_close) / 15.0
            if not all(math.isfinite(value) and value > 0 for value in (
                kc_lower, current_ma15, reference_atr,
            )):
                raise ValueError("invalid anti-FOMO indicator")
        except (KeyError, TypeError, ValueError, OverflowError):
            return False, "BLOCKED_ANTI_FOMO_MARKET_DATA_INVALID", details
        short_bias_atr = abs(quote - current_ma15) / reference_atr
        details.update({
            "kc_lower": kc_lower,
            "current_ma15": current_ma15,
            "reference_atr": reference_atr,
            "ma15_bias_atr": short_bias_atr,
        })
        if quote < kc_lower:
            return False, "BLOCKED_SHORT_BELOW_KC_LOWER", details
        if short_bias_atr > 0.5:
            return False, "BLOCKED_SHORT_MA15_BIAS", details
        cross = ma5_prev >= ma15_prev and ma5_curr < ma15_curr
        valid = (
            cross and ma5_curr < ma5_prev
            and direction == "DOWN" and close < opening
        )
        failure_reason = "BLOCKED_MA5_MA15_DEATH_CROSS_REQUIRED"
    if not valid:
        return False, failure_reason, details

    try:
        from core.gates.macro_candle_structure_gate import MacroCandleStructureGate

        row = current
        previous_middle = float(previous["kc_middle"])
        current_middle = float(current["kc_middle"])
        indicators = {
            "kc_upper": float(row["kc_upper"]),
            "kc_lower": float(row["kc_lower"]),
            "kc_middle": current_middle,
            "ma15": ma15_curr,
            "atr": float(row["atr"]),
            "ma5_current": ma5_curr,
            "ma5_previous": ma5_prev,
            "kc_middle_current": current_middle,
            "kc_middle_previous": previous_middle,
        }
        macro_allowed, macro_reason = MacroCandleStructureGate(
            max_bias_atr=1.2,
        ).can_open(
            side,
            {
                name: float(row[name])
                for name in ("open", "high", "low", "close")
            },
            indicators,
            symbol="LOBSTER/USDT",
            entry_pattern="CROSSOVER",
            structure_confirmation="MA5_MA15_1M_CROSS",
            structure_confirmed=True,
        )
        kc_upper = indicators["kc_upper"]
        kc_lower = indicators["kc_lower"]
        atr = indicators["atr"]
        close_price = float(row["close"])
        body = abs(close_price - float(row["open"]))
        upper_wick = float(row["high"]) - max(float(row["open"]), close_price)
        lower_wick = min(float(row["open"]), close_price) - float(row["low"])
        channel_width = kc_upper - kc_lower
        if (
            not macro_allowed
            or channel_width <= 0
            or atr <= 0
        ):
            return False, macro_reason, details
        kc_position = (close_price - kc_lower) / channel_width
        bias_atr = abs(close_price - ma15_curr) / atr
        if side == "LONG" and kc_position > 0.98:
            return False, "VETO_MACRO_KC_POSITION_LONG", details
        if side == "SHORT" and kc_position < 0.02:
            return False, "VETO_MACRO_KC_POSITION_SHORT", details
        if bias_atr > 2.0:
            return False, "VETO_MACRO_MA15_BIAS", details
        if side == "LONG" and upper_wick > body * 2.5 and upper_wick > 0.7 * atr and close_price < float(row["high"]):
            return False, "VETO_MACRO_LONG_UPPER_WICK_EXHAUSTION", details
        if side == "SHORT" and lower_wick > body * 2.5 and lower_wick > 0.7 * atr and close_price > float(row["low"]):
            return False, "VETO_MACRO_SHORT_LOWER_WICK_EXHAUSTION", details
    except (KeyError, TypeError, ValueError, OverflowError):
        return False, "BLOCKED_MACRO_STRUCTURE_DATA_INVALID", details

    if side == "LONG":
        if quote > kc_upper:
            return False, "BLOCKED_LONG_ABOVE_KC_UPPER", details
        if long_bias_atr > 0.5:
            return False, "BLOCKED_LONG_MA15_BIAS", details
        if ma5_curr <= ma5_prev:
            return False, "BLOCKED_LONG_MA5_NOT_RISING", details

    return (
        True,
        "PERMIT_MA5_MA15_GOLDEN_CROSS"
        if side == "LONG" else "PERMIT_MA5_MA15_DEATH_CROSS",
        details,
    )
