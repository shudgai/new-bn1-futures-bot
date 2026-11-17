"""Fail-closed Keltner channel alignment gate for automated entries."""

import numpy as np
import pandas as pd

from core.services.candle_data import closed_entry_candles


KC_OVERALL_UP_ALIGNED = "UP_ALIGNED"
KC_OVERALL_DOWN_ALIGNED = "DOWN_ALIGNED"
KC_OVERALL_CHOP_BOX = "CHOP_BOX"
LONG_SLOPE_LOOKBACK = 15


def check_kc_overall_structure(df: pd.DataFrame) -> str:
    """Classify closed KC bands as aligned up, aligned down, or blocked."""
    if not isinstance(df, pd.DataFrame) or df.empty:
        return KC_OVERALL_CHOP_BOX

    closed = closed_entry_candles(df)
    required = ("kc_upper", "kc_middle", "kc_lower", "atr")
    if len(closed) < 8 or not all(column in closed.columns for column in required):
        return KC_OVERALL_CHOP_BOX

    values = closed.loc[:, required].apply(pd.to_numeric, errors="coerce")
    recent = values.tail(8)
    if (
        not np.isfinite(recent.to_numpy(dtype=float)).all()
        or (recent["atr"] <= 0).any()
        or (recent["kc_upper"] <= recent["kc_middle"]).any()
        or (recent["kc_middle"] <= recent["kc_lower"]).any()
    ):
        return KC_OVERALL_CHOP_BOX

    upper = recent["kc_upper"]
    middle = recent["kc_middle"]
    lower = recent["kc_lower"]
    atr = float(recent["atr"].iloc[-1])
    upper_range = float(upper.max() - upper.min())
    lower_range = float(lower.max() - lower.min())
    if upper_range < 0.1 * atr and lower_range < 0.1 * atr:
        return KC_OVERALL_CHOP_BOX

    upper_change = float(values["kc_upper"].iloc[-1] - values["kc_upper"].iloc[-3])
    middle_change = float(values["kc_middle"].iloc[-1] - values["kc_middle"].iloc[-3])
    lower_change = float(values["kc_lower"].iloc[-1] - values["kc_lower"].iloc[-3])
    if upper_change > 0 and middle_change > 0 and lower_change > 0:
        return KC_OVERALL_UP_ALIGNED
    if upper_change < 0 and middle_change < 0 and lower_change < 0:
        return KC_OVERALL_DOWN_ALIGNED
    return KC_OVERALL_CHOP_BOX


def kc_midline_slope_evidence(df: pd.DataFrame) -> dict[str, float] | None:
    if not isinstance(df, pd.DataFrame) or df.empty:
        return None
    closed = closed_entry_candles(df)
    if (
        len(closed) < LONG_SLOPE_LOOKBACK
        or "kc_middle" not in closed.columns
    ):
        return None
    middle = pd.to_numeric(closed["kc_middle"], errors="coerce")
    if not np.isfinite(middle.tail(LONG_SLOPE_LOOKBACK).to_numpy(dtype=float)).all():
        return None

    return {
        "short_slope": float(middle.iloc[-1] - middle.iloc[-3]),
        "long_slope": float(
            middle.iloc[-1] - middle.iloc[-LONG_SLOPE_LOOKBACK]
        ),
    }
