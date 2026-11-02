"""Closed-candle entry authorities requested for the unified pipeline."""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

import pandas as pd

from core.services.candle_data import closed_entry_candles
from core.gates.meme_entry_policy import (
    MAX_EXTENSION_ATR, is_exhausted_wick, uses_relaxed_entry_limits,
)


KC_TWO_BAR_BREAKOUT = "KC_TWO_BAR_BREAKOUT"
POST_CLOSE_KC_REENTRY = "POST_CLOSE_KC_REENTRY"
THREE_BAR_PIVOT_REVERSAL = "THREE_BAR_PIVOT_REVERSAL"
KC_OUTER_PIVOT_REVERSAL = "KC_OUTER_PIVOT_REVERSAL"
MA5_MA15_CROSS = "MA5_MA15_CROSS"
# Import compatibility only; persisted literal MA3 authorities are not accepted.
MA3_MA15_CROSS = MA5_MA15_CROSS

ENTRY_METHODS = frozenset({
    KC_TWO_BAR_BREAKOUT,
    POST_CLOSE_KC_REENTRY,
    THREE_BAR_PIVOT_REVERSAL,
    KC_OUTER_PIVOT_REVERSAL,
    MA5_MA15_CROSS,
})

_METHOD_RULES = {
    KC_TWO_BAR_BREAKOUT: ("BREAKOUT", "KC_OUTER_BREAKOUT"),
    POST_CLOSE_KC_REENTRY: ("CONTINUATION", "POST_CLOSE_KC_REENTRY"),
    THREE_BAR_PIVOT_REVERSAL: (
        "REVERSAL", "CONFIRMED_THREE_BAR_PIVOT_REVERSAL",
    ),
    KC_OUTER_PIVOT_REVERSAL: (
        "REVERSAL", "CONFIRMED_THREE_BAR_PIVOT_REVERSAL",
    ),
    MA5_MA15_CROSS: ("CROSSOVER", "MA_CROSSOVER"),
}

_REQUIRED_COLUMNS = (
    "timestamp", "open", "high", "low", "close", "kc_upper", "kc_lower",
    "kc_middle", "ma5", "ma15", "atr",
)


def _number(row: Mapping[str, Any], key: str) -> float | None:
    try:
        value = float(row[key])
    except (KeyError, TypeError, ValueError, OverflowError):
        return None
    return value if math.isfinite(value) else None


def _valid_row(row: Mapping[str, Any]) -> bool:
    values = {key: _number(row, key) for key in _REQUIRED_COLUMNS}
    if any(value is None for value in values.values()):
        return False
    open_price = values["open"]
    high = values["high"]
    low = values["low"]
    close = values["close"]
    if (
        min(
            values["timestamp"], open_price, high, low, close,
            values["kc_upper"], values["kc_lower"], values["kc_middle"],
            values["ma5"], values["ma15"], values["atr"],
        ) <= 0
        or values["kc_upper"] <= values["kc_lower"]
        or not values["kc_lower"] < values["kc_middle"] < values["kc_upper"]
        or values["atr"] <= 0
        or high < max(open_price, close, low)
        or low > min(open_price, close, high)
    ):
        return False
    return True


def _timestamp_ms(value: Any) -> float | None:
    try:
        stamp = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(stamp) or stamp <= 0:
        return None
    return stamp * 1000.0 if 1e8 < stamp < 1e11 else stamp


def _new_signal(
    method: str,
    side: str,
    bars: list[dict[str, Any]],
    *,
    last_exit_timestamp: float | None = None,
) -> dict[str, Any]:
    confirmation = bars[-1]
    confirmation_id = int(float(confirmation["timestamp"]))
    source_ids = [
        int(float(bars[index]["timestamp"]))
        for index in range(len(bars) - (3 if method in {
            THREE_BAR_PIVOT_REVERSAL, KC_OUTER_PIVOT_REVERSAL,
        } else 2), len(bars))
    ]

    exit_ms = _timestamp_ms(last_exit_timestamp)
    exit_id = int(exit_ms) if exit_ms is not None else None
    event_id = "|".join(
        str(value) for value in (
            "ENTRY_METHOD", method, side, *source_ids, exit_id,
        ) if value is not None
    )
    pattern, structure_confirmation = _METHOD_RULES[method]
    return {
        "method": method,
        "side": side,
        "event_id": event_id,
        "bar_timestamp": confirmation_id,
        "confirmation_bar_id": confirmation_id,
        "source_bar_ids": source_ids,
        "last_exit_timestamp": last_exit_timestamp,
        "entry_pattern": pattern,
        "structure_confirmation": structure_confirmation,
        "structure_confirmed": True,
        "channel_expanding": method == KC_TWO_BAR_BREAKOUT,
        "entry_atr": float(confirmation["atr"]),
    }


def evaluate_entry_method(
    frame: Any,
    *,
    quote: float,
    symbol: str,
    last_exit_timestamp: float | None = None,
    expected_method: str | None = None,
) -> dict[str, Any] | None:
    """Return one deterministic signal from closed bars, or fail closed."""
    if not isinstance(frame, pd.DataFrame) or frame.empty:
        return None
    try:
        timeframe_ms = float(frame.attrs.get("timeframe_ms") or 0)
    except (TypeError, ValueError, OverflowError):
        return None
    if timeframe_ms != 60_000.0 or not set(_REQUIRED_COLUMNS).issubset(frame.columns):
        return None
    if expected_method is not None and expected_method not in ENTRY_METHODS:
        return None
    try:
        current_quote = float(quote)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(current_quote) or current_quote <= 0:
        return None

    closed_frame = closed_entry_candles(frame)
    bars = closed_frame.to_dict("records")
    if len(bars) < 2 or any(not _valid_row(row) for row in bars[-3:]):
        return None
    stamps = [_timestamp_ms(row["timestamp"]) for row in bars[-3:]]
    if any(stamp is None for stamp in stamps) or any(
        later <= earlier for earlier, later in zip(stamps, stamps[1:])
    ):
        return None

    candidates: list[dict[str, Any]] = []

    if len(bars) >= 3 and expected_method in (None, KC_OUTER_PIVOT_REVERSAL):
        import time
        from core.services.exits.trend_lock import _closed_snapshot_fresh

        left, pivot, current = bars[-3:]
        snapshot = _number(frame.attrs, "snapshot_ms")
        exit_ms = _timestamp_ms(last_exit_timestamp) if last_exit_timestamp is not None else None
        fresh = (
            snapshot is not None and 0 <= time.time() * 1000 - snapshot <= 180_000
            and _closed_snapshot_fresh(frame, closed_frame)
            and all(math.isclose(b - a, 60_000, abs_tol=1)
                    for a, b in zip(stamps, stamps[1:]))
            and (last_exit_timestamp is None or (
                exit_ms is not None and stamps[0] >= math.ceil(exit_ms / 60_000) * 60_000
            ))
        )
        if fresh:
            midpoint = (float(pivot["open"]) + float(pivot["close"])) / 2
            for side in ("LONG", "SHORT"):
                confirmed = (
                    float(pivot["low"]) < min(float(left["low"]), float(current["low"]))
                    and float(pivot["low"]) < float(pivot["kc_lower"])
                    and float(current["close"]) > max(float(current["open"]), midpoint, float(current["ma5"]))
                    and float(current["ma5"]) > float(pivot["ma5"])
                    and current_quote > max(midpoint, float(current["ma5"]), float(pivot["low"]))
                    if side == "LONG" else
                    float(pivot["high"]) > max(float(left["high"]), float(current["high"]))
                    and float(pivot["high"]) > float(pivot["kc_upper"])
                    and float(current["close"]) < min(float(current["open"]), midpoint, float(current["ma5"]))
                    and float(current["ma5"]) < float(pivot["ma5"])
                    and current_quote < min(midpoint, float(current["ma5"]), float(pivot["high"]))
                )
                if confirmed:
                    candidates.append(_new_signal(
                        KC_OUTER_PIVOT_REVERSAL, side, bars,
                        last_exit_timestamp=last_exit_timestamp,
                    ))

    if len(bars) >= 2 and expected_method in (None, KC_TWO_BAR_BREAKOUT):
        first, second = bars[-2], bars[-1]
        for side in ("LONG", "SHORT"):
            edge = "kc_upper" if side == "LONG" else "kc_lower"
            first_open = float(first["open"])
            first_close = float(first["close"])
            second_open = float(second["open"])
            second_close = float(second["close"])
            first_inside = float(first["kc_lower"]) <= first_open <= float(first["kc_upper"])
            first_body_break = (
                first_close > float(first[edge])
                if side == "LONG" else first_close < float(first[edge])
            )
            same_color = (
                first_close > first_open and second_close > second_open
                if side == "LONG"
                else first_close < first_open and second_close < second_open
            )
            second_outside = (
                second_close > float(second[edge])
                if side == "LONG" else second_close < float(second[edge])
            )
            ma5_direction = (
                float(second["ma5"]) > float(first["ma5"])
                if side == "LONG" else float(second["ma5"]) < float(first["ma5"])
            )
            second_beyond_ma5 = (
                second_close > float(second["ma5"])
                if side == "LONG" else second_close < float(second["ma5"])
            )
            quote_beyond = (
                current_quote > float(second[edge])
                and current_quote > float(second["ma5"])
                if side == "LONG"
                else current_quote < float(second[edge])
                and current_quote < float(second["ma5"])
            )
            if (
                first_inside and first_body_break and same_color and second_outside
                and ma5_direction and second_beyond_ma5 and quote_beyond
            ):
                candidates.append(_new_signal(
                    KC_TWO_BAR_BREAKOUT, side, bars,
                ))

    if (
        last_exit_timestamp is not None
        and len(bars) >= 2
        and expected_method in (None, POST_CLOSE_KC_REENTRY)
    ):
        exit_ms = _timestamp_ms(last_exit_timestamp)
        latest_ms = _timestamp_ms(bars[-1]["timestamp"])
        if exit_ms is not None and latest_ms is not None:
            exit_bar_start = math.floor(exit_ms / 60_000.0) * 60_000.0
            if latest_ms > exit_bar_start:
                previous, current = bars[-2], bars[-1]
                for side in ("LONG", "SHORT"):
                    edge = "kc_upper" if side == "LONG" else "kc_lower"
                    outside = (
                        float(current["close"]) > float(current[edge])
                        and current_quote > float(current[edge])
                        if side == "LONG"
                        else float(current["close"]) < float(current[edge])
                        and current_quote < float(current[edge])
                    )
                    ma5_direction = (
                        float(current["ma5"]) > float(previous["ma5"])
                        if side == "LONG"
                        else float(current["ma5"]) < float(previous["ma5"])
                    )
                    if outside and ma5_direction:
                        candidates.append(_new_signal(
                            POST_CLOSE_KC_REENTRY, side, bars,
                            last_exit_timestamp=last_exit_timestamp,
                        ))

    if len(bars) >= 3 and expected_method in (None, THREE_BAR_PIVOT_REVERSAL):
        left, pivot, current = bars[-3:]
        for side in ("LONG", "SHORT"):
            is_valley_or_peak = (
                float(pivot["low"]) < float(left["low"])
                and float(pivot["low"]) < float(current["low"])
                if side == "LONG"
                else float(pivot["high"]) > float(left["high"])
                and float(pivot["high"]) > float(current["high"])
            )
            confirmation_color = (
                float(current["close"]) > float(current["open"])
                if side == "LONG"
                else float(current["close"]) < float(current["open"])
            )
            ma5_turned = (
                float(pivot["ma5"]) <= float(left["ma5"])
                and float(current["ma5"]) > float(pivot["ma5"])
                if side == "LONG"
                else float(pivot["ma5"]) >= float(left["ma5"])
                and float(current["ma5"]) < float(pivot["ma5"])
            )
            pivot_intact = (
                current_quote > float(pivot["low"])
                if side == "LONG" else current_quote < float(pivot["high"])
            )
            if is_valley_or_peak and confirmation_color and ma5_turned and pivot_intact:
                candidates.append(_new_signal(THREE_BAR_PIVOT_REVERSAL, side, bars))

    if len(bars) >= 2 and expected_method in (None, MA5_MA15_CROSS):
        previous, current = bars[-2], bars[-1]
        if (
            float(previous["ma5"]) <= float(previous["ma15"])
            and float(current["ma5"]) > float(current["ma15"])
        ):
            candidates.append(_new_signal(MA5_MA15_CROSS, "LONG", bars))
        if (
            float(previous["ma5"]) >= float(previous["ma15"])
            and float(current["ma5"]) < float(current["ma15"])
        ):
            candidates.append(_new_signal(MA5_MA15_CROSS, "SHORT", bars))

    if not candidates:
        return None
    sides = {signal["side"] for signal in candidates}
    if len(sides) != 1:
        return None

    priority = (
        KC_OUTER_PIVOT_REVERSAL,
        POST_CLOSE_KC_REENTRY,
        KC_TWO_BAR_BREAKOUT,
        THREE_BAR_PIVOT_REVERSAL,
        MA5_MA15_CROSS,
    )
    for method in priority:
        for signal in candidates:
            if signal["method"] == method:
                signal["symbol"] = symbol
                return signal
    return None


def revalidate_entry_method(
    frame: Any,
    *,
    symbol: str,
    side: str,
    quote: float,
    context: Mapping[str, Any],
) -> tuple[bool, str]:
    """Rebuild the exact trigger and apply the Macro Candle Structure veto."""
    method = str(context.get("entry_method_authority") or "")
    if method not in ENTRY_METHODS:
        return False, "BLOCKED_ENTRY_METHOD_UNSUPPORTED"
    last_exit_timestamp = context.get("entry_method_last_exit_timestamp")
    signal = evaluate_entry_method(
        frame,
        quote=quote,
        symbol=symbol,
        last_exit_timestamp=last_exit_timestamp,
        expected_method=method,
    )
    expected = context.get("entry_method_signal")
    if (
        signal is None
        or signal["side"] != str(side).upper()
        or not isinstance(expected, Mapping)
        or signal["event_id"] != expected.get("event_id")
        or signal["confirmation_bar_id"] != context.get("entry_method_bar_id")
        or signal["event_id"] != context.get("entry_method_event_id")
    ):
        return False, "BLOCKED_ENTRY_METHOD_SIGNAL_STALE_OR_INVALID"

    from core.gates.macro_candle_structure_gate import MacroCandleStructureGate

    closed = closed_entry_candles(frame)
    if closed.empty:
        return False, "BLOCKED_ENTRY_METHOD_NO_CLOSED_BARS"
    current = closed.iloc[-1]
    try:
        candle = {
            "open": float(current["open"]),
            "high": float(current["high"]),
            "low": float(current["low"]),
            "close": float(current["close"]),
        }
        indicators = {
            "kc_upper": float(current["kc_upper"]),
            "kc_lower": float(current["kc_lower"]),
            "kc_middle": float(current["kc_middle"]),
            "ma15": float(current["ma15"]),
            "atr": float(current["atr"]),
            "kc_middle_current": float(current["kc_middle"]),
            "kc_middle_previous": float(closed.iloc[-2]["kc_middle"]),
            "ma15_current": float(current["ma15"]),
            "ma15_previous": float(closed.iloc[-2]["ma15"]),
        }
        for key in ("ma5",):
            if key in closed.columns:
                indicators["ma5_current"] = float(current[key])
                indicators["ma5_previous"] = float(closed.iloc[-2][key])
    except (IndexError, KeyError, TypeError, ValueError, OverflowError):
        return False, "BLOCKED_ENTRY_METHOD_MACRO_DATA_INVALID"

    kc_width = indicators["kc_upper"] - indicators["kc_lower"]
    close = candle["close"]
    kc_position = (close - indicators["kc_lower"]) / kc_width
    bias_atr = abs(close - indicators["ma15"]) / indicators["atr"]
    body = abs(close - candle["open"])
    upper_wick = candle["high"] - max(candle["open"], close)
    lower_wick = min(candle["open"], close) - candle["low"]
    if method == KC_TWO_BAR_BREAKOUT:
        ma5 = _number(current, "ma5")
        if ma5 is None or ma5 <= 0:
            return False, "BLOCKED_ENTRY_METHOD_BREAKOUT_MA5_INVALID"
    elif method != POST_CLOSE_KC_REENTRY and (
        (side == "LONG" and kc_position > 0.35)
        or (side == "SHORT" and kc_position < 0.65)
    ):
        return False, "BLOCKED_ENTRY_METHOD_MACRO_KC_RELATIVE_LOCATION"
    relaxed = uses_relaxed_entry_limits(symbol)
    if not relaxed and bias_atr > 1.2:
        return False, "BLOCKED_ENTRY_METHOD_MACRO_MA15_BIAS"
    relaxed_outer = relaxed and method in {KC_TWO_BAR_BREAKOUT, POST_CLOSE_KC_REENTRY}
    if relaxed_outer:
        ma5 = _number(current, "ma5")
        if ma5 is None or ma5 <= 0:
            return False, "BLOCKED_ENTRY_METHOD_BREAKOUT_MA5_INVALID"
        price = max(close, float(quote)) if side == "LONG" else min(close, float(quote))
        band_distance = (
            price - indicators["kc_upper"] if side == "LONG"
            else indicators["kc_lower"] - price
        )
        if band_distance > MAX_EXTENSION_ATR * indicators["atr"]:
            return False, "BLOCKED_ENTRY_METHOD_BAND_EXTENSION"
        if abs(price - ma5) > MAX_EXTENSION_ATR * indicators["atr"]:
            return False, "BLOCKED_ENTRY_METHOD_MA5_DISTANCE"
    long_wick_exhausted = (
        is_exhausted_wick(upper_wick, body, candle["high"] - candle["low"])
        if relaxed_outer else upper_wick > body
    )
    short_wick_exhausted = (
        is_exhausted_wick(lower_wick, body, candle["high"] - candle["low"])
        if relaxed_outer else lower_wick > body
    )
    if (
        side == "LONG"
        and long_wick_exhausted
        and close < candle["high"]
    ):
        return False, "BLOCKED_ENTRY_METHOD_MACRO_LONG_EXHAUSTION_WICK"
    if (
        side == "SHORT"
        and short_wick_exhausted
        and close > candle["low"]
    ):
        return False, "BLOCKED_ENTRY_METHOD_MACRO_SHORT_EXHAUSTION_WICK"

    pattern, confirmation = _METHOD_RULES[method]
    allowed, reason = MacroCandleStructureGate(max_bias_atr=1.2).can_open(
        side,
        candle,
        indicators,
        symbol=symbol,
        entry_pattern=pattern,
        structure_confirmation=confirmation,
        structure_confirmed=True,
        channel_expanding=signal["channel_expanding"],
    )
    if not allowed:
        return False, "BLOCKED_ENTRY_METHOD_MACRO_GATE:" + reason
    return True, "ENTRY_METHOD_REVALIDATED:" + method
