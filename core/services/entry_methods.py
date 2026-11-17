"""Closed-candle entry authorities requested for the unified pipeline."""

from __future__ import annotations

import logging
import math
import time
from collections.abc import Mapping
from typing import Any

import pandas as pd

from core.services.candle_data import (
    closed_entry_candles,
    missed_breakout_trend_problem,
)
from core.symbol_aliases import is_meme_breakout_symbol
from core.gates.meme_entry_policy import (
    MAX_EXTENSION_ATR, is_exhausted_wick, uses_relaxed_entry_limits,
)

logger = logging.getLogger(__name__)

KC_TWO_BAR_BREAKOUT = "KC_TWO_BAR_BREAKOUT"
KC_EARLY_MIDLINE_BREAK = "KC_EARLY_MIDLINE_BREAK"
MEME_GATE_ENTRY = "MEME_GATE_ENTRY"
MEME_SHORT_CONTINUATION_TRIGGERS = frozenset({
    "TRIGGER_ALIGNED_TREND_SLOPES",
    "TRIGGER_B_BREAKDOWN_LOWER_RAIL",
    "TRIGGER_C_BREAKDOWN_LOWER_RAIL",
    "TRIGGER_OUTER_RAIL_CONTINUATION_SHORT",
})
POST_CLOSE_KC_REENTRY = "POST_CLOSE_KC_REENTRY"
THREE_BAR_PIVOT_REVERSAL = "THREE_BAR_PIVOT_REVERSAL"
KC_OUTER_PIVOT_REVERSAL = "KC_OUTER_PIVOT_REVERSAL"
MA5_MA15_CROSS = "MA5_MA15_CROSS"
KC_SINGLE_BAR_BREAKOUT = "KC_SINGLE_BAR_BREAKOUT"
KC_MIDLINE_MOMENTUM = "KC_MIDLINE_MOMENTUM"
KC_DUAL_TREND_LOCATION = "KC_DUAL_TREND_LOCATION"
KC_BEARISH_PULLBACK_RESUMPTION = "KC_BEARISH_PULLBACK_RESUMPTION"
MTF_KC_STATE_MACHINE = "MTF_KC_STATE_MACHINE"
# Import compatibility only; persisted literal MA3 authorities are not accepted.
MA3_MA15_CROSS = MA5_MA15_CROSS

ENTRY_METHODS = frozenset({
    KC_TWO_BAR_BREAKOUT,
    KC_EARLY_MIDLINE_BREAK,
    MEME_GATE_ENTRY,
    POST_CLOSE_KC_REENTRY,
    THREE_BAR_PIVOT_REVERSAL,
    KC_OUTER_PIVOT_REVERSAL,
    MA5_MA15_CROSS,
    KC_SINGLE_BAR_BREAKOUT,
    KC_MIDLINE_MOMENTUM,
    KC_DUAL_TREND_LOCATION,
    KC_BEARISH_PULLBACK_RESUMPTION,
    MTF_KC_STATE_MACHINE,
})

_METHOD_RULES = {
    KC_TWO_BAR_BREAKOUT: ("BREAKOUT", "KC_OUTER_BREAKOUT"),
    KC_EARLY_MIDLINE_BREAK: ("BREAKOUT", "CONFIRMED_EARLY_MIDLINE_BREAK"),
    MEME_GATE_ENTRY: ("BREAKOUT", "CONFIRMED_MEME_GATE_TRIGGER"),
    POST_CLOSE_KC_REENTRY: ("CONTINUATION", "POST_CLOSE_KC_REENTRY"),
    THREE_BAR_PIVOT_REVERSAL: (
        "REVERSAL", "CONFIRMED_THREE_BAR_PIVOT_REVERSAL",
    ),
    KC_OUTER_PIVOT_REVERSAL: (
        "REVERSAL", "CONFIRMED_THREE_BAR_PIVOT_REVERSAL",
    ),
    MA5_MA15_CROSS: ("CROSSOVER", "MA_CROSSOVER"),
    KC_SINGLE_BAR_BREAKOUT: ("BREAKOUT", "KC_OUTER_BREAKOUT"),
    KC_MIDLINE_MOMENTUM: ("CONTINUATION", "KC_MIDLINE_MOMENTUM"),
    KC_DUAL_TREND_LOCATION: ("REVERSAL", KC_DUAL_TREND_LOCATION),
    KC_BEARISH_PULLBACK_RESUMPTION: (
        "REVERSAL", "CONFIRMED_BEARISH_PULLBACK_RESUMPTION",
    ),
    MTF_KC_STATE_MACHINE: ("TREND", "CONFIRMED_MTF_KC_STATE_ENTRY"),
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


def _live_candle_veto(frame: Any, symbol: str, side: str, quote: float) -> str | None:
    if side != "SHORT" or not is_meme_breakout_symbol(symbol):
        if side == "LONG" and is_meme_breakout_symbol(symbol) and isinstance(frame, pd.DataFrame) and not frame.empty and "is_closed" in frame.columns:
            try:
                live_rows = frame.loc[frame["is_closed"].eq(False)]
                if len(live_rows) == 1 and live_rows.index[-1] == frame.index[-1]:
                    opened = _number(live_rows.iloc[-1], "open")
                    current_quote = float(quote)
                    if opened is not None and opened > 0 and math.isfinite(current_quote) and current_quote <= opened:
                        return "BLOCKED_ENTRY_METHOD_LONG_LIVE_CANDLE_NOT_GREEN"
            except Exception:
                pass
        return None
    attrs = getattr(frame, "attrs", None)
    if not isinstance(attrs, dict) or attrs.get("entry_finality_verified") is not True:
        return None
    if (
        not isinstance(frame, pd.DataFrame)
        or frame.empty
        or "is_closed" not in frame.columns
        or "timestamp" not in frame.columns
        or "open" not in frame.columns
    ):
        return "BLOCKED_ENTRY_METHOD_SHORT_LIVE_CANDLE_UNAVAILABLE"
    try:
        interval = float(attrs.get("timeframe_ms") or 0)
        snapshot_ms = float(attrs.get("snapshot_ms") or 0)
        age_ms = time.time() * 1000 - snapshot_ms
        live_rows = frame.loc[frame["is_closed"].eq(False)]
    except (TypeError, ValueError, OverflowError):
        return "BLOCKED_ENTRY_METHOD_SHORT_LIVE_CANDLE_UNAVAILABLE"
    if (
        interval != 60_000
        or not math.isfinite(snapshot_ms)
        or snapshot_ms <= 0
        or not 0 <= age_ms <= 180_000
        or len(live_rows) != 1
        or live_rows.index[-1] != frame.index[-1]
    ):
        return "BLOCKED_ENTRY_METHOD_SHORT_LIVE_CANDLE_UNAVAILABLE"
    row = live_rows.iloc[-1]
    timestamp = _timestamp_ms(row.get("timestamp"))
    opened = _number(row, "open")
    high = _number(row, "high")
    low = _number(row, "low")
    close = _number(row, "close")
    try:
        current_quote = float(quote)
    except (TypeError, ValueError, OverflowError):
        return "BLOCKED_ENTRY_METHOD_SHORT_LIVE_CANDLE_UNAVAILABLE"
    if (
        timestamp is None
        or not timestamp <= snapshot_ms < timestamp + interval
        or any(value is None or value <= 0 for value in (opened, high, low, close))
        or high < max(opened, close)
        or low > min(opened, close)
        or not math.isfinite(current_quote)
        or current_quote <= 0
    ):
        return "BLOCKED_ENTRY_METHOD_SHORT_LIVE_CANDLE_UNAVAILABLE"
    if current_quote >= opened:
        return "BLOCKED_ENTRY_METHOD_SHORT_LIVE_CANDLE_NOT_RED"
    return None


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
            THREE_BAR_PIVOT_REVERSAL,
            KC_OUTER_PIVOT_REVERSAL,
            KC_BEARISH_PULLBACK_RESUMPTION,
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


def _kc_position(row: Mapping[str, Any], price: float) -> float | None:
    upper = _number(row, "kc_upper")
    lower = _number(row, "kc_lower")
    if upper is None or lower is None or upper <= lower:
        return None
    return (price - lower) / (upper - lower)


def _bearish_pullback_resumption(
    frame: pd.DataFrame,
    bars: list[dict[str, Any]],
    quote: float,
) -> bool:
    if (
        len(bars) < 3
        or str(frame.attrs.get("macro_trend_htf") or "").upper() != "BEARISH"
    ):
        return False
    previous, pullback, trigger = bars[-3:]
    positions = (
        _kc_position(previous, float(previous["close"])),
        _kc_position(pullback, float(pullback["high"])),
        _kc_position(trigger, float(trigger["close"])),
        _kc_position(trigger, quote),
    )
    if any(position is None for position in positions):
        return False
    previous_position, pullback_high_position, close_position, quote_position = positions
    return (
        previous_position < 0.65
        and float(pullback["close"]) > float(pullback["open"])
        and float(pullback["close"]) > float(previous["close"])
        and 0.65 <= pullback_high_position <= 1.0
        and float(trigger["close"]) < float(trigger["open"])
        and float(trigger["close"]) < float(pullback["close"])
        and 0.65 <= close_position <= 1.0
        and 0.65 <= quote_position <= 1.0
        and float(trigger["kc_middle"]) < float(pullback["kc_middle"])
        and float(trigger["ma5"]) < float(pullback["ma5"])
        and float(trigger["ma5"]) < float(trigger["ma15"])
    )


def evaluate_entry_method(
    frame: Any,
    *,
    quote: float,
    symbol: str,
    last_exit_timestamp: float | None = None,
    expected_method: str | None = None,
    allow_same_bar_after_close: bool = False,
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

    if expected_method in (None, MEME_GATE_ENTRY) and is_meme_breakout_symbol(symbol):
        from core.gates.master_gate_funnel import MasterGateFunnel, market_snapshot_from_frame
        try:
            snapshot = market_snapshot_from_frame(frame, symbol, current_quote)
            exit_ms = _timestamp_ms(last_exit_timestamp)
            if last_exit_timestamp is not None and (exit_ms is None or stamps[-1] <= exit_ms):
                return None
            for side in ("LONG", "SHORT"):
                decision = (
                    MasterGateFunnel.evaluate_open_long(snapshot) if side == "LONG"
                    else MasterGateFunnel.evaluate_open_short(snapshot)
                )
                if decision.can_execute:
                    signal = _new_signal(MEME_GATE_ENTRY, side, bars, last_exit_timestamp=last_exit_timestamp)
                    signal.update(entry_atr=snapshot.atr, trigger=decision.details["trigger"])
                    candidates.append(signal)
        except (KeyError, TypeError, ValueError, OverflowError, AttributeError, IndexError) as exc:
            logger.warning("MEME_GATE_ENTRY_DATA_BLOCK symbol=%s error=%s", symbol, exc)

    if expected_method in (None, KC_EARLY_MIDLINE_BREAK):
        from core.services.exits.trend_lock import _closed_snapshot_fresh


        previous, current = bars[-2:]
        snapshot = _number(frame.attrs, "snapshot_ms")
        exit_ms = _timestamp_ms(last_exit_timestamp)
        fresh = (
            is_meme_breakout_symbol(symbol)
            and snapshot is not None and 0 <= time.time() * 1000 - snapshot <= 180_000
            and _closed_snapshot_fresh(frame, closed_frame)
            and math.isclose(stamps[-1] - stamps[-2], 60_000, abs_tol=1)
            and (last_exit_timestamp is None or (
                exit_ms is not None and stamps[-1] > exit_ms
            ))
        )
        if fresh:
            for side in ("LONG", "SHORT"):
                sign = 1 if side == "LONG" else -1
                middle = float(current["kc_middle"])
                body = sign * (float(current["close"]) - float(current["open"]))
                reference_atr = float(previous["atr"])
                price = max(float(current["close"]), current_quote) if sign == 1 else min(
                    float(current["close"]), current_quote,
                )
                if (
                    (body >= 0.7 * reference_atr or math.isclose(body, 0.7 * reference_atr, rel_tol=1e-12))
                    and sign * (float(current["close"]) - middle) > 0
                    and sign * (middle - float(previous["kc_middle"])) > 0
                    and sign * (float(current["ma5"]) - float(previous["ma5"])) > 0
                    and sign * (current_quote - middle) > 0
                    and sign * (current_quote - float(current["ma5"])) > 0
                ):
                    if sign * (price - float(current["ma15"])) > reference_atr:
                        logger.info(
                            "BLOCKED_ENTRY_METHOD_MA15_EXTENSION_1_ATR symbol=%s side=%s method=%s",
                            symbol, side, KC_EARLY_MIDLINE_BREAK,
                        )
                        continue
                    signal = _new_signal(
                        KC_EARLY_MIDLINE_BREAK, side, bars,
                        last_exit_timestamp=last_exit_timestamp,
                    )
                    signal["entry_atr"] = reference_atr
                    candidates.append(signal)

    if len(bars) >= 3 and expected_method in (None, KC_OUTER_PIVOT_REVERSAL):
        from core.gates.master_gate_funnel import is_kc_middle_flat
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
            kc_middle_flat = is_kc_middle_flat(
                float(current["kc_middle"]),
                float(pivot["kc_middle"]),
                float(pivot["atr"]),
            )
            macro_direction = str(
                frame.attrs.get("macro_trend_htf")
                or frame.attrs.get("macro_trend")
                or ""
            ).upper()
            for side in ("LONG", "SHORT"):
                kc_direction_aligned = (
                    macro_direction == ("BULLISH" if side == "LONG" else "BEARISH")
                    if kc_middle_flat
                    else (
                        float(current["kc_middle"]) > float(pivot["kc_middle"])
                        if side == "LONG"
                        else float(current["kc_middle"]) < float(pivot["kc_middle"])
                    )
                )
                confirmed = (
                    kc_direction_aligned
                    and float(pivot["low"]) < min(float(left["low"]), float(current["low"]))
                    and float(pivot["low"]) < float(pivot["kc_lower"])
                    and float(current["close"]) > max(float(current["open"]), midpoint, float(current["ma5"]))
                    and float(current["ma5"]) > float(pivot["ma5"])
                    and current_quote > max(midpoint, float(current["ma5"]), float(pivot["low"]))
                    if side == "LONG" else
                    kc_direction_aligned
                    and float(pivot["high"]) > max(float(left["high"]), float(current["high"]))
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
        from core.gates.master_gate_funnel import is_kc_middle_flat
        kc_middle_flat = is_kc_middle_flat(
            float(second["kc_middle"]),
            float(first["kc_middle"]),
            float(first["atr"]),
        )
        macro_direction = str(
            frame.attrs.get("macro_trend_htf")
            or frame.attrs.get("macro_trend")
            or ""
        ).upper()
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
            expected_macro = "BULLISH" if side == "LONG" else "BEARISH"
            kc_direction = (
                macro_direction == expected_macro
                if kc_middle_flat
                else (
                    float(second["kc_middle"]) > float(first["kc_middle"])
                    if side == "LONG"
                    else float(second["kc_middle"]) < float(first["kc_middle"])
                )
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
                and ma5_direction and kc_direction and second_beyond_ma5 and quote_beyond
            ):
                bias = (
                    max(second_close, current_quote) - float(second["ma15"])
                    if side == "LONG" else
                    float(second["ma15"]) - min(second_close, current_quote)
                )
                if is_meme_breakout_symbol(symbol) and bias > float(first["atr"]):
                    logger.info(
                        "BLOCKED_ENTRY_METHOD_MA15_EXTENSION_1_ATR symbol=%s side=%s method=%s",
                        symbol, side, KC_TWO_BAR_BREAKOUT,
                    )
                    continue
                candidates.append(_new_signal(
                    KC_TWO_BAR_BREAKOUT, side, bars,
                ))
            elif (
                not first_inside
                and (
                    first_open > float(first["kc_upper"])
                    if side == "LONG"
                    else first_open < float(first["kc_lower"])
                )
                and first_body_break and same_color and second_outside
                and ma5_direction and second_beyond_ma5 and quote_beyond
                and last_exit_timestamp is None
            ):
                trend_problem = missed_breakout_trend_problem(frame, side)
                if trend_problem:
                    logger.info(
                        "MISSED_BREAKOUT_CONTINUATION_BLOCKED symbol=%s side=%s reason=%s",
                        symbol, side, trend_problem,
                    )
                    continue
                from core.gates.breakout_continuation_entry_gate import (
                    BreakoutContinuationEntryGate,
                    CONTINUATION_OUTSIDE_KC,
                )

                continuation_candidate = {
                    "type": CONTINUATION_OUTSIDE_KC,
                    "action": f"OPEN_{side}",
                    "price": current_quote,
                    "kc_upper": float(second["kc_upper"]),
                    "kc_lower": float(second["kc_lower"]),
                    "ma5_current": float(second["ma5"]),
                }
                late_chase_reason = BreakoutContinuationEntryGate.late_chase_block_reason(
                    continuation_candidate,
                    bars,
                    [float(row["kc_upper"]) for row in bars],
                    [float(row["kc_lower"]) for row in bars],
                    tick_price=current_quote,
                    symbol=symbol,
                )
                if late_chase_reason:
                    logger.info(
                        "MISSED_BREAKOUT_CONTINUATION_BLOCKED symbol=%s side=%s reason=%s",
                        symbol, side, late_chase_reason,
                    )
                    continue
                signal = _new_signal(KC_TWO_BAR_BREAKOUT, side, bars)
                signal["entry_phase"] = "MISSED_BREAKOUT_CONTINUATION"
                candidates.append(signal)

    if len(bars) >= 2 and expected_method in (None, KC_SINGLE_BAR_BREAKOUT):
        previous, current = bars[-2], bars[-1]
        for side in ("LONG", "SHORT"):
            sign = 1 if side == "LONG" else -1
            edge = "kc_upper" if side == "LONG" else "kc_lower"
            close = float(current["close"])
            ma5 = float(current["ma5"])
            ma5_slope = ma5 - float(previous["ma5"])
            if (
                sign * (close - float(current[edge])) > 0
                and sign * (current_quote - float(current[edge])) > 0
                and sign * (close - float(current["open"])) > 0
                and sign * ma5_slope > 0
                and sign * (close - ma5) > 0
            ):
                candidates.append(_new_signal(
                    KC_SINGLE_BAR_BREAKOUT, side, bars,
                ))

    if len(bars) >= 2 and expected_method in (None, KC_MIDLINE_MOMENTUM):
        previous, current = bars[-2], bars[-1]
        previous_close = float(previous["close"])
        current_ma5 = float(current["ma5"])
        angle = math.degrees(math.atan(
            (current_ma5 - float(previous["ma5"])) / previous_close * 1000.0,
        ))
        for side in ("LONG", "SHORT"):
            sign = 1 if side == "LONG" else -1
            aligned = (
                angle > 30.0
                if side == "LONG" else angle < -30.0
            )
            if (
                aligned
                and sign * (float(current["kc_middle"]) - float(previous["kc_middle"])) > 0
                and sign * (float(current["close"]) - current_ma5) > 0
                and sign * (current_quote - current_ma5) > 0
            ):
                candidates.append(_new_signal(
                    KC_MIDLINE_MOMENTUM, side, bars,
                ))

    if expected_method in (None, KC_DUAL_TREND_LOCATION):
        previous, current = bars[-2:]
        snapshot_ms = _number(frame.attrs, "snapshot_ms")
        try:
            from core.services.exits.trend_lock import _closed_snapshot_fresh

            fresh = (
                frame.attrs.get("entry_finality_verified") is True
                and snapshot_ms is not None
                and 0 <= time.time() * 1000 - snapshot_ms <= 180_000
                and _closed_snapshot_fresh(frame, closed_frame)
                and math.isclose(stamps[-1] - stamps[-2], 60_000, abs_tol=1)
            )
        except (AttributeError, KeyError, TypeError, ValueError, OverflowError):
            fresh = False

        macro_trend = str(frame.attrs.get("macro_trend_htf") or "").upper()
        exit_ms = _timestamp_ms(last_exit_timestamp)
        same_bar_flip = (
            allow_same_bar_after_close
            and exit_ms is not None
            and stamps[-1] <= exit_ms < stamps[-1] + 60_000
        )
        exit_is_eligible = (
            last_exit_timestamp is None
            or (exit_ms is not None and stamps[-1] > exit_ms)
            or same_bar_flip
        )
        if fresh and exit_is_eligible:
            kc_slope = float(current["kc_middle"]) - float(previous["kc_middle"])
            for side, expected_macro, aligned in (
                ("LONG", "BULLISH", kc_slope > 0),
            ):
                if (
                    macro_trend == expected_macro
                    and aligned
                    and (
                        current_quote > float(current["kc_middle"])
                        if side == "LONG"
                        else current_quote < float(current["kc_middle"])
                    )
                ):
                    candidates.append(_new_signal(
                        KC_DUAL_TREND_LOCATION,
                        side,
                        bars,
                        last_exit_timestamp=last_exit_timestamp,
                    ))

    if (
        expected_method in (None, KC_BEARISH_PULLBACK_RESUMPTION)
        and is_meme_breakout_symbol(symbol)
        and _bearish_pullback_resumption(frame, bars, current_quote)
    ):
        exit_ms = _timestamp_ms(last_exit_timestamp)
        if last_exit_timestamp is None or (
            exit_ms is not None and stamps[-1] > exit_ms
        ):
            signal = _new_signal(KC_BEARISH_PULLBACK_RESUMPTION, "SHORT", bars)
            signal["trigger"] = "TRIGGER_KC_BEARISH_PULLBACK_RESUMPTION"
            candidates.append(signal)

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
                    atr_val = float(previous.get("atr") or current.get("atr") or 0.0)
                    body_size = abs(float(current["close"]) - float(current["open"]))
                    if atr_val > 0 and body_size > 1.5 * atr_val:
                        continue
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
                    kc_direction = (
                        float(current["kc_middle"]) > float(previous["kc_middle"])
                        if side == "LONG"
                        else float(current["kc_middle"]) < float(previous["kc_middle"])
                    )
                    if outside and ma5_direction and kc_direction:
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
            kc_direction = (
                float(current["kc_middle"]) > float(pivot["kc_middle"])
                if side == "LONG"
                else float(current["kc_middle"]) < float(pivot["kc_middle"])
            )
            pivot_intact = (
                current_quote > float(pivot["low"])
                if side == "LONG" else current_quote < float(pivot["high"])
            )
            if is_valley_or_peak and confirmation_color and ma5_turned and kc_direction and pivot_intact:
                candidates.append(_new_signal(THREE_BAR_PIVOT_REVERSAL, side, bars))

    if len(bars) >= 2 and expected_method in (None, MA5_MA15_CROSS):
        current = bars[-1]
        latest_cross = None
        for index in range(1, len(bars)):
            previous_cross, current_cross = bars[index - 1], bars[index]
            prior_spread = float(previous_cross["ma5"]) - float(previous_cross["ma15"])
            spread = float(current_cross["ma5"]) - float(current_cross["ma15"])
            if prior_spread <= 0 < spread:
                latest_cross = (index, "LONG")
            elif prior_spread >= 0 > spread:
                latest_cross = (index, "SHORT")

        if latest_cross is not None:
            cross_index, cross_side = latest_cross
            cross_age = len(bars) - 1 - cross_index
            sign = 1 if cross_side == "LONG" else -1
            current_spread = float(current["ma5"]) - float(current["ma15"])
            current_mid_slope = (
                float(current["kc_middle"]) - float(bars[-2]["kc_middle"])
            )
            delayed_price_aligned = (
                sign * (float(current["close"]) - float(current["ma5"])) > 0
                and sign * (current_quote - float(current["ma5"])) > 0
            )
            if (
                cross_age <= 2
                and cross_side == "LONG"
                and current_spread > 0
                and current_mid_slope > 0
                and (cross_age == 0 or delayed_price_aligned)
            ):
                candidates.append(_new_signal(MA5_MA15_CROSS, "LONG", bars))
            elif (
                cross_age <= 2
                and cross_side == "SHORT"
                and current_spread < 0
                and current_mid_slope < 0
                and (cross_age == 0 or delayed_price_aligned)
            ):
                candidates.append(_new_signal(MA5_MA15_CROSS, "SHORT", bars))

    if is_meme_breakout_symbol(symbol):
        from core.gates.master_gate_funnel import evaluate_meme_entry_frame
        candidates = [
            signal for signal in candidates
            if evaluate_meme_entry_frame(
                frame,
                symbol,
                signal["side"],
                current_quote,
                allow_kc_location_override=(
                    signal["method"] == KC_DUAL_TREND_LOCATION
                ),
                allow_bearish_pullback_override=(
                    signal["method"] == KC_BEARISH_PULLBACK_RESUMPTION
                ),
                allow_confirmed_breakout_override=(
                    signal["method"] == KC_TWO_BAR_BREAKOUT
                ),
                allow_confirmed_pivot_override=(
                    signal["method"] == KC_OUTER_PIVOT_REVERSAL
                ),
            ).can_execute
        ]
    if not candidates:
        return None
    sides = {signal["side"] for signal in candidates}
    if len(sides) != 1:
        return None

    priority = (
        KC_BEARISH_PULLBACK_RESUMPTION,
        MEME_GATE_ENTRY,
        KC_DUAL_TREND_LOCATION,
        KC_OUTER_PIVOT_REVERSAL,
        KC_EARLY_MIDLINE_BREAK,
        POST_CLOSE_KC_REENTRY,
        KC_TWO_BAR_BREAKOUT,
        KC_SINGLE_BAR_BREAKOUT,
        THREE_BAR_PIVOT_REVERSAL,
        KC_MIDLINE_MOMENTUM,
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
    final_submission: bool = False,
) -> tuple[bool, str]:
    """Rebuild the exact trigger and apply the Macro Candle Structure veto."""
    method = str(context.get("entry_method_authority") or "")
    if method not in ENTRY_METHODS:
        return False, "BLOCKED_ENTRY_METHOD_UNSUPPORTED"
    if method == MTF_KC_STATE_MACHINE:
        from core.services.mtf_kc_state_machine import evaluate_mtf_entry

        allow_colorless_reverse = (
            context.get("entry_method_allow_same_bar_after_close") is True
            and context.get("mtf_cycle_reverse_after_close") is True
            and context.get("mtf_colorless_reverse") is True
        )
        evaluation = evaluate_mtf_entry(
            frame,
            context.get("frame_15m"),
            quote=quote,
            side=side,
            pending_candidate=(
                None
                if allow_colorless_reverse
                else context.get("mtf_pending_candidate")
            ),
            allow_colorless_reverse=allow_colorless_reverse,
        )
        if not evaluation.get("allowed"):
            return False, str(evaluation.get("reason") or "BLOCKED_MTF_KC_ENTRY")
        closed = closed_entry_candles(frame)
        if closed.empty:
            return False, "BLOCKED_MTF_MACRO_STRUCTURE_NO_CLOSED_BARS"
        try:
            current = closed.iloc[-1]
            macro_bar = current.to_dict()
            macro_indicators = {
                "kc_upper": float(current["kc_upper"]),
                "kc_lower": float(current["kc_lower"]),
                "kc_middle": float(current["kc_middle"]),
                "ma15": float(current["ma15"]),
                "atr": float(current["atr"]),
            }
        except (AttributeError, KeyError, TypeError, ValueError, OverflowError):
            return False, "BLOCKED_MTF_MACRO_STRUCTURE_DATA_INVALID"

        from core.gates.macro_candle_structure_gate import (
            MTF_KC_DIRECTIONAL_ENTRY,
            MacroCandleStructureGate,
        )

        macro_allowed, macro_reason = MacroCandleStructureGate(
            max_bias_atr=1.2,
        ).can_open(
            side,
            macro_bar,
            macro_indicators,
            symbol=symbol,
            entry_pattern="CONTINUATION",
            structure_confirmation=MTF_KC_DIRECTIONAL_ENTRY,
            structure_confirmed=True,
        )
        if not macro_allowed:
            return False, "BLOCKED_MTF_MACRO_STRUCTURE:" + macro_reason
        return True, "MTF_KC_STATE_MACHINE_ENTRY_CONFIRMED"

    # 🚨 多空方向一致性保護：
    # 當市場方向明確為 KC_UP（中軌向上 / 介面標頭「只評估多單」）：
    # - 不允許一般趨勢/突破/交叉引擎開空。
    # - 只有獨立的極值峰頂反轉引擎，在具備完整反轉證據時，才可評估逆勢空單。
    # 當市場方向明確為 KC_DOWN（中軌向下 / 介面標頭「只評估空單」）：
    # - 不允許一般趨勢/突破/交叉引擎開多。
    # - 只有獨立的極值谷底反轉引擎，在具備完整反轉證據時，才可評估逆勢多單。
    closed_for_dir = closed_entry_candles(frame) if isinstance(frame, pd.DataFrame) else None
    if closed_for_dir is not None and len(closed_for_dir) >= 2:
        c_curr_mid = float(closed_for_dir.iloc[-1].get("kc_middle", 0.0))
        c_prev_mid = float(closed_for_dir.iloc[-2].get("kc_middle", c_curr_mid))
        is_reversal_method = method in (KC_OUTER_PIVOT_REVERSAL, THREE_BAR_PIVOT_REVERSAL)
        if c_curr_mid > c_prev_mid and str(side).upper() == "SHORT" and not is_reversal_method:
            return False, "BLOCKED_SHORT_ON_KC_UPWARD_TREND"
        if c_curr_mid < c_prev_mid and str(side).upper() == "LONG" and not is_reversal_method:
            return False, "BLOCKED_LONG_ON_KC_DOWNWARD_TREND"

    last_exit_timestamp = context.get("entry_method_last_exit_timestamp")
    same_bar_flip = (
        method == KC_DUAL_TREND_LOCATION
        and context.get("entry_method_allow_same_bar_after_close") is True
        and context.get("is_flip") is True
        and context.get("trigger") == "TRIGGER_DUAL_TREND_REVERSAL"
    )
    expected = context.get("entry_method_signal")
    confirmed_flat_structure = False
    if (
        method in (KC_TWO_BAR_BREAKOUT, KC_OUTER_PIVOT_REVERSAL)
        and isinstance(expected, Mapping)
    ):
        verified_signal = evaluate_entry_method(
            frame,
            quote=quote,
            symbol=symbol,
            last_exit_timestamp=last_exit_timestamp,
            expected_method=method,
            allow_same_bar_after_close=same_bar_flip,
        )
        confirmed_flat_structure = bool(
            verified_signal is not None
            and verified_signal["side"] == str(side).upper()
            and verified_signal["event_id"] == expected.get("event_id")
            and verified_signal["confirmation_bar_id"] == context.get("entry_method_bar_id")
            and verified_signal["event_id"] == context.get("entry_method_event_id")
        )
    if is_meme_breakout_symbol(symbol):
        from core.gates.master_gate_funnel import evaluate_meme_entry_frame
        gate = evaluate_meme_entry_frame(
            frame,
            symbol,
            side,
            quote,
            allow_kc_location_override=method == KC_DUAL_TREND_LOCATION,
            allow_bearish_pullback_override=(
                method == KC_BEARISH_PULLBACK_RESUMPTION
            ),
            allow_confirmed_breakout_override=(
                confirmed_flat_structure and method == KC_TWO_BAR_BREAKOUT
            ),
            allow_confirmed_pivot_override=(
                confirmed_flat_structure and method == KC_OUTER_PIVOT_REVERSAL
            ),
        )
        if not gate.can_execute:
            return False, gate.reason
    if method in (KC_EARLY_MIDLINE_BREAK, KC_TWO_BAR_BREAKOUT) and is_meme_breakout_symbol(symbol):
        closed_bias = closed_entry_candles(frame) if isinstance(frame, pd.DataFrame) else None
        if closed_bias is not None and len(closed_bias) >= 2:
            bar = closed_bias.iloc[-1]
            try:
                atr = float(closed_bias.iloc[-2]["atr"])
                close, live, ma15 = float(bar["close"]), float(quote), float(bar["ma15"])
                bias = max(close, live) - ma15 if side == "LONG" else ma15 - min(close, live)
                if math.isfinite(bias) and math.isfinite(atr) and atr > 0 and bias > atr:
                    return False, "BLOCKED_ENTRY_METHOD_MA15_EXTENSION_1_ATR"
            except (KeyError, TypeError, ValueError, OverflowError):
                return False, "BLOCKED_ENTRY_METHOD_MACRO_DATA_INVALID"

    signal = evaluate_entry_method(
        frame,
        quote=quote,
        symbol=symbol,
        last_exit_timestamp=last_exit_timestamp,
        expected_method=method,
        allow_same_bar_after_close=same_bar_flip,
    )
    expected = context.get("entry_method_signal")
    if (
        signal is None
        or signal["side"] != str(side).upper()
        or not isinstance(expected, Mapping)
        or signal["event_id"] != expected.get("event_id")
        or signal["confirmation_bar_id"] != context.get("entry_method_bar_id")
        or signal["event_id"] != context.get("entry_method_event_id")
        or (
            method == MEME_GATE_ENTRY
            and signal.get("trigger") != expected.get("trigger")
        )
    ):
        return False, "BLOCKED_ENTRY_METHOD_SIGNAL_STALE_OR_INVALID"

    if method == KC_DUAL_TREND_LOCATION:
        from core.gates.master_gate_funnel import (
            MasterGateFunnel,
            market_snapshot_from_frame,
        )

        try:
            snapshot = market_snapshot_from_frame(
                frame,
                symbol,
                quote,
                context.get("macro_trend") or frame.attrs.get("macro_trend_htf"),
            )
        except (AttributeError, IndexError, KeyError, TypeError, ValueError, OverflowError):
            return False, "BLOCKED_ENTRY_METHOD_MASTER_GATE_DATA_INVALID"
        gate = MasterGateFunnel.evaluate_entry_filters(
            snapshot,
            side,
            allow_kc_location_override=True,
        )
        if not gate.can_execute:
            return False, "BLOCKED_ENTRY_METHOD_MASTER_GATE:" + gate.reason

    if final_submission and method != KC_TWO_BAR_BREAKOUT:
        live_candle_veto = _live_candle_veto(frame, symbol, side, quote)
        if live_candle_veto:
            return False, live_candle_veto

    if method == MEME_GATE_ENTRY:
        closed_meme = closed_entry_candles(frame)
        if len(closed_meme) >= 2:
            cur_bar = closed_meme.iloc[-1]
            prev_bar = closed_meme.iloc[-2]
            bar_atr = float(prev_bar.get("atr") or cur_bar.get("atr") or 0.0)
            bar_body = abs(float(cur_bar["close"]) - float(cur_bar["open"]))
            band_key = "kc_upper" if str(side).upper() == "LONG" else "kc_lower"
            band_val = float(cur_bar.get(band_key) or 0.0)
            sign = 1 if str(side).upper() == "LONG" else -1
            if bar_atr > 0 and bar_body > 1.5 * bar_atr and band_val > 0 and sign * (float(cur_bar["close"]) - band_val) > 0:
                return False, "BLOCKED_ENTRY_METHOD_EXHAUSTED_SPIKE_CHASE"
        return True, "ENTRY_METHOD_REVALIDATED:" + method

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
    if method == KC_SINGLE_BAR_BREAKOUT and (
        (side == "LONG" and kc_position > 0.35)
        or (side == "SHORT" and kc_position < 0.65)
    ):
        return False, "BLOCKED_SINGLE_BAR_BREAKOUT_MACRO_LOCATION"
    bias_atr = abs(close - indicators["ma15"]) / indicators["atr"]
    body = abs(close - candle["open"])
    upper_wick = candle["high"] - max(candle["open"], close)
    lower_wick = min(candle["open"], close) - candle["low"]
    if method in (
        KC_TWO_BAR_BREAKOUT, KC_SINGLE_BAR_BREAKOUT,
        KC_EARLY_MIDLINE_BREAK, MEME_GATE_ENTRY,
    ):
        ma5 = _number(current, "ma5")
        if ma5 is None or ma5 <= 0:
            return False, "BLOCKED_ENTRY_METHOD_BREAKOUT_MA5_INVALID"
    elif method not in (POST_CLOSE_KC_REENTRY, KC_DUAL_TREND_LOCATION) and not (
        method == KC_OUTER_PIVOT_REVERSAL and is_meme_breakout_symbol(symbol)
    ):
        long_position_max, short_position_min = 0.35, 0.65
        if (
            is_meme_breakout_symbol(symbol)
            and method in {THREE_BAR_PIVOT_REVERSAL, MA5_MA15_CROSS}
        ):
            from core.gates.master_gate_funnel import pivot_kc_position_bounds
            long_position_max, short_position_min = pivot_kc_position_bounds(symbol)
        if (
            (side == "LONG" and kc_position > long_position_max)
            or (side == "SHORT" and kc_position < short_position_min)
        ):
            return False, "BLOCKED_ENTRY_METHOD_MACRO_KC_RELATIVE_LOCATION"
    relaxed = uses_relaxed_entry_limits(symbol)
    if not relaxed and bias_atr > 1.2:
        return False, "BLOCKED_ENTRY_METHOD_MACRO_MA15_BIAS"
    relaxed_outer = relaxed and method in {
        KC_TWO_BAR_BREAKOUT, KC_SINGLE_BAR_BREAKOUT, POST_CLOSE_KC_REENTRY,
    }
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

    if method in (KC_EARLY_MIDLINE_BREAK, MEME_GATE_ENTRY):
        return True, "ENTRY_METHOD_REVALIDATED:" + method

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
