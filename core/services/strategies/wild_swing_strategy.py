"""Closed-candle entry and exit rules for high-volatility directional swings."""

from dataclasses import dataclass, field
import math
from typing import Any, Dict, Optional

import pandas as pd

from core.gates.entry_pattern_permitted_gate import PERMITTED_ENTRY_PATTERNS
from core.services.candle_data import closed_entry_candles


ENTRY_MODE_BREAKOUT = "BREAKOUT"
ENTRY_MODE_CONTINUATION = "CONTINUATION"
ENTRY_MODE_CROSSOVER = "CROSSOVER"
ENTRY_MODE_REVERSAL = "REVERSAL"
WILD_SWING_ENTRY_MODES = PERMITTED_ENTRY_PATTERNS


@dataclass
class WildSwingSymbolState:
    bars_since_last_exit: int = 999
    last_exit_side: Optional[str] = None
    last_exit_price: Optional[float] = None
    last_exit_reason: Optional[str] = None
    last_exit_bar_id: Optional[float] = None
    last_processed_bar_id: Optional[float] = None
    reset_bar_id: Optional[float] = None
    pending_short_wick_bar_id: Optional[float] = None
    pending_short_wick_low: Optional[float] = None
    veto_flags: set[str] = field(default_factory=set)


class WildSwingStrategy:
    """Evaluate one-minute swing signals without consuming intrabar candles."""

    ENTRY_MODES = WILD_SWING_ENTRY_MODES

    def __init__(self, volume_lookback: int = 10, climax_volume_multiple: float = 2.5):
        if volume_lookback < 1 or climax_volume_multiple <= 0:
            raise ValueError("Invalid wild-swing volume settings")
        self.volume_lookback = volume_lookback
        self.climax_volume_multiple = climax_volume_multiple
        self.states: Dict[str, WildSwingSymbolState] = {}

    def state_for(self, symbol: str) -> WildSwingSymbolState:
        return self.states.setdefault(symbol, WildSwingSymbolState())

    def reset_execution_state(self, latest_closed_bar_ids: Optional[Dict[str, float]] = None) -> None:
        """Clear strategy-only transient blockers while preserving candle history and positions."""
        latest_closed_bar_ids = latest_closed_bar_ids or {}
        symbols = set(self.states) | set(latest_closed_bar_ids)
        self.states = {
            symbol: WildSwingSymbolState(reset_bar_id=latest_closed_bar_ids.get(symbol))
            for symbol in symbols
        }

    def reset_symbol(
        self, symbol: str, latest_closed_bar_id: Optional[float] = None,
    ) -> None:
        self.states[symbol] = WildSwingSymbolState(reset_bar_id=latest_closed_bar_id)

    @staticmethod
    def _number(row: pd.Series, *names: str) -> Optional[float]:
        for name in names:
            if name in row:
                try:
                    value = float(row[name])
                except (TypeError, ValueError, OverflowError):
                    continue
                if math.isfinite(value):
                    return value
        return None

    @staticmethod
    def _bar_id(row: pd.Series) -> Optional[float]:
        value = WildSwingStrategy._number(row, "timestamp", "time")
        return value

    def _closed_frame(self, frame: pd.DataFrame) -> pd.DataFrame:
        if frame is None or frame.empty:
            return pd.DataFrame()
        closed = closed_entry_candles(frame)
        required = {
            "open", "high", "low", "close", "volume",
            "kc_upper", "kc_middle", "kc_lower", "ma5", "ma15",
        }
        if not required.issubset(closed.columns):
            return pd.DataFrame()
        closed = closed.copy()
        closed["_wild_ma5"] = pd.to_numeric(closed["ma5"], errors="coerce")
        for column in (
            "open", "high", "low", "close", "volume",
            "kc_upper", "kc_middle", "kc_lower", "ma15",
        ):
            closed[column] = pd.to_numeric(closed[column], errors="coerce")
        closed = closed.dropna(
            subset=[
                "open", "high", "low", "close", "volume", "kc_upper",
                "kc_middle", "kc_lower", "ma15", "_wild_ma5",
            ],
        )
        if closed.empty:
            return closed
        valid = (
            (closed["open"] > 0)
            & (closed["high"] > 0)
            & (closed["low"] > 0)
            & (closed["close"] > 0)
            & (closed["volume"] >= 0)
            & (closed["kc_upper"] > closed["kc_lower"])
            & (closed["kc_middle"] > 0)
            & (closed["ma15"] > 0)
            & (closed["_wild_ma5"] > 0)
            & (closed["high"] >= closed[["open", "close", "low"]].max(axis=1))
            & (closed["low"] <= closed[["open", "close", "high"]].min(axis=1))
        )
        return closed.loc[valid]

    def evaluate_entry(self, symbol: str, frame: pd.DataFrame, side: str) -> Optional[dict[str, Any]]:
        side = str(side).upper()
        if side not in ("LONG", "SHORT"):
            raise ValueError("side must be LONG or SHORT")
        bars = self._closed_frame(frame)
        if len(bars) < self.volume_lookback + 2:
            return None

        current, previous = bars.iloc[-1], bars.iloc[-2]
        bar_id = self._bar_id(current)
        state = self.state_for(symbol)
        if bar_id is None:
            return None
        if state.reset_bar_id is not None and bar_id <= state.reset_bar_id:
            return None
        if state.last_exit_bar_id is not None and bar_id <= state.last_exit_bar_id:
            return None
        if state.last_processed_bar_id is not None and bar_id <= state.last_processed_bar_id:
            return None

        close = float(current["close"])
        open_price = float(current["open"])
        middle = float(current["kc_middle"])
        ma15 = float(current["ma15"])
        ma5 = float(current["_wild_ma5"])
        prev_close = float(previous["close"])
        prev_middle = float(previous["kc_middle"])
        prev_ma15 = float(previous["ma15"])
        prev_ma5 = float(previous["_wild_ma5"])

        channel_width = float(current["kc_upper"] - current["kc_lower"])
        previous_channel_width = float(previous["kc_upper"] - previous["kc_lower"])
        channel_expanding = channel_width > previous_channel_width

        if side == "SHORT":
            trend = middle < prev_middle and ma15 < prev_ma15
            below_baseline = close < middle and close < ma15
            directional_bar = close < open_price
            crossed_baseline = (
                prev_close >= max(prev_middle, prev_ma15)
                and close < min(middle, ma15)
                and prev_ma5 >= prev_ma15
                and ma5 < ma15
            )
            broke_previous_low = close < float(previous["low"])
            pullback_reversal = self._pullback_reversal(bars, "SHORT", middle, ma15)
            breakout = (
                close < float(current["kc_lower"])
                and prev_close >= float(previous["kc_lower"])
                and directional_bar
                and middle < prev_middle
                and ma15 < prev_ma15
                and float(current["kc_lower"]) < float(previous["kc_lower"])
                and channel_expanding
            )
            reversal = self._confirmed_pivot_reversal(bars, "SHORT")
        else:
            trend = middle > prev_middle and ma15 > prev_ma15
            above_baseline = close > middle and close > ma15
            directional_bar = close > open_price
            crossed_baseline = (
                prev_close <= min(prev_middle, prev_ma15)
                and close > max(middle, ma15)
                and prev_ma5 <= prev_ma15
                and ma5 > ma15
            )
            broke_previous_high = close > float(previous["high"])
            pullback_reversal = self._pullback_reversal(bars, "LONG", middle, ma15)

            breakout = (
                close > float(current["kc_upper"])
                and prev_close <= float(previous["kc_upper"])
                and directional_bar
                and middle > prev_middle
                and ma15 > prev_ma15
                and float(current["kc_upper"]) > float(previous["kc_upper"])
                and channel_expanding
            )
            reversal = self._confirmed_pivot_reversal(bars, "LONG")

        if reversal:
            signal_mode = ENTRY_MODE_REVERSAL
        elif breakout:
            signal_mode = ENTRY_MODE_BREAKOUT
        else:
            location = below_baseline if side == "SHORT" else above_baseline
            if not (trend and location and directional_bar):
                return None

            if side == "SHORT":
                continuation = broke_previous_low or pullback_reversal
            else:
                continuation = broke_previous_high or pullback_reversal
            if crossed_baseline:
                signal_mode = ENTRY_MODE_CROSSOVER
            elif continuation:
                signal_mode = ENTRY_MODE_CONTINUATION
            else:
                return None

        if signal_mode in (ENTRY_MODE_CROSSOVER, ENTRY_MODE_CONTINUATION):
            location = below_baseline if side == "SHORT" else above_baseline
            if not (trend and location and directional_bar):
                return None

        if signal_mode == ENTRY_MODE_CROSSOVER and not crossed_baseline:
            return None
        if signal_mode == ENTRY_MODE_CONTINUATION and not (
            (broke_previous_low or pullback_reversal)
            if side == "SHORT"
            else (broke_previous_high or pullback_reversal)
        ):
            return None

        if side == "SHORT":
            pending_bar_id = state.pending_short_wick_bar_id
            pending_low = state.pending_short_wick_low
            previous_bar_id = self._bar_id(previous)
            if pending_bar_id is not None:
                confirmed_next_bar = (
                    previous_bar_id == pending_bar_id
                    and pending_low is not None
                    and close < open_price
                    and close < pending_low
                )
                state.pending_short_wick_bar_id = None
                state.pending_short_wick_low = None
                if previous_bar_id == pending_bar_id and not confirmed_next_bar:
                    if self._has_short_exhaustion_wick(current):
                        state.pending_short_wick_bar_id = bar_id
                        state.pending_short_wick_low = float(current["low"])
                    state.last_processed_bar_id = bar_id
                    return None

            if self._has_short_exhaustion_wick(current):
                state.pending_short_wick_bar_id = bar_id
                state.pending_short_wick_low = float(current["low"])
                state.last_processed_bar_id = bar_id
                return None

        return {
            "side": side,
            "reason": {
                ENTRY_MODE_BREAKOUT: "KC_OUTER_BAND_BREAKOUT",
                ENTRY_MODE_CONTINUATION: "TREND_CONTINUATION",
                ENTRY_MODE_CROSSOVER: "BASELINE_BREAK_AND_MA_CROSS",
                ENTRY_MODE_REVERSAL: "CONFIRMED_PEAK_VALLEY_REVERSAL",
            }[signal_mode],
            "entry_pattern": signal_mode,
            "entry_signal_mode": signal_mode,
            "structure_confirmation": {
                ENTRY_MODE_BREAKOUT: "KC_OUTER_BREAKOUT",
                ENTRY_MODE_CONTINUATION: "TREND_CONTINUATION",
                ENTRY_MODE_CROSSOVER: "MA5_MA15_1M_CROSS",
                ENTRY_MODE_REVERSAL: "CONFIRMED_KC_OUTER_PEAK_VALLEY",
            }[signal_mode],
            "structure_confirmed": True,
            "channel_expanding": channel_expanding,
            "bar_id": bar_id,
            "close": close,
            "entry_mode": "WILD_SWING",
        }

    @staticmethod
    def _confirmed_pivot_reversal(bars: pd.DataFrame, side: str) -> bool:
        if len(bars) < 3:
            return False
        left, pivot, trigger = bars.iloc[-3], bars.iloc[-2], bars.iloc[-1]
        pivot_ma5 = float(pivot["_wild_ma5"])
        pivot_ma15 = float(pivot["ma15"])
        trigger_ma5 = float(trigger["_wild_ma5"])
        trigger_ma15 = float(trigger["ma15"])
        open_price = float(trigger["open"])
        close = float(trigger["close"])

        if side == "LONG":
            return bool(
                pivot["low"] < left["low"]
                and pivot["low"] < trigger["low"]
                and pivot["low"] <= pivot["kc_lower"]
                and close > open_price
                and close > float(pivot["high"])
                and pivot_ma5 <= pivot_ma15
                and trigger_ma5 > trigger_ma15
            )
        return bool(
            pivot["high"] > left["high"]
            and pivot["high"] > trigger["high"]
            and pivot["high"] >= pivot["kc_upper"]
            and close < open_price
            and close < float(pivot["low"])
            and pivot_ma5 >= pivot_ma15
            and trigger_ma5 < trigger_ma15
        )

    @staticmethod
    def _has_short_exhaustion_wick(bar: pd.Series) -> bool:
        open_price = float(bar["open"])
        close = float(bar["close"])
        high = float(bar["high"])
        low = float(bar["low"])
        candle_range = high - low
        if candle_range <= 0:
            return True
        body = abs(close - open_price)
        lower_wick = min(open_price, close) - low
        return (
            lower_wick / candle_range > 0.40
            or lower_wick > body * 1.2
            or body / candle_range < 0.25
        )

    @staticmethod
    def _pullback_reversal(
        bars: pd.DataFrame, side: str, current_middle: float, current_ma15: float,
    ) -> bool:
        if len(bars) < 2:
            return False
        trigger = bars.iloc[-1]
        if side == "SHORT":
            if not (
                trigger["close"] < trigger["open"]
                and trigger["close"] < current_middle
                and trigger["close"] < current_ma15
            ):
                return False
            for count in (1, 2):
                if len(bars) <= count:
                    continue
                prior = bars.iloc[-count - 1:-1]
                weak_pullback = (
                    (prior["close"] > prior["open"])
                    & (prior["close"] <= prior["kc_middle"])
                    & (prior["close"] <= prior["ma15"])
                )
                if weak_pullback.all() and trigger["close"] < float(prior["low"].min()):
                    return True
            return False

        if not (
            trigger["close"] > trigger["open"]
            and trigger["close"] > current_middle
            and trigger["close"] > current_ma15
        ):
            return False
        for count in (1, 2):
            if len(bars) <= count:
                continue
            prior = bars.iloc[-count - 1:-1]
            weak_pullback = (
                (prior["close"] < prior["open"])
                & (prior["close"] >= prior["kc_middle"])
                & (prior["close"] >= prior["ma15"])
            )
            if weak_pullback.all() and trigger["close"] > float(prior["high"].max()):
                return True
        return False

    def evaluate_exit(
        self,
        symbol: str,
        position: dict,
        frame: pd.DataFrame,
        *,
        profit_lock_triggered: bool = False,
    ) -> Optional[dict[str, Any]]:
        side = str((position or {}).get("side", "")).upper()
        if side not in ("LONG", "SHORT"):
            return None
        bars = self._closed_frame(frame)
        if len(bars) < self.volume_lookback + 1:
            return None
        current = bars.iloc[-1]
        bar_id = self._bar_id(current)
        if bar_id is None:
            return None
        state = self.state_for(symbol)
        if state.reset_bar_id is not None and bar_id <= state.reset_bar_id:
            return None
        if state.last_processed_bar_id is not None and bar_id <= state.last_processed_bar_id:
            return None

        close = float(current["close"])
        middle = float(current["kc_middle"])
        upper = self._number(current, "kc_upper")
        lower = self._number(current, "kc_lower")
        open_price = float(current["open"])
        body = abs(close - open_price)
        volume_mean = float(bars.iloc[-self.volume_lookback - 1:-1]["volume"].mean())
        volume = float(current["volume"])
        upper_wick = float(current["high"]) - max(open_price, close)
        lower_wick = min(open_price, close) - float(current["low"])

        if profit_lock_triggered:
            reason = "EXIT_BY_PROFIT_LOCK"
        elif side == "SHORT" and close > middle:
            reason = "EXIT_SHORT_ON_CONFIRMED_KC_MIDDLE_BREAK"
        elif side == "LONG" and close < middle:
            reason = "EXIT_LONG_ON_CONFIRMED_KC_MIDDLE_BREAK"
        elif (
            side == "SHORT" and lower is not None and close < lower
            and volume_mean > 0 and volume >= self.climax_volume_multiple * volume_mean
            and lower_wick > 2.0 * body
        ):
            reason = "EXIT_SHORT_ON_EXTREME_VOLUME_PIN"
        elif (
            side == "LONG" and upper is not None and close > upper
            and volume_mean > 0 and volume >= self.climax_volume_multiple * volume_mean
            and upper_wick > 2.0 * body
        ):
            reason = "EXIT_LONG_ON_EXTREME_VOLUME_PIN"
        else:
            state.last_processed_bar_id = bar_id
            return None

        return {
            "side": side,
            "reason": reason,
            "bar_id": bar_id,
            "close": close,
            "entry_mode": "WILD_SWING",
        }

    def record_exit(
        self, symbol: str, side: str, price: float, reason: str, bar_id: float,
    ) -> None:
        state = self.state_for(symbol)
        state.last_exit_side = str(side).upper()
        state.last_exit_price = float(price)
        state.last_exit_reason = str(reason)
        state.last_exit_bar_id = float(bar_id)
        state.last_processed_bar_id = float(bar_id)
        state.bars_since_last_exit = 0

    def record_entry_fill(self, symbol: str, bar_id: float) -> None:
        state = self.state_for(symbol)
        state.last_processed_bar_id = float(bar_id)
