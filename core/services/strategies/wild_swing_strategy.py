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

    @staticmethod
    def latest_profit_lock_exit(
        trades: Any, symbol: str,
    ) -> Optional[dict[str, Any]]:
        """Return the latest WILD_SWING close only when its reason was profit lock."""
        if not isinstance(trades, list):
            return None
        for trade in trades:
            if not isinstance(trade, dict) or trade.get("symbol") != symbol:
                continue
            action = str(trade.get("action") or "").upper()
            if not (action.startswith("OPEN_") or action.startswith("CLOSE_")):
                continue
            if not action.startswith("CLOSE_"):
                return None
            reason = str(trade.get("reason") or "").upper()
            if (
                str(trade.get("entry_mode") or "").upper() != "WILD_SWING"
                or action not in {"CLOSE_LONG", "CLOSE_SHORT"}
                or "EXIT_BY_PROFIT_LOCK" not in reason
            ):
                return None
            try:
                close_id = float(trade.get("id") or trade.get("timestamp"))
            except (TypeError, ValueError, OverflowError):
                return None
            if not math.isfinite(close_id) or close_id <= 0:
                return None
            close_ms = close_id * 1000.0 if close_id < 1e11 else close_id
            return {
                "side": action.removeprefix("CLOSE_"),
                "close_id": close_id,
                "close_bar_id": math.floor(close_ms / 60_000) * 60_000,
            }
        return None

    def evaluate_entry(
        self,
        symbol: str,
        frame: pd.DataFrame,
        side: str,
        *,
        profit_lock_exit: Optional[dict[str, Any]] = None,
    ) -> Optional[dict[str, Any]]:
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
        ma5 = float(current["_wild_ma5"])
        ma15 = float(current["ma15"])
        prev_middle = float(previous["kc_middle"])
        prev_ma5 = float(previous["_wild_ma5"])
        prev_ma15 = float(previous["ma15"])
        cross_up = prev_ma5 <= prev_ma15 and ma5 > ma15
        cross_down = prev_ma5 >= prev_ma15 and ma5 < ma15
        channel_expanding = (
            float(current["kc_upper"] - current["kc_lower"])
            > float(previous["kc_upper"] - previous["kc_lower"])
        )

        if (
            profit_lock_exit
            and str(profit_lock_exit.get("side") or "").upper() == side
            and bar_id > float(profit_lock_exit.get("close_bar_id") or 0.0)
        ):
            return self._profit_lock_continuation_signal(
                bars, current, previous, side, bar_id, profit_lock_exit,
            )

        min_pivot_bar_id = max(
            float(state.reset_bar_id or 0.0),
            float(state.last_exit_bar_id or 0.0),
        )
        if side == "LONG":
            pivot = self._latest_kc_outer_pivot(
                bars.iloc[:-1], "LONG", min_pivot_bar_id,
            )
            valid_cross = (
                cross_up
                and ma5 > prev_ma5
                and close > open_price
                and close > ma5
            )
        else:
            pivot = self._latest_kc_outer_pivot(
                bars.iloc[:-1], "SHORT", min_pivot_bar_id,
            )
            valid_cross = (
                cross_down
                and ma5 < prev_ma5
                and close < open_price
                and close < ma5
            )
        if pivot is None or not valid_cross:
            return None

        return {
            "side": side,
            "reason": "REVERSAL_CROSSOVER_ENTRY",
            "entry_label": "REVERSAL_CROSSOVER_ENTRY",
            "entry_pattern": ENTRY_MODE_REVERSAL,
            "entry_signal_mode": ENTRY_MODE_REVERSAL,
            "structure_confirmation": "CONFIRMED_KC_OUTER_PIVOT_THEN_MA5_MA15_CROSS",
            "structure_confirmed": True,
            "channel_expanding": channel_expanding,
            "ma5_current": ma5,
            "ma5_previous": prev_ma5,
            "kc_middle_current": float(current["kc_middle"]),
            "kc_middle_previous": prev_middle,
            "volume_exhausted": False,
            "pivot_bar_id": self._bar_id(bars.iloc[pivot["index"]]),
            "pivot_price": pivot["price"],
            "bar_id": bar_id,
            "close": close,
            "entry_mode": "WILD_SWING",
        }

    def _profit_lock_continuation_signal(
        self,
        bars: pd.DataFrame,
        current: pd.Series,
        previous: pd.Series,
        side: str,
        bar_id: float,
        profit_lock_exit: dict[str, Any],
    ) -> Optional[dict[str, Any]]:
        close = float(current["close"])
        ma5 = float(current["_wild_ma5"])
        prev_ma5 = float(previous["_wild_ma5"])
        ma15 = float(current["ma15"])
        middle = float(current["kc_middle"])
        prev_middle = float(previous["kc_middle"])
        if side == "LONG":
            aligned = (
                close > float(current["kc_upper"])
                and close > ma5
                and ma5 > prev_ma5
                and middle >= prev_middle
            )
        else:
            aligned = (
                close < float(current["kc_lower"])
                and close < ma5
                and ma5 < prev_ma5
                and middle <= prev_middle
            )
        if not aligned:
            return None

        volume_exhausted = self._volume_exhausted(bars, side)
        if volume_exhausted:
            return None
        return {
            "side": side,
            "reason": "PROFIT_LOCK_CONTINUATION_ENTRY",
            "entry_label": "PROFIT_LOCK_CONTINUATION_ENTRY",
            "entry_pattern": ENTRY_MODE_CONTINUATION,
            "entry_signal_mode": ENTRY_MODE_CONTINUATION,
            "structure_confirmation": "PROFIT_LOCK_CONTINUATION",
            "structure_confirmed": True,
            "channel_expanding": (
                float(current["kc_upper"] - current["kc_lower"])
                > float(previous["kc_upper"] - previous["kc_lower"])
            ),
            "ma5_current": ma5,
            "ma5_previous": prev_ma5,
            "kc_middle_current": middle,
            "kc_middle_previous": prev_middle,
            "volume_exhausted": False,
            "profit_lock_close_id": float(profit_lock_exit["close_id"]),
            "profit_lock_close_bar_id": float(profit_lock_exit["close_bar_id"]),
            "bar_id": bar_id,
            "close": close,
            "entry_mode": "WILD_SWING",
        }

    @staticmethod
    def _volume_exhausted(bars: pd.DataFrame, side: str) -> bool:
        if side not in ("LONG", "SHORT") or len(bars) < 6:
            return True
        previous_volumes = pd.to_numeric(
            bars["volume"].iloc[-6:-1], errors="coerce",
        )
        current_volume = float(bars.iloc[-1]["volume"])
        baseline = float(previous_volumes.mean())
        if (
            not previous_volumes.map(math.isfinite).all()
            or not math.isfinite(current_volume)
            or not math.isfinite(baseline)
            or baseline <= 0
        ):
            return True
        low_volume = current_volume < 0.60 * baseline
        recent = bars.iloc[-3:]
        stalled = True
        for index in range(1, len(recent)):
            previous = recent.iloc[index - 1]
            current = recent.iloc[index]
            if side == "LONG":
                stalled = stalled and (
                    float(current["high"]) <= float(previous["high"])
                    and float(current["close"]) <= float(previous["close"])
                )
            else:
                stalled = stalled and (
                    float(current["low"]) >= float(previous["low"])
                    and float(current["close"]) >= float(previous["close"])
                )
        return low_volume and stalled

    def _latest_kc_outer_pivot(
        self, bars: pd.DataFrame, side: str, min_bar_id: float,
    ) -> Optional[dict[str, Any]]:
        if side not in ("LONG", "SHORT") or len(bars) < 3:
            return None
        first_index = max(1, len(bars) - self.volume_lookback - 1)
        for pivot_index in range(len(bars) - 2, first_index - 1, -1):
            left = bars.iloc[pivot_index - 1]
            pivot = bars.iloc[pivot_index]
            right = bars.iloc[pivot_index + 1]
            pivot_bar_id = self._bar_id(pivot)
            if pivot_bar_id is None or pivot_bar_id <= min_bar_id:
                continue
            if side == "LONG":
                confirmed = (
                    float(pivot["low"]) < float(left["low"])
                    and float(pivot["low"]) < float(right["low"])
                    and float(pivot["low"]) <= float(pivot["kc_lower"])
                )
                price = float(pivot["low"])
            else:
                confirmed = (
                    float(pivot["high"]) > float(left["high"])
                    and float(pivot["high"]) > float(right["high"])
                    and float(pivot["high"]) >= float(pivot["kc_upper"])
                )
                price = float(pivot["high"])
            if confirmed:
                return {"index": pivot_index, "price": price}
        return None

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
        if bars.empty:
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
        if profit_lock_triggered:
            reason = "EXIT_BY_PROFIT_LOCK"
        else:
            evidence = self._true_pivot_volume_divergence_exit(
                position, bars, side,
            )
            if evidence is None:
                state.last_processed_bar_id = bar_id
                return None
            reason = (
                "EXIT_BY_TRUE_PEAK_VOLUME_DIVERGENCE"
                if side == "LONG"
                else "EXIT_BY_TRUE_VALLEY_VOLUME_DIVERGENCE"
            )

        return {
            "side": side,
            "reason": reason,
            "bar_id": bar_id,
            "close": close,
            **(evidence if not profit_lock_triggered else {}),
            "entry_mode": "WILD_SWING",
        }

    @staticmethod
    def _position_entry_bar_floor(
        position: dict, frame: pd.DataFrame,
    ) -> Optional[float]:
        timeframe_ms = float(frame.attrs.get("timeframe_ms", 60_000) or 60_000)
        for key in ("open_bar_id", "entry_bar_id", "candidate_bar_id",
                    "open_timestamp", "entry_timestamp"):
            value = (position or {}).get(key)
            if value is None:
                continue
            try:
                timestamp = float(value)
            except (TypeError, ValueError, OverflowError):
                continue
            if not math.isfinite(timestamp) or timestamp <= 0:
                continue
            timestamp_ms = timestamp * 1000.0 if timestamp < 1e11 else timestamp
            return math.floor(timestamp_ms / timeframe_ms) * timeframe_ms
        return None

    @classmethod
    def _true_pivot_volume_divergence_exit(
        cls,
        position: dict,
        bars: pd.DataFrame,
        side: str,
    ) -> Optional[dict[str, Any]]:
        entry_bar_floor = cls._position_entry_bar_floor(position, bars)
        if entry_bar_floor is None or len(bars) < 5:
            return None

        pivots = []
        for index in range(1, len(bars) - 1):
            left, pivot, right = (
                bars.iloc[index - 1], bars.iloc[index], bars.iloc[index + 1],
            )
            bar_id = cls._bar_id(pivot)
            volume = cls._number(pivot, "volume")
            if bar_id is None or bar_id < entry_bar_floor or volume is None:
                continue
            if side == "LONG":
                confirmed = (
                    float(pivot["high"]) > float(left["high"])
                    and float(pivot["high"]) > float(right["high"])
                )
                price = float(pivot["high"])
            else:
                confirmed = (
                    float(pivot["low"]) < float(left["low"])
                    and float(pivot["low"]) < float(right["low"])
                )
                price = float(pivot["low"])
            if confirmed:
                pivots.append({
                    "index": index, "bar_id": bar_id,
                    "price": price, "volume": volume,
                })

        if len(pivots) < 2:
            return None
        current_index = len(bars) - 1
        current_close = float(bars.iloc[-1]["close"])
        for previous_pivot, pivot in reversed(list(zip(pivots, pivots[1:]))):
            directional_divergence = (
                pivot["price"] > previous_pivot["price"]
                if side == "LONG"
                else pivot["price"] < previous_pivot["price"]
            )
            if not directional_divergence:
                continue
            if (
                pivot["index"] >= current_index
                or pivot["index"] + 1 > current_index
                or pivot["volume"] >= previous_pivot["volume"]
            ):
                continue
            intervening = bars.iloc[
                previous_pivot["index"] + 1:pivot["index"]
            ]
            if intervening.empty:
                continue
            if side == "LONG":
                structure_level = float(intervening["low"].min())
                structure_broken = current_close < structure_level
            else:
                structure_level = float(intervening["high"].max())
                structure_broken = current_close > structure_level
            if structure_broken:
                return {
                    "pivot_bar_id": pivot["bar_id"],
                    "pivot_price": pivot["price"],
                    "previous_pivot_price": previous_pivot["price"],
                    "pivot_volume": pivot["volume"],
                    "previous_pivot_volume": previous_pivot["volume"],
                    "broken_structure_level": structure_level,
                }
        return None

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
