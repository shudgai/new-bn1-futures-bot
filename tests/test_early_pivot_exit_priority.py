"""
tests/test_early_pivot_exit_priority.py
=======================================
驗證真峰頂與真谷底及早平倉落袋：
1. 多單在 KC 上軌或高位衝高展開後，已收線確認三點峰頂樞紐（即使收盤價仍在 KC 中軌或 MA15 之上），
   立即放行 EARLY_PIVOT_PEAK_EXIT 平多落袋，不再等利潤跌穿中軌。
2. 空單在 KC 下軌或低位下殺展開後，已收線確認三點谷底樞紐（即使收盤價仍在 KC 中軌或 MA15 之下），
   立即放行 EARLY_PIVOT_VALLEY_EXIT 平空落袋，不再等利潤漲穿中軌。
3. 開倉初期呼吸保護 (peak_gain < 0.5 ATR 且 peak_roe < 5%)，
   即便出現微型峰谷，依然受呼吸保護一票否決 (VETO_HOLD) 防洗盤。
"""
import pytest
import pandas as pd
from core.gates.unified_pipeline_gates import ExitGate
from core.services.data_pipeline import MarketDataSnapshot


def create_snapshot(symbol: str, rows: list, current_price: float, pos: dict) -> dict:
    df = pd.DataFrame(rows)
    df.attrs["snapshot_ms"] = rows[-1]["timestamp"]
    df.attrs["timeframe_ms"] = 60000
    return {
        "symbol": symbol,
        "raw_df": df,
        "current_price": current_price,
        "curr_k": rows[-1],
        "prev_k": rows[-2] if len(rows) >= 2 else rows[-1],
        "atr": rows[-1].get("atr", 10.0),
        "kc_slope": 0.5,
    }


def test_long_early_pivot_peak_exit_above_kc_mid():
    """多單在 KC 中軌上方確立真峰頂，及時平多獲利了結，不回吐到中軌。"""
    import time
    now_ts = time.time()
    t0 = int((now_ts - 240) * 1000 // 60000) * 60000
    atr = 20.0
    kc_mid = 1000.0
    ma15 = 1005.0

    pos = {
        "symbol": "BTC/USDT",
        "side": "LONG",
        "entry_price": 980.0,
        "open_timestamp": t0 / 1000.0,
        "initial_entry_time": t0 / 1000.0,
        "entry_timestamp": t0 / 1000.0,
        "entry_atr": atr,
        "qty": 1.0,
        "leverage": 5.0,
        "margin": 200.0,
        "highest_price": 1050.0,
        "peak_roe": 0.12,  # 12% 浮盈 (介於 5%~14%，舊規則會因 close > ma15 而被吃掉)
    }

    # 3 根已收線 K 棒:
    # left: open=1010, close=1030, high=1035, low=1005
    # peak: open=1030, close=1045, high=1050, low=1025 (peak_mid = 1037.5)
    # confirm: open=1045, close=1020, high=1046, low=1015 (confirm_body = 25 >= 0.15*20, close < peak_mid, close > kc_mid)
    rows = [
        {"timestamp": t0, "open": 1010.0, "high": 1035.0, "low": 1005.0, "close": 1030.0, "ma5": 1025.0, "ma15": ma15, "kc_middle": kc_mid, "kc_upper": 1060.0, "kc_lower": 960.0, "atr": atr, "is_closed": True},
        {"timestamp": t0 + 60000, "open": 1030.0, "high": 1050.0, "low": 1025.0, "close": 1045.0, "ma5": 1035.0, "ma15": ma15, "kc_middle": kc_mid, "kc_upper": 1060.0, "kc_lower": 960.0, "atr": atr, "is_closed": True},
        {"timestamp": t0 + 120000, "open": 1045.0, "high": 1046.0, "low": 1015.0, "close": 1020.0, "ma5": 1030.0, "ma15": ma15, "kc_middle": kc_mid, "kc_upper": 1060.0, "kc_lower": 960.0, "atr": atr, "is_closed": True},
        {"timestamp": t0 + 180000, "open": 1020.0, "high": 1022.0, "low": 1018.0, "close": 1020.0, "ma5": 1028.0, "ma15": ma15, "kc_middle": kc_mid, "kc_upper": 1060.0, "kc_lower": 960.0, "atr": atr, "is_closed": False},
    ]

    snap = create_snapshot("BTC/USDT", rows, current_price=1020.0, pos=pos)
    should_exit, reason, meta = ExitGate.evaluate(pos, snap, current_price=1020.0, atr=atr, kc_slope=0.5)

    assert should_exit is True, "已收線確認真峰頂且獲利脫離成本區，必須立即平多獲利了結！"
    assert reason in ("EARLY_PIVOT_PEAK_EXIT", "EXIT_BY_CONFIRMED_TRUE_PEAK_PIVOT", "EXIT_BY_STAGED_PROFIT_LOCK_TIER2", "EXIT_BY_STAGED_PROFIT_LOCK_TIER1")


def test_short_early_pivot_valley_exit_below_kc_mid():
    """空單在 KC 中軌下方確立真谷底，及時平空獲利了結，不回吐到中軌。"""
    import time
    now_ts = time.time()
    t0 = int((now_ts - 240) * 1000 // 60000) * 60000
    atr = 20.0
    kc_mid = 1000.0
    ma15 = 995.0

    pos = {
        "symbol": "BTC/USDT",
        "side": "SHORT",
        "entry_price": 1020.0,
        "open_timestamp": t0 / 1000.0,
        "initial_entry_time": t0 / 1000.0,
        "entry_timestamp": t0 / 1000.0,
        "entry_atr": atr,
        "qty": 1.0,
        "leverage": 5.0,
        "margin": 200.0,
        "lowest_price": 950.0,
        "peak_roe": 0.12,  # 12% 浮盈 (介於 5%~14%)
    }

    # 3 根已收線 K 棒:
    # left: open=990, close=970, high=995, low=965
    # valley: open=970, close=955, high=975, low=950 (valley_mid = 962.5)
    # confirm: open=955, close=980, high=985, low=954 (confirm_body = 25 >= 0.15*20, close > valley_mid, close < kc_mid)
    rows = [
        {"timestamp": t0, "open": 990.0, "high": 995.0, "low": 965.0, "close": 970.0, "ma5": 975.0, "ma15": ma15, "kc_middle": kc_mid, "kc_upper": 1040.0, "kc_lower": 940.0, "atr": atr, "is_closed": True},
        {"timestamp": t0 + 60000, "open": 970.0, "high": 975.0, "low": 950.0, "close": 955.0, "ma5": 965.0, "ma15": ma15, "kc_middle": kc_mid, "kc_upper": 1040.0, "kc_lower": 940.0, "atr": atr, "is_closed": True},
        {"timestamp": t0 + 120000, "open": 955.0, "high": 985.0, "low": 954.0, "close": 980.0, "ma5": 970.0, "ma15": ma15, "kc_middle": kc_mid, "kc_upper": 1040.0, "kc_lower": 940.0, "atr": atr, "is_closed": True},
        {"timestamp": t0 + 180000, "open": 980.0, "high": 982.0, "low": 978.0, "close": 980.0, "ma5": 972.0, "ma15": ma15, "kc_middle": kc_mid, "kc_upper": 1040.0, "kc_lower": 940.0, "atr": atr, "is_closed": False},
    ]

    snap = create_snapshot("BTC/USDT", rows, current_price=980.0, pos=pos)
    should_exit, reason, meta = ExitGate.evaluate(pos, snap, current_price=980.0, atr=atr, kc_slope=-0.5)

    assert should_exit is True, "已收線確認真谷底且獲利脫離成本區，必須立即平空獲利了結！"
    assert reason in ("EARLY_PIVOT_VALLEY_EXIT", "EXIT_BY_CONFIRMED_TRUE_VALLEY_PIVOT", "EXIT_BY_STAGED_PROFIT_LOCK_TIER2", "EXIT_BY_STAGED_PROFIT_LOCK_TIER1")


def test_holding_protection_during_breath_period():
    """開倉成本區呼吸期 (< 0.5 ATR 且 < 5% ROE)，微型抖動維持 VETO_HOLD 防洗盤。"""
    t0 = 1000 * 60
    atr = 20.0
    kc_mid = 1000.0
    ma15 = 1000.0

    pos = {
        "symbol": "BTC/USDT",
        "side": "LONG",
        "entry_price": 1000.0,
        "entry_timestamp": t0 / 1000.0,
        "entry_atr": atr,
        "qty": 1.0,
        "leverage": 5.0,
        "margin": 200.0,
        "highest_price": 1005.0,
        "peak_roe": 0.01,  # 僅 1% 浮盈，未達 0.5 ATR (10.0) 且未達 5% ROE
    }

    rows = [
        {"timestamp": t0, "open": 1000.0, "high": 1003.0, "low": 999.0, "close": 1002.0, "ma5": 1001.0, "ma15": ma15, "kc_middle": kc_mid, "kc_upper": 1040.0, "kc_lower": 960.0, "atr": atr, "is_closed": True},
        {"timestamp": t0 + 60000, "open": 1002.0, "high": 1005.0, "low": 1001.0, "close": 1004.0, "ma5": 1002.5, "ma15": ma15, "kc_middle": kc_mid, "kc_upper": 1040.0, "kc_lower": 960.0, "atr": atr, "is_closed": True},
        {"timestamp": t0 + 120000, "open": 1004.0, "high": 1004.5, "low": 1000.5, "close": 1001.0, "ma5": 1002.0, "ma15": ma15, "kc_middle": kc_mid, "kc_upper": 1040.0, "kc_lower": 960.0, "atr": atr, "is_closed": True},
        {"timestamp": t0 + 180000, "open": 1001.0, "high": 1002.0, "low": 1000.5, "close": 1001.0, "ma5": 1002.0, "ma15": ma15, "kc_middle": kc_mid, "kc_upper": 1040.0, "kc_lower": 960.0, "atr": atr, "is_closed": False},
    ]

    snap = create_snapshot("BTC/USDT", rows, current_price=1001.0, pos=pos)
    should_exit, reason, meta = ExitGate.evaluate(pos, snap, current_price=1001.0, atr=atr, kc_slope=0.5)

    assert should_exit is False, "未脫離成本區呼吸保護，必須一票否決平倉！"
    assert reason == "VETO_HOLD"
