import pytest
import pandas as pd
from core.services.exits.entry_atr_protection import check_true_pivot_exit, check_trend_lock_exit_permission, kc_midline_turn_exit_reason
from core.services.mtf_kc_state_machine import kc_band_trend_side, _evaluate_kc_band_trend_entry

def test_true_peak_exit_without_waiting_midline():
    # 建立 3 根收線棒：左棒、峰值棒(高點最高)、確認棒(收黑跌破實體中點)
    rows = [
        {"timestamp": 1000.0, "open": 100.0, "high": 102.0, "low": 99.0, "close": 101.0, "is_closed": True, "atr": 2.0, "kc_middle": 100.0, "kc_upper": 104.0, "kc_lower": 96.0},
        {"timestamp": 2000.0, "open": 101.0, "high": 106.0, "low": 100.5, "close": 105.0, "is_closed": True, "atr": 2.0, "kc_middle": 101.0, "kc_upper": 105.0, "kc_lower": 97.0},
        {"timestamp": 3000.0, "open": 104.5, "high": 105.0, "low": 101.0, "close": 102.0, "is_closed": True, "atr": 2.0, "kc_middle": 102.0, "kc_upper": 106.0, "kc_lower": 98.0},
    ]
    df = pd.DataFrame(rows)
    pos = {"side": "LONG", "entry_price": 100.0, "peak_roe": 0.05}
    # 儘管中軌仍在向上 (101.0 -> 102.0)，真峰頂成型必須立即放行
    pivot_exit = check_true_pivot_exit(pos, df)
    assert pivot_exit == "EXIT_BY_TRUE_PEAK_EXTREME_REVERSAL"
    assert check_trend_lock_exit_permission(pos, df, 102.0) is True

def test_sideways_chop_not_mistaken_for_true_pivot():
    # 盤整死水案例：浮盈未達標 或 高低點振幅極小平頭震盪
    rows = [
        {"timestamp": 1000.0, "open": 100.0, "high": 100.4, "low": 99.8, "close": 100.2, "is_closed": True, "atr": 2.0, "kc_middle": 100.0, "kc_upper": 104.0, "kc_lower": 96.0, "ma5": 100.1},
        {"timestamp": 2000.0, "open": 100.2, "high": 100.5, "low": 99.9, "close": 100.3, "is_closed": True, "atr": 2.0, "kc_middle": 100.0, "kc_upper": 104.0, "kc_lower": 96.0, "ma5": 100.2},
        {"timestamp": 3000.0, "open": 100.3, "high": 100.4, "low": 99.8, "close": 100.0, "is_closed": True, "atr": 2.0, "kc_middle": 100.0, "kc_upper": 104.0, "kc_lower": 96.0, "ma5": 100.1},
    ]
    df = pd.DataFrame(rows)
    pos = {"side": "LONG", "entry_price": 100.0, "peak_roe": 0.01} # 浮盈極小
    assert check_true_pivot_exit(pos, df) is None # 嚴格判定為盤整，絕不平倉

def test_true_valley_exit_without_waiting_midline():
    # 建立 3 根收線棒：左棒、谷底棒(低點最低)、確認棒(收紅衝破實體中點)
    rows = [
        {"timestamp": 1000.0, "open": 100.0, "high": 101.0, "low": 98.0, "close": 99.0, "is_closed": True, "atr": 2.0, "kc_middle": 100.0, "kc_upper": 104.0, "kc_lower": 96.0},
        {"timestamp": 2000.0, "open": 99.0, "high": 99.5, "low": 94.0, "close": 95.0, "is_closed": True, "atr": 2.0, "kc_middle": 99.0, "kc_upper": 103.0, "kc_lower": 95.0},
        {"timestamp": 3000.0, "open": 95.5, "high": 99.0, "low": 95.0, "close": 98.0, "is_closed": True, "atr": 2.0, "kc_middle": 98.0, "kc_upper": 102.0, "kc_lower": 94.0},
    ]
    df = pd.DataFrame(rows)
    pos = {"side": "SHORT", "entry_price": 100.0, "peak_roe": 0.05}
    # 儘管中軌仍在向下 (99.0 -> 98.0)，真谷底成型必須立即放行
    pivot_exit = check_true_pivot_exit(pos, df)
    assert pivot_exit == "EXIT_BY_TRUE_VALLEY_EXTREME_REVERSAL"
    assert check_trend_lock_exit_permission(pos, df, 98.0) is True

def test_early_entry_on_midline_turn():
    # 15m HTF 向上，1m 中軌向上拐頭 (mid_now > mid_prev)，即使上軌暫未創高也提早開多
    import time
    now = int(time.time() // 60) * 60_000
    rows = []
    for i in range(30):
        rows.append({
            "timestamp": float(now - (29 - i) * 60_000),
            "open": 1.0, "high": 1.01, "low": 0.99, "close": 1.002,
            "kc_middle": 1.00 + (0.001 if i >= 28 else 0.0), # 第 28 根已收線中軌剛拐頭向上
            "kc_upper": 1.02, "kc_lower": 0.98,
            "atr": 0.01, "ma5": 1.002, "ma15": 1.0, "is_closed": i < 29,
        })
    df = pd.DataFrame(rows)
    df["is_closed"] = df["is_closed"].astype(bool)
    df.attrs.update(timeframe_ms=60_000.0, snapshot_ms=time.time() * 1000.0, entry_finality_verified=True)
    htf = {"valid": True, "state": "UPTREND", "closed_trend": "UPTREND"}
    
    assert kc_band_trend_side(df, htf) == "LONG"
    res = _evaluate_kc_band_trend_entry(df, htf, 1.002, "LONG")
    assert res is not None and res["allowed"] is True
