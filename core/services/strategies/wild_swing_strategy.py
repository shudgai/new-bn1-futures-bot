"""Mathematical entry gates and trend-reversal exits for directional swings."""

from dataclasses import dataclass, field
import logging
import math
import time
from typing import Any, Dict, Optional

import pandas as pd

from core.gates.breakout_continuation_entry_gate import (
    CONFIRMED_2BAR_BREAKOUT,
    CONTINUATION_OUTSIDE_KC,
    INSTANT_BREAKOUT_INSIDE,
    INITIAL_2BAR_BREAKOUT,
    MA_CROSSOVER,
    FIVE_LINE_CONTINUATION,
    INSTANT_BURST_BREAKOUT,
    BreakoutContinuationEntryGate,
)
from core.gates.entry_pattern_permitted_gate import PERMITTED_ENTRY_PATTERNS
from core.gates.ma5_ma15_cross_entry_gate import validate_ma5_ma15_cross_entry
from core.services.candle_data import closed_entry_candles
from core.symbol_aliases import is_auto_entry_symbol


ENTRY_MODE_BREAKOUT = "BREAKOUT"
ENTRY_MODE_CONTINUATION = "CONTINUATION"
ENTRY_MODE_CROSSOVER = "CROSSOVER"
ENTRY_MODE_REVERSAL = "REVERSAL"
WILD_SWING_ENTRY_MODES = PERMITTED_ENTRY_PATTERNS
PULLBACK_EXIT_REASONS = frozenset({
    "EXIT_BY_MA_DEATH_CROSS",
    "EXIT_BY_MA_GOLDEN_CROSS",
    "EXIT_BY_MA5_TREND_REVERSAL",
    "EXIT_BY_MA15_BREAKOUT",
    "EXIT_BY_KC_TREND_REVERSAL",
    "EXIT_BY_KC_BAND_BREACH",
})
logger = logging.getLogger(__name__)


@dataclass
class WildSwingSymbolState:
    bars_since_last_exit: int = 999
    last_exit_side: Optional[str] = None
    last_exit_price: Optional[float] = None
    last_exit_reason: Optional[str] = None
    last_exit_bar_id: Optional[float] = None
    last_processed_bar_id: Optional[float] = None
    reset_bar_id: Optional[float] = None
    pending_wick_bar_ids: Dict[str, float] = field(default_factory=dict)
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
        if len(closed) >= self.volume_lookback + 2 and not valid.tail(
            self.volume_lookback + 2,
        ).all():
            return closed.iloc[:0]
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
        tick_price: Optional[float] = None,
        expected_wick_bar_id: Optional[float] = None,
    ) -> Optional[dict[str, Any]]:
        """Evaluate only the five explicitly authorized mathematical entries."""
        side = str(side).upper()
        if side not in ("LONG", "SHORT"):
            raise ValueError("side must be LONG or SHORT")
        if not is_auto_entry_symbol(symbol):
            return None
        bars = self._closed_frame(frame)
        if len(bars) < self.volume_lookback + 2:
            return None

        current = bars.iloc[-1]
        previous = bars.iloc[-2]
        bar_id = self._bar_id(current)
        previous_bar_id = self._bar_id(previous)
        if bar_id is None or previous_bar_id is None:
            return None
        state = self.state_for(symbol)
        live_bar = frame.iloc[-1] if frame is not None and not frame.empty else None
        live_bar_id = self._bar_id(live_bar) if live_bar is not None else None
        live_is_open = bool(
            live_bar is not None
            and live_bar.get("is_closed") is not None
            and not bool(live_bar.get("is_closed"))
            and live_bar_id is not None
            and live_bar_id > bar_id
        )
        evaluation_bar_id = bar_id
        if (
            live_is_open
            and state.last_exit_bar_id is not None
            and live_bar_id is not None
            and live_bar_id > state.last_exit_bar_id
        ):
            evaluation_bar_id = live_bar_id
        if state.last_exit_bar_id is not None:
            try:
                timeframe_ms = float(
                    frame.attrs.get("timeframe_ms", 60_000.0) or 60_000.0
                )
            except (TypeError, ValueError, OverflowError):
                timeframe_ms = 0.0
            timestamp_step = bar_id - previous_bar_id
            timestamp_scale = (
                1000.0
                if math.isclose(
                    timestamp_step,
                    timeframe_ms / 1000.0,
                    rel_tol=1e-6,
                )
                else 1.0
            )
            if (
                math.isfinite(timeframe_ms)
                and timeframe_ms > 0
                and math.isfinite(state.last_exit_bar_id)
                and math.isfinite(timestamp_step)
                and timestamp_step > 0
            ):
                state.bars_since_last_exit = max(
                    0,
                    int(
                        (evaluation_bar_id - state.last_exit_bar_id)
                        * timestamp_scale
                        // timeframe_ms
                    ),
                )
        if (
            (state.reset_bar_id is not None and bar_id <= state.reset_bar_id)
            or (
                state.last_exit_bar_id is not None
                and evaluation_bar_id <= state.last_exit_bar_id
            )
            or (
                state.last_processed_bar_id is not None
                and evaluation_bar_id <= state.last_processed_bar_id
            )
        ):
            return None

        candidate = self._pure_math_candidate(
            bars, frame=frame, tick_price=tick_price,
        )
        supported_types = {
            CONFIRMED_2BAR_BREAKOUT,
            CONTINUATION_OUTSIDE_KC,
            MA_CROSSOVER,
            FIVE_LINE_CONTINUATION,
        }
        if candidate and candidate.get("is_intrabar") is True and candidate.get("type") in {"INSTANT_BREAKOUT_INSIDE", "INSTANT_BURST_BREAKOUT"}:
            # 鐵律：第一根實體破軌嚴禁用即時價搶開，必須等待第二根確認 (CONFIRMED_2BAR_BREAKOUT) 才允許進場
            candidate = None

        if (
            candidate is None
            or candidate.get("action") != f"OPEN_{side}"
            or candidate.get("type") not in supported_types
        ):
            legacy_signal = self._evaluate_legacy_entry(symbol, frame, side)
            if (
                legacy_signal is None
                or legacy_signal.get("side") != side
            ):
                return None
            legacy_confirmation = legacy_signal.get("structure_confirmation")
            if legacy_confirmation == "MA5_MA15_1M_CROSS":
                candidate_type = MA_CROSSOVER
            elif legacy_confirmation in {
                CONFIRMED_2BAR_BREAKOUT, CONTINUATION_OUTSIDE_KC,
            }:
                candidate_type = legacy_confirmation
            else:
                return None
            candidate = {
                "action": f"OPEN_{side}",
                "type": candidate_type,
                "confirmation": legacy_confirmation,
                "price": float(legacy_signal["close"]),
                "is_intrabar": False,
                "ma5_current": float(legacy_signal["ma5_current"]),
                "ma5_previous": float(legacy_signal["ma5_previous"]),
                "ma15_current": float(current["ma15"]),
                "ma15_previous": float(previous["ma15"]),
                "kc_upper": float(current["kc_upper"]),
                "kc_lower": float(current["kc_lower"]),
                "kc_basis": float(legacy_signal["kc_middle_current"]),
                "channel_expanding": bool(legacy_signal["channel_expanding"]),
                "bar_id": float(legacy_signal["bar_id"]),
                "reason": legacy_signal.get("candidate_reason") or legacy_signal["reason"],
            }

        # Breakouts can trigger immediately; an established outside-band move
        # can also enter through its separate trend-aligned continuation gate.
        entry_type = candidate["type"]
        delayed = False
        post_exit_continuation = False
        is_intrabar = bool(candidate.get("is_intrabar"))
        if not delayed and entry_type in {
            INSTANT_BREAKOUT_INSIDE,
            CONFIRMED_2BAR_BREAKOUT,
            CONTINUATION_OUTSIDE_KC,
        }:
            latest = frame.iloc[-1]
            try:
                quote = float(
                    tick_price if tick_price is not None else current["close"]
                )
                latest_upper = float(latest["kc_upper"])
                latest_lower = float(latest["kc_lower"])
            except (KeyError, TypeError, ValueError, OverflowError):
                return None
            if not all(
                math.isfinite(value) and value > 0
                for value in (quote, latest_upper, latest_lower)
            ) or latest_upper <= latest_lower:
                return None
            if (
                side == "LONG" and quote <= latest_upper
            ) or (
                side == "SHORT" and quote >= latest_lower
            ):
                logger.info(
                    "WILD_SWING_OUTER_QUOTE_BLOCK symbol=%s side=%s quote=%s "
                    "upper=%s lower=%s",
                    symbol, side, quote, latest_upper, latest_lower,
                )
                return None

        from core.gates.trend_riding_strategy import can_open_position
        state = self.state_for(symbol)
        gate_confirmation = (
            candidate.get("confirmation", entry_type) if not delayed else None
        )
        tick_quote = float(candidate.get("price") or (frame.iloc[-1]["close"] if frame is not None and not frame.empty else 0.0))
        can_open, gate_reason, _ = can_open_position(
            df=frame,
            side=side,
            symbol=symbol,
            quote=tick_quote,
            bars_since_last_exit=state.bars_since_last_exit,
            last_closed_side=state.last_exit_side,
            last_closed_ts=state.last_exit_bar_id or 0.0,
            last_closed_price=state.last_exit_price or 0.0,
            entry_confirmation=gate_confirmation,
        )
        if not can_open:
            logger.info(
                "WILD_SWING_ENTRY_BLOCK symbol=%s side=%s reason=%s",
                symbol, side, gate_reason,
            )
            return None

        if delayed:
            candle_directional = (
                float(current["close"]) > float(current["open"])
                if side == "LONG"
                else float(current["close"]) < float(current["open"])
            )
            if not candle_directional:
                return None

        current_ma5 = (
            float(candidate["ma5_current"])
            if is_intrabar else float(current["_wild_ma5"])
        )
        current_ma15 = (
            float(candidate["ma15_current"])
            if is_intrabar else float(current["ma15"])
        )
        current_middle = (
            float(candidate["kc_basis"])
            if is_intrabar else float(current["kc_middle"])
        )
        previous_ma5 = (
            float(candidate["ma5_previous"])
            if is_intrabar else float(previous["_wild_ma5"])
        )
        previous_middle = (
            float(current["kc_middle"])
            if is_intrabar else float(previous["kc_middle"])
        )
        kc_middle_slope = current_middle - previous_middle
        kc_middle_rising = kc_middle_slope > 0
        kc_middle_falling = kc_middle_slope < 0
        ma5_slope = current_ma5 - previous_ma5
        channel_expanding = bool(candidate.get("channel_expanding")) if is_intrabar else (
            float(current["kc_upper"] - current["kc_lower"])
            > float(previous["kc_upper"] - previous["kc_lower"])
        )
        if entry_type in (INSTANT_BREAKOUT_INSIDE, MA_CROSSOVER, FIVE_LINE_CONTINUATION, INSTANT_BURST_BREAKOUT):
            trend_aligned = True
        elif not delayed and entry_type == CONTINUATION_OUTSIDE_KC:
            trend_aligned = (
                kc_middle_rising
                and ma5_slope > 0
                and current_ma5 > current_middle
                and current_middle > float(previous["kc_middle"])
                if side == "LONG"
                else kc_middle_falling
                and ma5_slope < 0
                and current_ma5 < current_middle
                and current_middle < float(previous["kc_middle"])
            )
        elif side == "LONG":
            trend_aligned = (
                channel_expanding
                and ma5_slope > 0
                and kc_middle_rising
                and current_ma5 > current_ma15
                and (not delayed or float(current["close"]) > current_ma5)
            )
        else:
            trend_aligned = (
                kc_middle_falling
                and current_ma5 < current_ma15
                and ma5_slope < 0
                and (not delayed or float(current["close"]) < current_ma5)
            )
        if not trend_aligned:
            logger.info(
                "[拒絕開%s] symbol=%s KC方向=%s KC中軌斜率=%+.8g "
                "MA5=%g MA15=%g MA5斜率=%+.8g",
                "多" if side == "LONG" else "空",
                symbol,
                "UP" if kc_middle_rising else "DOWN" if kc_middle_falling else "FLAT",
                kc_middle_slope,
                current_ma5,
                current_ma15,
                ma5_slope,
            )
            return None

        entry_pattern = (
            ENTRY_MODE_BREAKOUT
            if entry_type in {
                CONFIRMED_2BAR_BREAKOUT,
                INITIAL_2BAR_BREAKOUT,
                "INSTANT_BREAKOUT_INSIDE",
                "INSTANT_BREAKOUT_OUTSIDE",
                INSTANT_BURST_BREAKOUT,
            }
            else ENTRY_MODE_CROSSOVER
            if entry_type == MA_CROSSOVER
            else ENTRY_MODE_CONTINUATION
        )
        ma5_current = current_ma5
        ma5_previous = previous_ma5
        channel_expanding = (
            float(current["kc_upper"] - current["kc_lower"])
            > float(previous["kc_upper"] - previous["kc_lower"])
        )
        reason = (
            f"{entry_type}_WICK_FOLLOW_THROUGH_ENTRY"
            if delayed
            else f"{entry_type}_ENTRY"
        )
        signal = {
            "side": side,
            "reason": reason,
            "entry_label": reason,
            "entry_pattern": entry_pattern,
            "entry_signal_mode": entry_pattern,
            "structure_confirmation": candidate.get("confirmation", entry_type),
            "structure_confirmed": True,
            "is_intrabar": is_intrabar,
            "channel_expanding": channel_expanding,
            "ma5_current": ma5_current,
            "ma5_previous": ma5_previous,
            "kc_middle_current": current_middle,
            "kc_middle_previous": previous_middle,
            "volume_exhausted": False,
            "bar_id": (
                float(candidate["bar_id"]) if is_intrabar
                else bar_id
            ),
            "close": (
                float(candidate["price"])
                if is_intrabar
                else float(current["close"])
            ),
            "entry_mode": "WILD_SWING",
            "candidate_reason": candidate["reason"],
        }
        if delayed:
            signal["wick_trigger_bar_id"] = float(candidate["wick_bar_id"])
        return signal

    @staticmethod
    def _pure_math_candidate(
        bars: pd.DataFrame,
        *,
        frame: Optional[pd.DataFrame] = None,
        tick_price: Optional[float] = None,
    ) -> Optional[dict[str, Any]]:
        live_candle = None
        if frame is not None and not frame.empty:
            live_candle = frame.iloc[-1].to_dict()
            if (
                tick_price is not None
                and not bool(live_candle.get("is_closed", True))
            ):
                try:
                    quote = float(tick_price)
                    live_candle["close"] = quote
                    live_candle["high"] = max(float(live_candle["high"]), quote)
                    live_candle["low"] = min(float(live_candle["low"]), quote)
                except (KeyError, TypeError, ValueError, OverflowError):
                    return None
        return BreakoutContinuationEntryGate.evaluate_pure_math(
            bars.to_dict("records"),
            bars["kc_upper"].tolist(),
            bars["kc_lower"].tolist(),
            bars["kc_middle"].tolist(),
            bars["_wild_ma5"].tolist(),
            bars["ma15"].tolist(),
            tick_price=tick_price,
            live_candle=live_candle,
        )

    @staticmethod
    def _has_directional_exhaustion_wick(
        bar: pd.Series, side: str,
    ) -> bool:
        open_price = float(bar["open"])
        close = float(bar["close"])
        high = float(bar["high"])
        low = float(bar["low"])
        body = abs(close - open_price)
        wick = (
            high - max(open_price, close)
            if side == "LONG"
            else min(open_price, close) - low
        )
        return wick > body

    def _wick_follow_through_candidate(
        self,
        bars: pd.DataFrame,
        side: str,
        wick_bar_id: float,
    ) -> Optional[dict[str, Any]]:
        trigger_bar = bars.iloc[-2]
        confirmation_bar = bars.iloc[-1]
        if (
            self._bar_id(trigger_bar) != wick_bar_id
            or self._bar_id(confirmation_bar) != wick_bar_id + 60_000
        ):
            return None
        candidate = self._pure_math_candidate(bars.iloc[:-1])
        if (
            candidate is None
            or candidate["action"] != f"OPEN_{side}"
            or not self._has_directional_exhaustion_wick(trigger_bar, side)
        ):
            return None
        return {
            **candidate,
            "wick_bar_id": wick_bar_id,
        }

    def _evaluate_legacy_entry(
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
        candle_range = float(current["high"] - current["low"])
        body_ratio = (
            abs(close - open_price) / candle_range
            if candle_range > 0
            else 0.0
        )
        if side == "LONG":
            breakout_confirmed = (
                close > float(current["kc_upper"])
                and open_price <= float(current["kc_upper"])
                and float(previous["close"]) <= float(previous["kc_upper"])
                and close > open_price
                and body_ratio >= 0.35
            )
        else:
            breakout_confirmed = (
                close < float(current["kc_lower"])
                and open_price >= float(current["kc_lower"])
                and float(previous["close"]) >= float(previous["kc_lower"])
                and close < open_price
                and body_ratio >= 0.35
            )
        breakout_signal = channel_expanding and breakout_confirmed

        if (
            profit_lock_exit
            and str(profit_lock_exit.get("side") or "").upper() == side
            and bar_id > float(profit_lock_exit.get("close_bar_id") or 0.0)
        ):
            profit_lock_signal = self._profit_lock_continuation_signal(
                bars, current, previous, side, bar_id, profit_lock_exit,
            )
            if profit_lock_signal is not None:
                return profit_lock_signal

        numeric_candidate = BreakoutContinuationEntryGate.evaluate(
            bars.to_dict("records"),
            bars["kc_upper"].tolist(),
            bars["kc_lower"].tolist(),
            bars["kc_middle"].tolist(),
            ma5_series=bars["_wild_ma5"].tolist(),
            ma15_series=bars["ma15"].tolist(),
        )
        if (
            numeric_candidate is not None
            and numeric_candidate["action"] == f"OPEN_{side}"
            and (
                numeric_candidate["type"] == INITIAL_2BAR_BREAKOUT
                or (
                    numeric_candidate["type"] == CONTINUATION_OUTSIDE_KC
                    and not breakout_signal
                )
            )
        ):
            is_initial_breakout = (
                numeric_candidate["type"] == INITIAL_2BAR_BREAKOUT
            )
            pattern = (
                ENTRY_MODE_BREAKOUT
                if is_initial_breakout
                else ENTRY_MODE_CONTINUATION
            )
            reason = (
                "INITIAL_2BAR_BREAKOUT_ENTRY"
                if is_initial_breakout
                else "CONTINUATION_OUTSIDE_KC_ENTRY"
            )
            return {
                "side": side,
                "reason": reason,
                "entry_label": reason,
                "entry_pattern": pattern,
                "entry_signal_mode": pattern,
                "structure_confirmation": numeric_candidate["type"],
                "structure_confirmed": True,
                "channel_expanding": channel_expanding,
                "ma5_current": ma5,
                "ma5_previous": prev_ma5,
                "kc_middle_current": float(current["kc_middle"]),
                "kc_middle_previous": prev_middle,
                "volume_exhausted": False,
                "bar_id": bar_id,
                "close": close,
                "entry_mode": "WILD_SWING",
                "candidate_reason": numeric_candidate["reason"],
            }

        if side == "LONG":
            valid_cross = (
                cross_up
                and channel_expanding
                and ma5 > prev_ma5
                and float(current["kc_middle"]) > prev_middle
                and close > float(current["kc_middle"])
                and close > open_price
                and close > ma5
            )
        else:
            valid_cross = (
                cross_down
                and channel_expanding
                and ma5 < prev_ma5
                and float(current["kc_middle"]) < prev_middle
                and close < float(current["kc_middle"])
                and close < float(current["kc_upper"])
                and close < open_price
                and close < ma5
            )
        if valid_cross:
            return {
                "side": side,
                "reason": "REVERSAL_CROSSOVER_ENTRY",
                "entry_label": "REVERSAL_CROSSOVER_ENTRY",
                "entry_pattern": ENTRY_MODE_CROSSOVER,
                "entry_signal_mode": ENTRY_MODE_CROSSOVER,
                "structure_confirmation": "MA5_MA15_1M_CROSS",
                "structure_confirmed": True,
                "channel_expanding": channel_expanding,
                "ma5_current": ma5,
                "ma5_previous": prev_ma5,
                "kc_middle_current": float(current["kc_middle"]),
                "kc_middle_previous": prev_middle,
                "volume_exhausted": False,
                "bar_id": bar_id,
                "close": close,
                "entry_mode": "WILD_SWING",
            }

        if not breakout_signal:
            # 依據 AGENTS.md 妖幣規範：漏單補救與「延續開倉（強制上車）」機制
            # 只要上一根或當前價格實體運行在 KC 外軌強勢區，且 MA5 與中軌同向向上/向下，允許延續開倉上車
            if side == "LONG":
                continuation_aligned = (
                    close > float(current["kc_upper"])
                    and close > ma5
                    and ma5 > prev_ma5
                    and float(current["kc_middle"]) >= prev_middle
                    and close > open_price
                    and body_ratio >= 0.25
                )
            else:
                continuation_aligned = (
                    close < float(current["kc_lower"])
                    and close < ma5
                    and ma5 < prev_ma5
                    and float(current["kc_middle"]) <= prev_middle
                    and close < open_price
                    and body_ratio >= 0.25
                )
            if continuation_aligned:
                return {
                    "side": side,
                    "reason": "KC_OUTER_CONTINUATION_ENTRY",
                    "entry_label": "KC_OUTER_CONTINUATION_ENTRY",
                    "entry_pattern": ENTRY_MODE_CONTINUATION,
                    "entry_signal_mode": ENTRY_MODE_CONTINUATION,
                    "structure_confirmation": CONTINUATION_OUTSIDE_KC,
                    "structure_confirmed": True,
                    "channel_expanding": channel_expanding,
                    "ma5_current": ma5,
                    "ma5_previous": prev_ma5,
                    "kc_middle_current": float(current["kc_middle"]),
                    "kc_middle_previous": prev_middle,
                    "volume_exhausted": False,
                    "bar_id": bar_id,
                    "close": close,
                    "entry_mode": "WILD_SWING",
                }
            return self._additional_breakout_continuation_signal(
                bars, current, side, bar_id, channel_expanding,
                ma5, prev_ma5, prev_middle,
            )

        return {
            "side": side,
            "reason": "KC_OUTER_BREAKOUT_ENTRY",
            "entry_label": "KC_OUTER_BREAKOUT_ENTRY",
            "entry_pattern": ENTRY_MODE_BREAKOUT,
            "entry_signal_mode": ENTRY_MODE_BREAKOUT,
            "structure_confirmation": "KC_OUTER_BREAKOUT",
            "structure_confirmed": True,
            "channel_expanding": True,
            "ma5_current": ma5,
            "ma5_previous": prev_ma5,
            "kc_middle_current": float(current["kc_middle"]),
            "kc_middle_previous": prev_middle,
            "body_ratio": body_ratio,
            "volume_exhausted": False,
            "bar_id": bar_id,
            "close": close,
            "entry_mode": "WILD_SWING",
        }

    @staticmethod
    def _additional_breakout_continuation_signal(
        bars: pd.DataFrame,
        current: pd.Series,
        side: str,
        bar_id: float,
        channel_expanding: bool,
        ma5: float,
        prev_ma5: float,
        prev_middle: float,
    ) -> Optional[dict[str, Any]]:
        candidate = BreakoutContinuationEntryGate.evaluate(
            bars.to_dict("records"),
            bars["kc_upper"].tolist(),
            bars["kc_lower"].tolist(),
            bars["kc_middle"].tolist(),
        )
        if candidate is None or candidate["action"] != f"OPEN_{side}":
            return None

        is_breakout = candidate["type"] == "CHANNEL_CONSOLIDATION_BREAKOUT"
        pattern = ENTRY_MODE_BREAKOUT if is_breakout else ENTRY_MODE_CONTINUATION
        reason = (
            "CHANNEL_CONSOLIDATION_BREAKOUT_ENTRY"
            if is_breakout
            else "OUTSIDE_CHANNEL_CONTINUATION_ENTRY"
        )
        close = float(current["close"])
        candle_range = float(current["high"] - current["low"])
        body_ratio = (
            abs(close - float(current["open"])) / candle_range
            if candle_range > 0
            else 0.0
        )
        return {
            "side": side,
            "reason": reason,
            "entry_label": reason,
            "entry_pattern": pattern,
            "entry_signal_mode": pattern,
            "structure_confirmation": (
                "KC_OUTER_BREAKOUT"
                if is_breakout
                else "OUTSIDE_CHANNEL_CONTINUATION"
            ),
            "structure_confirmed": True,
            "channel_expanding": channel_expanding,
            "ma5_current": ma5,
            "ma5_previous": prev_ma5,
            "kc_middle_current": float(current["kc_middle"]),
            "kc_middle_previous": prev_middle,
            "body_ratio": body_ratio,
            "volume_exhausted": False,
            "bar_id": bar_id,
            "close": close,
            "entry_mode": "WILD_SWING",
            "candidate_reason": candidate["reason"],
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
        quote: Optional[float] = None,
    ) -> Optional[dict[str, Any]]:
        side = str((position or {}).get("side", "")).upper()
        if side not in ("LONG", "SHORT"):
            return None
        current_price = float(quote) if quote is not None else 0.0
        if current_price <= 0 and frame is not None and not frame.empty:
            try:
                current_price = float(frame.iloc[-1]["close"])
            except (KeyError, TypeError, ValueError, OverflowError):
                return None
        if current_price > 0:
            if frame is not None and not frame.empty:
                last_candle = frame.iloc[-1].to_dict()
                prev_candle = frame.iloc[-2].to_dict() if len(frame) > 1 else last_candle
                c_close = float(last_candle.get("close", current_price))
                c_ma5 = float(last_candle.get("ma5", last_candle.get("ema5", 0.0)))
                p_ma5 = float(prev_candle.get("ma5", prev_candle.get("ema5", c_ma5)))
                c_mid = float(last_candle.get("kc_middle", last_candle.get("kc_basis", 0.0)))
                p_mid = float(prev_candle.get("kc_middle", prev_candle.get("kc_basis", c_mid)))

                from core.gates.strict_entry_and_immunity_gate import resolve_kc_trend
                current_kc_trend = resolve_kc_trend(frame)
                bar_id = float(last_candle.get("timestamp", 0))

                # ── 獲利 R 值與配置參數 ──
                entry_p = float((position or {}).get("entry_price") or 0.0)
                atr = float(last_candle.get("atr", 0.0001) or 0.0001)
                initial_sl = float((position or {}).get("initial_sl") or 0.0)
                risk_r = abs(entry_p - initial_sl) if (initial_sl > 0 and entry_p > 0) else (1.5 * atr)
                cur_pnl = (current_price - entry_p) if side == "LONG" else (entry_p - current_price)
                profit_r = cur_pnl / risk_r if risk_r > 0 else 0.0

                c_open = float(last_candle.get("open", current_price))
                p_close = float(prev_candle.get("close", c_close))
                p2_candle = frame.iloc[-3].to_dict() if len(frame) > 2 else prev_candle
                p2_ma5 = float(p2_candle.get("ma5", p2_candle.get("ema5", p_ma5)))
                c_kc_up = float(last_candle.get("kc_upper", 0.0))
                p_kc_up = float(prev_candle.get("kc_upper", 0.0))
                c_kc_low = float(last_candle.get("kc_lower", 0.0))
                p_kc_low = float(prev_candle.get("kc_lower", 0.0))
                p_ema13 = float(prev_candle.get("ema13", prev_candle.get("ma15", p_mid)))

                c_ema13 = float(last_candle.get("ema13", last_candle.get("ma15", c_mid)))
                p_ema13 = float(prev_candle.get("ema13", prev_candle.get("ma15", p_mid)))

                if side == "LONG":
                    # ── 多單 3 分鐘專業動量衰退平倉 SOP ──
                    # 1. 【訊號 1】收盤實體跌破 EMA 13：代表短線主動買盤無力支撐，果斷平倉
                    # （正常回踩：盤中影線瞬間刺穿但收盤收回在 EMA13 之上，一律視為回踩洗盤，嚴禁平倉）
                    if c_ema13 > 0 and c_close < c_ema13:
                        logger.info("[趨勢衰退平多] %s 3M 收盤實體跌破 EMA13 (close=%.6g < ema13=%.6g)，短線買盤無力，果斷平倉！", symbol, c_close, c_ema13)
                        print(f"[趨勢衰退平多] {symbol} 3M 收盤實體跌破 EMA13，果斷平倉！", flush=True)
                        return {
                            "action": "CLOSE_LONG", "type": "EXIT_BY_EMA13_BREAKDOWN", "side": "LONG",
                            "reason": "EXIT_BY_EMA13_BREAKDOWN", "bar_id": bar_id, "close": current_price,
                            "entry_mode": "WILD_SWING", "kc_exit": {"cause": "3M收盤實體跌破EMA13"},
                        }

                    # 2. 【訊號 2】均線走平／死叉 + 跌入 KC 通道中軌：趨勢徹底告一段落，果斷平倉
                    is_dead_cross_or_flat = (c_ma5 <= c_ema13) or (c_ma5 <= p_ma5 and c_ma5 <= c_ema13 * 1.0005)
                    is_kc_mid_pierced = (c_mid > 0 and c_close < c_mid)
                    if is_dead_cross_or_flat and is_kc_mid_pierced:
                        logger.info("[趨勢終結平多] %s EMA5死叉/走平下穿EMA13 且摜破 KC 中軌，趨勢告一段落，果斷平倉！", symbol)
                        print(f"[趨勢終結平多] {symbol} EMA5死叉且摜破 KC 中軌，果斷平倉！", flush=True)
                        return {
                            "action": "CLOSE_LONG", "type": "EXIT_BY_DEAD_CROSS_KC_MID_BREAKDOWN", "side": "LONG",
                            "reason": "EXIT_BY_DEAD_CROSS_KC_MID_BREAKDOWN", "bar_id": bar_id, "close": current_price,
                            "entry_mode": "WILD_SWING", "kc_exit": {"cause": "EMA5死叉且摜破KC中軌"},
                        }

                    # 3. 大週期趨勢強制平多：大週期轉為 KC 向下
                    is_trend_bearish = (current_kc_trend in ("DOWN", "向下", "DOWNTREND", "BEARISH")) or (c_mid > 0 and p_mid > 0 and c_mid < p_mid and c_close < c_mid)
                    if is_trend_bearish:
                        logger.info("[強制平多] %s 大週期轉為 KC 向下，強制市價全平多單！", symbol)
                        print(f"[強制平多] {symbol} 大週期轉為 KC 向下，強制市價全平多單！", flush=True)
                        return {
                            "action": "CLOSE_LONG", "type": "EXIT_BY_BEARISH_TREND_REVERSAL", "side": "LONG",
                            "reason": "EXIT_BY_BEARISH_TREND_REVERSAL", "bar_id": bar_id, "close": current_price,
                            "entry_mode": "WILD_SWING", "kc_exit": {"cause": "KC_TREND_TURNED_BEARISH"},
                        }

                elif side == "SHORT":
                    # ── 空單 3 分鐘專業動量衰退平倉 SOP (反向對稱) ──
                    # 1. 【訊號 1】收盤實體突破 EMA 13：代表短線主動賣盤無力壓制，果斷平倉
                    # （正常回踩：盤中影線瞬間刺穿但收盤壓回在 EMA13 之下，一律視為回踩洗盤，嚴禁平倉）
                    if c_ema13 > 0 and c_close > c_ema13:
                        logger.info("[趨勢衰退平空] %s 3M 收盤實體突破 EMA13 (close=%.6g > ema13=%.6g)，短線賣盤無力，果斷平倉！", symbol, c_close, c_ema13)
                        print(f"[趨勢衰退平空] {symbol} 3M 收盤實體突破 EMA13，果斷平倉！", flush=True)
                        return {
                            "action": "CLOSE_SHORT", "type": "EXIT_BY_EMA13_BREAKOUT", "side": "SHORT",
                            "reason": "EXIT_BY_EMA13_BREAKOUT", "bar_id": bar_id, "close": current_price,
                            "entry_mode": "WILD_SWING", "kc_exit": {"cause": "3M收盤實體突破EMA13"},
                        }

                    # 2. 【訊號 2】均線走平／金叉 + 衝入 KC 通道中軌：趨勢徹底告一段落，果斷平倉
                    is_golden_cross_or_flat = (c_ma5 >= c_ema13) or (c_ma5 >= p_ma5 and c_ma5 >= c_ema13 * 0.9995)
                    is_kc_mid_reclaimed = (c_mid > 0 and c_close > c_mid)
                    if is_golden_cross_or_flat and is_kc_mid_reclaimed:
                        logger.info("[趨勢終結平空] %s EMA5金叉/走平上穿EMA13 且衝破 KC 中軌，趨勢告一段落，果斷平倉！", symbol)
                        print(f"[趨勢終結平空] {symbol} EMA5金叉且衝破 KC 中軌，果斷平倉！", flush=True)
                        return {
                            "action": "CLOSE_SHORT", "type": "EXIT_BY_GOLDEN_CROSS_KC_MID_BREAKOUT", "side": "SHORT",
                            "reason": "EXIT_BY_GOLDEN_CROSS_KC_MID_BREAKOUT", "bar_id": bar_id, "close": current_price,
                            "entry_mode": "WILD_SWING", "kc_exit": {"cause": "EMA5金叉且衝破KC中軌"},
                        }

                    # 3. 大週期趨勢強制平空：大週期轉為 KC 向上
                    is_trend_bullish = (current_kc_trend in ("UP", "向上", "UPTREND", "BULLISH")) or (c_mid > 0 and p_mid > 0 and c_mid > p_mid and c_close > c_mid)
                    if is_trend_bullish:
                        logger.info("[強制平空] %s 大週期轉為 KC 向上，強制市價全平空單！", symbol)
                        print(f"[強制平空] {symbol} 大週期轉為 KC 向上，強制市價全平空單！", flush=True)
                        return {
                            "action": "CLOSE_SHORT", "type": "EXIT_BY_BULLISH_TREND_REVERSAL", "side": "SHORT",
                            "reason": "EXIT_BY_BULLISH_TREND_REVERSAL", "bar_id": bar_id, "close": current_price,
                            "entry_mode": "WILD_SWING", "kc_exit": {"cause": "KC_TREND_TURNED_BULLISH"},
                        }

            # ── 1. 大利潤即時動態鎖利（利潤曾達標 5U 以上，動態回撤 25% 立即止盈）──
            entry_p = float((position or {}).get("entry_price") or 0.0)
            pos_amt = abs(float((position or {}).get("amount") or 0.0))
            if entry_p > 0 and pos_amt > 0:
                if side == "SHORT":
                    unrealized_profit_usdt = (entry_p - current_price) * pos_amt
                else:
                    unrealized_profit_usdt = (current_price - entry_p) * pos_amt

                prev_max = float((position or {}).get("max_profit") or 0.0)
                if unrealized_profit_usdt > prev_max:
                    prev_max = unrealized_profit_usdt
                    position["max_profit"] = prev_max

                # 利潤曾達到 5U 以上，當回跌到最大利潤的 70%（回撤達 30%）立即市價平倉
                if prev_max >= 5.0 and unrealized_profit_usdt <= prev_max * 0.70:
                    bar_id = None
                    if frame is not None and not frame.empty:
                        try:
                            bar_id = float(frame.iloc[-1].get("timestamp", 0))
                        except Exception:
                            bar_id = None
                    print(
                        f"[5U動態鎖利平倉] {symbol} {side} - 峰值利潤: {prev_max:.2f}U, 當前回撤利潤: {unrealized_profit_usdt:.2f}U (<= 70%) | 市價平倉",
                        flush=True,
                    )
                    return {
                        "action": f"CLOSE_{side}",
                        "type": "EXIT_BY_5U_DYNAMIC_TRAILING_LOCK",
                        "side": side,
                        "reason": "EXIT_BY_5U_DYNAMIC_TRAILING_LOCK",
                        "bar_id": bar_id,
                        "close": current_price,
                        "entry_mode": "WILD_SWING",
                        "kc_exit": {
                            "cause": "PROFIT_5U_TRAILING_70PCT_LOCK",
                            "max_profit": prev_max,
                            "current_profit": unrealized_profit_usdt,
                        },
                        "kc_exit_state_key": "KC_EXIT_STATE_KEY",
                    }

            from core.services.exits.kc_true_extreme_exit import (
                STATE_KEY as KC_EXIT_STATE_KEY,
                evaluate_kc_true_extreme_exit,
            )

            quote_ms = frame.attrs.get("snapshot_ms") if frame is not None else None
            if quote_ms is None:
                quote_ms = time.time() * 1000
            kc_exit, _ = evaluate_kc_true_extreme_exit(
                position, frame, current_price, quote_ms,
            )

            # ── 2. 趨勢平倉：防假突破與回踩震盪持倉保護（EMA13/MA15 + 0.15% 緩衝帶）──
            from core.services.exits.anti_fakeout_exit_guard import check_exit_with_anti_fakeout
            trend_broken = False
            if frame is not None and not frame.empty:
                last_candle = frame.iloc[-1].to_dict()
                c_close = float(last_candle.get("close", current_price))
                c_ma15 = float(last_candle.get("ma15", last_candle.get("ema13", 0.0)))
                c_ma5 = float(last_candle.get("ma5", last_candle.get("ema5", 0.0)))
                kc_dict = {
                    "mid": float(last_candle.get("kc_middle", last_candle.get("kc_basis", 0.0))),
                    "upper": float(last_candle.get("kc_upper", 0.0)),
                    "lower": float(last_candle.get("kc_lower", 0.0)),
                }

                # 調用 anti-fakeout 檢查 (回踩有守或未達 0.15% 緩衝帶均判定為 HOLD 嚴禁平倉)
                af_action, af_reason = check_exit_with_anti_fakeout(
                    candle=last_candle,
                    kc=kc_dict,
                    ema5=c_ma5,
                    ema13=c_ma15,
                    position=position,
                    current_pnl_usdt=unrealized_profit_usdt if (entry_p > 0 and pos_amt > 0) else None,
                    buffer_ratio=0.0015,
                )

                if af_action in ("CLOSE_LONG", "CLOSE_SHORT"):
                    trend_broken = True
                else:
                    trend_broken = False

            from core.services.exits.strict_true_pivot import (
                confirmed_true_pivot_exit,
                store_confirmed_pivot_exit,
            )
            confirmed_pivot = confirmed_true_pivot_exit(position, frame) if trend_broken else None
            if confirmed_pivot is not None and store_confirmed_pivot_exit(
                position, confirmed_pivot,
            ):
                reason, details = confirmed_pivot
                return {
                    "action": f"CLOSE_{side}",
                    "type": reason,
                    "side": side,
                    "reason": reason,
                    "bar_id": details.get("confirm_bar_id"),
                    "close": current_price,
                    "entry_mode": "WILD_SWING",
                    "kc_exit": {
                        **details,
                        "cause": "THREE_POINT_CONFIRMED_PIVOT",
                    },
                    "kc_exit_state_key": KC_EXIT_STATE_KEY,
                }
            # Ignore ordinary pullbacks and wick signals. Shorts may also
            # exit on a completed bullish close back above KC midline.
            is_confirmed_pivot = bool(
                trend_broken
                and kc_exit is not None
                and kc_exit[1].get("cause") == "THREE_POINT_CONFIRMED_PIVOT"
                and kc_exit[0] in {
                    "EXIT_BY_TRUE_PEAK_EXTREME_REVERSAL",
                    "EXIT_BY_TRUE_VALLEY_EXTREME_REVERSAL",
                }
            )
            # 波段抱單模式：只要 13EMA/MA15 依然向下傾斜，反抽至中軌/上軌嚴禁平空！
            is_ma15_falling = False
            if frame is not None and len(frame) >= 2:
                prev_ma15 = float(frame.iloc[-2].get("ma15", frame.iloc[-2].get("ema13", 0.0)) or 0.0)
                curr_ma15 = float(frame.iloc[-1].get("ma15", frame.iloc[-1].get("ema13", 0.0)) or 0.0)
                if prev_ma15 > 0 and curr_ma15 <= prev_ma15:
                    is_ma15_falling = True

            is_short_midline_reversal = bool(
                trend_broken
                and side == "SHORT"
                and not is_ma15_falling
                and current_price > (c_ma15 * 1.0015)
                and kc_exit is not None
                and kc_exit[0] == "EXIT_BY_SHORT_CONFIRMED_KC_MIDLINE_REVERSAL"
                and kc_exit[1].get("cause") == "KC_MIDLINE_RECLAIM_CLOSE"
            )
            if is_confirmed_pivot or is_short_midline_reversal:
                reason, details = kc_exit
                bar_id = details.get("bar_ms")
                if bar_id is None:
                    try:
                        bar_id = float(frame.iloc[-1]["timestamp"])
                    except (AttributeError, IndexError, KeyError, TypeError, ValueError):
                        bar_id = None
                return {
                    "action": f"CLOSE_{side}",
                    "type": reason,
                    "side": side,
                    "reason": reason,
                    "bar_id": bar_id,
                    "close": current_price,
                    "entry_mode": "WILD_SWING",
                    "kc_exit": details,
                    "kc_exit_state_key": KC_EXIT_STATE_KEY,
                }
        bars = self._closed_frame(frame)
        if len(bars) < 2:
            return None
        current = bars.iloc[-1]
        bar_id = self._bar_id(current)
        if bar_id is None:
            return None
        if (
            frame is not None
            and not frame.empty
            and "is_closed" in frame
            and not bool(frame.iloc[-1].get("is_closed", True))
        ):
            live_bar_id = self._bar_id(frame.iloc[-1])
            if live_bar_id is not None and live_bar_id > bar_id:
                bar_id = live_bar_id

        from core.services.strategies.defensive_trend_strategy import (
            check_long_exit,
            check_short_exit,
        )

        current_price = float(quote) if quote is not None else float(current["close"])

        if side == "SHORT":
            should_exit, reason, details = check_short_exit(
                position, frame, quote=current_price,
                allow_pullback_exits=False,
            )
        else:
            should_exit, reason, details = check_long_exit(
                position, frame, quote=current_price,
                allow_pullback_exits=False,
            )

        if should_exit and reason in PULLBACK_EXIT_REASONS:
            logger.info(
                "WILD_SWING_PULLBACK_EXIT_SUPPRESSED symbol=%s side=%s reason=%s",
                symbol, side, reason,
            )
            should_exit = False

        allowed_exit_reasons = {
            "EXIT_BY_TRAILING_PROFIT_LOCK",
            "EXIT_BY_BREAKEVEN",
            "EXIT_BY_HARD_STOP_LOSS",
        }
        if should_exit and reason in allowed_exit_reasons:
            if not trend_broken and reason != "EXIT_BY_HARD_STOP_LOSS":
                logger.info(
                    "WILD_SWING_TREND_INTACT_EXIT_BLOCKED symbol=%s side=%s reason=%s",
                    symbol, side, reason,
                )
                return None
            close = float(details.get("close", current["close"]))
            print(
                f"[平倉觸發] {symbol} {side} - 原因: {reason} | 價格: {close}",
                flush=True,
            )
            return {
                "action": f"CLOSE_{side}",
                "type": reason,
                "side": side,
                "reason": reason,
                "bar_id": bar_id,
                "close": close,
                "entry_mode": "WILD_SWING",
            }

        if should_exit:
            logger.info(
                "WILD_SWING_NON_AUTHORIZED_EXIT_SUPPRESSED "
                "symbol=%s side=%s reason=%s",
                symbol, side, reason,
            )
        return None

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

    def record_exit(
        self, symbol: str, side: str, price: float, reason: str, bar_id: float,
    ) -> None:
        state = self.state_for(symbol)
        effective_bar_id = max(
            float(bar_id),
            state.last_exit_bar_id or float(bar_id),
            state.last_processed_bar_id or float(bar_id),
        )
        state.last_exit_side = str(side).upper()
        state.last_exit_price = float(price)
        state.last_exit_reason = str(reason)
        state.last_exit_bar_id = effective_bar_id
        state.last_processed_bar_id = max(
            state.last_processed_bar_id or effective_bar_id,
            effective_bar_id,
        )
        state.bars_since_last_exit = 0

    def record_entry_fill(self, symbol: str, bar_id: float) -> None:
        state = self.state_for(symbol)
        state.last_processed_bar_id = float(bar_id)
