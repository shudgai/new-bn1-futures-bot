import math

from core.services.candle_data import closed_entry_candles


def has_completed_post_entry_bar(position, frame, default_timeframe_ms=60_000):
    """Return whether a completed candle began after the position's entry candle."""
    if not isinstance(position, dict) or frame is None or frame.empty:
        return False

    try:
        timeframe_ms = float(frame.attrs.get("timeframe_ms", default_timeframe_ms))
    except (TypeError, ValueError, OverflowError):
        return False
    if not math.isfinite(timeframe_ms) or timeframe_ms <= 0:
        return False

    closed = closed_entry_candles(frame)
    if closed.empty or "timestamp" not in closed:
        return False

    try:
        latest_closed_ms = float(closed.iloc[-1]["timestamp"])
    except (TypeError, ValueError, OverflowError):
        return False
    if not math.isfinite(latest_closed_ms):
        return False

    entry_timestamp = position.get("open_timestamp")
    if entry_timestamp is None:
        entry_timestamp = position.get("entry_timestamp")
    try:
        entry_timestamp = float(entry_timestamp)
    except (TypeError, ValueError, OverflowError):
        entry_timestamp = math.nan

    if math.isfinite(entry_timestamp) and entry_timestamp > 0:
        entry_timestamp_ms = entry_timestamp * 1000 if entry_timestamp < 1e11 else entry_timestamp
        entry_bar_start_ms = math.floor(entry_timestamp_ms / timeframe_ms) * timeframe_ms
    else:
        entry_bar_start_ms = math.nan
        for key in ("entry_bar_id", "open_bar_id"):
            try:
                entry_bar_start_ms = float(position.get(key))
            except (TypeError, ValueError, OverflowError):
                continue
            if math.isfinite(entry_bar_start_ms):
                break
        if not math.isfinite(entry_bar_start_ms):
            signal_bar_id = math.nan
            for key in ("candidate_bar_id", "channel_confirmation_bar_id"):
                try:
                    signal_bar_id = float(position.get(key))
                except (TypeError, ValueError, OverflowError):
                    continue
                if math.isfinite(signal_bar_id):
                    break
            if not math.isfinite(signal_bar_id):
                return False
            entry_bar_start_ms = signal_bar_id + timeframe_ms

    return latest_closed_ms >= entry_bar_start_ms + timeframe_ms
