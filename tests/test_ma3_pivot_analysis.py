import pandas as pd

from services.ma3_pivot_analysis import analyze_ma3_pivots


def test_ma3_pivot_analysis_returns_atr_normalized_geometry():
    closes = [100, 99, 98, 97, 96, 95, 100, 105, 106, 107, 108, 109, 110, 111, 112, 113, 114]
    frame = pd.DataFrame({
        "timestamp": range(len(closes)), "open": closes,
        "high": [price + 0.4 for price in closes],
        "low": [price - 0.4 for price in closes], "close": closes,
    })
    report = analyze_ma3_pivots(frame, horizon_bars=3, target_atr=0.3, stop_atr=0.3)
    assert report["summary"]["pivots"] == 1
    pivot = report["recent_pivots"][0]
    assert pivot["side"] == "LONG"
    assert pivot["sharpness_atr"] > 0
    assert 0 < pivot["balance"] <= 1
    assert pivot["outcome"] == "success"
