"""Closed-candle confirmation for real meme-coin pressure exits."""

import math

import pandas as pd

from core.services.candle_data import closed_entry_candles


def evaluate_confirmed_pressure_exit(position, frame, side=None):
    """Return a symmetric pressure exit only on a valid post-entry closed bar.

    LONG exits classify selling pressure. SHORT exits classify buying pressure.
    Small adverse candles above either key MA, or candles with a meaningful
    supporting wick, are treated as healthy pullbacks and do not exit.
    """
    result = {"reason": None, "details": {"bar_closed": False}}
    side = str(side or (position or {}).get("side") or "").upper()
    if side not in ("LONG", "SHORT") or frame is None:
        return result

    try:
        closed = closed_entry_candles(frame)
        if closed.empty:
            return result
        row = closed.iloc[-1]
        required = (
            "open", "high", "low", "close", "ma3", "ma15", "atr",
            "kc_upper", "kc_lower", "kc_middle",
        )
        values = {key: float(row[key]) for key in required}
        if not all(math.isfinite(value) for value in values.values()):
            return result
        if any(values[key] <= 0 for key in required):
            return result
        opening, high, low, close = (
            values[key] for key in ("open", "high", "low", "close")
        )
        atr = values["atr"]
        if not low <= min(opening, close) <= max(opening, close) <= high:
            return result
        if values["kc_lower"] >= values["kc_upper"]:
            return result

        # Do not use a closed candle from before or during the entry candle.
        entry_time = next((
            (position or {}).get(key) for key in (
                "open_timestamp", "entry_timestamp", "open_bar_id", "entry_bar_id",
            ) if (position or {}).get(key) is not None
        ), None)
        stamp = float(row.get("timestamp") or 0.0)
        if not math.isfinite(stamp) or stamp <= 0 or entry_time is None:
            return result
        entry_time = float(entry_time)
        if not math.isfinite(entry_time) or entry_time <= 0:
            return result
        timeframe_ms = float(getattr(frame, "attrs", {}).get("timeframe_ms", 60_000) or 60_000)
        if not math.isfinite(timeframe_ms) or timeframe_ms <= 0:
            return result
        entry_ms = entry_time * 1000.0 if entry_time < 1e11 else entry_time
        candle_ms = stamp * 1000.0 if stamp < 1e11 else stamp
        entry_bar_floor = math.floor(entry_ms / timeframe_ms) * timeframe_ms
        if candle_ms <= entry_bar_floor:
            return result

        body = abs(close - opening)
        upper_wick = high - max(opening, close)
        lower_wick = min(opening, close) - low
        volume_ratio = None
        volume_reference = None
        if "volume" in closed:
            try:
                current_volume = float(row.get("volume", 0.0))
                prior = closed.iloc[:-1]
                if side == "LONG":
                    prior = prior[pd.to_numeric(prior["close"], errors="coerce")
                                  > pd.to_numeric(prior["open"], errors="coerce")]
                else:
                    prior = prior[pd.to_numeric(prior["close"], errors="coerce")
                                  < pd.to_numeric(prior["open"], errors="coerce")]
                if not prior.empty:
                    volume_reference = float(prior.iloc[-1].get("volume", 0.0))
                if (volume_reference is not None
                        and math.isfinite(volume_reference) and volume_reference > 0
                        and math.isfinite(current_volume) and current_volume >= 0):
                    volume_ratio = current_volume / volume_reference
            except (TypeError, ValueError, IndexError, OverflowError):
                volume_ratio = None

        details = {
            "bar_closed": True,
            "bar_id": stamp,
            "side": side,
            "open": opening,
            "high": high,
            "low": low,
            "close": close,
            "body": body,
            "upper_wick": upper_wick,
            "lower_wick": lower_wick,
            "atr": atr,
            "ma3": values["ma3"],
            "ma15": values["ma15"],
            "kc_upper": values["kc_upper"],
            "kc_lower": values["kc_lower"],
            "kc_middle": values["kc_middle"],
            "volume_reference": volume_reference,
            "volume_ratio_vs_previous": volume_ratio,
        }
        volume_confirmed = volume_ratio is not None and volume_ratio >= 1.5
        details["volume_confirmed"] = volume_confirmed
        result["details"] = details

        if side == "LONG":
            adverse = close < opening
            structural = adverse and close < values["ma3"] and close < values["ma15"]
            blowoff = (
                adverse and high > values["kc_upper"]
                and upper_wick > 1.5 * body
            )
            heavy = adverse and body > 0.5 * atr and close < values["ma3"]
            healthy = (
                lower_wick >= 0.5 * body
                or (not blowoff and body <= 0.5 * atr and (
                    close >= values["ma3"] or close >= values["ma15"]
                ))
            )
            if healthy:
                details["classification"] = "HEALTHY_PULLBACK"
                return result
            if structural or blowoff or heavy:
                details.update(
                    classification="CONFIRMED_SELLING_PRESSURE",
                    structural_break=structural,
                    exhaustion_blowoff=blowoff,
                    heavy_selling=heavy,
                )
                result["reason"] = "EXIT_BY_CONFIRMED_SELLING_PRESSURE"
                return result
        else:
            adverse = close > opening
            structural = adverse and close > values["ma3"] and close > values["ma15"]
            blowoff = (
                adverse and low < values["kc_lower"]
                and lower_wick > 1.5 * body
            )
            heavy = adverse and body > 0.5 * atr and close > values["ma3"]
            healthy = (
                upper_wick >= 0.5 * body
                or (not blowoff and body <= 0.5 * atr and (
                    close <= values["ma3"] or close <= values["ma15"]
                ))
            )
            if healthy:
                details["classification"] = "HEALTHY_PULLBACK"
                return result
            if structural or blowoff or heavy:
                details.update(
                    classification="CONFIRMED_BUYING_PRESSURE",
                    structural_break=structural,
                    exhaustion_blowoff=blowoff,
                    heavy_selling=heavy,
                )
                result["reason"] = "EXIT_BY_CONFIRMED_BUYING_PRESSURE"
                return result
        details["classification"] = "NO_CONFIRMED_PRESSURE"
        return result
    except (AttributeError, KeyError, TypeError, ValueError, IndexError, OverflowError):
        return result
