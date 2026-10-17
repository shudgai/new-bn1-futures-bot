"""Closed-candle entry and exit rules for high-volatility directional swings."""

from dataclasses import dataclass, field
import math
import os
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
        tick_price: Optional[float] = None,
    ) -> Optional[dict[str, Any]]:
        """Evaluate only the five explicitly authorized mathematical entries."""
        side = str(side).upper()
        if side not in ("LONG", "SHORT"):
            raise ValueError("side must be LONG or SHORT")
        bars = self._closed_frame(frame)
        if len(bars) < self.volume_lookback + 2:
            return None

        live_candle = None
        live_bar_id = None
        try:
            interval_ms = float(frame.attrs.get("timeframe_ms"))
        except (AttributeError, TypeError, ValueError, OverflowError):
            interval_ms = None
        if (
            frame is not None
            and not frame.empty
            and "is_closed" in frame
            and not bool(frame.iloc[-1]["is_closed"])
            and interval_ms == 60_000.0
        ):
            live_candle = frame.iloc[-1].to_dict()
            live_bar_id = self._bar_id(frame.iloc[-1])
            closed_bar_id = self._bar_id(bars.iloc[-1])
            if (
                live_bar_id is None
                or closed_bar_id is None
                or live_bar_id != closed_bar_id + 60_000.0
            ):
                live_candle = None
                live_bar_id = None

        candidate = BreakoutContinuationEntryGate.evaluate_pure_math(
            bars.to_dict("records"),
            bars["kc_upper"].tolist(),
            bars["kc_lower"].tolist(),
            bars["kc_middle"].tolist(),
            bars["_wild_ma5"].tolist(),
            bars["ma15"].tolist(),
            tick_price=tick_price if live_candle is not None else None,
            live_candle=live_candle,
        )
        if candidate is None or candidate["action"] != f"OPEN_{side}":
            return None

        entry_type = str(candidate["type"])
        current = bars.iloc[-1]
        previous = bars.iloc[-2]
        is_intrabar = candidate.get("is_intrabar") is True
        bar_id = live_bar_id if is_intrabar else self._bar_id(current)
        if bar_id is None:
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
        ma5_current = float(candidate["ma5_current"])
        ma5_previous = float(candidate["ma5_previous"])
        if (
            (side == "LONG" and ma5_current <= ma5_previous)
            or (side == "SHORT" and ma5_current >= ma5_previous)
        ):
            return None

        live_middle = (
            float(candidate["kc_basis"])
            if is_intrabar
            else float(current["kc_middle"])
        )
        previous_middle = float(previous["kc_middle"])
        channel_expanding = (
            float(current["kc_upper"] - current["kc_lower"])
            > float(previous["kc_upper"] - previous["kc_lower"])
        )
        reason = f"{entry_type}_ENTRY"
        return {
            "side": side,
            "reason": reason,
            "entry_label": reason,
            "entry_pattern": entry_pattern,
            "entry_signal_mode": entry_pattern,
            "structure_confirmation": entry_type,
            "structure_confirmed": True,
            "is_intrabar": is_intrabar,
            "channel_expanding": channel_expanding,
            "ma5_current": ma5_current,
            "ma5_previous": ma5_previous,
            "kc_middle_current": live_middle,
            "kc_middle_previous": previous_middle,
            "volume_exhausted": False,
            "bar_id": bar_id,
            "close": float(candidate["price"]),
            "entry_mode": "WILD_SWING",
            "candidate_reason": candidate["reason"],
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
        *,
        profit_lock_triggered: bool = False,
        current_tick_price: Optional[float] = None,
    ) -> Optional[dict[str, Any]]:
        side = str((position or {}).get("side", "")).upper()
        if side not in ("LONG", "SHORT"):
            return None
        bars = self._closed_frame(frame)
        if bars.empty:
            if not profit_lock_triggered or frame is None or frame.empty:
                return None
            current = frame.iloc[-1]
        else:
            current = bars.iloc[-1]
        bar_id = self._bar_id(current)
        if bar_id is None:
            return None
        state = self.state_for(symbol)
        close = self._number(current, "close")
        evidence: dict[str, Any] = {}
        if profit_lock_triggered:
            reason = "EXIT_BY_PROFIT_LOCK"
            exit_type = "RATCHET_PROFIT_LOCK"
        else:
            if bars.empty:
                return None
            if state.reset_bar_id is not None and bar_id <= state.reset_bar_id:
                return None
            if state.last_processed_bar_id is not None and bar_id <= state.last_processed_bar_id:
                return None
            pivot_exit, pivot_reason, pivot_evidence = self._pivot_exhaustion_exit(
                position, bars, side, current_tick_price=current_tick_price,
            )
            if pivot_exit:
                reason = pivot_reason
                exit_type = (
                    "PIVOT_PEAK_EXIT"
                    if side == "LONG"
                    else "PIVOT_VALLEY_EXIT"
                )
                evidence = pivot_evidence
            else:
                state.last_processed_bar_id = bar_id
                return None

        return {
            "action": f"CLOSE_{side}",
            "type": exit_type,
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
    def _pivot_exhaustion_exit(
        cls,
        position: dict,
        bars: pd.DataFrame,
        side: str,
        min_profit_pct: Optional[float] = None,
        *,
        current_tick_price: Optional[float] = None,
    ) -> tuple[bool, str, dict[str, Any]]:
        """
        偵測真峰谷提前平倉 (Pivot Peak/Valley Early Exit):
        k0 (bars.iloc[-1]): 當前確認收盤 K 棒
        k1 (bars.iloc[-2]): 潛在峰谷 K 棒
        k2 (bars.iloc[-3]): 前置確認 K 棒
        min_profit_pct: 只有在已有微幅獲利（預設 > 0.3%）時才啟動，避免剛開倉就因微幅震盪被刷出場
        """
        if len(bars) < 5:
            return False, "", {}

        entry_bar_floor = cls._position_entry_bar_floor(position, bars)
        k1 = bars.iloc[-2]
        k1_bar_id = cls._bar_id(k1)
        if entry_bar_floor is not None and k1_bar_id is not None and k1_bar_id < entry_bar_floor:
            return False, "", {}

        try:
            entry_price = float(
                (position or {}).get("entry_price")
                or (position or {}).get("price")
                or (position or {}).get("avg_price")
                or 0.0
            )
        except (TypeError, ValueError, OverflowError):
            return False, "", {}

        if min_profit_pct is None:
            try:
                min_profit_pct = float(os.getenv("WILD_SWING_PIVOT_MIN_PROFIT_PCT", "0.003"))
            except (TypeError, ValueError):
                min_profit_pct = 0.003

        k0 = bars.iloc[-1]
        k2 = bars.iloc[-3]

        try:
            current_price = (
                float(k0["close"])
                if current_tick_price is None
                else float(current_tick_price)
            )
            k1_open = float(k1["open"])
            k1_close = float(k1["close"])
            k1_high = float(k1["high"])
            k1_low = float(k1["low"])
            k0_close = float(k0["close"])
            k0_high = float(k0["high"])
            k0_low = float(k0["low"])
            k2_high = float(k2["high"])
            k2_low = float(k2["low"])
        except (KeyError, TypeError, ValueError, OverflowError):
            return False, "", {}
        if (
            not math.isfinite(entry_price)
            or entry_price <= 0
            or not math.isfinite(current_price)
            or current_price <= 0
            or not math.isfinite(k0_close)
        ):
            return False, "", {}

        # --- 1. 多單持倉：偵測「真頂/真峰 (Pivot Peak)」---
        if side == "LONG":
            profit_ratio = (current_price - entry_price) / entry_price
            if profit_ratio < min_profit_pct:
                return False, "", {}

            # 真峰條件：
            # 1. k1 的高點高於 k2 與 k0 的高點（形成 3 根局部高點）
            # 2. k0 收盤同時跌破 k1 的中點與最低點（確認反轉）
            is_pivot_high = (k1_high > k2_high) and (k1_high > k0_high)
            is_break_k1_low = (
                k0_close < (k1_open + k1_close) / 2
                and k0_close < k1_low
            )

            if is_pivot_high and is_break_k1_low:
                return True, "EXIT_BY_PIVOT_PEAK", {
                    "pivot_bar_id": k1_bar_id,
                    "pivot_price": k1_high,
                    "broken_structure_level": k1_low,
                    "profit_ratio": profit_ratio,
                }

        # --- 2. 空單持倉：偵測「真谷/真底 (Pivot Valley)」---
        elif side == "SHORT":
            profit_ratio = (entry_price - current_price) / entry_price
            if profit_ratio < min_profit_pct:
                return False, "", {}

            # 真谷條件：
            # 1. k1 的低點低於 k2 與 k0 的低點（形成 3 根局部低點）
            # 2. k0 收盤同時突破 k1 的中點與最高點（確認反轉）
            is_pivot_low = (k1_low < k2_low) and (k1_low < k0_low)
            is_break_k1_high = (
                k0_close > (k1_open + k1_close) / 2
                and k0_close > k1_high
            )

            if is_pivot_low and is_break_k1_high:
                return True, "EXIT_BY_PIVOT_VALLEY", {
                    "pivot_bar_id": k1_bar_id,
                    "pivot_price": k1_low,
                    "broken_structure_level": k1_high,
                    "profit_ratio": profit_ratio,
                }

        return False, "", {}

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
