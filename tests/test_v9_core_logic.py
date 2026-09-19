import pytest
import pandas as pd
import math
from core.services.strategies.unified_entry_strategy import check_streamlined_entry_signal
from core.services.exits.dual_track_exit_service import (
    DualTrackExitStrategy
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
    ok, reason, _ = check_streamlined_entry_signal(df, "LONG", live_price)
    assert ok is True
    assert reason == "[SPECIAL_ENTRY] Overextended Impulse LONG (Limit Order)"

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
    ok, reason, _ = check_streamlined_entry_signal(df, "LONG", live_price)
    assert ok is True
    assert reason == "[SPECIAL_ENTRY] Overextended Impulse LONG (Limit Order)"

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
    ok, reason, _ = check_streamlined_entry_signal(df, "LONG", live_price)
    assert ok is True
    assert reason == "[SPECIAL_ENTRY] Extreme Impulse LONG (>=2.0 ATR)"


def test_dynamic_atr_phase_jump():
    strategy = DualTrackExitStrategy()
    position = {
        "side": "LONG",
        "entry_price": 100.0,
        "size": 1.0,
        "v10_phase_trailing": {}
    }
    # df needs at least 3 rows
    data = [_default_row(), _default_row(), _default_row({"atr": 1.0, "kc_upper": 105.0})]
    df = pd.DataFrame(data)

    reason = strategy.evaluate_exit(position, df, 101.6)
    assert reason is None
    # Step trailing only fires at bar close — intraday evaluation keeps level == 0
    assert position["v10_phase_trailing"]["last_locked_level"] == 0
    # Hard stop = entry_price - 1.5 * atr = 100 - 1.5 = 98.5
    assert position["v10_phase_trailing"]["active_stop_price"] == 98.5

    reason2 = strategy.evaluate_exit(position, df, 103.1)
    assert reason2 is None
    # Step trailing still not fired (bar-close only) — level stays 0
    assert position["v10_phase_trailing"]["last_locked_level"] == 0
    assert position["v10_phase_trailing"]["active_stop_price"] == 98.5
def test_ratchet_lock_no_retreat():
    # 棘輪機制：止損不會往下退
    position = {
        "side": "LONG",
        "entry_price": 100.0,
        "size": 1.0,
        "v10_phase_trailing": {
            "last_locked_level": 1,
            "active_stop_price": 100.3,
            "defense_line": 98.5
        }
    }
    data = [_default_row(), _default_row({"atr": 1.0, "kc_upper": 105.0})]
    df = pd.DataFrame(data)
    
    strategy = DualTrackExitStrategy()
    strategy.evaluate_exit(position, df, 100.8)
    assert position["v10_phase_trailing"]["active_stop_price"] == 100.3


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
    
    ok, reason, _ = check_streamlined_entry_signal(df, "LONG", live_price, relay_forced=True)
    assert ok is True
    assert reason == "[SPECIAL_ENTRY] Extreme Impulse LONG (>=2.0 ATR)"

def test_entry_track_d_trend_continuation_short():
    # KC 通道連續 4 根下降，最近 3 根中 2 根陰線且階梯式收低，即時報價在中軌下方
    rows = []
    kc_mids = [105.0, 104.0, 103.0, 102.0, 101.0]
    for i, mid in enumerate(kc_mids):
        c = 103.0 - i * 0.5   # 103.0, 102.5, 102.0, 101.5, 101.0 → 陰線階梯下跌
        o = c + 0.3
        rows.append({
            "open": o, "close": c, "high": o + 0.1, "low": c - 0.1,
            "volume": 80.0, "vol_ma_5": 100.0,
            "kc_upper": mid + 3 + (i * 0.1), "kc_middle": mid, "kc_lower": mid - 3 - (i * 0.1),
            # 空頭排列：ma3 必須 < kc_middle，且 live_price 也必須 < ma3 < kc_middle
            "ma3": mid - 0.3, "ma15": mid + 0.5,
            "ma15_slope": -0.0003, "atr": 1.0, "ema_20": mid
        })
    df = pd.DataFrame(rows)
    last_mid = kc_mids[-1]       # 101.0
    # latest ma3 = 101.0 - 0.3 = 100.7
    live_price = last_mid - 0.8  # 100.2 < ma3(100.7) < kc_middle(101.0) ✓
    ok, reason, _ = check_streamlined_entry_signal(df, "SHORT", live_price)
    assert ok is True
    assert reason == "[TREND_PRIVILEGE_ENTRY] Band Riding SHORT"

def test_entry_track_d_blocked_when_not_cascading():
    # 最近 3 根中有陰線但收盤價未階梯式下跌（反彈），不應觸發 Track D
    rows = []
    kc_mids = [105.0, 104.0, 103.0, 102.0, 101.0]
    closes = [103.0, 102.5, 103.2, 102.8, 102.0]  # 第 3 根反彈，非階梯式
    for i, (mid, c) in enumerate(zip(kc_mids, closes)):
        o = c + 0.3
        rows.append({
            "open": o, "close": c, "high": o + 0.1, "low": c - 0.1,
            "volume": 80.0, "vol_ma_5": 100.0,
            "kc_upper": mid + 3, "kc_middle": mid, "kc_lower": mid - 3,
            "ma3": mid - 0.5, "ma15": mid + 0.2,
            "ma15_slope": -0.0003, "atr": 1.0, "ema_20": mid
        })
    df = pd.DataFrame(rows)
    live_price = 101.2
    ok, reason, _ = check_streamlined_entry_signal(df, "SHORT", live_price)
    assert ok is False

def test_entry_filtered_extreme_distance():
    rows = []
    # Create an extreme distance scenario
    mid = 100.0
    for i in range(5):
        c = 105.0 + i  # Prices moving up far away from mid
        o = c - 0.5
        rows.append({
            "open": o, "close": c, "high": c + 0.1, "low": o - 0.1,
            "volume": 80.0, "vol_ma_5": 100.0,
            "kc_upper": mid + 2.0, "kc_middle": mid, "kc_lower": mid - 2.0,
            "ma3": c - 0.1, "ma15": mid - 1.0,
            "ma15_slope": 0.01, "atr": 1.0, "ema_20": mid,
            "ma5": c - 0.2
        })
    df = pd.DataFrame(rows)
    # The last close is 109.0, middle is 100.0. Distance is 9.0, which is > 2.0 * atr (2.0)
    ok, reason, _ = check_streamlined_entry_signal(df, "LONG", 109.0)
    assert ok is False
    assert "FILTERED_EXTREME_DISTANCE" in reason

def test_entry_track_a_trend_defense():
    # Long Special K but Strong Bearish Trend
    data_long = [
        _default_row(), _default_row(), _default_row(),
        _default_row({
            "kc_middle": 105.0, "ma15": 106.0
        }),
        _default_row({
            "open": 98.0, "close": 101.0, "high": 101.5, "low": 97.5, # body=3.0, total=5.0, ratio=0.60
            "kc_middle": 100.0, "ma15": 100.0,  # slope_ma15 = -6.0, slope_mid = -5.0 (Strong bear!)
            "atr": 1.0,
            "volume": 100, "vol_ma_5": 50
        }),
        _default_row()
    ]
    df_long = pd.DataFrame(data_long)
    ok, reason, _ = check_streamlined_entry_signal(df_long, "LONG", 101.5)
    assert ok is False
    assert "FILTERED_EXTREME_COUNTER_TREND" in reason
