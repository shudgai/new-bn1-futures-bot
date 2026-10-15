"""Single closed-candle authority for the three automatic entry modes."""

import math
import time
from typing import Any, Optional

import numpy as np
import pandas as pd

from core.services.candle_data import closed_entry_candles
from core.gates.smart_entry_gate import (
    COOLDOWN_BARS,
    RANGE_BREAKOUT_VOLUME_RATIO,
    WHIPSAW_LOOKBACK,
)


MODE_A_LONG = "THREE_MODE_A_MA_SWING_LONG"
MODE_A_SHORT = "THREE_MODE_A_MA_SWING_SHORT"
MODE_B_LONG = "THREE_MODE_B_VOLUME_BREAKOUT_LONG"
MODE_B_SHORT = "THREE_MODE_B_VOLUME_BREAKOUT_SHORT"
MODE_C_SHORT = "THREE_MODE_C_DUMP_REVERSAL_SHORT"

THREE_MODE_ENTRY_CODES = frozenset(
    (MODE_A_LONG, MODE_A_SHORT, MODE_B_LONG, MODE_B_SHORT, MODE_C_SHORT)
)
MODE_A_LONG_POSITION = (0.40, 0.70)
MODE_A_SHORT_POSITION = (0.30, 0.60)
MODE_B_VOLUME_LOOKBACK = 15
MODE_C_MIN_BODY_ATR = 1.5
MODE_C_DUMP_BODY_ATR = 1.8


def has_confirmed_dump_close(account: Any, symbol: str, max_age_seconds: float = 60.0) -> bool:
    if account is None or not symbol:
        return False
    if (
        symbol in getattr(account, "positions", {})
        or symbol in getattr(account, "closing_lock", set())
        or symbol in getattr(account, "pending_limit_orders", {})
        or str(getattr(account, "last_closed_side", {}).get(symbol, "")).upper() != "LONG"
    ):
        return False
    try:
        closed_at = float(getattr(account, "last_closed_at", {}).get(symbol))
        closed_at_seconds = closed_at / 1000.0 if closed_at > 1e11 else closed_at
        age = time.time() - closed_at_seconds
        if not math.isfinite(age) or not 0 <= age <= max_age_seconds:
            return False
        close_events = []
        for trade in getattr(account, "trades", []):
            if trade.get("symbol") != symbol or trade.get("action") not in (
                "CLOSE_LONG", "CLOSE_SHORT",
            ):
                continue
            stamp = float(trade.get("timestamp") or trade.get("id") or 0)
            if stamp > 0 and math.isfinite(stamp):
                close_events.append((stamp, trade))
        if not close_events:
            return False
        _, latest = max(close_events, key=lambda event: event[0])
        trade_stamp = float(latest.get("timestamp") or latest.get("id") or 0)
        if trade_stamp > 1e11:
            trade_stamp /= 1000.0
        return (
            latest.get("action") == "CLOSE_LONG"
            and "EXIT_LONG_ON_DUMP_FLUSH" in str(latest.get("reason", ""))
            and abs(trade_stamp - closed_at_seconds) <= max_age_seconds
        )
    except (AttributeError, KeyError, TypeError, ValueError, OverflowError):
        return False


def _finite_positive(value: Any) -> Optional[float]:
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return parsed if math.isfinite(parsed) and parsed > 0 else None


def _cooldown_problem(frame: pd.DataFrame, account: Any, symbol: str) -> Optional[str]:
    if account is None or not symbol:
        return None
    last_closed_at = getattr(account, "last_closed_at", {}).get(symbol)
    if last_closed_at is None:
        return None
    try:
        closed_at_ms = float(last_closed_at)
        if closed_at_ms < 1e11:
            closed_at_ms *= 1000.0
        interval_ms = float(frame.attrs.get("timeframe_ms") or 60_000.0)
        timestamps = pd.to_numeric(frame["timestamp"], errors="coerce").to_numpy(dtype=float)
        if (
            not math.isfinite(closed_at_ms)
            or closed_at_ms <= 0
            or not math.isfinite(interval_ms)
            or interval_ms <= 0
            or not np.isfinite(timestamps).all()
        ):
            return "BLOCKED_THREE_MODE_COOLDOWN_INVALID_DATA"
        completed_after_close = int((timestamps + interval_ms > closed_at_ms).sum())
        if completed_after_close < COOLDOWN_BARS:
            return (
                "BLOCKED_THREE_MODE_COOLDOWN "
                f"({completed_after_close}/{COOLDOWN_BARS} closed bars)"
            )
    except (AttributeError, KeyError, TypeError, ValueError, OverflowError):
        return "BLOCKED_THREE_MODE_COOLDOWN_INVALID_DATA"
    return None


def _cross_count(bars: pd.DataFrame) -> int:
    recent = bars.tail(WHIPSAW_LOOKBACK)
    prior_side = 0
    crosses = 0
    for _, row in recent.iterrows():
        relative = float(row["close"]) - float(row["kc_middle"])
        side = 1 if relative > 0 else -1 if relative < 0 else 0
        if side and prior_side and side != prior_side:
            crosses += 1
        if side:
            prior_side = side
    return crosses


def _kc_position(price: float, row: pd.Series) -> Optional[float]:
    lower = _finite_positive(row.get("kc_lower"))
    upper = _finite_positive(row.get("kc_upper"))
    if lower is None or upper is None or upper <= lower:
        return None
    return (price - lower) / (upper - lower)


def _decision(
    code: str,
    side: str,
    frame: pd.DataFrame,
    bars: pd.DataFrame,
    quote: float,
    reason: str,
    symbol: str,
) -> dict[str, Any]:
    current = bars.iloc[-1]
    previous = bars.iloc[-2]
    bar_id = float(current["timestamp"])
    atr = float(previous["atr"])
    return {
        "action": "ENTER",
        "type": code,
        "side": side,
        "reason": reason,
        "price": quote,
        "close": float(current["close"]),
        "close_price": float(current["close"]),
        "bar_id": bar_id,
        "entry_atr": atr,
        "confirmation_bar_id": bar_id,
        "breakout_bar_id": bar_id,
        "pair_confirmation_bar_id": bar_id,
        "pending_signal_id": f"{symbol}:{code}:{bar_id}",
        "entry_phase": code.removeprefix("THREE_MODE_").rsplit("_", 1)[0],
        "strategy_type": "THREE_MODE_SWING",
        "entry_mode": "THREE_MODE",
        "_is_authorized": True,
    }


def _dump_reversal(
    frame: pd.DataFrame,
    bars: pd.DataFrame,
    quote: float,
    position: Any,
    symbol: str,
) -> Optional[dict[str, Any]]:
    if not isinstance(position, dict) or str(position.get("side", "")).upper() != "LONG":
        return None
    if len(bars) < 4:
        return None
    current = bars.iloc[-1]
    previous = bars.iloc[-2]
    opening = float(current["open"])
    close = float(current["close"])
    middle = float(current["kc_middle"])
    atr = float(previous["atr"])
    body = opening - close
    if opening <= close or body <= MODE_C_MIN_BODY_ATR * atr or close >= middle or quote >= middle:
        return None

    prior = bars.iloc[-4:-1]
    prior_bodies = prior["close"].astype(float) - prior["open"].astype(float)
    engulfed_three_bull_bodies = (
        bool((prior_bodies > 0).all())
        and body > float(prior_bodies.sum())
    )
    atr_dump = body > MODE_C_DUMP_BODY_ATR * atr and close < float(current["ma5"])
    if not (engulfed_three_bull_bodies or atr_dump):
        return None
    return _decision(
        MODE_C_SHORT, "SHORT", frame, bars, quote,
        "DUMP_ENGULFING_BREAKS_KC_MIDDLE",
        symbol,
    )


def evaluate_entry_signal(
    frame: pd.DataFrame,
    position: Any = None,
    *,
    account: Any = None,
    symbol: str = "",
    quote: Optional[float] = None,
    requested_side: Optional[str] = None,
    diagnostics: Optional[dict[str, Any]] = None,
) -> Optional[dict[str, Any]]:
    """Evaluate only MA swing, volume breakout, or dump-reversal entries."""

    def reject(reason: str) -> None:
        if diagnostics is not None:
            diagnostics.clear()
            diagnostics["reason"] = reason

    if frame is None or frame.empty:
        reject("BLOCKED_THREE_MODE_INVALID_DATA")
        return None
    bars = closed_entry_candles(frame)
    minimum = 4 if isinstance(position, dict) and position.get("side") == "LONG" else 2
    required = {
        "timestamp", "open", "high", "low", "close", "kc_upper",
        "kc_middle", "kc_lower", "ma5", "ma15", "atr",
    }
    if len(bars) < minimum or not required.issubset(bars.columns):
        reject("BLOCKED_THREE_MODE_INSUFFICIENT_DATA")
        return None
    if requested_side is not None and str(requested_side).upper() not in ("LONG", "SHORT"):
        reject("BLOCKED_THREE_MODE_INVALID_SIDE")
        return None

    validation_columns = sorted(required)
    if "volume" in bars.columns:
        validation_columns.append("volume")
    values = bars.tail(max(WHIPSAW_LOOKBACK, MODE_B_VOLUME_LOOKBACK + 1, minimum))[
        validation_columns
    ].apply(pd.to_numeric, errors="coerce")
    if values.isna().any().any() or not np.isfinite(values.to_numpy(dtype=float)).all():
        reject("BLOCKED_THREE_MODE_INVALID_DATA")
        return None
    if (
        (values[["timestamp", "open", "high", "low", "close", "kc_upper",
                 "kc_middle", "kc_lower", "ma5", "ma15", "atr"]] <= 0).any().any()
        or (values["kc_lower"] >= values["kc_upper"]).any()
        or (values["low"] > values[["open", "close"]].min(axis=1)).any()
        or (values["high"] < values[["open", "close"]].max(axis=1)).any()
        or (values["high"] < values["low"]).any()
        or ("volume" in values and (values["volume"] < 0).any())
    ):
        reject("BLOCKED_THREE_MODE_INVALID_DATA")
        return None

    current = bars.iloc[-1]
    try:
        execution_quote = float(
            quote if quote is not None else current["close"]
        )
    except (TypeError, ValueError, OverflowError):
        reject("BLOCKED_THREE_MODE_INVALID_QUOTE")
        return None
    if not math.isfinite(execution_quote) or execution_quote <= 0:
        reject("BLOCKED_THREE_MODE_INVALID_QUOTE")
        return None

    current_position = position
    if current_position is None and account is not None and symbol:
        current_position = getattr(account, "positions", {}).get(symbol)
    if current_position:
        candidate = _dump_reversal(
            frame, bars, execution_quote, current_position, symbol,
        )
        if candidate is not None and requested_side in (None, "SHORT"):
            if diagnostics is not None:
                diagnostics.clear()
                diagnostics.update(candidate)
            return candidate
        reject("WAIT_EXISTING_POSITION_OR_NO_DUMP_REVERSAL")
        return None

    if len(bars) < max(WHIPSAW_LOOKBACK, MODE_B_VOLUME_LOOKBACK + 1, 2):
        reject("BLOCKED_THREE_MODE_INSUFFICIENT_HISTORY")
        return None
    cooldown = _cooldown_problem(bars, account, symbol)
    if cooldown:
        reject(cooldown)
        return None
    crosses = _cross_count(bars)
    if crosses >= 2:
        reject(f"BLOCKED_THREE_MODE_CHOP_CROSSES ({crosses})")
        return None

    previous = bars.iloc[-2]
    current = bars.iloc[-1]
    lower = float(current["kc_lower"])
    upper = float(current["kc_upper"])
    middle = float(current["kc_middle"])
    previous_middle = float(previous["kc_middle"])
    atr = float(previous["atr"])
    close = float(current["close"])
    opening = float(current["open"])
    close_position = _kc_position(close, current)
    quote_position = _kc_position(execution_quote, current)
    if close_position is None or quote_position is None or atr <= 0:
        reject("BLOCKED_THREE_MODE_INVALID_INDICATORS")
        return None

    from core.gates.pipeline import MOMENTUM_BURST_BODY_ATR

    sides = (str(requested_side).upper(),) if requested_side else ("LONG", "SHORT")
    for side in sides:
        if side == "LONG":
            lower_bound, upper_bound = MODE_A_LONG_POSITION
            mode_a = (
                float(previous["ma5"]) <= float(previous["ma15"])
                and float(current["ma5"]) > float(current["ma15"])
                and middle > previous_middle
                and lower_bound <= close_position <= upper_bound
                and lower_bound <= quote_position <= upper_bound
            )
            if mode_a and close < upper and execution_quote < upper:
                candidate = _decision(
                    MODE_A_LONG, side, frame, bars, execution_quote,
                    "MA5_MA15_BULL_CROSS_WITH_RISING_KC_MIDDLE",
                    symbol,
                )
                if diagnostics is not None:
                    diagnostics.clear()
                    diagnostics.update(candidate)
                return candidate

            body = close - opening
            volume_ready = False
            if "volume" in bars.columns:
                average_volume = float(bars.iloc[-MODE_B_VOLUME_LOOKBACK - 1:-1]["volume"].mean())
                volume_ready = (
                    average_volume > 0
                    and float(current["volume"])
                    >= RANGE_BREAKOUT_VOLUME_RATIO * average_volume
                )
            if (
                close > upper
                and execution_quote > upper
                and body > 0
                and body >= MOMENTUM_BURST_BODY_ATR * atr
                and volume_ready
                and not (quote_position < 0.20)
            ):
                candidate = _decision(
                    MODE_B_LONG, side, frame, bars, execution_quote,
                    "HIGH_VOLUME_BULLISH_KC_BREAKOUT",
                    symbol,
                )
                if diagnostics is not None:
                    diagnostics.clear()
                    diagnostics.update(candidate)
                return candidate
        else:
            lower_bound, upper_bound = MODE_A_SHORT_POSITION
            mode_a = (
                float(previous["ma5"]) >= float(previous["ma15"])
                and float(current["ma5"]) < float(current["ma15"])
                and middle < previous_middle
                and lower_bound <= close_position <= upper_bound
                and lower_bound <= quote_position <= upper_bound
            )
            if mode_a and close > lower and execution_quote > lower:
                candidate = _decision(
                    MODE_A_SHORT, side, frame, bars, execution_quote,
                    "MA5_MA15_BEAR_CROSS_WITH_FALLING_KC_MIDDLE",
                    symbol,
                )
                if diagnostics is not None:
                    diagnostics.clear()
                    diagnostics.update(candidate)
                return candidate

            body = opening - close
            volume_ready = False
            if "volume" in bars.columns:
                average_volume = float(bars.iloc[-MODE_B_VOLUME_LOOKBACK - 1:-1]["volume"].mean())
                volume_ready = (
                    average_volume > 0
                    and float(current["volume"])
                    >= RANGE_BREAKOUT_VOLUME_RATIO * average_volume
                )
            if (
                close < lower
                and execution_quote < lower
                and body > 0
                and body >= MOMENTUM_BURST_BODY_ATR * atr
                and volume_ready
                and not (quote_position > 0.80)
            ):
                candidate = _decision(
                    MODE_B_SHORT, side, frame, bars, execution_quote,
                    "HIGH_VOLUME_BEARISH_KC_BREAKOUT",
                    symbol,
                )
                if diagnostics is not None:
                    diagnostics.clear()
                    diagnostics.update(candidate)
                return candidate

    reject("WAIT_NO_THREE_MODE_SETUP")
    return None
