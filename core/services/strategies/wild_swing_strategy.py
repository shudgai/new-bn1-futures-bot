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
    INITIAL_2BAR_BREAKOUT,
    MA_CROSSOVER,
    BreakoutContinuationEntryGate,
)
from core.gates.entry_pattern_permitted_gate import PERMITTED_ENTRY_PATTERNS
from core.gates.ma5_ma15_cross_entry_gate import validate_ma5_ma15_cross_entry
from core.services.candle_data import closed_entry_candles
from core.services.exits.strict_true_pivot import (
    confirmed_true_pivot_exit,
    pending_confirmed_pivot_exit,
    store_confirmed_pivot_exit,
)
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
        from core.gates.trend_riding_strategy import can_open_position
        state = self.state_for(symbol)
        can_open, gate_reason, _ = can_open_position(
            df=frame,
            side=side,
            symbol=symbol,
            bars_since_last_exit=state.bars_since_last_exit,
            last_closed_side=state.last_exit_side,
            last_closed_ts=state.last_exit_bar_id or 0.0,
        )
        if not can_open:
            logger.info(
                "WILD_SWING_ENTRY_BLOCK symbol=%s side=%s reason=%s",
                symbol, side, gate_reason,
            )
            return None

        current = bars.iloc[-1]
        previous = bars.iloc[-2]
        bar_id = self._bar_id(current)
        previous_bar_id = self._bar_id(previous)
        if bar_id is None or previous_bar_id is None:
            return None
        state = self.state_for(symbol)
        if (
            (state.reset_bar_id is not None and bar_id <= state.reset_bar_id)
            or (state.last_exit_bar_id is not None and bar_id <= state.last_exit_bar_id)
            or (
                state.last_processed_bar_id is not None
                and bar_id <= state.last_processed_bar_id
            )
        ):
            return None

        delayed = False
        candidate = None
        if expected_wick_bar_id is not None:
            try:
                expected_id = float(expected_wick_bar_id)
            except (TypeError, ValueError, OverflowError):
                return None
            if not math.isfinite(expected_id):
                return None
            candidate = self._wick_follow_through_candidate(
                bars, side, expected_id,
            )
            delayed = candidate is not None
        else:
            pending_id = state.pending_wick_bar_ids.get(side)
            if pending_id is not None and bar_id > pending_id:
                state.pending_wick_bar_ids.pop(side, None)
                if bar_id == pending_id + 60_000:
                    candidate = self._wick_follow_through_candidate(
                        bars, side, pending_id,
                    )
                    if candidate is None:
                        logger.info(
                            "WILD_SWING_WICK_CONFIRMATION_BLOCK "
                            "symbol=%s side=%s wick_bar=%s confirmation_bar=%s",
                            symbol, side, pending_id, bar_id,
                        )
                        return None
                    delayed = True

            if candidate is None:
                candidate = self._pure_math_candidate(bars)
                if candidate is None or candidate["action"] != f"OPEN_{side}":
                    return None
                if self._has_directional_exhaustion_wick(current, side):
                    state.pending_wick_bar_ids[side] = bar_id
                    logger.info(
                        "WILD_SWING_WICK_CONFIRMATION_WAIT "
                        "symbol=%s side=%s wick_bar=%s",
                        symbol, side, bar_id,
                    )
                    return None

        if candidate is None or candidate["action"] != f"OPEN_{side}":
            return None

        if delayed:
            candle_directional = (
                float(current["close"]) > float(current["open"])
                if side == "LONG"
                else float(current["close"]) < float(current["open"])
            )
            if not candle_directional:
                return None

        current_ma5 = float(current["_wild_ma5"])
        current_ma15 = float(current["ma15"])
        current_middle = float(current["kc_middle"])
        previous_ma5 = float(previous["_wild_ma5"])
        previous_middle = float(previous["kc_middle"])
        kc_middle_slope = current_middle - previous_middle
        kc_middle_rising = kc_middle_slope > 0
        kc_middle_falling = kc_middle_slope < 0
        ma5_slope = current_ma5 - previous_ma5
        if side == "LONG":
            trend_aligned = (
                kc_middle_rising
                and current_ma5 > current_ma15
                and ma5_slope > 0
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

        entry_type = str(candidate["type"])
        entry_pattern = (
            ENTRY_MODE_BREAKOUT
            if entry_type in {
                CONFIRMED_2BAR_BREAKOUT,
                INITIAL_2BAR_BREAKOUT,
                "INSTANT_BREAKOUT_INSIDE",
                "INSTANT_BREAKOUT_OUTSIDE",
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
            "structure_confirmation": entry_type,
            "structure_confirmed": True,
            "is_intrabar": False,
            "channel_expanding": channel_expanding,
            "ma5_current": ma5_current,
            "ma5_previous": ma5_previous,
            "kc_middle_current": current_middle,
            "kc_middle_previous": previous_middle,
            "volume_exhausted": False,
            "bar_id": bar_id,
            "close": float(current["close"]),
            "entry_mode": "WILD_SWING",
            "candidate_reason": candidate["reason"],
        }
        if delayed:
            signal["wick_trigger_bar_id"] = float(candidate["wick_bar_id"])
        return signal

    @staticmethod
    def _pure_math_candidate(bars: pd.DataFrame) -> Optional[dict[str, Any]]:
        return BreakoutContinuationEntryGate.evaluate_pure_math(
            bars.to_dict("records"),
            bars["kc_upper"].tolist(),
            bars["kc_lower"].tolist(),
            bars["kc_middle"].tolist(),
            bars["_wild_ma5"].tolist(),
            bars["ma15"].tolist(),
            tick_price=None,
            live_candle=None,
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
                and ma5 > prev_ma5
                and float(current["kc_middle"]) > prev_middle
                and close > float(current["kc_middle"])
                and close > open_price
                and close > ma5
            )
        else:
            valid_cross = (
                cross_down
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
        pending_pivot = pending_confirmed_pivot_exit(position)
        if pending_pivot is not None:
            reason, details = pending_pivot
            close = float(quote) if quote is not None else float(
                position.get("entry_price") or 0.0
            )
            return {
                "action": f"CLOSE_{side}",
                "type": reason,
                "side": side,
                "reason": reason,
                "bar_id": details.get("exit_bar_id", details["confirm_bar_id"]),
                "close": close,
                "entry_mode": "WILD_SWING",
                "pivot": details,
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
            "EXIT_BY_PROFIT_EXHAUSTION_WICK",
            "EXIT_BY_HARD_STOP_LOSS",
        }
        if should_exit and reason in allowed_exit_reasons:
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

        pivot = self._confirmed_pivot_exit(position, bars, frame)
        if pivot is None:
            if should_exit:
                logger.info(
                    "WILD_SWING_NON_AUTHORIZED_EXIT_SUPPRESSED "
                    "symbol=%s side=%s reason=%s",
                    symbol, side, reason,
                )
            return None

        reason, pivot_details = pivot
        pending_details = {**pivot_details, "exit_bar_id": bar_id}
        if not store_confirmed_pivot_exit(
            position, (reason, pending_details),
        ):
            return None
        close = float(current_price)
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
            "pivot": pivot_details,
        }

    def _confirmed_pivot_exit(
        self,
        position: dict,
        bars: pd.DataFrame,
        frame: pd.DataFrame,
    ) -> Optional[tuple[str, dict[str, Any]]]:
        return confirmed_true_pivot_exit(position, frame)

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
