"""Multi-timeframe KC entry and position-protection rules."""

from __future__ import annotations

import math
import logging
import time
from collections.abc import Mapping
from typing import Any, Optional

import numpy as np
import pandas as pd

SLOPE_THRESHOLD = 0.005
MAX_ENTRY_BAND_RATIO = 1.0
INITIAL_STOP_ATR = 2.0
RATCHET_ARM_ATR = 1.5
RATCHET_GIVEBACK_ATR = 1.0
MTF_KC_STATE_MACHINE = "MTF_KC_STATE_MACHINE"
MTF_KC_EXIT_REASON_PREFIX = "MTF_KC_STATE_MACHINE_"


def _number(row: Mapping[str, Any], key: str) -> Optional[float]:
    try:
        value = float(row[key])
    except (KeyError, TypeError, ValueError, OverflowError):
        return None
    return value if math.isfinite(value) else None


def _snapshot_is_fresh(frame: pd.DataFrame, interval_ms: float) -> bool:
    attrs = getattr(frame, "attrs", {})
    try:
        snapshot_ms = float(attrs["snapshot_ms"])
        if snapshot_ms < 1e11:
            snapshot_ms *= 1000.0
        timeframe_ms = float(attrs["timeframe_ms"])
    except (KeyError, TypeError, ValueError, OverflowError):
        return False
    if (
        not math.isfinite(snapshot_ms)
        or not math.isclose(timeframe_ms, interval_ms, rel_tol=0, abs_tol=0)
        or snapshot_ms <= 0
    ):
        return False
    now_ms = time.time() * 1000.0
    return 0 <= now_ms - snapshot_ms <= 180_000


def _valid_live_frame(frame: Any, interval_ms: float) -> tuple[pd.DataFrame, pd.Series] | None:
    from core.services.candle_data import closed_entry_candles

    if not isinstance(frame, pd.DataFrame) or len(frame) < 2:
        return None
    attrs = getattr(frame, "attrs", {})
    if not _snapshot_is_fresh(frame, interval_ms):
        return None
    live = frame.iloc[-1]
    previous = frame.iloc[-2]
    live_closed = live.get("is_closed")
    previous_closed = previous.get("is_closed")
    if (
        not isinstance(live_closed, (bool, np.bool_)) or bool(live_closed)
        or not isinstance(previous_closed, (bool, np.bool_)) or not bool(previous_closed)
    ):
        return None
    if not math.isclose(float(attrs.get("timeframe_ms", 0)), interval_ms, rel_tol=0, abs_tol=0):
        return None
    closed = closed_entry_candles(frame)
    if closed.empty:
        return None
    try:
        live_stamp = float(live["timestamp"])
        closed_stamp = float(closed.iloc[-1]["timestamp"])
    except (KeyError, TypeError, ValueError, OverflowError):
        return None
    if not all(math.isfinite(value) and value > 0 for value in (live_stamp, closed_stamp)):
        return None
    allowed_gaps = (interval_ms, interval_ms / 1000.0)
    if not any(math.isclose(live_stamp - closed_stamp, gap, rel_tol=0, abs_tol=1) for gap in allowed_gaps):
        return None
    return closed, live


def evaluate_htf_kc_state(frame_15m: Any) -> dict[str, Any]:
    """Classify 15m KC direction from the current, still-forming candle."""
    data = _valid_live_frame(frame_15m, 900_000.0)
    if data is None:
        return {"valid": False, "reason": "BLOCKED_HTF_KC_LIVE_DATA_INVALID"}
    _closed, current = data
    previous = frame_15m.iloc[-2]
    live_ohlc = tuple(_number(current, key) for key in ("open", "high", "low", "close"))
    middle_current = _number(current, "kc_middle")
    middle_previous = _number(previous, "kc_middle")
    atr_current = _number(current, "atr")
    upper = _number(current, "kc_upper")
    lower = _number(current, "kc_lower")
    if (
        any(value is None for value in live_ohlc)
        or live_ohlc[1] < max(live_ohlc[0], live_ohlc[3])
        or live_ohlc[2] > min(live_ohlc[0], live_ohlc[3])
        or middle_current is None or middle_previous is None or atr_current is None
        or upper is None or lower is None or atr_current <= 0
        or not lower < middle_current < upper
    ):
        return {"valid": False, "reason": "BLOCKED_HTF_KC_INDICATORS_INVALID"}

    normalized_slope = (middle_current - middle_previous) / atr_current
    state = (
        "UPTREND" if normalized_slope > SLOPE_THRESHOLD
        else "DOWNTREND" if normalized_slope < -SLOPE_THRESHOLD
        else "FLAT"
    )
    return {
        "valid": True,
        "state": state,
        "normalized_slope": normalized_slope,
        "kc_middle": middle_current,
        "kc_upper": upper,
        "kc_lower": lower,
        "atr": atr_current,
        "bar_timestamp": _number(current, "timestamp"),
    }


def evaluate_mtf_entry(
    frame_1m: Any,
    frame_15m: Any,
    *,
    quote: float,
    side: str | None = None,
    pending_candidate: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Evaluate an entry inside a directional HTF KC cycle without side effects."""
    from core.services.ma5_entry_slope import quote_adjusted_entry_mas

    htf = evaluate_htf_kc_state(frame_15m)
    if not htf.get("valid"):
        return {**htf, "allowed": False, "reason": htf["reason"]}
    if htf["state"] == "FLAT":
        return {
            **htf,
            "allowed": False,
            "reason": "BLOCKED_MTF_KC_CYCLE_NOT_DIRECTIONAL",
        }
    data = _valid_live_frame(frame_1m, 60_000.0)
    if data is None or getattr(frame_1m, "attrs", {}).get("entry_finality_verified") is not True:
        return {
            **htf,
            "allowed": False,
            "reason": "BLOCKED_LTF_KC_LIVE_DATA_INVALID",
        }
    closed, live = data
    if len(closed) < 15:
        return {**htf, "allowed": False, "reason": "BLOCKED_LTF_MA_HISTORY_INVALID"}
    try:
        quote_value = float(quote)
    except (TypeError, ValueError, OverflowError):
        quote_value = math.nan
    if not math.isfinite(quote_value) or quote_value <= 0:
        return {**htf, "allowed": False, "reason": "BLOCKED_LIVE_QUOTE_INVALID"}

    ma_values = quote_adjusted_entry_mas(frame_1m, quote_value)
    if ma_values is None:
        return {**htf, "allowed": False, "reason": "BLOCKED_LIVE_MA_DATA_INVALID"}
    ma5_current, ma5_previous, ma15_current = map(float, ma_values)
    open_price = _number(live, "open")
    live_high = _number(live, "high")
    live_low = _number(live, "low")
    live_close = _number(live, "close")
    previous_atr = _number(closed.iloc[-1], "atr")
    live_ohlc = {
        "open": open_price,
        "high": live_high,
        "low": live_low,
        "close": live_close,
    }
    try:
        live_ohlc["close"] = quote_value
        live_ohlc["high"] = max(float(live_ohlc["high"]), quote_value)
        live_ohlc["low"] = min(float(live_ohlc["low"]), quote_value)
    except (TypeError, ValueError, OverflowError):
        return {**htf, "allowed": False, "reason": "BLOCKED_LIVE_OHLC_INVALID"}
    live_frame = pd.concat(
        [closed[["open", "high", "low", "close"]], pd.DataFrame([live_ohlc])],
        ignore_index=True,
    )
    from core.services.technical_indicators import compute_keltner_channels

    kc = compute_keltner_channels(live_frame, ema_period=20, atr_period=14, multiplier=1.5)
    middle = _number(kc.iloc[-1], "kc_middle")
    middle_previous = _number(kc.iloc[-2], "kc_middle") if len(kc) >= 2 else None
    upper = _number(kc.iloc[-1], "kc_upper")
    lower = _number(kc.iloc[-1], "kc_lower")
    values = (
        open_price, live_high, live_low, live_close, middle, middle_previous,
        upper, lower, previous_atr,
    )
    if (
        any(value is None for value in values)
        or live_high < max(open_price, live_close)
        or live_low > min(open_price, live_close)
        or open_price <= 0 or middle <= 0 or lower <= 0 or upper <= lower
        or middle_previous <= 0 or not lower < middle < upper or previous_atr <= 0
    ):
        return {**htf, "allowed": False, "reason": "BLOCKED_LTF_KC_INDICATORS_INVALID"}

    expected_side = "LONG" if htf["state"] == "UPTREND" else "SHORT"
    from core.services.entry_contract import ma5_ma15_kc_midline_entry_problem
    alignment_problem = ma5_ma15_kc_midline_entry_problem(
        expected_side, ma5_current, ma15_current, middle, previous_atr,
        middle_previous,
    )
    if alignment_problem:
        return {**htf, "allowed": False, "reason": alignment_problem}

    half_width = middle - lower
    bandwidth_pct = (upper - lower) / middle
    if half_width <= 0:
        return {
            **htf,
            "allowed": False,
            "reason": "BLOCKED_LTF_KC_EXTREME_NARROW",
            "bandwidth_pct": bandwidth_pct,
        }

    normalized_side = str(side or "").upper()
    sides = (normalized_side,) if normalized_side in ("LONG", "SHORT") else ("LONG", "SHORT")
    live_bar_id = _number(live, "timestamp")
    deferred_wait: dict[str, Any] | None = None
    for candidate_side in sides:
        is_long = candidate_side == "LONG"
        expected_state = "UPTREND" if is_long else "DOWNTREND"
        if htf["state"] != expected_state:
            continue
        distance = quote_value - middle if is_long else middle - quote_value
        ratio = distance / half_width

        if is_long:
            ma_slope_aligned = ma5_current > ma5_previous
            candle_aligned = quote_value > open_price
            breakout_edge_offset = quote_value - upper if quote_value > upper else 0.0
            breakout_allowed = (
                quote_value > upper
                and 0.0 < breakout_edge_offset <= 0.35 * half_width
                and ma_slope_aligned
            )
        else:
            ma_slope_aligned = ma5_current < ma5_previous
            candle_aligned = quote_value < open_price
            breakout_edge_offset = lower - quote_value if quote_value < lower else 0.0
            breakout_allowed = (
                quote_value < lower
                and 0.0 < breakout_edge_offset <= 0.35 * half_width
                and ma_slope_aligned
            )

        location_allowed = -1.0 < ratio < MAX_ENTRY_BAND_RATIO

        pending_matches_next_bar = False
        pending_source_state = ""
        if (
            isinstance(pending_candidate, Mapping)
            and str(pending_candidate.get("side") or "").upper() == candidate_side
            and live_bar_id is not None
        ):
            try:
                pending_source_bar = float(pending_candidate.get("source_bar_id"))
                pending_source_state = str(pending_candidate.get("state") or "").upper()
                pending_matches_next_bar = (
                    math.isfinite(pending_source_bar)
                    and math.isclose(
                        live_bar_id - pending_source_bar,
                        60_000.0,
                        rel_tol=0,
                        abs_tol=1,
                    )
                )
            except (TypeError, ValueError, OverflowError):
                pending_matches_next_bar = False

        if pending_matches_next_bar and not breakout_allowed:
            state_still_valid = (
                htf["state"] == expected_state
                and pending_source_state == expected_state
            )
            if (
                state_still_valid
                and location_allowed
                and ma_slope_aligned
            ):
                if candle_aligned:
                    return {
                        **htf,
                        "allowed": True,
                        "reason": "MTF_KC_DEFERRED_ENTRY",
                        "side": candidate_side,
                        "price": quote_value,
                        "open": open_price,
                        "ma5": ma5_current,
                        "previous_ma5": ma5_previous,
                        "ma15": ma15_current,
                        "kc_middle_1m": middle,
                        "kc_upper_1m": upper,
                        "kc_lower_1m": lower,
                        "half_width_1m": half_width,
                        "entry_band_ratio": ratio,
                        "ltf_atr": previous_atr,
                        "bandwidth_pct": bandwidth_pct,
                        "deferred_candidate": dict(pending_candidate),
                    }
                deferred_wait = {
                    **htf,
                    "allowed": False,
                    "reason": "WAIT_MTF_KC_DEFERRED_NEXT_BAR_COLOR",
                    "side": candidate_side,
                    "price": quote_value,
                    "open": open_price,
                    "ma5": ma5_current,
                    "previous_ma5": ma5_previous,
                    "ma15": ma15_current,
                    "kc_middle_1m": middle,
                    "entry_band_ratio": ratio,
                    "deferred_candidate": dict(pending_candidate),
                }

        # A fresh outside-band breakout is evaluated immediately without
        # requiring candle color, while retaining the extension cap.
        if not breakout_allowed and (not location_allowed or not ma_slope_aligned):
            continue

        if not breakout_allowed and not candle_aligned:
            if pending_matches_next_bar:
                continue
            if live_bar_id is None:
                continue
            deferred_wait = {
                **htf,
                "allowed": False,
                "reason": "WAIT_MTF_KC_NEXT_BAR_COLOR",
                "side": candidate_side,
                "price": quote_value,
                "open": open_price,
                "ma5": ma5_current,
                "previous_ma5": ma5_previous,
                "ma15": ma15_current,
                "kc_middle_1m": middle,
                "entry_band_ratio": ratio,
                "deferred_candidate": {
                    "side": candidate_side,
                    "source_bar_id": live_bar_id,
                    "state": htf["state"],
                    "reason": "MTF_KC_TREND_ENTRY",
                },
            }
            continue

        return {
            **htf,
            "allowed": True,
            "reason": (
                "MTF_KC_BREAKOUT_ENTRY"
                if breakout_allowed
                else "MTF_KC_TREND_ENTRY"
            ),
            "side": candidate_side,
            "price": quote_value,
            "open": open_price,
            "ma5": ma5_current,
            "previous_ma5": ma5_previous,
            "ma15": ma15_current,
            "kc_middle_1m": middle,
            "kc_upper_1m": upper,
            "kc_lower_1m": lower,
            "half_width_1m": half_width,
            "entry_band_ratio": ratio,
            "ltf_atr": previous_atr,
            "bandwidth_pct": bandwidth_pct,
            "breakout_edge_offset": breakout_edge_offset,
        }

    if deferred_wait is not None:
        return deferred_wait

    return {
        **htf,
        "allowed": False,
        "reason": "BLOCKED_MTF_KC_ENTRY_CONDITIONS",
    }


def is_mtf_managed_position(
    position: Mapping[str, Any] | None,
    metadata: Mapping[str, Any] | None = None,
) -> bool:
    return any(
        isinstance(source, Mapping)
        and (
            source.get("mtf_kc_state_machine")
            or source.get("entry_method_authority") == MTF_KC_STATE_MACHINE
        )
        for source in (position, metadata)
    )


def update_mtf_position_protection(
    position: dict[str, Any], metadata: dict[str, Any], price: float,
) -> dict[str, Any] | None:
    """Update the 2 ATR hard stop and 1 ATR ratchet for an MTF-managed position."""
    if not is_mtf_managed_position(position, metadata):
        return None
    try:
        quote = float(price)
        entry = float(position.get("entry_price") or 0.0)
        atr = float(
            position.get("mtf_ltf_atr") or metadata.get("mtf_ltf_atr")
            or position.get("atr") or metadata.get("atr") or 0.0
        )
        side = str(position.get("side") or "").upper()
    except (TypeError, ValueError, OverflowError):
        return {"reason": "BLOCKED_MTF_KC_PROTECTION_DATA_INVALID"}
    if (
        not all(math.isfinite(value) and value > 0 for value in (quote, entry, atr))
        or side not in ("LONG", "SHORT")
    ):
        return {"reason": "BLOCKED_MTF_KC_PROTECTION_DATA_INVALID"}

    sign = 1.0 if side == "LONG" else -1.0
    favorable_price = max(entry, quote) if side == "LONG" else min(entry, quote)
    peak = position.get("mtf_peak_favorable_price", metadata.get("mtf_peak_favorable_price"))
    try:
        peak_value = float(peak) if peak is not None else entry
    except (TypeError, ValueError, OverflowError):
        peak_value = entry
    peak_value = max(peak_value, favorable_price) if side == "LONG" else min(peak_value, favorable_price)
    position["mtf_peak_favorable_price"] = peak_value
    metadata["mtf_peak_favorable_price"] = peak_value

    hard_stop = entry - INITIAL_STOP_ATR * atr if side == "LONG" else entry + INITIAL_STOP_ATR * atr
    favorable_move = sign * (peak_value - entry)
    ratchet_stop = None
    if favorable_move >= RATCHET_ARM_ATR * atr:
        ratchet_stop = (
            peak_value - RATCHET_GIVEBACK_ATR * atr
            if side == "LONG"
            else peak_value + RATCHET_GIVEBACK_ATR * atr
        )
    band = metadata.get("mtf_kc_lower_15m") if side == "LONG" else metadata.get("mtf_kc_upper_15m")
    try:
        band_value = float(band) if band is not None else None
    except (TypeError, ValueError, OverflowError):
        band_value = None
    if band_value is not None and (not math.isfinite(band_value) or band_value <= 0):
        band_value = None

    stop_candidates = [hard_stop]
    if ratchet_stop is not None:
        stop_candidates.append(ratchet_stop)
    if band_value is not None:
        stop_candidates.append(band_value)
    effective_stop = max(stop_candidates) if side == "LONG" else min(stop_candidates)
    position["mtf_kc_stop_price"] = effective_stop
    metadata["mtf_kc_stop_price"] = effective_stop
    if side == "LONG" and quote <= hard_stop or side == "SHORT" and quote >= hard_stop:
        return {"reason": "MTF_KC_STATE_MACHINE_HARD_STOP", "stop": hard_stop}
    if band_value is not None and (
        side == "LONG" and quote <= band_value
        or side == "SHORT" and quote >= band_value
    ):
        return {"reason": "MTF_KC_STATE_MACHINE_HTF_BAND_STOP", "stop": band_value}
    if ratchet_stop is not None and (
        side == "LONG" and quote <= ratchet_stop
        or side == "SHORT" and quote >= ratchet_stop
    ):
        return {"reason": "MTF_KC_STATE_MACHINE_RATCHET_STOP", "stop": ratchet_stop}
    return None


def authorize_mtf_exit(
    account: Any, symbol: str, reason: str, price: float, evidence: Mapping[str, Any],
) -> bool:
    meta = getattr(account, "position_meta", {}).get(symbol)
    if not isinstance(meta, dict):
        return False
    position = getattr(account, "positions", {}).get(symbol, {})
    if not is_mtf_managed_position(position, meta):
        return False
    meta["mtf_kc_exit_authority"] = {
        "reason": reason,
        "side": str(position.get("side") or "").upper(),
        "price": float(price),
        "evidence": dict(evidence),
        "created_at": time.time(),
    }
    save_state = getattr(account, "save_state", None)
    if callable(save_state):
        try:
            save_state()
        except Exception as exc:
            meta.pop("mtf_kc_exit_authority", None)
            logging.getLogger(__name__).error(
                "MTF_EXIT_AUTHORITY_PERSIST_FAILED symbol=%s reason=%s error=%s",
                symbol, reason, exc,
            )
            return False
    return True


def mtf_exit_is_authorized(
    metadata: Mapping[str, Any] | None,
    reason: str,
    price: float,
    side: str | None = None,
) -> bool:
    if not isinstance(metadata, Mapping):
        return False
    ticket = metadata.get("mtf_kc_exit_authority")
    if (
        not isinstance(ticket, Mapping)
        or ticket.get("reason") != reason
        or (
            side is not None
            and str(ticket.get("side") or "").upper() != str(side).upper()
        )
    ):
        return False
    try:
        ticket_price = float(ticket.get("price"))
        exit_price = float(price)
    except (TypeError, ValueError, OverflowError):
        return False
    return (
        math.isfinite(ticket_price) and ticket_price > 0
        and math.isfinite(exit_price) and exit_price > 0
        and math.isclose(ticket_price, exit_price, rel_tol=1e-12, abs_tol=0.0)
    )
