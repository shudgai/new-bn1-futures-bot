import pandas as pd

from core.engine import detect_ma_angle_pivot


def _frame(ma3, ma5, atr=1.0, ma25=None):
    rows = len(ma3)
    return pd.DataFrame({
        "timestamp": list(range(rows)),
        "open": [100.0] * rows,
        "high": [101.0] * rows,
        "low": [99.0] * rows,
        "close": [100.0] * rows,
        "atr": [atr] * rows,
        "ma3": ma3,
        "ma5": ma5,
        "ma25": ma25 if ma25 is not None else [100.0] * rows,
    })


def test_small_ma_wiggle_does_not_change_direction():
    result = detect_ma_angle_pivot(_frame(
        [100.00, 100.05, 100.10, 100.08, 100.06, 100.07, 100.07],
        [100.00, 100.02, 100.04, 100.035, 100.030, 100.035, 100.035],
    ))
    assert result["side"] is None


def test_accumulated_peak_angle_opens_short_without_ma25_cross():
    result = detect_ma_angle_pivot(_frame(
        [100.00, 100.08, 100.18, 100.14, 100.07, 99.99, 99.99],
        [99.98, 100.02, 100.07, 100.06, 100.03, 99.99, 99.99],
    ))
    assert result["side"] == "SHORT"
    assert result["entry_type"] == "PEAK_ANGLE_DOWN"


def test_small_green_after_confirmed_peak_keeps_short_direction():
    result = detect_ma_angle_pivot(_frame(
        [100.00, 100.08, 100.18, 100.10, 100.02, 100.04, 100.04],
        [99.98, 100.02, 100.07, 100.04, 100.00, 100.01, 100.01],
    ))
    assert result["side"] == "SHORT"


def test_accumulated_trough_angle_opens_long_without_ma25_cross():
    result = detect_ma_angle_pivot(_frame(
        [100.18, 100.10, 100.00, 100.04, 100.11, 100.19, 100.19],
        [100.20, 100.16, 100.11, 100.12, 100.15, 100.19, 100.19],
    ))
    assert result["side"] == "LONG"
    assert result["entry_type"] == "TROUGH_ANGLE_UP"


def test_small_red_after_confirmed_trough_keeps_long_direction():
    result = detect_ma_angle_pivot(_frame(
        [100.18, 100.10, 100.00, 100.08, 100.16, 100.14, 100.14],
        [100.20, 100.16, 100.11, 100.13, 100.18, 100.17, 100.17],
    ))
    assert result["side"] == "LONG"


def test_raw_ohlc_uses_market_atr_instead_of_price_percent_fallback():
    # 真實 fetch_klines() 沒有 atr 欄位。價格約 0.004 時，舊的 1.5%
    # fallback 是 0.00006，會把這個明顯的 1m 峰頂錯判成幅度不足。
    closes = [
        0.004062, 0.004064, 0.004066, 0.004068,
        0.004070, 0.004072, 0.004074, 0.004076, 0.004078,
        0.004080, 0.004090, 0.004105, 0.004120,
        0.004112, 0.004100, 0.004088, 0.004078, 0.004078,
    ]
    frame = pd.DataFrame({
        "timestamp": list(range(len(closes))),
        "open": closes,
        "high": [value + 0.000006 for value in closes],
        "low": [value - 0.000006 for value in closes],
        "close": closes,
    })

    result = detect_ma_angle_pivot(frame)

    assert result["side"] == "SHORT"
    assert result["atr"] < closes[-1] * 0.01


def test_stale_trough_is_not_used_for_late_long_entry():
    ma3 = [100.0 + index * 0.1 for index in range(13)]
    ma5 = [100.0 + index * 0.08 for index in range(13)]
    result = detect_ma_angle_pivot(_frame(ma3, ma5))
    assert result["side"] is None


def test_small_peak_pullback_above_rising_ma25_is_not_short():
    result = detect_ma_angle_pivot(_frame(
        [100.0, 100.4, 100.8, 100.6, 100.3, 100.2, 100.2],
        [100.0, 100.2, 100.5, 100.45, 100.3, 100.25, 100.25],
        ma25=[99.0, 99.1, 99.2, 99.3, 99.4, 99.5, 99.5],
    ))
    assert result["side"] is None


def test_decisive_peak_can_reverse_before_crossing_rising_ma25():
    result = detect_ma_angle_pivot(_frame(
        [100.0, 100.4, 100.9, 100.5, 100.1, 99.9, 99.9],
        [100.0, 100.2, 100.6, 100.5, 100.2, 100.0, 100.0],
        ma25=[99.0, 99.1, 99.2, 99.3, 99.4, 99.5, 99.5],
    ))
    assert result["side"] == "SHORT"
    assert result["entry_type"] == "PEAK_ANGLE_DOWN"
