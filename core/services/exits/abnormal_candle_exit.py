"""Shared evidence checks for confirmed adverse candles after entry."""

from __future__ import annotations

import math

import pandas as pd


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
