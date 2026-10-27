"""Fail-closed, closed-candle blacklist for choppy entry conditions."""

from __future__ import annotations

import math
from typing import Any, Optional

import pandas as pd
import numpy as np

from core.services.candle_data import closed_entry_candles


LOOKBACK = 20
MIDLINE_LOOKBACK = 10
MAX_MIDLINE_CROSSINGS = 3
MAX_MA_CROSSINGS = 2
SLOPE_FLAT_PCT = 0.0003
KC_BIG_MOUTH_RATIO = 2.0
LOW_ADX = 20.0
LOW_ATR_PCT = 0.003
MIN_VOLUME_RATIO = 0.8
MAX_WICK_BODY_RATIO = 2.0
COOLDOWN_BARS = 3


def _num(row: pd.Series, key: str) -> Optional[float]:
    try:
        value = float(row[key])
        return value if math.isfinite(value) else None
    except (KeyError, TypeError, ValueError, OverflowError):
        return None


def evaluate_market_blacklist(
    df: pd.DataFrame,
    i: int = -1,
    *,
    side: Optional[str] = None,
    last_closed_trade: Optional[dict[str, Any]] = None,
    current_bar_index: Optional[int] = None,
    cooldown_bars: int = COOLDOWN_BARS,
    macro_trend: Optional[str] = None,
    is_breakout: bool = False,
) -> tuple[bool, str, dict[str, Any]]:
    """Return (blocked, reason, evidence), using only explicitly closed bars.

    ``i`` indexes the original frame. The selected row must be explicitly
    closed; missing finality metadata is treated as unsafe at this boundary.
    """
    empty = {"blocked": True, "reason": "VETO_CONSOLIDATION_INVALID_OR_OPEN_BAR"}
    if not isinstance(df, pd.DataFrame) or df.empty:
        return True, empty["reason"], empty
    try:
        row = df.iloc[i]
        is_closed = row.get("is_kline_closed", row.get("is_closed", False))
        if not (is_closed is True or type(is_closed).__name__ == "bool_" and bool(is_closed)):
            return True, empty["reason"], empty
        source = df
        if "is_closed" not in df.columns and "is_kline_closed" in df.columns:
            source = df.copy()
            source["is_closed"] = source["is_kline_closed"]
        closed = closed_entry_candles(source)
        if closed.empty:
            return True, empty["reason"], empty
        ts = _num(row, "timestamp")
        positions = np.flatnonzero(closed.index.to_numpy() == row.name)
        if positions.size == 0 or positions[0] < 0:
            return True, empty["reason"], empty
        end = int(positions[0]) + 1
        bars = closed.iloc[:end]
        required_bars = max(LOOKBACK + 1, MIDLINE_LOOKBACK + 1)
        if len(bars) < required_bars:
            return True, "VETO_CONSOLIDATION_INSUFFICIENT_CLOSED_HISTORY", {"closed_bars": len(bars)}
        current = bars.iloc[-1]
        recent = bars.tail(LOOKBACK)
        needed = ("open", "high", "low", "close", "ma3", "ma15", "atr",
                  "kc_upper", "kc_middle", "kc_lower", "volume")
        if not all(key in bars.columns for key in needed):
            return True, "VETO_CONSOLIDATION_REQUIRED_DATA_MISSING", {
                "missing": [key for key in needed if key not in bars.columns]
            }
        values = recent[list(needed)].apply(pd.to_numeric, errors="coerce")
        if not values.notna().all().all() or not np.isfinite(values.to_numpy(dtype=float)).all():
            return True, "VETO_CONSOLIDATION_INVALID_INDICATORS", {}
        if (values[["kc_middle", "kc_upper", "kc_lower"]] <= 0).any().any() or (values["atr"] < 0).any():
            return True, "VETO_CONSOLIDATION_INVALID_INDICATORS", {}
        values["atr"] = values["atr"].clip(lower=1e-8)

        ma3 = values["ma3"]
        ma15 = values["ma15"]
        mid = values["kc_middle"]
        atr = values["atr"]
        close = values["close"]
        width = (values["kc_upper"] - values["kc_lower"]) / mid
        checks: dict[str, Any] = {}

        # Rapid MA3/KC-midline traversals, only counting low-amplitude crossings.
        delta = ma3 - mid
        signs = np.sign(delta).replace(0, np.nan).ffill()
        cross = (signs * signs.shift(1) < 0)
        low_amp = pd.concat([delta.abs(), delta.shift(1).abs()], axis=1).max(axis=1) < 0.5 * atr
        checks["ma3_midline_crossings"] = int((cross & low_amp).tail(MIDLINE_LOOKBACK).sum())
        if checks["ma3_midline_crossings"] >= MAX_MIDLINE_CROSSINGS:
            return True, "VETO_MA3_KC_MIDLINE_CHOP", checks

        # Flat short-term mean or channel basis.
        ma3_delta_pct = abs(float(ma3.iloc[-1] - ma3.iloc[-MIDLINE_LOOKBACK])) / max(abs(float(ma3.iloc[-MIDLINE_LOOKBACK])), 1e-12)
        mid_delta_pct = abs(float(mid.iloc[-1] - mid.iloc[-MIDLINE_LOOKBACK])) / max(abs(float(mid.iloc[-MIDLINE_LOOKBACK])), 1e-12)
        checks.update(ma3_net_slope_pct=ma3_delta_pct, kc_middle_net_slope_pct=mid_delta_pct)
        if ma3_delta_pct < SLOPE_FLAT_PCT or mid_delta_pct < SLOPE_FLAT_PCT:
            return True, "VETO_FLAT_MA3_OR_KC_MIDLINE", checks

        # MA3/MA15 whipsaw count over the analysis window.
        spread = ma3 - ma15
        ma_crossings = int((spread * spread.shift(1) < 0).tail(LOOKBACK).sum())
        checks["ma3_ma15_crossings"] = ma_crossings
        if ma_crossings >= MAX_MA_CROSSINGS:
            return True, "VETO_MA3_MA15_CHOP", checks

        # Sudden KC expansion (current width versus prior mean, excluding current bar).
        prior_width = width.iloc[:-1].tail(LOOKBACK)
        big_mouth_ratio = float(width.iloc[-1] / prior_width.mean()) if len(prior_width) else math.inf
        checks["kc_big_mouth_ratio"] = big_mouth_ratio
        if not is_breakout and (not math.isfinite(big_mouth_ratio) or big_mouth_ratio >= KC_BIG_MOUTH_RATIO):
            return True, "VETO_KC_BIG_MOUTH_EXPANSION", checks

        # Box/range with weak ADX or low normalized ATR.
        range_history = bars.tail(LOOKBACK + 1).iloc[:-1]
        inside_box = (
            float(current["close"]) <= float(range_history["high"].max())
            and float(current["close"]) >= float(range_history["low"].min())
        )
        adx = _num(current, "adx")
        atr_pct = float(atr.iloc[-1] / max(close.iloc[-1], 1e-12))
        checks.update(inside_donchian_box=inside_box, adx=adx, atr_pct=atr_pct)
        # Low ATR alone is common on 1m meme-coin charts even during a strong
        # directional move. Only classify an in-range market as low-trend when
        # both ADX and normalized ATR indicate weak conditions. Missing ADX
        # remains fail-closed when ATR is also below threshold.
        weak_adx = adx is None or adx < LOW_ADX
        if inside_box and weak_adx and atr_pct < LOW_ATR_PCT:
            return True, "VETO_DONCHIAN_RANGE_LOW_TREND", checks

        # Weak-volume and wick-heavy signal candle.
        prior_vol = pd.to_numeric(bars["volume"].iloc[:-1].tail(5), errors="coerce")
        avg_vol = float(prior_vol.mean()) if len(prior_vol) == 5 and prior_vol.notna().all() else 0.0
        vol_ratio = float(current["volume"] / avg_vol) if avg_vol > 0 else 0.0
        body = abs(float(current["close"]) - float(current["open"]))
        wick_total = max(0.0, float(current["high"]) - max(float(current["open"]), float(current["close"]))) + max(0.0, min(float(current["open"]), float(current["close"])) - float(current["low"]))
        checks.update(volume_ratio=vol_ratio, wick_body_ratio=wick_total / max(body, 1e-12))
        if vol_ratio < MIN_VOLUME_RATIO:
            return True, "VETO_VOLUME_CONTRACTION", checks
        if wick_total > MAX_WICK_BODY_RATIO * body:
            return True, "VETO_LONG_WICK_DOJI", checks

        # If the snapshot carries macro KC direction, reject counter-trend entries.
        if side:
            direction = str(side).upper()
            macro_label = str(
                macro_trend or current.get("macro_trend")
                or getattr(df, "attrs", {}).get("macro_trend") or ""
            ).upper()
            if (direction == "LONG" and macro_label in {"BEARISH", "SHORT", "DOWN"}
                    or direction == "SHORT" and macro_label in {"BULLISH", "LONG", "UP"}):
                return True, "VETO_MACRO_TREND_CONFLICT", {**checks, "macro_trend": macro_label}
            macro_slope = _num(current, "macro_kc_middle_slope")
            if macro_slope is None and "macro_kc_middle" in bars:
                macro_mid = pd.to_numeric(bars["macro_kc_middle"], errors="coerce")
                if len(macro_mid) >= 2 and macro_mid.notna().iloc[-2:].all():
                    macro_slope = float(macro_mid.iloc[-1] - macro_mid.iloc[-2])
            if macro_label not in {
                "BULLISH", "BEARISH", "LONG", "SHORT", "UP", "DOWN", "NEUTRAL",
            } and macro_slope is None:
                return True, "VETO_MACRO_TREND_UNAVAILABLE", checks
            if macro_slope is not None and (
                direction == "LONG" and macro_slope < 0
                or direction == "SHORT" and macro_slope > 0
            ):
                return True, "VETO_MACRO_TREND_CONFLICT", {**checks, "macro_kc_slope": macro_slope}

        # Cooldown supports both bar-index based trade records and timestamps.
        if last_closed_trade:
            exit_index = last_closed_trade.get("bar_index")
            if current_bar_index is None and ts is not None:
                stamp_ms = ts * 1000.0 if ts < 1e11 else ts
                current_bar_index = int(stamp_ms // 60_000)
            if exit_index is None and last_closed_trade.get("time") is not None:
                try:
                    exit_time = float(last_closed_trade["time"])
                    if math.isfinite(exit_time) and exit_time > 0:
                        exit_index = int((exit_time * 1000.0 if exit_time < 1e11 else exit_time) // 60_000)
                except (TypeError, ValueError, OverflowError):
                    exit_index = None
            try:
                if exit_index is None or current_bar_index is None:
                    return True, "VETO_INVALID_COOLDOWN_STATE", checks
                elapsed = int(current_bar_index) - int(exit_index)
                if elapsed < int(cooldown_bars):
                    return True, "VETO_TRADE_COOLDOWN", {**checks, "bars_since_exit": elapsed}
            except (TypeError, ValueError, OverflowError):
                return True, "VETO_INVALID_COOLDOWN_STATE", checks

        return False, "PASS_MARKET_BLACKLIST", checks
    except (IndexError, KeyError, TypeError, ValueError, OverflowError):
        return True, empty["reason"], empty


def is_market_in_consolidation(
    df: pd.DataFrame,
    i: int = -1,
    **kwargs: Any,
) -> bool:
    """Compatibility predicate: True means entry must be blocked."""
    blocked, _, _ = evaluate_market_blacklist(df, i, **kwargs)
    return blocked
