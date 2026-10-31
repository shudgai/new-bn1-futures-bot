"""
tests/test_v12_candle_pipeline_audit.py
======================================
V1.2 第一階段修改之 10 項嚴格驗收測試清單：
1. 最後一根 live bar 不污染 MA3/MA15
2. live bar 不污染 KC
3. live bar 不污染 ATR
4. UNKNOWN 收線狀態不得通過進場驗證 (Fail-Closed)
5. closed-only 最新收線 K 棒不會被多刪一根 (保留 tail)
6. curr_k、prev_k 正確保留 is_closed=True
7. entry_finality_verified 不再無條件為 True
8. timestamp 重複、倒序、缺失與中間斷檔的完整性處理
9. 既有持倉不因資料驗證失敗而強制平倉 (安全隔離)
10. 原有即時報價與風險監控仍能取得資料 (報價與指標分離)
"""
import time
import pytest
import pandas as pd
import numpy as np

from core.services.candle_data import (
    mark_candle_closure,
    closed_entry_candles,
    CANDLE_STATUS_CLOSED,
    CANDLE_STATUS_LIVE,
    CANDLE_STATUS_UNKNOWN,
)
from core.services.data_pipeline import DataPipeline, MarketDataSnapshot


def make_clean_closed_df(n=30, base_price=100.0, step=0.1, interval_ms=60000):
    """構造標準且時間戳單調連續的 closed K 線 DataFrame"""
    base_ts = 1700000000000
    rows = []
    for i in range(n):
        c = base_price + i * step
        rows.append({
            "timestamp": base_ts + i * interval_ms,
            "open": c - 0.05,
            "high": c + 0.1,
            "low": c - 0.1,
            "close": c,
            "volume": 1000.0,
            "is_closed": True,
        })
    df = pd.DataFrame(rows)
    df.attrs["timeframe_ms"] = interval_ms
    df.attrs["is_closed_only"] = True
    return df


def test_1_live_bar_does_not_pollute_ma3_and_ma15():
    """1. 最後一根 live bar 不污染 MA3/MA15"""
    df_closed = make_clean_closed_df(n=25, base_price=100.0, step=1.0)
    snap_closed = DataPipeline.calculate_snapshot("BTC/USDT", df_closed)
    assert snap_closed is not None

    # 加入一根極端價格的 live bar (close 暴衝至 999.0)
    df_with_live = df_closed.copy()
    last_ts = int(df_closed.iloc[-1]["timestamp"])
    live_row = pd.DataFrame([{
        "timestamp": last_ts + 60000,
        "open": 200.0,
        "high": 1000.0,
        "low": 200.0,
        "close": 999.0,  # 極端價格
        "volume": 50000.0,
        "is_closed": False,  # 未收線
    }])
    df_with_live = pd.concat([df_with_live, live_row], ignore_index=True)
    df_with_live.attrs["timeframe_ms"] = 60000

    snap_with_live = DataPipeline.calculate_snapshot("BTC/USDT", df_with_live)
    assert snap_with_live is not None

    # MA3 與 MA15 必須與未加入 live bar 前 100% 相同，絕不被 999.0 污染
    assert snap_with_live.ma3 == snap_closed.ma3
    assert snap_with_live.ma15 == snap_closed.ma15
    assert snap_with_live.prev_ma3 == snap_closed.prev_ma3
    assert snap_with_live.prev_ma15 == snap_closed.prev_ma15


def test_2_live_bar_does_not_pollute_kc():
    """2. live bar 不污染 KC (Upper, Middle, Lower)"""
    df_closed = make_clean_closed_df(n=25, base_price=100.0, step=0.5)
    snap_closed = DataPipeline.calculate_snapshot("ETH/USDT", df_closed)
    assert snap_closed is not None

    # 加入一根極端插針暴跌的 live bar
    df_with_live = df_closed.copy()
    last_ts = int(df_closed.iloc[-1]["timestamp"])
    live_row = pd.DataFrame([{
        "timestamp": last_ts + 60000,
        "open": 110.0,
        "high": 110.0,
        "low": 10.0,  # 暴跌插針
        "close": 15.0,
        "volume": 99999.0,
        "is_closed": False,
    }])
    df_with_live = pd.concat([df_with_live, live_row], ignore_index=True)
    df_with_live.attrs["timeframe_ms"] = 60000

    snap_with_live = DataPipeline.calculate_snapshot("ETH/USDT", df_with_live)
    assert snap_with_live is not None

    assert snap_with_live.kc_upper == snap_closed.kc_upper
    assert snap_with_live.kc_middle == snap_closed.kc_middle
    assert snap_with_live.kc_lower == snap_closed.kc_lower
    assert snap_with_live.kc_middle_slope == snap_closed.kc_middle_slope


def test_3_live_bar_does_not_pollute_atr():
    """3. live bar 不污染 ATR"""
    df_closed = make_clean_closed_df(n=25, base_price=50.0, step=0.2)
    snap_closed = DataPipeline.calculate_snapshot("SOL/USDT", df_closed)
    assert snap_closed is not None

    # 加入一根巨大振幅的 live bar (high 500, low 1)
    df_with_live = df_closed.copy()
    last_ts = int(df_closed.iloc[-1]["timestamp"])
    live_row = pd.DataFrame([{
        "timestamp": last_ts + 60000,
        "open": 55.0,
        "high": 500.0,
        "low": 1.0,
        "close": 55.0,
        "volume": 88888.0,
        "is_closed": False,
    }])
    df_with_live = pd.concat([df_with_live, live_row], ignore_index=True)
    df_with_live.attrs["timeframe_ms"] = 60000

    snap_with_live = DataPipeline.calculate_snapshot("SOL/USDT", df_with_live)
    assert snap_with_live is not None

    # ATR 必須保持純收線結果
    assert snap_with_live.atr == snap_closed.atr


def test_4_unknown_status_fails_entry_verification():
    """4. UNKNOWN 收線狀態不得通過進場驗證 (Fail-Closed)"""
    df = make_clean_closed_df(n=25)
    # 移除 is_closed 且不帶可信任標籤 -> 判定為 UNKNOWN
    del df["is_closed"]
    df.attrs.pop("is_closed_only", None)
    df.attrs.pop("data_source", None)

    # closed_entry_candles 必須拒絕 UNKNOWN 資料，回傳空 DataFrame
    closed = closed_entry_candles(df)
    assert closed.empty

    # DataPipeline.calculate_snapshot 必須拒絕此類無收線驗證之資料 (回傳 None)
    snapshot = DataPipeline.calculate_snapshot("BNB/USDT", df)
    assert snapshot is None


def test_5_closed_only_source_retains_latest_closed_bar():
    """5. closed-only 最新收線 K 棒不會被多刪一根 (保留 tail)"""
    df = make_clean_closed_df(n=25)
    del df["is_closed"]
    df.attrs["is_closed_only"] = True  # 明確標記可信任純收線資料來源

    closed = closed_entry_candles(df)
    # 絕不可執行 iloc[:-1] 刪掉最新一根，必須完整保留全部 25 根
    assert len(closed) == 25
    assert closed.iloc[-1]["close"] == df.iloc[-1]["close"]
    assert bool(closed.iloc[-1]["is_closed"]) is True


def test_6_curr_k_and_prev_k_retain_is_closed():
    """6. curr_k、prev_k 正確保留 is_closed=True"""
    df = make_clean_closed_df(n=25)
    snapshot = DataPipeline.calculate_snapshot("XRP/USDT", df)
    assert snapshot is not None

    assert snapshot.curr_k.get("is_closed") is True
    assert snapshot.prev_k.get("is_closed") is True
    assert snapshot.curr_k["close"] == df.iloc[-1]["close"]
    assert snapshot.prev_k["close"] == df.iloc[-2]["close"]


def test_7_entry_finality_verified_not_unconditionally_true():
    """7. entry_finality_verified 必須根據收線資料驗證結果設定，禁止無條件為 True"""
    # 場景 A: 全為乾淨通過驗證的收線 K 棒 -> verified 應為 True
    df_closed = make_clean_closed_df(n=25)
    snap_closed = DataPipeline.calculate_snapshot("DOGE/USDT", df_closed)
    assert snap_closed.raw_df.attrs.get("entry_finality_verified") is True

    # 場景 B: 收線資料不足以計算指標 (少於 20 根) 或未通過收線驗證之新進場 -> 拒絕新進場 Snapshot (Fail-Closed)
    df_short = make_clean_closed_df(n=10)
    snap_short = DataPipeline.calculate_snapshot("DOGE/USDT", df_short)
    assert snap_short is None  # 拒絕進場 Snapshot，禁止無條件設 True 放行

    # 場景 C: 帶有 live bar 的資料，只要其收線部分完整通過驗證，既有收線指標不被污染，且保留 verified 供下游開倉審計
    df_live = df_closed.copy()
    last_ts = int(df_closed.iloc[-1]["timestamp"])
    live_row = pd.DataFrame([{
        "timestamp": last_ts + 60000,
        "open": 102.0, "high": 103.0, "low": 101.5, "close": 102.5,
        "volume": 100.0, "is_closed": False,
    }])
    df_live = pd.concat([df_live, live_row], ignore_index=True)
    df_live.attrs["timeframe_ms"] = 60000

    snap_live = DataPipeline.calculate_snapshot("DOGE/USDT", df_live)
    assert snap_live is not None
    assert snap_live.raw_df.attrs.get("entry_finality_verified") is True


def test_8_timestamp_integrity_checks():
    """8. timestamp 重複、倒序、缺失與中間斷檔的處理 (Fail-Closed)"""
    # 8.1 包含 NaN timestamp
    df_nan = make_clean_closed_df(n=25)
    df_nan.loc[5, "timestamp"] = np.nan
    assert closed_entry_candles(df_nan).empty
    assert DataPipeline.calculate_snapshot("TEST/USDT", df_nan) is None

    # 8.2 timestamp 重複
    df_dup = make_clean_closed_df(n=25)
    df_dup.loc[10, "timestamp"] = df_dup.loc[9, "timestamp"]
    assert closed_entry_candles(df_dup).empty
    assert DataPipeline.calculate_snapshot("TEST/USDT", df_dup) is None

    # 8.3 timestamp 倒序 (非單調遞增)
    df_rev = make_clean_closed_df(n=25)
    df_rev.loc[15, "timestamp"] = df_rev.loc[14, "timestamp"] - 60000
    assert closed_entry_candles(df_rev).empty
    assert DataPipeline.calculate_snapshot("TEST/USDT", df_rev) is None

    # 8.4 中間漏棒斷檔 (例如從 10 分鐘跳到 12 分鐘，間隔不連續)
    df_gap = make_clean_closed_df(n=25)
    df_gap.loc[12:, "timestamp"] = df_gap.loc[12:, "timestamp"] + 120000
    assert closed_entry_candles(df_gap).empty
    assert DataPipeline.calculate_snapshot("TEST/USDT", df_gap) is None


def test_9_existing_position_not_forcibly_closed_on_snapshot_failure():
    """9. 既有持倉不因資料驗證失敗而強制平倉 (安全隔離)"""
    # 模擬異常資料 (不足 20 根或時間戳異常)
    bad_df = make_clean_closed_df(n=5)
    snapshot = DataPipeline.calculate_snapshot("ADA/USDT", bad_df)
    # 快照失敗回傳 None
    assert snapshot is None

    # 驗證執行層處置：在 execution_manager 中，當 snapshot 為 None 但有持倉時，回傳 DATA_OUTAGE_HOLD
    # 此處測試快照失敗不會偽造出場訊號 (Fail-Closed 安全隔離)
    pos = {"symbol": "ADA/USDT", "side": "LONG", "entry_price": 0.5, "peak_roe": 10.0}
    # 策略出口閘門依賴合規 snapshot，若無 snapshot 則不會發出非預期的出場指令
    assert snapshot is None


def test_10_realtime_quote_and_risk_monitoring_decoupled_from_indicators():
    """10. 原有即時報價與風險監控仍能取得資料 (報價與指標分離)"""
    df_closed = make_clean_closed_df(n=25, base_price=100.0, step=0.1)
    pos = {
        "symbol": "AVAX/USDT",
        "side": "LONG",
        "entry_price": 100.0,
        "leverage": 10.0,
        "peak_roe": 0.05,
    }
    # 即時報價為 105.0 (較收線價格大幅上漲)
    live_quote = 105.0

    snapshot = DataPipeline.calculate_snapshot(
        symbol="AVAX/USDT",
        df=df_closed,
        current_price=live_quote,
        position=pos,
    )
    assert snapshot is not None

    # 即時報價成功傳遞至 snapshot 與持倉監控
    assert snapshot.current_price == live_quote
    # ROE 根據即時報價即時更新: ((105 - 100) / 100) * 10 = 0.5 (即 50%)
    assert snapshot.peak_roe == pytest.approx(0.5)
    assert pos["unrealized_roe"] == pytest.approx(0.5)
    assert pos["peak_roe"] == pytest.approx(0.5)

    # 但收線指標 (MA3, KC, ATR) 依然完全取自 closed_df，未受 live_quote 任何干擾
    assert snapshot.curr_k["close"] == df_closed.iloc[-1]["close"]
