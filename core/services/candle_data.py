"""Explicit candle finality at the market-data snapshot boundary."""

import re

import numpy as np
import pandas as pd


def mark_candle_closure(frame, timeframe, snapshot_ms):
    match = re.fullmatch(r"([1-9][0-9]*)([mhdw])", timeframe)
    if not match:
        raise ValueError("Unsupported candle timeframe")
    interval = int(match[1]) * {"m": 60000, "h": 3600000, "d": 86400000, "w": 604800000}[match[2]]
    result = frame.copy()
    stamps = pd.to_numeric(result["timestamp"], errors="coerce")
    result["is_closed"] = stamps.notna() & (stamps + interval <= snapshot_ms)
    result.attrs["timeframe_ms"] = interval
    result.attrs["snapshot_ms"] = snapshot_ms
    return result


def closed_entry_candles(frame):
    """Use explicit finality; old injected frames conservatively omit the tail.

    Never reclassify a cached live candle using a later wall-clock time.
    A closed-only snapshot retains its last completed candle.
    """
    if frame is None or frame.empty:
        return pd.DataFrame()
    if "is_closed" in frame:
        mask = frame["is_closed"].map(lambda value: isinstance(value, (bool, np.bool_)) and bool(value))
        result = frame.loc[mask].copy()
        # Invalid ordering or a closed row after an unfinished row is not a snapshot.
        if mask.any() and not mask.iloc[:int(np.flatnonzero(mask.to_numpy())[-1])+1].all():
            return frame.iloc[:0].copy()
    else:
        result = frame.iloc[:-1].copy()
        result["is_closed"] = True
    if "timestamp" in result:
        stamps = pd.to_numeric(result["timestamp"], errors="coerce")
        if stamps.isna().any() or not stamps.is_monotonic_increasing or stamps.duplicated().any():
            return result.iloc[:0]
        interval = frame.attrs.get("timeframe_ms")
        if interval and len(stamps) >= 2 and stamps.iloc[-1]-stamps.iloc[-2] != interval:
            return result.iloc[:0]
    return result


def log_entry_gate(engine, symbol, side, stage, reason, bar_id=None, **details):
    """Bounded last-decision cache: expose silent gates without per-tick spam."""
    cache = getattr(engine, "_entry_gate_diagnostics", None)
    if not isinstance(cache, dict):
        cache = engine._entry_gate_diagnostics = {}
    key = (symbol, side, stage)
    fingerprint = (bar_id, reason)
    if cache.get(key) == fingerprint:
        return
    cache[key] = fingerprint
    text = f"ENTRY_GATE stage={stage} symbol={symbol} side={side} bar={bar_id} reason={reason}"
    if details:
        text += " " + " ".join(f"{key}={value}" for key, value in details.items())
    engine.account.log(text, "INFO")
    print(text, flush=True)


def closed_entry_problem(frame):
    required = {"open", "high", "low", "close", "kc_upper", "kc_lower", "atr"}
    if not required.issubset(frame.columns):
        return "WAIT_INVALID_MARKET_DATA"
    try:
        values = frame.iloc[-2:][list(required)].astype(float)
        if not np.isfinite(values.to_numpy()).all() or (values <= 0).any().any():
            return "WAIT_INVALID_MARKET_DATA"
        if (values["kc_lower"] >= values["kc_upper"]).any():
            return "WAIT_INVALID_MARKET_DATA"
    except (TypeError, ValueError):
        return "WAIT_INVALID_MARKET_DATA"
    return None
