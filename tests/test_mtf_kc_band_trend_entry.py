import time

import pandas as pd

from core.services import mtf_kc_state_machine as m


def _frame(upper_step: float, lower_step: float, close: float) -> pd.DataFrame:
    now = int(time.time() // 60) * 60_000
    rows = []
    for i in range(30):
        mid = 1.0
        rows.append({
            "timestamp": float(now - (29 - i) * 60_000),
            "open": close, "high": close + 0.001, "low": close - 0.001, "close": close,
            "kc_middle": mid, "kc_upper": 1.02 + upper_step * i, "kc_lower": 0.98 + lower_step * i,
            "atr": 0.01, "ma5": close, "ma15": close, "is_closed": i < 29,
        })
    df = pd.DataFrame(rows)
    df["is_closed"] = df["is_closed"].astype(bool)
    df.attrs.update(timeframe_ms=60_000.0, snapshot_ms=time.time() * 1000.0,
                    entry_finality_verified=True)
    return df


def test_long_when_upper_band_rises_and_htf_up():
    htf = {"valid": True, "state": "UPTREND", "closed_trend": "FLAT"}
    frame = _frame(0.0005, 0.0, 1.01)
    assert m.kc_band_trend_side(frame, htf) == "LONG"
    result = m._evaluate_kc_band_trend_entry(frame, htf, 1.01, "LONG")
    assert result["allowed"] and result["reason"] == m.MTF_KC_BAND_TREND_ENTRY


def test_short_mirror_when_lower_band_falls_and_htf_down():
    htf = {"valid": True, "state": "DOWNTREND", "closed_trend": "FLAT"}
    frame = _frame(0.0, -0.0005, 0.99)
    assert m.kc_band_trend_side(frame, htf) == "SHORT"
    assert m._evaluate_kc_band_trend_entry(frame, htf, 0.99, "SHORT")["side"] == "SHORT"


def test_no_entry_when_htf_opposes_or_chasing():
    frame = _frame(0.0005, 0.0, 1.01)
    assert m.kc_band_trend_side(frame, {"valid": True, "state": "DOWNTREND"}) is None
    htf = {"valid": True, "state": "UPTREND", "closed_trend": "UPTREND"}
    # quote more than 0.5 ATR above the upper band -> anti-chase veto
    assert m._evaluate_kc_band_trend_entry(frame, htf, 1.05, "LONG") is None
    # quote below 1m midline -> no long
    assert m._evaluate_kc_band_trend_entry(frame, htf, 0.995, "LONG") is None
