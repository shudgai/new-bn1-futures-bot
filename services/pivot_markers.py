"""Read-only pivot signal markers at the first quote after closed confirmation."""
from typing import Any

import pandas as pd

from core.services.strategies.outer_strategy import aligned_entry
from core.services.strategies.pivot_strategy import PIVOT_CODES


def build_pivot_markers(indicators: pd.DataFrame) -> dict[Any, list[dict]]:
    """Replay opening quotes without using later candle extrema or account state."""
    markers: dict[Any, list[dict]] = {}
    for position in range(3, len(indicators)):
        # Only the new candle's timestamp and opening quote existed at this instant.
        current = indicators.iloc[position]
        timestamp = float(current["timestamp"])
        confirmed_at = float(indicators.iloc[position - 1]["timestamp"]) + 60_000
        if timestamp != confirmed_at:
            continue
        frame = indicators.iloc[:position + 1].copy()
        frame.iloc[-1] = frame.iloc[-2]
        price = float(current["open"])
        for key in ("open", "high", "low", "close"):
            frame.loc[frame.index[-1], key] = price
        frame.loc[frame.index[-1], "timestamp"] = timestamp
        decision = aligned_entry(frame, price)
        if decision.get("action") != "ENTER" or decision.get("reason") not in PIVOT_CODES:
            continue
        side = decision["side"]
        markers[indicators.index[position]] = [{
            "side": side, "reason": decision["reason"],
            # Older open browser tabs still interpolate this retired field.
            "alignment": "",
            "timestamp": timestamp, "confirmed_at": confirmed_at,
            "kind": "strategy_signal",
        }]
    return markers
