"""已收線雙軌突破狀態機。

服務只負責策略判定，交易所送單由呼叫端執行；一般 K 的標準濾網可由呼叫端注入。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

import math
import pandas as pd
from core.services.anti_duplicate_execution import AntiDuplicateExecutionMixin


LONG = "LONG"
SHORT = "SHORT"


@dataclass(frozen=True)
class BreakoutDecision:
    action: str
    side: Optional[str] = None
    reason: str = "WAIT"

    def as_dict(self) -> dict:
        return {"action": self.action, "side": self.side, "reason": self.reason}


class DualTrackBreakoutStateMachine(AntiDuplicateExecutionMixin):
    """逐根處理已收線 K，並保證平倉優先於開倉。"""

    def __init__(
        self,
        *,
        lookback_bars: int = 20,
        extreme_range_atr: float = 1.6,
        minimum_body_atr: float = 1.0,
        minimum_body_ratio: float = 0.6,
        minimum_room_atr: float = 0.8,
        minimum_room_pct: float = 0.008,
        max_pending_bars: int = 3,
        position_amt_provider: Optional[Callable[[], float]] = None,
        order_executor: Optional[Callable[[str, str], bool]] = None,
        standard_entry_filter: Optional[Callable[[pd.Series, str, pd.DataFrame], bool]] = None,
    ) -> None:
        self.lookback_bars = lookback_bars
        self.extreme_range_atr = extreme_range_atr
        self.minimum_body_atr = minimum_body_atr
        self.minimum_body_ratio = minimum_body_ratio
        self.minimum_room_atr = minimum_room_atr
        self.minimum_room_pct = minimum_room_pct
        self.max_pending_bars = max(1, int(max_pending_bars))
        self.position_amt_provider = position_amt_provider
        self.standard_entry_filter = standard_entry_filter or (
            lambda bar, side, history: True
        )
        self.position: Optional[str] = None
        self.pending_signal: Optional[str] = None
        self.pending_bars_count = 0
        self.is_special_k = False
        self.last_processed_time = None
        self.closed_position_bar_time = None
        self._init_execution_guard(order_executor)

    @staticmethod
    def prepare_frame(frame: Optional[pd.DataFrame], max_bars: int = 100) -> pd.DataFrame:
        """固定指標輸入長度，讓 -2 永遠代表最後一根已收線 K。"""
        if frame is None:
            return pd.DataFrame()
        return frame.tail(max_bars).copy().reset_index(drop=True)

    def sync_position_from_exchange(self, actual_amt: float) -> Optional[str]:
        """以交易所實際 positionAmt 覆蓋本地持倉狀態。"""
        try:
            amount = float(actual_amt)
            if not math.isfinite(amount):
                raise ValueError("non-finite position amount")
            previous_position = self.position
            synced_position = LONG if amount > 0 else SHORT if amount < 0 else None
            self.position = synced_position
            if synced_position is not None or previous_position is not None:
                self.pending_signal = None
                self.pending_bars_count = 0
                self.is_special_k = False
            return self.position
        except (TypeError, ValueError, OverflowError):
            return None

    def _sync_position(self) -> bool:
        if self.position_amt_provider is None:
            return True
        try:
            actual_amt = self.position_amt_provider()
        except Exception:
            return False
        return self.sync_position_from_exchange(actual_amt) is not None or float(actual_amt) == 0.0

    def _clear_trigger_state(self) -> None:
        self.pending_bars_count = 0
        self.is_special_k = False

    @staticmethod
    def _bar_time(bar: pd.Series):
        return bar.get("timestamp")

    @staticmethod
    def _finite(*values: float) -> bool:
        return all(math.isfinite(float(value)) for value in values)

    @staticmethod
    def _is_bullish(bar: pd.Series) -> bool:
        return float(bar["close"]) > float(bar["open"])

    @staticmethod
    def _is_bearish(bar: pd.Series) -> bool:
        return float(bar["close"]) < float(bar["open"])

    @staticmethod
    def _is_doji(bar: pd.Series) -> bool:
        close = float(bar["close"])
        return abs(close - float(bar["open"])) <= 1e-5 * abs(close)

    def _is_extreme(self, bar: pd.Series) -> bool:
        return float(bar["high"]) - float(bar["low"]) >= self.extreme_range_atr * float(bar["atr"])

    def has_sufficient_profit_space(
        self,
        side: str,
        bar: pd.Series,
        history: Optional[pd.DataFrame] = None,
    ) -> bool:
        """檢查特例 K 實體、前方結構空間與手續費安全門檻。"""
        if side not in (LONG, SHORT):
            return False
        try:
            open_price = float(bar["open"])
            close = float(bar["close"])
            high = float(bar["high"])
            low = float(bar["low"])
            atr = float(bar["atr"])
            candle_range = high - low
            body = abs(close - open_price)
            if not self._finite(open_price, close, high, low, atr) or atr <= 0 or candle_range <= 0:
                return False
            if body < self.minimum_body_atr * atr or body / candle_range < self.minimum_body_ratio:
                return False

            prior = history if history is not None else pd.DataFrame()
            if not prior.empty:
                prior = prior.iloc[:-1] if len(prior) and prior.iloc[-1].equals(bar) else prior
                prior = prior.tail(self.lookback_bars)
            if prior.empty:
                return False
            if side == LONG:
                swing = float(prior["high"].max())
                if close < swing and swing - close < self.minimum_room_atr * atr:
                    return False
                room_pct = (swing - close) / close if swing > close else self.minimum_room_pct
            else:
                swing = float(prior["low"].min())
                if close > swing and close - swing < self.minimum_room_atr * atr:
                    return False
                room_pct = (close - swing) / close if swing < close else self.minimum_room_pct
            return room_pct >= self.minimum_room_pct
        except (AttributeError, KeyError, TypeError, ValueError, IndexError):
            return False

    def _breakout_side(self, bar: pd.Series) -> Optional[str]:
        if self._is_bullish(bar) and float(bar["close"]) > float(bar["kc_upper"]):
            return LONG
        if self._is_bearish(bar) and float(bar["close"]) < float(bar["kc_lower"]):
            return SHORT
        return None

    def _exit_side(self, bar: pd.Series) -> Optional[str]:
        if self.position == LONG and self._is_bearish(bar) and float(bar["close"]) < float(bar["kc_lower"]):
            return SHORT
        if self.position == SHORT and self._is_bullish(bar) and float(bar["close"]) > float(bar["kc_upper"]):
            return LONG
        return None

    def _peak_or_valley_exit(self, bar: pd.Series) -> bool:
        ma3_previous = bar.get("ma3_previous")
        ma3_current = bar.get("ma3")
        if ma3_previous is None or ma3_current is None:
            return False
        if self.position == LONG:
            return (
                float(bar.get("peak_high", float("-inf"))) >= float(bar["kc_upper"])
                and float(bar.get("peak_low", float("inf"))) > float(bar["close"])
                and float(ma3_current) < float(ma3_previous)
            )
        if self.position == SHORT:
            return (
                float(bar.get("valley_low", float("inf"))) <= float(bar["kc_lower"])
                and float(bar.get("valley_high", float("-inf"))) < float(bar["close"])
                and float(ma3_current) > float(ma3_previous)
            )
        return False

    def _open(self, side: str, reason: str) -> BreakoutDecision:
        self.position = side
        self.pending_signal = None
        self._clear_trigger_state()
        self.is_special_k = reason == "EXTREME_CANDLE_DIRECT_ENTRY"
        return BreakoutDecision("ENTER", side, reason)

    def on_order_result(self, decision: BreakoutDecision, filled: bool) -> None:
        """由下單層在成交確認後呼叫，清除已消費的觸發狀態。"""
        if not filled:
            return
        self._clear_trigger_state()
        if decision.action == "ENTER":
            self.position = decision.side
            self.pending_signal = None
        elif decision.action == "EXIT":
            self.position = None

    def process_closed_bar(
        self,
        bar: pd.Series,
        history: Optional[pd.DataFrame] = None,
        actual_position_amt: Optional[float] = None,
    ) -> BreakoutDecision:
        """每次只處理一根已收線 K；平倉決策永遠優先。"""
        bar_time = self._bar_time(bar)
        if bar_time is not None and bar_time == self.last_processed_time:
            return BreakoutDecision("WAIT", self.position, "DUPLICATE_CLOSED_BAR")
        if bar_time is not None:
            self.last_processed_time = bar_time
        if actual_position_amt is not None:
            if self.sync_position_from_exchange(actual_position_amt) is None and float(actual_position_amt) != 0.0:
                return BreakoutDecision("WAIT", None, "POSITION_SYNC_WAIT")
        elif not self._sync_position():
            return BreakoutDecision("WAIT", self.position, "POSITION_SYNC_WAIT")
        history = self.prepare_frame(history) if history is not None else pd.DataFrame()
        if self.position is not None:
            exit_signal = self._exit_side(bar)
            if exit_signal is not None:
                closed_side = self.position
                self.position = None
                self.pending_signal = exit_signal
                self._clear_trigger_state()
                self.is_special_k = self._is_extreme(bar)
                self.closed_position_bar_time = bar_time
                return BreakoutDecision("EXIT", closed_side, "CLOSE_THEN_WAIT_CONFIRMATION")
            if self._peak_or_valley_exit(bar):
                closed_side = self.position
                self.position = None
                self.pending_signal = None
                self._clear_trigger_state()
                return BreakoutDecision("EXIT", closed_side, "TRUE_PEAK_OR_VALLEY")
            return BreakoutDecision("HOLD", self.position, "POSITION_OPEN")

        side = self._breakout_side(bar)
        if self.pending_signal is not None:
            if bar_time is not None and bar_time == self.closed_position_bar_time:
                return BreakoutDecision("WAIT", self.pending_signal, "CLOSE_BAR_LOCK")
            self.pending_bars_count += 1
            if self.pending_bars_count > self.max_pending_bars:
                self.pending_signal = None
                self._clear_trigger_state()
                return BreakoutDecision("WAIT", None, "PENDING_SIGNAL_TIMEOUT")
            expected = self.pending_signal
            # A breakout candidate is valid only while closed candles remain
            # outside the triggering rail. Returning inside invalidates the
            # entire setup; a later breakout must start at bar one again.
            try:
                close = float(bar["close"])
                upper = float(bar["kc_upper"])
                lower = float(bar["kc_lower"])
                returned_inside = (
                    expected == LONG and close <= upper
                ) or (
                    expected == SHORT and close >= lower
                )
            except (KeyError, TypeError, ValueError):
                returned_inside = False
            if returned_inside:
                self.pending_signal = None
                self._clear_trigger_state()
                return BreakoutDecision("WAIT", None, "BREAKOUT_REENTERED_CHANNEL")
            if self._is_doji(bar):
                return BreakoutDecision("WAIT", expected, "DOJI_EXTENDS_PENDING")
            same_color = self._is_bullish(bar) if expected == LONG else self._is_bearish(bar)
            if not same_color:
                self.pending_signal = None
                self._clear_trigger_state()
                return BreakoutDecision("WAIT", None, "OPPOSITE_CANDLE_CANCELLED")
            was_special_k = self.is_special_k
            self.pending_signal = None
            self._clear_trigger_state()
            standard_ok = self.standard_entry_filter(
                bar, expected, history if history is not None else pd.DataFrame()
            )
            profit_ok = (
                not was_special_k
                or self.has_sufficient_profit_space(expected, bar, history)
            )
            if standard_ok and profit_ok:
                return self._open(expected, "STANDARD_CONFIRMATION")
            reason = "SPECIAL_CONFIRMATION_NO_PROFIT_SPACE" if standard_ok else "STANDARD_FILTER_REJECTED"
            return BreakoutDecision("WAIT", None, reason)

        if side is None:
            return BreakoutDecision("WAIT", None, "NO_BREAKOUT")
        if self._is_extreme(bar):
            if self.has_sufficient_profit_space(side, bar, history):
                return self._open(side, "EXTREME_CANDLE_DIRECT_ENTRY")
            self._clear_trigger_state()
            return BreakoutDecision("WAIT", None, "EXTREME_CANDLE_NO_PROFIT_SPACE")
        self.pending_signal = side
        self.pending_bars_count = 0
        self.is_special_k = False
        return BreakoutDecision("WAIT", side, "STANDARD_BREAKOUT_PENDING")

    def reset(self) -> None:
        self.position = None
        self.pending_signal = None
        self._clear_trigger_state()
        self.last_processed_time = None
        self.closed_position_bar_time = None


__all__ = ["BreakoutDecision", "DualTrackBreakoutStateMachine", "LONG", "SHORT"]
