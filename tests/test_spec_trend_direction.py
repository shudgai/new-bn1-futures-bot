import pandas as pd

from core.engine import TradingEngine
from core.strategy import SuperTrendKeltnerStrategy


def _long_setup_frame():
    return pd.DataFrame({
        "open": [100.2, 100.1, 100.0, 99.9, 99.8, 99.0, 98.5, 99.2],
        "high": [100.4, 100.3, 100.2, 100.1, 100.0, 99.2, 99.3, 99.9],
        "low": [100.0, 99.9, 99.8, 99.7, 99.6, 98.0, 98.4, 99.1],
        "close": [100.1, 100.0, 99.9, 99.8, 99.7, 98.4, 99.1, 99.7],
        "ma3": [100.1, 100.0, 99.9, 99.8, 99.8, 99.4, 99.6, 99.8],
        "ma15": [99.0] * 8,
        "atr": [1.0] * 8,
        "volume": [1000.0] * 8,
        "vol_ma_20": [1000.0] * 8,
        "rsi": [50.0] * 8,
        "adx": [25.0] * 8,
        "ema_20": [100.0] * 8,
        "kc_upper": [101.0] * 8,
        "kc_lower": [99.0] * 8,
        "kc_width": [2.0] * 8,
        "st_direction": [1] * 8,
        "macd_hist": [0.0] * 8,
        "macd_line": [0.0] * 8,
        "macd_signal": [0.0] * 8,
        "ema_50": [100.0] * 8,
        "stoch_rsi_k": [50.0] * 8,
        "spec_kc_middle": [100.0] * 8,
        "spec_kc_upper": [101.0] * 8,
        "spec_kc_lower": [99.0] * 8,
        "spec_atr_10": [1.0] * 8,
    })


def _short_setup_frame():
    return pd.DataFrame({
        "open": [99.8, 99.9, 100.0, 100.1, 100.2, 101.0, 101.5, 100.8],
        "high": [100.0, 100.1, 100.2, 100.3, 100.4, 102.0, 101.6, 100.9],
        "low": [99.6, 99.7, 99.8, 99.9, 100.0, 100.8, 100.7, 100.2],
        "close": [99.9, 100.0, 100.1, 100.2, 100.3, 101.6, 100.9, 100.3],
        "ma3": [99.9, 100.0, 100.1, 100.2, 100.2, 100.6, 100.4, 100.2],
        "ma15": [101.0] * 8,
        "atr": [1.0] * 8,
        "volume": [1000.0] * 8,
        "vol_ma_20": [1000.0] * 8,
        "rsi": [50.0] * 8,
        "adx": [25.0] * 8,
        "ema_20": [100.0] * 8,
        "kc_upper": [101.0] * 8,
        "kc_lower": [99.0] * 8,
        "kc_width": [2.0] * 8,
        "st_direction": [1] * 8,
        "macd_hist": [0.0] * 8,
        "macd_line": [0.0] * 8,
        "macd_signal": [0.0] * 8,
        "ema_50": [100.0] * 8,
        "stoch_rsi_k": [50.0] * 8,
        "spec_kc_middle": [100.0] * 8,
        "spec_kc_upper": [101.0] * 8,
        "spec_kc_lower": [99.0] * 8,
        "spec_atr_10": [1.0] * 8,
    })


def test_ma3_pivot_long_uses_current_confirmation_path():
    frame = _long_setup_frame()
    frame["ma15"] = 100.0

    signal = SuperTrendKeltnerStrategy().evaluate_signal(
        frame, indicators_precomputed=True,
    )

    assert signal["action"] == "ENTER_MARKET"
    assert signal["side"] == "LONG"
    assert signal["entry_mode"] == "MA3_PIVOT"


def test_ma3_pivot_short_uses_current_confirmation_path():
    frame = _short_setup_frame()
    frame["ma15"] = 100.0

    signal = SuperTrendKeltnerStrategy().evaluate_signal(
        frame, indicators_precomputed=True,
    )

    assert signal["action"] == "ENTER_MARKET"
    assert signal["side"] == "SHORT"
    assert signal["entry_mode"] == "MA3_PIVOT"


def test_high_atr_candidate_is_blocked_before_pivot_entry():
    frame = _long_setup_frame()
    frame["ma15"] = 100.0
    frame["ma3"] = [100.1, 100.0, 100.10, 99.95, 100.08, 99.94, 100.07, 100.03]

    signal = SuperTrendKeltnerStrategy().evaluate_signal(
        frame, indicators_precomputed=True,
    )

    assert signal["action"] == "HOLD"
    assert "ATR_Too_High" in signal["reason"]


def test_progress_log_reports_current_direction_and_failed_qualification():
    detail = TradingEngine._format_signal_progress(
        "SPEC/USDT",
        {"action": "HOLD", "eligible": False, "score": 0,
         "reason": "Mandatory_Fail: MomentumCross_Not_Aligned(5m=1,1h=-1)"},
        "LONG",
    )

    assert "SPEC" in detail
    assert "多單" in detail
    assert "資格未通過" in detail


def test_pivot_candidate_expires_before_eight_bar_late_confirmation():
    rows = 11
    frame = pd.DataFrame({
        "open": [100.0] * rows,
        "high": [100.2] * rows,
        "low": [99.5] * rows,
        "close": [99.8] * rows,
        "ma3": [100.0] * rows,
        "ma15": [99.0] * rows,
        "atr": [1.0] * rows,
        "volume": [1000.0] * rows,
        "vol_ma_20": [1000.0] * rows,
        "rsi": [50.0] * rows,
        "adx": [25.0] * rows,
        "ema_20": [100.0] * rows,
        "kc_upper": [101.0] * rows,
        "kc_lower": [99.0] * rows,
        "kc_width": [2.0] * rows,
        "st_direction": [1] * rows,
        "macd_hist": [0.0] * rows,
        "macd_line": [0.0] * rows,
        "macd_signal": [0.0] * rows,
        "ema_50": [100.0] * rows,
        "stoch_rsi_k": [50.0] * rows,
        "spec_kc_middle": [100.0] * rows,
        "spec_kc_upper": [101.0] * rows,
        "spec_kc_lower": [99.0] * rows,
        "spec_atr_10": [1.0] * rows,
    })
    frame.loc[2, ["open", "high", "low", "close"]] = [99.0, 99.2, 98.0, 98.4]
    frame.loc[9, ["open", "high", "low", "close"]] = [98.8, 99.3, 98.7, 99.1]
    frame.loc[10, ["open", "high", "low", "close"]] = [99.2, 99.9, 99.1, 99.7]

    signal = SuperTrendKeltnerStrategy().evaluate_signal(
        frame, indicators_precomputed=True,
    )

    assert signal["action"] == "HOLD"
