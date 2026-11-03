"""Shared evidence checks for confirmed adverse candles after entry."""

from __future__ import annotations

import math
import logging
import time

import numpy as np
import pandas as pd


def live_adverse_waterfall_evidence(position, frame, quote, *, now_ms=None, max_staleness_ms=180_000):
    """Validate a current adverse body against the preceding completed ATR."""
    logger = logging.getLogger(__name__)
    try:
        from core.services.candle_data import closed_entry_candles

        if not isinstance(frame, pd.DataFrame) or len(frame) < 2:
            raise ValueError("current and preceding candles required")
        side = str(position.get("side") or "").upper()
        if side not in ("LONG", "SHORT"):
            raise ValueError("invalid position side")
        now = time.time() * 1000 if now_ms is None else float(now_ms)
        snapshot = float(frame.attrs["snapshot_ms"])
        interval = float(frame.attrs["timeframe_ms"])
        if (interval != 60_000 or not math.isfinite(now)
                or not 0 <= now - snapshot <= max_staleness_ms):
            raise ValueError("stale or invalid snapshot")
        closed = closed_entry_candles(frame)
        if closed.empty:
            raise ValueError("preceding completed candle required")
        current, previous = frame.iloc[-1], frame.iloc[-2]
        stamp, prior_stamp = float(current["timestamp"]), float(previous["timestamp"])
        if (not all(math.isfinite(v) and v >= 1e11 for v in (stamp, prior_stamp))
                or stamp - prior_stamp != interval or prior_stamp not in closed["timestamp"].values
                or prior_stamp + interval > snapshot
                or stamp > snapshot
                or stamp != math.floor(now / interval) * interval):
            raise ValueError("invalid candle chronology")
        if (not isinstance(current.get("is_closed"), (bool, np.bool_))
                or bool(current["is_closed"])):
            raise ValueError("current live candle finality required")
        opened = float(position["open_timestamp"])
        opened_ms = opened * 1000 if opened < 1e11 else opened
        if (not math.isfinite(opened_ms) or opened_ms <= 0 or opened_ms > now
                or stamp < math.floor(opened_ms / interval) * interval):
            raise ValueError("waterfall predates position")
        opening, middle, atr, price = (
            float(current["open"]), float(current["kc_middle"]),
            float(previous["atr"]), float(quote),
        )
        for candle in (previous, current):
            o, h, l, c = (float(candle[key]) for key in ("open", "high", "low", "close"))
            if (not all(math.isfinite(v) and v > 0 for v in (o, h, l, c))
                    or h < max(o, c) or l > min(o, c) or h < l):
                raise ValueError("invalid OHLC")
        if not all(math.isfinite(v) and v > 0 for v in (opening, middle, atr, price)):
            raise ValueError("invalid price or preceding ATR")
        sign = 1 if side == "LONG" else -1
        body = sign * (opening - price)
        threshold = 1.5 * atr
        if body < threshold and not math.isclose(body, threshold, rel_tol=1e-12, abs_tol=0):
            return None
        if sign * (price - middle) >= 0:
            return None
        return {
            "exit_type": "ABNORMAL_CANDLE", "trigger": "LIVE_ADVERSE_WATERFALL",
            "side": side, "body": body, "atr": atr, "threshold": threshold,
            "kc_middle": middle, "quote": price, "candle_open": opening,
            "bar_timestamp": stamp, "reference_bar_timestamp": prior_stamp,
        }
    except (AttributeError, KeyError, TypeError, ValueError, IndexError, OverflowError) as exc:
        symbol = position.get("symbol") if isinstance(position, dict) else None
        logger.warning("WATERFALL_EVIDENCE_INVALID symbol=%s error=%s", symbol, exc)
        return None


def two_closed_adverse_abnormal_evidence(position, frame):
    """Require two adjacent, post-entry closed adverse bodies of at least 0.5 ATR each."""
    try:
        from core.config import RAPID_PIVOT_IMMEDIATE_REVERSE_BODY_ATR
        from core.services.candle_data import closed_entry_candles

        side = str((position or {}).get("side") or "").upper()
        if side not in ("LONG", "SHORT") or not isinstance(frame, pd.DataFrame):
            return None
        timeframe_ms = float(frame.attrs.get("timeframe_ms", 60_000) or 60_000)
        if not math.isfinite(timeframe_ms) or timeframe_ms != 60_000:
            return None
        closed = closed_entry_candles(frame)
        if len(closed) < 3:
            return None

        opened_at = float(
            (position or {}).get("open_timestamp")
            or (position or {}).get("entry_timestamp")
            or 0.0
        )
        if not math.isfinite(opened_at) or opened_at <= 0:
            return None
        opened_ms = opened_at * 1000.0 if opened_at < 1e11 else opened_at
        entry_bar = math.floor(opened_ms / timeframe_ms) * timeframe_ms

        before, first, second = (closed.iloc[-3], closed.iloc[-2], closed.iloc[-1])
        stamps = [float(row.get("timestamp") or 0.0) for row in (before, first, second)]
        if any(not math.isfinite(stamp) or stamp <= 0 for stamp in stamps):
            return None
        if stamps[1] - stamps[0] != timeframe_ms or stamps[2] - stamps[1] != timeframe_ms:
            return None
        if stamps[1] <= entry_bar or stamps[2] <= entry_bar:
            return None

        sign = 1.0 if side == "LONG" else -1.0
        bodies = []
        for prior, candle in ((before, first), (first, second)):
            opening = float(candle["open"])
            closing = float(candle["close"])
            atr = float(prior["atr"])
            if not all(math.isfinite(value) and value > 0 for value in (opening, closing, atr)):
                return None
            adverse_body = -sign * (closing - opening)
            threshold = float(RAPID_PIVOT_IMMEDIATE_REVERSE_BODY_ATR) * atr
            if not math.isfinite(threshold) or threshold <= 0 or adverse_body < threshold:
                return None
            bodies.append({"body": adverse_body, "atr": atr, "threshold": threshold})

        return {
            "trigger": "TWO_CLOSED_ADVERSE_ABNORMAL",
            "side": side,
            "first_bar_ms": stamps[1],
            "second_bar_ms": stamps[2],
            "first_body": bodies[0]["body"],
            "first_atr": bodies[0]["atr"],
            "first_threshold": bodies[0]["threshold"],
            "second_body": bodies[1]["body"],
            "second_atr": bodies[1]["atr"],
            "second_threshold": bodies[1]["threshold"],
            "body_atr_multiple": float(RAPID_PIVOT_IMMEDIATE_REVERSE_BODY_ATR),
        }
    except (AttributeError, KeyError, TypeError, ValueError, IndexError, OverflowError):
        return None
