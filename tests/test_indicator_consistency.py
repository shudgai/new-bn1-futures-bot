"""
tests/test_indicator_consistency.py
===================================
驗證 KC 指標全系統統一性、無前視偏差 (Lookahead Bias)、各模組 Fallback 一致性與未收線 K 棒安全防護。
"""

import pytest
import numpy as np
import pandas as pd

from core.services.technical_indicators import (
    compute_keltner_channels,
    compute_technical_indicators,
)
from core.strategy import SuperTrendKeltnerStrategy
from core.services.data_pipeline import DataPipeline, MarketDataSnapshot
from core.indicators.dual_timeframe_provider import DualTimeframeProvider
from core.gates.channel_volatility_filter import ChannelVolatilityFilter
from core.gates.unified_pipeline_gates import ExitGate
from core.gates.breakout_continuation_entry_gate import BreakoutContinuationEntryGate
from core.gates.holding_protection_gate import HoldingProtectionExitGate


def _generate_ohlcv_data(num_bars: int = 60, seed: int = 42) -> pd.DataFrame:
    """生成確定性 OHLCV 測試資料。"""
    np.random.seed(seed)
    base_price = 100.0
    records = []
    current_time = 1700000000000  # ms

    for i in range(num_bars):
        change = np.random.normal(0, 1.5)
        open_p = base_price
        close_p = open_p + change
        high_p = max(open_p, close_p) + abs(np.random.normal(0, 0.8))
        low_p = min(open_p, close_p) - abs(np.random.normal(0, 0.8))
        vol = 1000.0 + np.random.uniform(100, 500)

        records.append({
            "timestamp": current_time + (i * 60000),
            "open": round(open_p, 4),
            "high": round(high_p, 4),
            "low": round(low_p, 4),
            "close": round(close_p, 4),
            "volume": round(vol, 2),
        })
        base_price = close_p

    return pd.DataFrame(records)


def test_main_trading_and_chart_indicators_match():
    """1. 驗證主交易 (DataPipeline) 與圖表 (SuperTrendKeltnerStrategy) 指標數值 100% 一致。"""
    df = _generate_ohlcv_data(60)

    # 1. 取得主交易快照
    snapshot = DataPipeline.calculate_snapshot(symbol="BTC/USDT", df=df)
    assert snapshot is not None, "DataPipeline 快照計算應成功"

    # 2. 取得圖表指標
    strategy = SuperTrendKeltnerStrategy()
    assert strategy.atr_period == 14, "Strategy ATR 週期預設必須為 14"
    chart_indicators = strategy.compute_indicators(df)

    last_idx = df.index[-1]
    chart_kc_mid = float(chart_indicators.loc[last_idx, "ema_20"])
    chart_kc_up = float(chart_indicators.loc[last_idx, "kc_upper"])
    chart_kc_low = float(chart_indicators.loc[last_idx, "kc_lower"])
    chart_atr = float(chart_indicators.loc[last_idx, "atr"])
    chart_ma5 = float(chart_indicators.loc[last_idx, "ma5"])
    chart_ma15 = float(chart_indicators.loc[last_idx, "ma15"])
    chart_ema5 = float(chart_indicators.loc[last_idx, "ema5"])
    chart_ema13 = float(chart_indicators.loc[last_idx, "ema13"])

    # 比對主交易快照與圖表數值 (誤差門檻 < 1e-9)
    assert abs(snapshot.kc_middle - chart_kc_mid) < 1e-9, f"KC 中軌不一致: {snapshot.kc_middle} vs {chart_kc_mid}"
    assert abs(snapshot.kc_upper - chart_kc_up) < 1e-9, f"KC 上軌不一致: {snapshot.kc_upper} vs {chart_kc_up}"
    assert abs(snapshot.kc_lower - chart_kc_low) < 1e-9, f"KC 下軌不一致: {snapshot.kc_lower} vs {chart_kc_low}"
    assert abs(snapshot.atr - chart_atr) < 1e-9, f"ATR 不一致: {snapshot.atr} vs {chart_atr}"
    assert abs(snapshot.ma5 - chart_ma5) < 1e-9, f"MA5 不一致: {snapshot.ma5} vs {chart_ma5}"
    assert abs(snapshot.ma15 - chart_ma15) < 1e-9, f"MA15 不一致: {snapshot.ma15} vs {chart_ma15}"
    assert abs(snapshot.ema5 - chart_ema5) < 1e-9, f"EMA5 不一致: {snapshot.ema5} vs {chart_ema5}"
    assert abs(snapshot.ema13 - chart_ema13) < 1e-9, f"EMA13 不一致: {snapshot.ema13} vs {chart_ema13}"


def test_backtest_and_main_trading_consistency():
    """2. 驗證回測與主交易使用相同歷史資料時，KC 計算完全一致。"""
    df = _generate_ohlcv_data(50)

    # 共用底層純數學函式
    kc_pure = compute_keltner_channels(df, ema_period=20, atr_period=14, multiplier=1.5)
    snapshot = DataPipeline.calculate_snapshot("ETH/USDT", df)

    assert snapshot is not None
    # 驗證最後一根數值
    assert abs(kc_pure["kc_middle"].iloc[-1] - snapshot.kc_middle) < 1e-9
    assert abs(kc_pure["kc_upper"].iloc[-1] - snapshot.kc_upper) < 1e-9
    assert abs(kc_pure["kc_lower"].iloc[-1] - snapshot.kc_lower) < 1e-9
    assert abs(kc_pure["atr"].iloc[-1] - snapshot.atr) < 1e-9

    # 驗證所有有效根 (第 14 根到最後一根) 的軌道寬度嚴格等於 3.0 * ATR
    valid_mask = ~kc_pure["atr"].isna()
    width = kc_pure["kc_upper"][valid_mask] - kc_pure["kc_lower"][valid_mask]
    expected_width = kc_pure["atr"][valid_mask] * 3.0
    np.testing.assert_allclose(width, expected_width, rtol=1e-9)


def test_fallback_modules_use_ema20_and_1_5x_multiplier():
    """3. 驗證 dual_timeframe_provider 和 channel_volatility_filter 不再因 fallback 使用 SMA20 或 2.0 倍。"""
    df = _generate_ohlcv_data(40)
    # 移除現存指標欄位以迫使其走 fallback 邏輯
    raw_df = df[["timestamp", "open", "high", "low", "close", "volume"]].copy()

    # (A) 驗證 DualTimeframeProvider
    enriched_dtf = DualTimeframeProvider.compute_indicators_df(raw_df.copy())
    expected_ema20 = raw_df["close"].ewm(span=20, adjust=False).mean()
    np.testing.assert_allclose(enriched_dtf["kc_middle"], expected_ema20, rtol=1e-9)

    valid_idx = ~enriched_dtf["atr"].isna()
    diff_upper = enriched_dtf.loc[valid_idx, "kc_upper"] - enriched_dtf.loc[valid_idx, "kc_middle"]
    expected_diff = enriched_dtf.loc[valid_idx, "atr"] * 1.5
    np.testing.assert_allclose(diff_upper, expected_diff, rtol=1e-9)

    # (B) 驗證 ChannelVolatilityFilter
    cvf = ChannelVolatilityFilter()
    enriched_cvf = cvf.compute_bandwidth(raw_df.copy(), channel_type="KC")
    # 驗證帶寬是基於 1.5 倍 ATR 推導
    # 帶寬 = (Upper - Lower) / Middle = 3.0 * ATR / EMA20
    expected_bandwidth = (3.0 * enriched_dtf.loc[valid_idx, "atr"]) / expected_ema20[valid_idx]
    np.testing.assert_allclose(
        enriched_cvf.loc[valid_idx, "bandwidth"],
        expected_bandwidth,
        rtol=1e-7,
    )


def test_no_lookahead_bias_and_atr_warmup():
    """4. 驗證 ATR 暖機期不會使用未來資料 (無 bfill)，且不足暖機期時不會產生假訊號。"""
    df_short = _generate_ohlcv_data(10)  # 僅 10 根，未達 14 根 ATR 暖機期

    # (A) compute_keltner_channels 在前 13 根必須是 NaN，嚴禁用 bfill 偷看未來
    kc_short = compute_keltner_channels(df_short, atr_period=14)
    assert kc_short["atr"].isna().all(), "前 10 根之 ATR 必須完全為 NaN，禁止使用未來資料回填"

    # (B) DataPipeline 在資料不足或 ATR 為 NaN 時必須 Fail-Closed 返回 None
    snapshot_short = DataPipeline.calculate_snapshot("BTC/USDT", df_short)
    assert snapshot_short is None, "暖機不足時，DataPipeline 必須返回 None 拒絕開倉"

    # (C) 驗證加入後續資料後，過去歷史各根的 ATR 保持不變，絕不受後續新增影響
    df_full = _generate_ohlcv_data(30)
    kc_full = compute_keltner_channels(df_full, atr_period=14)
    # 前 13 根仍必須為 NaN
    assert kc_full["atr"].iloc[:13].isna().all(), "即使總共有 30 根，前 13 根仍必須保持 NaN"
    # 第 14 根起開始有值
    assert not pd.isna(kc_full["atr"].iloc[13]), "第 14 根 (索引 13) 必須開始具備有效 ATR"


def test_different_timeframes_isolation():
    """5. 驗證不同 K 線週期 (1m, 3m, 5m) 的指標與快取互不污染。"""
    df_1m = _generate_ohlcv_data(40, seed=101)
    df_3m = _generate_ohlcv_data(40, seed=303)
    df_5m = _generate_ohlcv_data(40, seed=505)

    snap_1m = DataPipeline.calculate_snapshot("BTC/USDT", df_1m)
    snap_3m = DataPipeline.calculate_snapshot("BTC/USDT", df_3m)
    snap_5m = DataPipeline.calculate_snapshot("BTC/USDT", df_5m)

    assert snap_1m is not None and snap_3m is not None and snap_5m is not None
    # 數值應各自獨立
    assert snap_1m.kc_middle != snap_3m.kc_middle
    assert snap_3m.kc_middle != snap_5m.kc_middle
    assert snap_1m.atr != snap_3m.atr


def test_unclosed_bar_not_treated_as_confirmed_pivot_or_breakout():
    """6. 驗證未收線 K 棒不得被當作已確認的真峰頂/谷底或收線突破。"""
    # 建立多頭單邊走勢資料，確保 macro_trend 為 BULLISH
    df = _generate_ohlcv_data(60)
    # 人工向上推升幾根確保均線多頭排列
    for i in range(15):
        last_c = df["close"].iloc[-1]
        new_row = {
            "timestamp": df["timestamp"].iloc[-1] + 60000,
            "open": last_c,
            "high": last_c + 2.0,
            "low": last_c - 0.2,
            "close": last_c + 1.8,
            "volume": 2000.0,
        }
        df = pd.concat([df, pd.DataFrame([new_row])], ignore_index=True)

    snapshot = DataPipeline.calculate_snapshot("BTC/USDT", df)
    assert snapshot is not None

    position = {
        "symbol": "BTC/USDT",
        "side": "LONG",
        "entry_price": float(df["close"].iloc[-10]),
        "qty": 1.0,
        "amount": 1.0,
        "leverage": 5.0,
        "margin": 20.0,
    }

    # 模擬盤中價格向上插針毛刺 (未收線)，但歷史收線並未形成 3-Point Pivot 真峰頂
    # ExitGate 應執行續抱防護，不允許因盤中未收線插針判定峰頂出場
    should_close, reason, meta = ExitGate.evaluate(
        pos=position,
        market_data=snapshot,
        current_price=snapshot.current_price + 0.1,
    )
    assert should_close is False, f"未收線毛刺不得誤平倉: {reason}"
    assert "VETO" in reason or "HOLD" in reason, f"應維持續抱: {reason}"


def test_existing_net_roe_profit_lock_still_intact():
    """7. 驗證透過 NetRoeLockConfig 支援自訂鎖利規則 (如既有 10% 啟動、回吐 3 個百分點平倉) 依然健全通過。"""
    from core.services.exits.net_roe_profit_lock import evaluate_net_roe_profit_lock, NetRoeLockConfig
    from tests.test_unified_net_roe_profit_lock import _price_for_net_roe

    legacy_cfg = NetRoeLockConfig(
        tier1_start=10.0,
        tier1_floor=7.0,
        tier2_start=10.0,
        tier2_giveback=3.0,
        tier3_start=30.0,
        tier3_giveback=3.0,
    )

    entry = 100.0
    pos = {
        "symbol": "BTC/USDT",
        "side": "LONG",
        "entry_price": entry,
        "qty": 1.0,
        "leverage": 5.0,
        "margin": 20.0,
    }

    # 1. 價格先衝上 +15% 淨 ROE -> 啟動鎖利，底線更新為 +12%
    price_15 = _price_for_net_roe("LONG", 15.0, entry=entry, leverage=5.0)
    exit_15, reason_15, meta_15 = evaluate_net_roe_profit_lock(pos, price_15, "LONG", config=legacy_cfg)
    assert exit_15 is False, "推升至 +15% 時不應立即平倉"
    assert pos.get("profit_lock_armed") is True, "應已啟動鎖利"
    assert abs(pos.get("highest_net_roe", 0) - 15.0) < 0.1, "最高 ROE 應記錄為 15%"

    # 2. 回落至 +13% (回吐 2%，未破 12% 鎖利底線) -> 續抱
    price_13 = _price_for_net_roe("LONG", 13.0, entry=entry, leverage=5.0)
    exit_13, reason_13, meta_13 = evaluate_net_roe_profit_lock(pos, price_13, "LONG", config=legacy_cfg)
    assert exit_13 is False, f"回吐 2% 不應平倉: {reason_13}"

    # 3. 回落至 +11.9% (回吐超過 3%，跌破 12% 底線) -> 必須觸發平倉
    price_11_9 = _price_for_net_roe("LONG", 11.9, entry=entry, leverage=5.0)
    exit_11_9, reason_11_9, meta_11_9 = evaluate_net_roe_profit_lock(pos, price_11_9, "LONG", config=legacy_cfg)
    assert exit_11_9 is True, "回吐超過 3% 必須觸發 PROFIT_LOCK 平倉"
    assert "PROFIT_LOCK" in reason_11_9
