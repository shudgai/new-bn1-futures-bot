import pytest
import pandas as pd
from core.gates.unified_pipeline_gates import EntryGate
from core.services.data_pipeline import DataPipeline, MarketDataSnapshot


def _build_mock_df(n_bars: int = 30, start_price: float = 100.0, step: float = 0.0) -> pd.DataFrame:
    rows = []
    base_ts = 1700000000000
    p = start_price
    for i in range(n_bars):
        rows.append({
            "timestamp": base_ts + i * 60_000,
            "open": p,
            "high": p + 0.1,
            "low": p - 0.1,
            "close": p,
            "volume": 100.0,
        })
        p += step
    return pd.DataFrame(rows)


def test_veto_kc_squeeze_chop():
    """
    [門禁測試 1] 通道帶寬壓縮硬鎖 (VETO_KC_SQUEEZE_CHOP):
    當 bandwidth < min_bandwidth_threshold (極窄收斂區間)，非起爆長棒 100% 一票否決！
    """
    df = _build_mock_df(30, start_price=100.0, step=0.0)
    snapshot = DataPipeline.calculate_snapshot("LOBSTER/USDT", df, current_price=100.0)
    snapshot.kc_middle = 100.0
    snapshot.kc_upper = 100.15  # bw = 0.30 / 100.0 = 0.003 < 0.005
    snapshot.kc_lower = 99.85
    snapshot.atr = 0.5
    snapshot.curr_k = {"open": 100.0, "close": 100.05, "high": 100.08, "low": 99.98}
    snapshot.prev_k = {"open": 100.0, "close": 100.01, "high": 100.04, "low": 99.98}

    should_entry, reason, meta = EntryGate.evaluate("ENTRY_LONG_EMA_CROSS_MID", snapshot)
    assert should_entry is False
    assert "VETO_KC_SQUEEZE_CHOP" in reason
    assert meta["kc_bandwidth"] < 0.005


def test_veto_ma15_flat_cross():
    """
    [門禁測試 2] 中長均線躺平硬鎖 (VETO_MA15_FLAT):
    過去 3 根 MA15 變化量 / ATR < 0.15，判定水平橫躺麻花捲，100% 一票否決！
    """
    df = _build_mock_df(30, start_price=100.0, step=0.0)
    df["ma15"] = 100.0  # 完全平走，斜率 = 0
    snapshot = DataPipeline.calculate_snapshot("LOBSTER/USDT", df, current_price=100.0)
    snapshot.atr = 1.0
    snapshot.kc_middle = 100.0
    snapshot.kc_upper = 102.0
    snapshot.kc_lower = 98.0
    snapshot.curr_k = {"open": 100.0, "close": 100.4, "high": 100.5, "low": 99.9}
    snapshot.prev_k = {"open": 100.0, "close": 100.1, "high": 100.2, "low": 99.9}
    snapshot.raw_df = df

    should_entry, reason, meta = EntryGate.evaluate("ENTRY_LONG_EMA_CROSS_MID", snapshot)
    assert should_entry is False
    assert "VETO_MA15_FLAT" in reason
    assert abs(meta["ma15_slope_3bars"]) < 0.15


def test_veto_long_ma15_not_up():
    """
    [門禁測試 3] 做多方向性檢驗 (VETO_LONG_MA15_NOT_UP):
    KC 中軌向上，但 MA15 往下掉或向上角度不足 (< 0.12 ATR)，禁止逆勢摸底開多！
    """
    df = _build_mock_df(30, start_price=100.0, step=0.0)
    df["kc_middle"] = [100.0 + i * 0.15 for i in range(len(df))]
    # MA15 緩慢下行
    df["ma15"] = [100.0 - i * 0.08 for i in range(len(df))]
    snapshot = DataPipeline.calculate_snapshot("LOBSTER/USDT", df, current_price=104.0)
    snapshot.atr = 1.0
    snapshot.kc_middle = 104.0
    snapshot.kc_upper = 107.0
    snapshot.kc_lower = 101.0
    snapshot.curr_k = {"open": 104.0, "close": 104.5, "high": 104.6, "low": 103.9}
    snapshot.prev_k = {"open": 104.0, "close": 104.1, "high": 104.2, "low": 103.9}
    snapshot.raw_df = df

    should_entry, reason, meta = EntryGate.evaluate("ENTRY_LONG_EMA_CROSS_MID", snapshot)
    assert should_entry is False
    assert "VETO_LONG_MA15_NOT_UP" in reason


def test_veto_short_ma15_not_down():
    """
    [門禁測試 4] 做空方向性檢驗 (VETO_SHORT_MA15_NOT_DOWN):
    KC 中軌向下，但 MA15 往上推或向下壓制角度不足 (> -0.12 ATR)，禁止逆勢摸頂開空！
    """
    df = _build_mock_df(30, start_price=100.0, step=0.0)
    df["kc_middle"] = [100.0 - i * 0.15 for i in range(len(df))]
    # MA15 上行
    df["ma15"] = [100.0 + i * 0.08 for i in range(len(df))]
    snapshot = DataPipeline.calculate_snapshot("LOBSTER/USDT", df, current_price=96.0)
    snapshot.atr = 1.0
    snapshot.kc_middle = 96.0
    snapshot.kc_upper = 99.0
    snapshot.kc_lower = 93.0
    snapshot.curr_k = {"open": 96.0, "close": 95.5, "high": 96.1, "low": 95.4}
    snapshot.prev_k = {"open": 96.0, "close": 95.9, "high": 96.1, "low": 95.8}
    snapshot.raw_df = df

    should_entry, reason, meta = EntryGate.evaluate("ENTRY_SHORT_EMA_CROSS_MID", snapshot)
    assert should_entry is False
    assert "VETO_SHORT_MA15_NOT_DOWN" in reason


def test_veto_ma_cross_no_room():
    """
    [門禁測試 5] 盈虧比空間限制 (VETO_MA_CROSS_NO_ROOM):
    向上金叉時距離 KC 上軌不足 0.25 ATR，直接頂在天花板，嚴禁撞天花板追多！
    """
    df = _build_mock_df(30, start_price=100.0, step=0.0)
    df["kc_middle"] = [100.0 + i * 0.15 for i in range(len(df))]
    df["ma15"] = [100.0 + i * 0.15 for i in range(len(df))]  # 強勢向上發散
    snapshot = DataPipeline.calculate_snapshot("LOBSTER/USDT", df, current_price=104.4)
    snapshot.atr = 1.0
    snapshot.kc_middle = 103.0
    snapshot.kc_upper = 104.5  # 空間僅 0.1 ATR (< 0.25 ATR)
    snapshot.kc_lower = 101.5
    snapshot.curr_k = {"open": 103.8, "close": 104.4, "high": 104.45, "low": 103.7}
    snapshot.prev_k = {"open": 103.5, "close": 103.8, "high": 103.9, "low": 103.4}
    snapshot.raw_df = df

    should_entry, reason, meta = EntryGate.evaluate("ENTRY_LONG_EMA_CROSS_MID", snapshot)
    assert should_entry is False
    assert "VETO_MA_CROSS_NO_ROOM" in reason
    assert "天花板" in meta["cause"]


def test_veto_ma_cross_weak_body():
    """
    [門禁測試 6] 帶量實體引導 (VETO_MA_CROSS_WEAK_BODY):
    交叉當根 K 線實體 < 0.25 ATR (十字星或微型小棒)，動能不足一票否決！
    """
    df = _build_mock_df(30, start_price=100.0, step=0.0)
    df["kc_middle"] = [100.0 + i * 0.15 for i in range(len(df))]
    df["ma15"] = [100.0 + i * 0.15 for i in range(len(df))]
    snapshot = DataPipeline.calculate_snapshot("LOBSTER/USDT", df, current_price=103.8)
    snapshot.atr = 1.0
    snapshot.kc_middle = 103.0
    snapshot.kc_upper = 105.5
    snapshot.kc_lower = 100.5
    # 實體僅 0.1 ATR (< 0.25 ATR)
    snapshot.curr_k = {"open": 103.7, "close": 103.8, "high": 104.0, "low": 103.6}
    snapshot.prev_k = {"open": 103.5, "close": 103.7, "high": 103.8, "low": 103.4}
    snapshot.raw_df = df

    should_entry, reason, meta = EntryGate.evaluate("ENTRY_LONG_EMA_CROSS_MID", snapshot)
    assert should_entry is False
    assert "VETO_MA_CROSS_WEAK_BODY" in reason


def test_veto_kc_middle_flat():
    """
    [門禁測試 7] KC 中軌走平硬鎖 (VETO_KC_MIDDLE_FLAT):
    中軌 3 根斜率 < 0.12 ATR，判定通道軸心完全水平橫躺，100% 一票否決！
    """
    df = _build_mock_df(30, start_price=100.0, step=0.0)
    df["kc_middle"] = 100.0
    df["ma15"] = [100.0 + i * 0.05 for i in range(len(df))]
    snapshot = DataPipeline.calculate_snapshot("LOBSTER/USDT", df, current_price=100.5)
    snapshot.atr = 1.0
    snapshot.kc_middle = 100.0
    snapshot.kc_upper = 102.0
    snapshot.kc_lower = 98.0
    snapshot.curr_k = {"open": 100.1, "close": 100.5, "high": 100.6, "low": 100.0}
    snapshot.prev_k = {"open": 100.0, "close": 100.1, "high": 100.2, "low": 99.9}
    snapshot.raw_df = df

    should_entry, reason, meta = EntryGate.evaluate("ENTRY_LONG_EMA_CROSS_MID", snapshot)
    assert should_entry is False
    assert "VETO_KC_MIDDLE_FLAT" in reason
    assert abs(meta["kc_mid_slope"]) < 0.12


def test_veto_buy_kc_not_up():
    """
    [門禁測試 8] 做多時 KC 中軌未明確上揚 (VETO_BUY_KC_NOT_UP):
    KC 中軌向下傾斜或未達 0.12 ATR，禁止開多！
    """
    df = _build_mock_df(30, start_price=100.0, step=0.0)
    df["kc_middle"] = [100.0 - i * 0.05 for i in range(len(df))]
    df["ma15"] = [100.0 + i * 0.05 for i in range(len(df))]
    snapshot = DataPipeline.calculate_snapshot("LOBSTER/USDT", df, current_price=98.5)
    snapshot.atr = 1.0
    snapshot.kc_middle = 98.5
    snapshot.kc_upper = 100.5
    snapshot.kc_lower = 96.5
    snapshot.curr_k = {"open": 98.1, "close": 98.5, "high": 98.6, "low": 98.0}
    snapshot.prev_k = {"open": 98.0, "close": 98.1, "high": 98.2, "low": 97.9}
    snapshot.raw_df = df

    should_entry, reason, meta = EntryGate.evaluate("ENTRY_LONG_EMA_CROSS_MID", snapshot)
    assert should_entry is False
    assert "VETO_BUY_KC_NOT_UP" in reason


def test_veto_sell_kc_not_down():
    """
    [門禁測試 9] 做空時 KC 中軌未明確下傾 (VETO_SELL_KC_NOT_DOWN):
    KC 中軌向上傾斜或未達 -0.12 ATR，禁止開空！
    """
    df = _build_mock_df(30, start_price=100.0, step=0.0)
    df["kc_middle"] = [100.0 + i * 0.05 for i in range(len(df))]
    df["ma15"] = [100.0 - i * 0.05 for i in range(len(df))]
    snapshot = DataPipeline.calculate_snapshot("LOBSTER/USDT", df, current_price=101.5)
    snapshot.atr = 1.0
    snapshot.kc_middle = 101.5
    snapshot.kc_upper = 103.5
    snapshot.kc_lower = 99.5
    snapshot.curr_k = {"open": 101.9, "close": 101.5, "high": 102.0, "low": 101.4}
    snapshot.prev_k = {"open": 102.0, "close": 101.9, "high": 102.1, "low": 101.8}
    snapshot.raw_df = df

    should_entry, reason, meta = EntryGate.evaluate("ENTRY_SHORT_EMA_CROSS_MID", snapshot)
    assert should_entry is False
    assert "VETO_SELL_KC_NOT_DOWN" in reason

