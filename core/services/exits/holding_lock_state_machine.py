"""Closed-bar holding lock with multi-factor top/bottom confirmation."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import pandas as pd

from core.services.candle_data import closed_entry_candles


@dataclass(frozen=True)
class HoldingLockResult:
    decision: str
    side: Optional[str] = None
    trend_state: str = "UNKNOWN"
    hard_stop_triggered: bool = False
    factor_count: int = 0
    factors: Dict[str, bool] = field(default_factory=dict)
    evidence: Dict[str, Any] = field(default_factory=dict)
    reason: str = "INSUFFICIENT_CONFIRMED_EVIDENCE"


class HoldingLockStateMachine:
    """Default to HOLD_LOCKED; close only on a hard stop or 2 of 4 factors."""

    def __init__(
        self,
        *,
        swing_lookback: int = 5,
        panic_body_atr: float = 2.0,
        peak_profit_min_pct: float = 0.15,
        profit_retrace_pct: float = 0.25,
        factors_required: int = 2,
    ) -> None:
        self.swing_lookback = max(2, int(swing_lookback))
        self.panic_body_atr = float(panic_body_atr)
        self.peak_profit_min_pct = float(peak_profit_min_pct)
        self.profit_retrace_pct = float(profit_retrace_pct)
        self.factors_required = max(2, min(4, int(factors_required)))

    @staticmethod
    def _finite_positive(value: Any) -> Optional[float]:
        try:
            number = float(value)
        except (TypeError, ValueError, OverflowError):
            return None
        return number if math.isfinite(number) and number > 0 else None

    @staticmethod
    def _after_entry(frame: pd.DataFrame, position: Dict[str, Any]) -> pd.DataFrame:
        if frame.empty or "timestamp" not in frame.columns:
            return frame
        entry = position.get("open_timestamp") or position.get("entry_timestamp")
        if entry is None:
            entry = position.get("open_bar_id") or position.get("entry_bar_id")
        try:
            entry_ms = float(entry)
            if entry_ms <= 0 or not math.isfinite(entry_ms):
                return frame
            if entry_ms < 1e11:
                entry_ms *= 1000.0
            stamps = pd.to_numeric(frame["timestamp"], errors="coerce")
            stamp_ms = stamps.where(stamps >= 1e11, stamps * 1000.0)
            return frame.loc[stamp_ms > entry_ms]
        except (TypeError, ValueError, OverflowError):
            return frame.iloc[:0]

    def evaluate(
        self,
        position: Dict[str, Any],
        frame: Optional[pd.DataFrame],
        quote: float,
        *,
        macro_frame: Optional[pd.DataFrame] = None,
    ) -> HoldingLockResult:
        side = str((position or {}).get("side") or "").upper()
        if side not in ("LONG", "SHORT"):
            return HoldingLockResult("HOLD_LOCKED", reason="INVALID_POSITION_SIDE")
        entry = self._finite_positive((position or {}).get("entry_price"))
        price = self._finite_positive(quote)
        if entry is None or price is None:
            return HoldingLockResult("HOLD_LOCKED", side=side, reason="INVALID_POSITION_OR_QUOTE")

        peak = self._finite_positive(
            (position or {}).get("highest_price" if side == "LONG" else "lowest_price")
            or (position or {}).get("peak_price" if side == "LONG" else "trough_price")
        )
        if peak is None:
            peak = entry
        has_profit_history = peak > entry if side == "LONG" else peak < entry

        raw = closed_entry_candles(frame)
        closed = self._after_entry(raw, position)
        factor_names = (
            "SWING_STRUCTURE_BREAK",
            "EXTREME_BODY_AND_KC_MIDLINE_BREAK",
            "MACRO_TREND_END_FAILED_RETEST",
            "PEAK_PROFIT_RETRACE_LOCK",
        )
        factors = {name: False for name in factor_names}
        evidence: Dict[str, Any] = {"closed_bar_count": len(closed)}

        # The underlying stored stop (or the project 3 ATR stop) remains absolute.
        atr = self._finite_positive(
            (position or {}).get("entry_atr")
            or (closed.iloc[-1].get("atr") if not closed.empty else None)
        )
        stop = self._finite_positive(
            (position or {}).get("sl") or (position or {}).get("initial_sl")
        )
        # ⚠️ 2026-10-27 最新最高指示：開倉後不要硬止損割肉，只能鎖利、大瀑布、市場熔斷及真峰谷真谷底才能平倉
        # hard_stop = stop is not None and (price <= stop if side == "LONG" else price >= stop)
        # if hard_stop: return HoldingLockResult(...) [DISABLED]

        if len(closed) >= self.swing_lookback + 1:
            current = closed.iloc[-1]
            prior = closed.iloc[-(self.swing_lookback + 1):-1]
            try:
                opening, high, low, close = (
                    float(current[key]) for key in ("open", "high", "low", "close")
                )
                prior_opens = pd.to_numeric(prior["open"], errors="coerce")
                prior_closes = pd.to_numeric(prior["close"], errors="coerce")
                if side == "LONG":
                    support = float(pd.concat([prior_opens, prior_closes], axis=1).min(axis=1).min())
                    recent_high = float(pd.to_numeric(prior["high"], errors="coerce").max())
                    factors["SWING_STRUCTURE_BREAK"] = has_profit_history and peak >= recent_high and close < support
                else:
                    resistance = float(pd.concat([prior_opens, prior_closes], axis=1).max(axis=1).max())
                    recent_low = float(pd.to_numeric(prior["low"], errors="coerce").min())
                    factors["SWING_STRUCTURE_BREAK"] = has_profit_history and peak <= recent_low and close > resistance
                evidence.update(swing_level=support if side == "LONG" else resistance, close=close)

                body = abs(close - opening)
                span = max(high - low, 1e-12)
                bar_atr = self._finite_positive(current.get("atr"))
                middle = self._finite_positive(current.get("kc_middle"))
                bearish = close < opening
                bullish = close > opening
                factors["EXTREME_BODY_AND_KC_MIDLINE_BREAK"] = bool(
                    has_profit_history and bar_atr and middle and body >= self.panic_body_atr * bar_atr
                    and body / span >= 0.70
                    and ((side == "LONG" and bearish and close < middle)
                         or (side == "SHORT" and bullish and close > middle))
                )
                evidence.update(
                    body_atr=(body / bar_atr if bar_atr else None),
                    body_range_ratio=body / span,
                    kc_middle=middle,
                )
            except (KeyError, TypeError, ValueError, OverflowError):
                pass

        macro = closed_entry_candles(macro_frame)
        if len(macro) >= 3 and "kc_middle" in macro.columns:
            try:
                prev2 = float(macro.iloc[-3]["kc_middle"])
                prev1 = float(macro.iloc[-2]["kc_middle"])
                now = float(macro.iloc[-1]["kc_middle"])
                last = macro.iloc[-1]
                macro_close = float(last["close"])
                macro_high = float(last["high"])
                macro_low = float(last["low"])
                if side == "LONG":
                    factors["MACRO_TREND_END_FAILED_RETEST"] = has_profit_history and (
                        prev1 > prev2 and now <= prev1
                        and macro_high >= now and macro_close < now
                    )
                    trend_state = "UP" if now > prev1 else "DOWN" if now < prev1 else "FLAT"
                else:
                    factors["MACRO_TREND_END_FAILED_RETEST"] = has_profit_history and (
                        prev1 < prev2 and now >= prev1
                        and macro_low <= now and macro_close > now
                    )
                    trend_state = "DOWN" if now < prev1 else "UP" if now > prev1 else "FLAT"
                evidence.update(
                    macro_kc_middle=now,
                    macro_previous_middle=prev1,
                    macro_retest_close=macro_close,
                )
            except (KeyError, TypeError, ValueError, OverflowError):
                trend_state = "UNKNOWN"
        else:
            trend_state = "UNKNOWN"

        if side == "LONG":
            favorable = max(0.0, (peak - entry) / entry)
            retrace = max(0.0, (peak - price) / max(peak - entry, 1e-12))
        else:
            favorable = max(0.0, (entry - peak) / entry)
            retrace = max(0.0, (price - peak) / max(entry - peak, 1e-12))
        factors["PEAK_PROFIT_RETRACE_LOCK"] = (
            favorable >= self.peak_profit_min_pct
            and retrace >= self.profit_retrace_pct
        )
        evidence.update(
            peak_price=peak,
            peak_favorable_pct=favorable,
            peak_profit_retrace_pct=retrace,
            quote=price,
        )

        factor_count = sum(factors.values())
        if factor_count >= self.factors_required:
            return HoldingLockResult(
                "CLOSE_POSITION", side=side, trend_state=trend_state,
                factor_count=factor_count, factors=factors, evidence=evidence,
                reason="MULTI_FACTOR_TRUE_PEAK" if side == "LONG" else "MULTI_FACTOR_TRUE_VALLEY",
            )
        return HoldingLockResult(
            "HOLD_LOCKED", side=side, trend_state=trend_state,
            factor_count=factor_count, factors=factors, evidence=evidence,
            reason="TRUE_PEAK_VALLEY_CONFIRMATION_INSUFFICIENT",
        )
