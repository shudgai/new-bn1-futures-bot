import pytest
import pandas as pd
import math
from core.services.strategies.unified_entry_strategy import check_streamlined_entry_signal
from core.services.exits.dual_track_exit_service import (
    DualTrackExitStrategy,
    check_kc_phase_trailing_stop,
    check_peak_exhaustion_exit,
    check_emergency_exit
)

def _default_row(updates=None):
    row = {
        "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0,
        "volume": 100.0, "vol_ma_5": 50.0,
        "kc_upper": 102.0, "kc_middle": 100.0, "kc_lower": 98.0,
        "ma3": 100.0, "ma15": 100.0, "ma15_slope": 0.0, "atr": 1.0
    }
    if updates:
        row.update(updates)
    return row

def test_entry_track_c_breakout():
    # 上一根實體破軌 (收在軌道外，比例 >= 0.3, 放量)
    # 最新報價站穩 (在軌道外且高於 MA3)
    data = [
        _default_row(),
        _default_row(),
        _default_row(),
        _default_row({
            "open": 100.0, "close": 103.0, "high": 103.5, "low": 99.5,
            "kc_upper": 102.0, "volume": 100, "vol_ma_5": 50 # 實體 3.0, 總長 4.0, ratio=0.75
        }),
        _default_row({
            "kc_upper": 102.0, "ma3": 102.5
        })
    ]
    df = pd.DataFrame(data)
    live_price = 103.0 # > kc_upper and > ma3
    ok, reason = check_streamlined_entry_signal(df, "LONG", live_price)
    assert ok is True
    assert reason == "TRACK_C_BREAKOUT_LONG"

def test_entry_track_c_structural_collapse():
    # 上一根實體破軌，但最新報價跌破 MA3 (結構瓦解)
    data = [
        _default_row(), _default_row(), _default_row(),
        _default_row({
            "open": 100.0, "close": 103.0, "high": 103.5, "low": 99.5,
            "kc_upper": 102.0, "volume": 100, "vol_ma_5": 50
        }),
        _default_row({
            "kc_upper": 102.0, "ma3": 102.5
        })
    ]
    df = pd.DataFrame(data)
    live_price = 101.0 # < ma3
    ok, reason = check_streamlined_entry_signal(df, "LONG", live_price)
    assert ok is False

def test_entry_track_b_mid_pullback():
    # 回測中軌，大實體扭頭(>=0.4)，量大，斜率向上
    data = [
        _default_row(), _default_row(), _default_row(),
        _default_row({
            "open": 100.0, "close": 102.0, "high": 102.0, "low": 99.0, # low <= kc_mid(100), ratio=2/3 >= 0.4
            "kc_upper": 105.0, "kc_middle": 100.0, "kc_lower": 95.0,
            "volume": 100, "vol_ma_5": 50
        }),
        _default_row({
            "kc_upper": 105.0, "kc_middle": 100.0, "kc_lower": 95.0,
            "ma15_slope": 0.0002 # > 1e-4
        })
    ]
    df = pd.DataFrame(data)
    live_price = 102.5
    ok, reason = check_streamlined_entry_signal(df, "LONG", live_price)
    assert ok is True
    assert reason == "TRACK_B_MID_PULLBACK_LONG"

def test_dynamic_atr_phase_jump():
    # 測試 0.7 ATR 階段跳躍
    position = {
        "side": "LONG",
        "entry_price": 100.0,
        "size": 1.0,
        "v10_phase_trailing": {}
    }
    data = [_default_row(), _default_row({"atr": 1.0})]
    df = pd.DataFrame(data)
    
    # Check Phase 1 Jump
    reason = check_kc_phase_trailing_stop(position, df, 101.0, fee=0.0, slippage=0.0)
    assert reason is None 
    assert position["v10_phase_trailing"]["phase"] == 1
    assert position["locked_profit_atr"] == 0.7
    assert position["v10_phase_trailing"]["stop_price"] == 100.3
    
    # Check Phase 2 Jump (price = 102.0 => net profit 2.0. 2.0 / 0.7 = 2.85 -> Phase 2)
    reason2 = check_kc_phase_trailing_stop(position, df, 102.0, fee=0.0, slippage=0.0)
    assert reason2 is None
    assert position["v10_phase_trailing"]["phase"] == 2
    assert position["locked_profit_atr"] == 1.4
    assert position["v10_phase_trailing"]["stop_price"] == 102.0 - 1.4  # 100.6
    
    # Check Retreat triggers Exit
    reason3 = check_kc_phase_trailing_stop(position, df, 100.5, fee=0.0, slippage=0.0)
    assert reason3 == "EXIT_PHASE_TRAIL_LONG_P2"

def test_ratchet_lock_no_retreat():
    # 棘輪機制：止損不會往下退
    position = {
        "side": "LONG",
        "entry_price": 100.0,
        "size": 1.0,
        "v10_phase_trailing": {
            "phase": 1,
            "stop_price": 100.3
        }
    }
    data = [_default_row(), _default_row({"atr": 1.0})]
    df = pd.DataFrame(data)
    
    check_kc_phase_trailing_stop(position, df, 100.8, fee=0.0, slippage=0.0)
    assert position["v10_phase_trailing"]["stop_price"] == 100.3

def test_peak_exhaustion_harvest():
    # 峰谷收割 (LONG)：收回軌道，大實體反轉(陰線)，跌破防線
    position = {"side": "LONG"}
    data = [
        _default_row(),
        _default_row({"open": 100.0, "close": 101.0, "high": 102.0, "low": 99.0}), # Prev: Bullish
        _default_row({
            "open": 103.0, "close": 98.0, "high": 103.5, "low": 97.5, # Curr: Bearish, Huge Reversal
            "kc_upper": 104.0, "kc_middle": 99.0, "ma3": 99.5, "atr": 2.0,
            "volume": 200, "vol_ma_5": 50
        }),
        _default_row()
    ]
    df = pd.DataFrame(data)
    live_price = 98.0
    reason = check_peak_exhaustion_exit(position, df, live_price)
    assert reason == "PEAK_EXHAUSTION_EXIT_LONG"

def test_circuit_breaker_waterfall():
    position = {"side": "LONG"}
    data = [
        _default_row({"close": 100.0, "open": 100.0}), # prev
        _default_row({"close": 94.0, "open": 98.0})    # last (drop > 5% of prev_close)
    ]
    df = pd.DataFrame(data)
    reason = check_emergency_exit(position, df, 94.0)
    assert reason == "EXIT_EMERGENCY_WATERFALL_LONG"

def test_entry_trend_relay_bypass():
    data = [
        _default_row(), _default_row(), _default_row(),
        _default_row({
            "open": 100.0, "close": 102.0, "high": 102.0, "low": 99.0, # low <= kc_mid(100), ratio=2/3 >= 0.4
            "kc_upper": 105.0, "kc_middle": 100.0, "kc_lower": 95.0,
            "volume": 100, "vol_ma_5": 50
        }),
        _default_row({
            "kc_upper": 105.0, "kc_middle": 100.0, "kc_lower": 95.0,
            "ma15_slope": 0.0002 # > 1e-4
        })
    ]
    df = pd.DataFrame(data)
    live_price = 102.5
    
    ok, reason = check_streamlined_entry_signal(df, "LONG", live_price, relay_forced=True)
    assert ok is True
    assert reason == "TRACK_B_MID_PULLBACK_LONG"
