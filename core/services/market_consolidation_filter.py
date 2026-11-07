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
MEME_IMPULSE_BODY_ATR = 0.6
MEME_STRUCTURAL_BODY_ATR = 0.4


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
    bars_since_last_exit: Optional[int] = None,
    cooldown_bars: int = COOLDOWN_BARS,
    macro_trend: Optional[str] = None,
    is_breakout: bool = False,
    independent_kc_direction: bool = False,
    meme_policy: bool = False,
    expansion: bool = False,
    meme_structural_entry: bool = False,
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
        needed = ("open", "high", "low", "close", "ma5", "ma15", "atr",
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

        ma5 = values["ma5"]
        ma3 = (
            pd.to_numeric(recent["ma3"], errors="coerce")
            if "ma3" in recent.columns and pd.to_numeric(recent["ma3"], errors="coerce").notna().all()
            else ma5
        )
        ma15 = values["ma15"]
        mid = values["kc_middle"]
        atr = values["atr"]
        close = values["close"]
        width = (values["kc_upper"] - values["kc_lower"]) / mid
        checks: dict[str, Any] = {}

        # Rapid MA5/KC-midline traversals, only counting low-amplitude crossings.
        delta = ma5 - mid
        signs = np.sign(delta).replace(0, np.nan).ffill()
        cross = (signs * signs.shift(1) < 0)
        low_amp = pd.concat([delta.abs(), delta.shift(1).abs()], axis=1).max(axis=1) < 0.5 * atr
        checks["ma5_midline_crossings"] = int((cross & low_amp).tail(MIDLINE_LOOKBACK).sum())
        if not meme_policy and not is_breakout and checks["ma5_midline_crossings"] >= MAX_MIDLINE_CROSSINGS:
            return True, "VETO_MA5_KC_MIDLINE_CHOP", checks

        # Rule A: Flat short-term mean (MA3 / MA5) or channel basis (KC middle).
        ma3_delta_pct = abs(float(ma3.iloc[-1] - ma3.iloc[-MIDLINE_LOOKBACK])) / max(abs(float(ma3.iloc[-MIDLINE_LOOKBACK])), 1e-12)
        ma5_delta_pct = abs(float(ma5.iloc[-1] - ma5.iloc[-MIDLINE_LOOKBACK])) / max(abs(float(ma5.iloc[-MIDLINE_LOOKBACK])), 1e-12)
        mid_delta_pct = abs(float(mid.iloc[-1] - mid.iloc[-MIDLINE_LOOKBACK])) / max(abs(float(mid.iloc[-MIDLINE_LOOKBACK])), 1e-12)
        checks.update(
            ma3_net_slope_pct=ma3_delta_pct,
            ma5_net_slope_pct=ma5_delta_pct,
            kc_middle_net_slope_pct=mid_delta_pct,
        )
        if not is_breakout and not expansion and (
            ma3_delta_pct < SLOPE_FLAT_PCT
            or ma5_delta_pct < SLOPE_FLAT_PCT
            or (not independent_kc_direction and mid_delta_pct < SLOPE_FLAT_PCT)
        ):
            return True, "VETO_FLAT_MA5_OR_KC_MIDLINE", checks

        # Rule B: Wave Entanglement — MA3/MA5 vs MA15 whipsaw count over recent window (Chop in last 8 bars >= 2, or over 20 bars >= 4)
        spread_ma5 = ma5 - ma15
        spread_ma3 = ma3 - ma15
        recent_crossings_ma5 = int((spread_ma5 * spread_ma5.shift(1) < 0).tail(8).sum())
        recent_crossings_ma3 = int((spread_ma3 * spread_ma3.shift(1) < 0).tail(8).sum())
        total_crossings_ma5 = int((spread_ma5 * spread_ma5.shift(1) < 0).tail(LOOKBACK).sum())
        total_crossings_ma3 = int((spread_ma3 * spread_ma3.shift(1) < 0).tail(LOOKBACK).sum())
        recent_crossings = max(recent_crossings_ma5, recent_crossings_ma3)
        total_crossings = max(total_crossings_ma5, total_crossings_ma3)
        checks["ma3_ma15_recent_crossings"] = recent_crossings_ma3
        checks["ma5_ma15_crossings"] = total_crossings
        checks["ma5_ma15_recent_crossings"] = recent_crossings
        if (meme_policy and recent_crossings >= 3) or (
            not meme_policy and not is_breakout and (recent_crossings >= MAX_MA_CROSSINGS or total_crossings >= 4)
        ):
            return True, "VETO_MA5_MA15_CHOP", checks

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
        direction_sign = {"LONG": 1, "SHORT": -1}.get(str(side or "").upper(), 0)
        signal_body_atr = direction_sign * float(current["close"] - current["open"]) / float(atr.iloc[-1])
        meme_structural_trigger = (
            meme_policy
            and (
                meme_structural_entry
                or expansion and signal_body_atr >= MEME_STRUCTURAL_BODY_ATR - 1e-12
            )
        )
        # Meme impulse bar (same-side body >= 0.6 ATR, or authorized MEME_GATE_ENTRY >= 0.35 ATR) starts a move from inside the box.
        meme_impulse = meme_policy and (
            signal_body_atr >= MEME_IMPULSE_BODY_ATR - 1e-12
            or (meme_structural_trigger and signal_body_atr >= 0.35 - 1e-12)
        )
        checks["signal_body_atr"] = signal_body_atr
        if inside_box and weak_adx and atr_pct < LOW_ATR_PCT and not meme_impulse:
            return True, "VETO_DONCHIAN_RANGE_LOW_TREND", checks

        # Weak-volume and wick-heavy signal candle.
        prior_vol = pd.to_numeric(bars["volume"].iloc[:-1].tail(5), errors="coerce")
        avg_vol = float(prior_vol.mean()) if len(prior_vol) == 5 and prior_vol.notna().all() else 0.0
        vol_ratio = float(current["volume"] / avg_vol) if avg_vol > 0 else 0.0
        body = abs(float(current["close"]) - float(current["open"]))
        wick_total = max(0.0, float(current["high"]) - max(float(current["open"]), float(current["close"]))) + max(0.0, min(float(current["open"]), float(current["close"])) - float(current["low"]))
        checks.update(
            volume_ratio=vol_ratio,
            wick_body_ratio=wick_total / max(body, 1e-12),
            meme_structural_volume_override=meme_structural_trigger,
        )
        if vol_ratio < MIN_VOLUME_RATIO and not meme_structural_trigger:
            return True, "VETO_VOLUME_CONTRACTION", checks
        if wick_total > MAX_WICK_BODY_RATIO * body:
            return True, "VETO_LONG_WICK_DOJI", checks

        # Rule G: Caller-supplied HTF KC slope or completed KC direction.
        if side and not independent_kc_direction:
            direction = str(side).upper()
            kc_slope = float(mid.iloc[-1] - mid.iloc[-2])
            htf_slope = _num(current, "macro_kc_middle_slope")
            if htf_slope is None and isinstance(df.attrs, dict) and df.attrs.get("macro_kc_middle_slope") is not None:
                try:
                    val = float(df.attrs["macro_kc_middle_slope"])
                    if math.isfinite(val):
                        htf_slope = val
                except (TypeError, ValueError, OverflowError):
                    htf_slope = None
            htf_conflict = bool(
                htf_slope is not None
                and ((direction == "LONG" and htf_slope < 0) or (direction == "SHORT" and htf_slope > 0))
            )
            if (
                htf_conflict
                or (direction == "LONG" and (kc_slope < 0 or kc_slope == 0 and not expansion))
                or (direction == "SHORT" and (kc_slope > 0 or kc_slope == 0 and not expansion))
            ):
                return True, "VETO_MACRO_TREND_CONFLICT", {
                    **checks,
                    "kc_middle_slope": kc_slope,
                    "macro_kc_middle_slope": htf_slope,
                }
        # Rule H: Cooldown supports direct bars_since_last_exit, bar-index based trade records, and timestamps.
        if bars_since_last_exit is None and isinstance(df.attrs, dict) and df.attrs.get("bars_since_last_exit") is not None:
            try:
                bars_since_last_exit = int(df.attrs["bars_since_last_exit"])
            except (TypeError, ValueError, OverflowError):
                return True, "VETO_INVALID_COOLDOWN_STATE", checks
        if bars_since_last_exit is not None:
            try:
                elapsed_direct = int(bars_since_last_exit)
                if elapsed_direct < int(cooldown_bars):
                    return True, "VETO_TRADE_COOLDOWN", {**checks, "bars_since_exit": elapsed_direct}
            except (TypeError, ValueError, OverflowError):
                return True, "VETO_INVALID_COOLDOWN_STATE", checks
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
    """Zero-Trust Entry Gate Blacklist (Rules A-H): True means entry must be blocked (ACTION: PASS)."""
    blocked, _, _ = evaluate_market_blacklist(df, i, **kwargs)
    return blocked


def check_entry_signal(
    df: pd.DataFrame,
    i: int = -1,
    *,
    side: Optional[str] = None,
    last_closed_trade: Optional[dict[str, Any]] = None,
    bars_since_last_exit: Optional[int] = None,
    cooldown_bars: int = COOLDOWN_BARS,
    macro_trend: Optional[str] = None,
) -> dict[str, Any]:
    """Zero-Trust Entry Gate Funnel:
    1. Enforce Closed-Bar Finality (is_kline_closed == True / is_closed == True).
    2. Run Blacklist Rules A-H via is_market_in_consolidation(df, i, ...).
       If ANY blacklist condition triggers -> immediately return ACTION: PASS.
    3. Only after passing all 8 blacklist defenses, evaluate MA3/MA5 x MA15 cross or KC breakout.
    """
    if not isinstance(df, pd.DataFrame) or df.empty:
        return {
            "action": "PASS",
            "can_execute": False,
            "side": None,
            "reason": "VETO_CONSOLIDATION_INVALID_OR_OPEN_BAR",
            "details": {},
        }
    try:
        row = df.iloc[i]
    except (IndexError, KeyError, TypeError, ValueError):
        return {
            "action": "PASS",
            "can_execute": False,
            "side": None,
            "reason": "VETO_CONSOLIDATION_INVALID_OR_OPEN_BAR",
            "details": {},
        }

    is_closed = row.get("is_kline_closed", row.get("is_closed", False))
    if not (is_closed is True or (type(is_closed).__name__ == "bool_" and bool(is_closed))):
        return {
            "action": "PASS",
            "can_execute": False,
            "side": None,
            "reason": "VETO_BAR_NOT_CLOSED",
            "details": {"is_kline_closed": False},
        }

    candidate_sides = (str(side).upper(),) if side in ("LONG", "SHORT", "long", "short") else ("LONG", "SHORT")

    # Determine if the current closed bar has a directional cross or breakout candidate
    source = df
    if "is_closed" not in df.columns and "is_kline_closed" in df.columns:
        source = df.copy()
        source["is_closed"] = source["is_kline_closed"]
    closed = closed_entry_candles(source)
    positions = np.flatnonzero(closed.index.to_numpy() == row.name) if not closed.empty else np.array([])
    if positions.size == 0 or int(positions[0]) < 1:
        return {
            "action": "PASS",
            "can_execute": False,
            "side": None,
            "reason": "VETO_CONSOLIDATION_INSUFFICIENT_CLOSED_HISTORY",
            "details": {},
        }
    end = int(positions[0]) + 1
    bars = closed.iloc[:end]
    curr = bars.iloc[-1]
    prev = bars.iloc[-2]

    # Step 1: Filter consolidation FIRST (Zero-Trust Blacklist Rules A-H)
    eval_side = candidate_sides[0] if len(candidate_sides) == 1 else (
        "LONG" if float(curr.get("close", 0.0)) >= float(curr.get("open", 0.0)) else "SHORT"
    )
    in_consolidation = is_market_in_consolidation(
        df,
        i,
        side=eval_side,
        last_closed_trade=last_closed_trade,
        bars_since_last_exit=bars_since_last_exit,
        cooldown_bars=cooldown_bars,
        macro_trend=macro_trend,
    )
    blocked, reason, checks = evaluate_market_blacklist(
        df,
        i,
        side=eval_side,
        last_closed_trade=last_closed_trade,
        bars_since_last_exit=bars_since_last_exit,
        cooldown_bars=cooldown_bars,
        macro_trend=macro_trend,
    )
    if in_consolidation or blocked:
        return {
            "action": "PASS",
            "can_execute": False,
            "side": None,
            "reason": reason,
            "details": checks,
        }

    # Step 2: Check Cross or Breakout only after passing Blacklist Rules A-H
    c_open = float(curr["open"])
    c_close = float(curr["close"])
    atr = max(float(curr["atr"]), 1e-8)
    kc_upper = float(curr["kc_upper"])
    kc_middle = float(curr["kc_middle"])
    kc_lower = float(curr["kc_lower"])
    prev_kc_middle = float(prev["kc_middle"])
    fast_curr = float(curr["ma3"]) if "ma3" in curr and pd.notna(curr["ma3"]) else float(curr["ma5"])
    fast_prev = float(prev["ma3"]) if "ma3" in prev and pd.notna(prev["ma3"]) else float(prev["ma5"])
    ma5_curr = float(curr["ma5"])
    ma5_prev = float(prev["ma5"])
    ma15_curr = float(curr["ma15"])
    ma15_prev = float(prev["ma15"])
    body = abs(c_close - c_open)

    for cand in candidate_sides:
        if cand == "LONG":
            is_green = c_close > c_open and body >= 0.35 * atr
            kc_up = kc_middle > prev_kc_middle
            ma_aligned = fast_curr > ma15_curr and ma5_curr > ma15_curr and fast_curr > fast_prev and ma5_curr > ma5_prev
            cross_long = (fast_prev <= ma15_prev and fast_curr > ma15_curr) or (ma5_prev <= ma15_prev and ma5_curr > ma15_curr)
            breakout_long = c_close > kc_upper and c_close > fast_curr and c_close > ma5_curr
            trend_impulse_long = c_close > kc_middle and c_close > ma5_curr and ma_aligned and body >= 0.35 * atr
            if is_green and kc_up and ma_aligned and (cross_long or breakout_long or trend_impulse_long):
                trigger = (
                    "KC_UPPER_BREAKOUT_LONG"
                    if breakout_long
                    else ("MA_GOLDEN_CROSS_LONG" if cross_long else "TREND_IMPULSE_LONG")
                )
                return {
                    "action": "OPEN_LONG",
                    "can_execute": True,
                    "side": "LONG",
                    "reason": f"AUTHORIZED_ZERO_TRUST_LONG:{trigger}",
                    "details": {**checks, "trigger": trigger},
                }
        elif cand == "SHORT":
            is_red = c_close < c_open and body >= 0.35 * atr
            kc_down = kc_middle < prev_kc_middle
            ma_aligned = fast_curr < ma15_curr and ma5_curr < ma15_curr and fast_curr < fast_prev and ma5_curr < ma5_prev
            cross_short = (fast_prev >= ma15_prev and fast_curr < ma15_curr) or (ma5_prev >= ma15_prev and ma5_curr < ma15_curr)
            breakout_short = c_close < kc_lower and c_close < fast_curr and c_close < ma5_curr
            trend_impulse_short = c_close < kc_middle and c_close < ma5_curr and ma_aligned and body >= 0.35 * atr
            if is_red and kc_down and ma_aligned and (cross_short or breakout_short or trend_impulse_short):
                trigger = (
                    "KC_LOWER_BREAKDOWN_SHORT"
                    if breakout_short
                    else ("MA_DEATH_CROSS_SHORT" if cross_short else "TREND_IMPULSE_SHORT")
                )
                return {
                    "action": "OPEN_SHORT",
                    "can_execute": True,
                    "side": "SHORT",
                    "reason": f"AUTHORIZED_ZERO_TRUST_SHORT:{trigger}",
                    "details": {**checks, "trigger": trigger},
                }

    return {
        "action": "PASS",
        "can_execute": False,
        "side": None,
        "reason": "WAIT_NO_VALID_CROSS_OR_BREAKOUT",
        "details": checks,
    }
