"""
tests/test_leveraged_roe_chop_and_cooldown_gates.py
===================================================
針對 2026-10-22 最新需求落實之專屬單元測試：
1. 修正鎖利為真實合約 ROE（納入 5 倍槓桿）：
   - peak_roe 與當前利潤乘上槓桿倍數計算
   - 達標 10% ROE 鎖留 70% 峰值利潤
   - 達標 15% ROE 鎖留 80% 峰值利潤
2. 建立進場前置過濾 (Chop Filter & 大週期順勢)：
   - 8 根 K 棒內 MA5/MA15 交叉 >= 2 次，強制 VETO (BLOCKED_MA5_MA15_CROSS_CHOP)
   - MA5/MA15 間距 < 0.2 ATR，強制 VETO (BLOCKED_MA5_MA15_GAP_TOO_NARROW)
   - 通道極度收縮 Squeeze <= 25% 禁止開倉 (BLOCKED_KC_SQUEEZE)
   - 大週期趨勢方向一票否決 (BEARISH 阻斷開多，BULLISH 阻斷開空)
3. 實作開倉冷卻與頻率上限：
   - 平倉後強制冷卻至少 3 根 1m K 棒 (180 秒) (VETO_EXIT_COOLDOWN)
   - 單一合約 1 小時內開倉上限為 3 次 (BLOCKED_HOURLY_ENTRY_LIMIT_EXCEEDED)
"""
import time
import pytest
import pandas as pd
import numpy as np

from core.gates.unified_pipeline_gates import EntryGate, ExitGate
from core.services.data_pipeline import DataPipeline, MarketDataSnapshot
from core.services.execution_manager import ExecutionManager


def _build_mock_df(n_bars=30, start_price=100.0, step=0.1):
    now_ms = int(time.time() * 1000)
    bars = []
    curr = start_price
    for i in range(n_bars):
        o = curr
        c = curr + step
        h = max(o, c) + 0.05
        l = min(o, c) - 0.05
        curr = c
        bars.append({
            "timestamp": now_ms - (n_bars - i) * 60_000,
            "open": o,
            "high": h,
            "low": l,
            "close": c,
            "volume": 1000.0,
        })
    return pd.DataFrame(bars)


# ==============================================================================
# 測試 1：真實合約 ROE（5 倍槓桿）雙階梯鎖利
# ==============================================================================
def test_exit_gate_5x_leverage_10pct_roe_locks_70pct():
    """
    5 倍槓桿下：價格波幅達 +2.4% -> ROE = +12.0% (已達標 10% 門檻)。
    當利潤回吐至 +1.5% 價格波幅 (當前 ROE = 7.5%)，
    跌破 70% 鎖利底線 (12% * 70% = 8.4%)，必須立即觸發 PROFIT_LOCK！
    """
    df = _build_mock_df(30, start_price=100.0, step=0.1)
    snapshot = DataPipeline.calculate_snapshot("LOBSTER/USDT", df, current_price=101.5)
    # 持倉設定 5 倍槓桿，進場價 100.0，最高價格波幅 +2.4% (即 ROE 12%)
    pos = {
        "side": "LONG",
        "entry_price": 100.0,
        "leverage": 5.0,
        "peak_roe": 0.12,  # 12% ROE
    }
    # 當前報價 101.5 (價格 +1.5%, 5x 槓桿 ROE = 7.5% <= 8.4%)
    should_exit, reason, meta = ExitGate.evaluate(pos, snapshot, current_price=101.5)
    assert should_exit is True
    assert reason == "PROFIT_LOCK"
    assert "10%鎖70%" in meta.get("cause", "")
    assert meta["unrealized_roe"] == pytest.approx(0.075)
    assert meta["peak_roe"] == pytest.approx(0.12)


def test_exit_gate_5x_leverage_15pct_roe_locks_80pct():
    """
    5 倍槓桿下：價格波幅達 +3.6% -> ROE = +18.0% (已達標 15% 門檻)。
    當利潤回吐至 +2.7% 價格波幅 (當前 ROE = 13.5%)，
    跌破 80% 鎖利底線 (18% * 80% = 14.4%)，必須立即觸發 PROFIT_LOCK！
    """
    df = _build_mock_df(30, start_price=100.0, step=0.1)
    snapshot = DataPipeline.calculate_snapshot("LOBSTER/USDT", df, current_price=102.7)
    pos = {
        "side": "LONG",
        "entry_price": 100.0,
        "leverage": 5.0,
        "peak_roe": 0.18,  # 18% ROE
    }
    should_exit, reason, meta = ExitGate.evaluate(pos, snapshot, current_price=102.7)
    assert should_exit is True
    assert reason == "PROFIT_LOCK"
    assert "15%鎖80%" in meta.get("cause", "")
    assert meta["unrealized_roe"] == pytest.approx(0.135)
    assert meta["peak_roe"] == pytest.approx(0.18)


# ==============================================================================
# 測試 2：均線糾纏震盪過濾 (Chop Filter)
# ==============================================================================
def test_entry_gate_blocked_by_ma5_ma15_cross_chop():
    """
    8 根 K 棒內 MA5 與 MA15 出現 2 次或以上交叉，判定為死水糾纏震盪，
    必須 100% 觸發 BLOCKED_MA5_MA15_CROSS_CHOP 一票否決！
    """
    df = _build_mock_df(30, start_price=100.0, step=0.1)
    snapshot = DataPipeline.calculate_snapshot("LOBSTER/USDT", df, current_price=103.0)
    # 在 snapshot.raw_df 的最近 8 根 K 棒人工製造反覆均線交叉震盪
    for i in range(8):
        idx = snapshot.raw_df.index[-8 + i]
        if i % 2 == 0:
            snapshot.raw_df.loc[idx, "ma5"] = 100.2
            snapshot.raw_df.loc[idx, "ma15"] = 100.0
        else:
            snapshot.raw_df.loc[idx, "ma5"] = 99.8
            snapshot.raw_df.loc[idx, "ma15"] = 100.0

    snapshot.curr_k = {"open": 102.5, "high": 103.1, "low": 102.4, "close": 103.0, "ma5": 102.8}
    snapshot.prev_k = {"open": 102.0, "high": 102.6, "low": 101.9, "close": 102.5, "ma5": 102.2}
    should_entry, reason, meta = EntryGate.evaluate("BREAKOUT_LONG", snapshot)
    assert should_entry is False
    assert reason == "BLOCKED_MA5_MA15_CROSS_CHOP"
    assert meta.get("cross_count_8", 0) >= 2


def test_entry_gate_blocked_by_narrow_ma_gap():
    """
    當非破軌模式下，MA5 與 MA15 間距 < 0.2 ATR 時，均線黏合無波幅空間，
    必須 100% 觸發 BLOCKED_MA5_MA15_GAP_TOO_NARROW 一票否決！
    """
    df = _build_mock_df(30, start_price=100.0, step=0.1)
    snapshot = DataPipeline.calculate_snapshot("LOBSTER/USDT", df, current_price=100.2)
    snapshot.atr = 2.0  # 0.2 ATR = 0.4
    snapshot.ma5 = 100.1
    snapshot.ma15 = 100.0  # gap = 0.1 < 0.4
    snapshot.curr_k = {"open": 100.0, "high": 100.3, "low": 99.9, "close": 100.2, "ma5": 100.1}
    snapshot.prev_k = {"open": 99.8, "high": 100.1, "low": 99.7, "close": 100.0, "ma5": 99.9}
    should_entry, reason, meta = EntryGate.evaluate("ENTRY_LONG_EMA_CROSS_MID", snapshot)
    assert should_entry is False
    assert reason == "BLOCKED_MA5_MA15_GAP_TOO_NARROW"


def test_entry_gate_blocked_by_kc_squeeze():
    """
    當前 KC 通道寬度縮小至過去 20 根最大寬度的 25% 或以下時，
    判定為極度收縮 Squeeze，必須觸發 BLOCKED_KC_SQUEEZE 拒絕開倉！
    """
    df = _build_mock_df(30, start_price=100.0, step=0.1)
    snapshot = DataPipeline.calculate_snapshot("LOBSTER/USDT", df, current_price=103.0)
    # 人工在過去 20 根製造大通道 (寬度 10.0)
    snapshot.raw_df.loc[snapshot.raw_df.index[-15], "kc_upper"] = 105.0
    snapshot.raw_df.loc[snapshot.raw_df.index[-15], "kc_lower"] = 95.0  # bw = 10.0
    # 當前通道極度收縮至 101.0 - 99.0 = 2.0 (2.0 / 10.0 = 20% <= 25%)
    snapshot.kc_upper = 101.0
    snapshot.kc_lower = 99.0
    should_entry, reason, meta = EntryGate.evaluate("BREAKOUT_LONG", snapshot)
    assert should_entry is False
    assert reason == "BLOCKED_KC_SQUEEZE"
    assert meta.get("bw_ratio", 1.0) <= 0.25


# ==============================================================================
# 測試 3：開倉冷卻 (180 秒) 與單一合約 1 小時頻率限制 (3 次)
# ==============================================================================
def test_entry_gate_blocked_by_post_exit_cooldown():
    """
    平倉後在 180 秒 (3 根 1m K 棒) 內，任何新開倉必須被 VETO_EXIT_COOLDOWN 阻斷！
    """
    df = _build_mock_df(30, start_price=100.0, step=0.1)
    snapshot = DataPipeline.calculate_snapshot("LOBSTER/USDT", df, current_price=103.0)
    last_exit_ctx = {
        "timestamp": time.time() - 60.0,  # 剛平倉 60 秒 (未滿 180 秒)
        "side": "LONG",
        "current_time": time.time(),
    }
    should_entry, reason, meta = EntryGate.evaluate("BREAKOUT_LONG", snapshot, last_exit_context=last_exit_ctx)
    assert should_entry is False
    assert reason == "VETO_EXIT_COOLDOWN"
    assert meta["elapsed_sec"] < 180


def test_entry_gate_blocked_by_hourly_entry_limit():
    """
    單一合約在 1 小時內開倉次數已達 3 次時，
    強制觸發 BLOCKED_HOURLY_ENTRY_LIMIT_EXCEEDED 拒絕繼續下單！
    """
    df = _build_mock_df(30, start_price=100.0, step=0.1)
    snapshot = DataPipeline.calculate_snapshot("LOBSTER/USDT", df, current_price=103.0)
    last_exit_ctx = {
        "timestamp": time.time() - 300.0,  # 已過 180 秒冷卻
        "hourly_entry_count": 3,           # 1 小時內已開 3 次
        "current_time": time.time(),
    }
    should_entry, reason, meta = EntryGate.evaluate("BREAKOUT_LONG", snapshot, last_exit_context=last_exit_ctx)
    assert should_entry is False
    assert reason == "BLOCKED_HOURLY_ENTRY_LIMIT_EXCEEDED"
    assert meta["hourly_count"] >= 3


def test_ma5_slope_flat_blocks_both_long_and_short():
    """
    測試 MA5 平行走平（|ΔMA5| <= ε = 0.05 * ATR）：
    多空皆一票否決 VETO，觸發 BLOCKED_MA5_FLAT。
    """
    atr_val = 1.0
    epsilon = 0.05 * atr_val  # 0.05
    # 微小波動 0.02 <= 0.05
    ma5_prev = 100.00
    ma5_curr = 100.02

    # 測試多單
    ok_long, reason_long, meta_long = EntryGate.evaluate_ma5_slope(
        ma5_curr=ma5_curr,
        ma5_prev=ma5_prev,
        atr_val=atr_val,
        signal_side="LONG",
        threshold_atr_mult=0.05,
    )
    assert ok_long is False
    assert reason_long == "BLOCKED_MA5_FLAT"
    assert meta_long["slope_status"] == "FLAT"

    # 測試空單
    ok_short, reason_short, meta_short = EntryGate.evaluate_ma5_slope(
        ma5_curr=ma5_curr,
        ma5_prev=ma5_prev,
        atr_val=atr_val,
        signal_side="SHORT",
        threshold_atr_mult=0.05,
    )
    assert ok_short is False
    assert reason_short == "BLOCKED_MA5_FLAT"
    assert meta_short["slope_status"] == "FLAT"


def test_ma5_slope_rising_allows_long_blocks_short():
    """
    測試 MA5 向上（ΔMA5 > ε = 0.05 * ATR）：
    允許做多，但做空被一票否決 VETO_MA5_SLOPE_NOT_DOWNWARD。
    """
    atr_val = 1.0
    ma5_prev = 100.0
    ma5_curr = 100.2  # ΔMA5 = 0.2 > 0.05

    ok_long, reason_long, meta_long = EntryGate.evaluate_ma5_slope(
        ma5_curr=ma5_curr,
        ma5_prev=ma5_prev,
        atr_val=atr_val,
        signal_side="LONG",
        threshold_atr_mult=0.05,
    )
    assert ok_long is True
    assert reason_long == "PASSED"
    assert meta_long["slope_status"] == "UP"

    ok_short, reason_short, meta_short = EntryGate.evaluate_ma5_slope(
        ma5_curr=ma5_curr,
        ma5_prev=ma5_prev,
        atr_val=atr_val,
        signal_side="SHORT",
        threshold_atr_mult=0.05,
    )
    assert ok_short is False
    assert reason_short == "VETO_MA5_SLOPE_NOT_DOWNWARD"
    assert meta_short["slope_status"] == "UP"


def test_ma5_slope_falling_allows_short_blocks_long():
    """
    測試 MA5 向下（ΔMA5 < -ε = -0.05 * ATR）：
    允許做空，但做多被一票否決 VETO_MA5_SLOPE_NOT_UPWARD。
    """
    atr_val = 1.0
    ma5_prev = 100.2
    ma5_curr = 100.0  # ΔMA5 = -0.2 < -0.05

    ok_short, reason_short, meta_short = EntryGate.evaluate_ma5_slope(
        ma5_curr=ma5_curr,
        ma5_prev=ma5_prev,
        atr_val=atr_val,
        signal_side="SHORT",
        threshold_atr_mult=0.05,
    )
    assert ok_short is True
    assert reason_short == "PASSED"
    assert meta_short["slope_status"] == "DOWN"

    ok_long, reason_long, meta_long = EntryGate.evaluate_ma5_slope(
        ma5_curr=ma5_curr,
        ma5_prev=ma5_prev,
        atr_val=atr_val,
        signal_side="LONG",
        threshold_atr_mult=0.05,
    )
    assert ok_long is False
    assert reason_long == "VETO_MA5_SLOPE_NOT_UPWARD"
    assert meta_long["slope_status"] == "DOWN"


def test_entry_gate_evaluate_with_ma5_flat_triggers_veto():
    """
    測試 EntryGate.evaluate 整合流程：
    若 MA5 走平 (|ΔMA5| <= 0.05*ATR)，直接觸發 BLOCKED_MA5_FLAT。
    """
    df = _build_mock_df(30, start_price=100.0, step=0.1)
    snapshot = DataPipeline.calculate_snapshot("LOBSTER/USDT", df, current_price=103.0)
    snapshot.atr = 1.0
    snapshot.kc_middle = 100.0
    snapshot.kc_upper = 102.0
    snapshot.kc_lower = 98.0
    snapshot.ema_fast = 102.0
    snapshot.ema_slow = 101.0
    # 設置微幅走平：ΔMA5 = 0.01 <= 0.05*ATR
    snapshot.prev_k = {"open": 102.5, "close": 102.8, "ma5": 102.80, "ma15": 102.0}
    snapshot.curr_k = {"open": 102.8, "close": 103.0, "high": 103.1, "low": 102.7, "ma5": 102.81, "ma15": 102.0}

    should_entry, reason, meta = EntryGate.evaluate("BREAKOUT_LONG", snapshot)
    assert should_entry is False
    assert reason == "BLOCKED_MA5_FLAT"
    assert "走平" in meta["cause"]


def test_exit_gate_is_healthy_pullback_vetoes_exit_on_long():
    """
    測試多單健康回踩保護 (is_healthy_pullback)：
    MA5 明顯向上且價格守穩在 MA15 之上，即使價格回踩跌破 MA5 或回踩中軌，
    強制一票否決平倉 (VETO_HOLD)，抱牢部位！
    """
    pos = {"side": "LONG", "entry_price": 102.0, "peak_roe": 0.0}
    df = _build_mock_df(30, start_price=100.0, step=0.1)
    df.loc[df.index[-1], ["open", "high", "low", "close"]] = [102.2, 102.4, 101.9, 102.0]
    snapshot = DataPipeline.calculate_snapshot("LOBSTER/USDT", df, current_price=102.0)
    snapshot.kc_upper = 103.0
    snapshot.ma15 = 101.5
    # 當前棒收盤 102.0 >= ma15=101.5，但小於 ma5_curr=102.3 (回踩跌破 MA5)
    # ma5_curr=102.3 > ma5_prev=102.0 (MA5向上)
    snapshot.prev_k = {"open": 101.8, "close": 102.2, "ma5": 102.0, "high": 102.3, "low": 101.7}
    snapshot.curr_k = {"open": 102.2, "close": 102.0, "ma5": 102.3, "high": 102.4, "low": 101.9}
    
    # 調用 is_healthy_pullback
    is_hp, cause = ExitGate.is_healthy_pullback(
        side="LONG",
        ma5_curr=102.3,
        ma5_prev=102.0,
        ma15=101.5,
        current_close=102.0,
        quote=102.0,
    )
    assert is_hp is True
    assert "健康回踩續抱" in cause

    # 整合調用 ExitGate.evaluate (跌破 MA5 但站在 MA15 之上)
    should_exit, reason, meta = ExitGate.evaluate(pos, snapshot, current_price=102.0)
    assert should_exit is False
    assert reason == "VETO_HOLD"
    assert meta.get("healthy_pullback") is True


def test_exit_gate_trend_decay_exit_on_long():
    """
    測試多單趨勢衰退確認平倉 (均線實質轉折)：
    非峰頂樞紐形態，純緩步陰跌收線確認跌破 MA15，且 MA5 轉為走平或向下，且大週期均線轉弱，放行平多！
    """
    pos = {"side": "LONG", "entry_price": 101.5, "peak_roe": 0.0}
    df = _build_mock_df(30, start_price=100.0, step=0.1)
    # 構造無衝頂形態（緩跌：high 遞減，非真峰頂中間高形態）
    df.loc[df.index[-3], ["open", "high", "low", "close"]] = [102.5, 102.6, 102.0, 102.2]
    df.loc[df.index[-2], ["open", "high", "low", "close"]] = [102.2, 102.3, 101.5, 101.8]
    df.loc[df.index[-1], ["open", "high", "low", "close"]] = [101.8, 101.9, 100.7, 100.8]
    snapshot = DataPipeline.calculate_snapshot("LOBSTER/USDT", df, current_price=100.8)
    snapshot.atr = 1.0
    snapshot.kc_middle = 100.5
    snapshot.kc_middle_slope = -0.01
    snapshot.ma15 = 102.0
    snapshot.ema_fast = 101.5
    snapshot.ema_slow = 102.5  # 大週期均線死叉
    snapshot.kc_lower = 99.0   # 防止觸發反側外軌擊穿
    snapshot.macro_trend = "NEUTRAL"
    snapshot.prev_k = {"open": 102.2, "close": 101.8, "ma5": 101.8, "high": 102.3, "low": 101.5}
    snapshot.curr_k = {"open": 101.8, "close": 100.8, "ma5": 101.2, "high": 101.9, "low": 100.7}
    snapshot.raw_df = df

    should_exit, reason, meta = ExitGate.evaluate(pos, snapshot, current_price=100.8)
    assert should_exit is True
    assert reason == "LONG_EXIT_MA15_BREAK_AND_MA5_DECAY"


def test_exit_gate_is_healthy_pullback_vetoes_exit_on_short():
    """
    測試空單健康反彈保護 (is_healthy_pullback)：
    MA5 明顯向下且價格壓制在 MA15 之下，盤中反彈一律強制否決平空 (VETO_HOLD)！
    """
    pos = {"side": "SHORT", "entry_price": 100.0, "peak_roe": 0.05}
    df = _build_mock_df(30, start_price=100.0, step=-0.1)
    snapshot = DataPipeline.calculate_snapshot("LOBSTER/USDT", df, current_price=97.5)
    snapshot.ma15 = 98.5
    # 收盤 98.0 <= ma15=98.5，ma5_curr=97.5 < ma5_prev=98.0 (MA5向下)
    is_hp, cause = ExitGate.is_healthy_pullback(
        side="SHORT",
        ma5_curr=97.5,
        ma5_prev=98.0,
        ma15=98.5,
        current_close=98.0,
        quote=98.0,
    )
    assert is_hp is True
    assert "健康反抽續抱" in cause


def test_entry_gate_anti_churning_blocks_reentry_at_same_price():
    """
    測試禁止原地追單 (Anti-Churning Re-entry Filter)：
    前次平多後再次開多，最新價未突破前次平倉最高價時，
    強制觸發 BLOCKED_REENTRY_PRICE_NOT_EXCEEDED 拒絕進場！
    """
    df = _build_mock_df(30, start_price=100.0, step=0.1)
    snapshot = DataPipeline.calculate_snapshot("LOBSTER/USDT", df, current_price=104.0)
    snapshot.kc_upper = 103.5
    snapshot.ema_fast = 103.0
    snapshot.ema_slow = 102.0
    snapshot.atr = 1.0
    snapshot.prev_k = {"open": 103.2, "close": 103.8, "ma5": 103.5, "ma15": 102.5}
    snapshot.curr_k = {"open": 103.8, "close": 104.0, "high": 104.2, "low": 103.7, "ma5": 103.9, "ma15": 102.5}

    # 前次在 105.0 平多，已過 180 秒冷卻，但最新價 104.0 <= 105.0
    last_exit_ctx = {
        "side": "LONG",
        "timestamp": time.time() - 300.0,
        "exit_high": 105.0,
        "current_time": time.time(),
    }
    should_entry, reason, meta = EntryGate.evaluate("BREAKOUT_LONG", snapshot, last_exit_context=last_exit_ctx)
    assert should_entry is False
    assert reason == "BLOCKED_REENTRY_PRICE_NOT_EXCEEDED"
    assert "未實質突破前次平倉最高價" in meta["cause"]


def test_entry_gate_allows_reentry_when_price_breaks_exit_high():
    """
    測試突破前次平倉高點後正常允許延續再進場：
    最新價 105.5 > 前次最高價 105.0，放行開多！
    """
    df = _build_mock_df(30, start_price=100.0, step=0.1)
    snapshot = DataPipeline.calculate_snapshot("LOBSTER/USDT", df, current_price=105.5)
    snapshot.kc_upper = 104.5
    snapshot.ema_fast = 104.0
    snapshot.ema_slow = 103.0
    snapshot.atr = 1.0
    snapshot.prev_k = {"open": 104.5, "close": 105.0, "ma5": 104.8, "ma15": 103.5}
    snapshot.curr_k = {"open": 105.0, "close": 105.5, "high": 105.6, "low": 104.9, "ma5": 105.2, "ma15": 103.5}

    last_exit_ctx = {
        "side": "LONG",
        "timestamp": time.time() - 300.0,
        "exit_high": 105.0,
        "current_time": time.time(),
    }
    should_entry, reason, meta = EntryGate.evaluate("BREAKOUT_LONG", snapshot, last_exit_context=last_exit_ctx)
    assert should_entry is True


def test_exit_gate_strictly_holds_on_early_pullback_after_entry_long():
    """
    [開倉後回踩不平倉鐵律] 多單剛開倉進場後，價格回踩小陰線甚至跌破 MA5，
    未達實質展開 (peak_roe < 0.75% 且 peak_gain < 0.5*ATR)，
    100% 強制觸發 VETO_HOLD，early_pullback_shield=True，絕對不平倉！
    """
    pos = {"side": "LONG", "entry_price": 100.0, "peak_roe": 0.002}
    df = _build_mock_df(30, start_price=100.0, step=0.05)
    # 剛開倉小幅回踩
    df.loc[df.index[-2], ["open", "high", "low", "close"]] = [100.0, 100.2, 99.9, 100.1]
    df.loc[df.index[-1], ["open", "high", "low", "close"]] = [100.1, 100.15, 99.7, 99.8]
    snapshot = DataPipeline.calculate_snapshot("LOBSTER/USDT", df, current_price=99.8)
    snapshot.atr = 1.0
    snapshot.kc_middle = 99.5
    snapshot.kc_lower = 98.0   # 確保回踩在通道內，不觸發反側外軌破位
    snapshot.kc_middle_slope = 0.001
    snapshot.prev_k = {"open": 100.0, "high": 100.2, "low": 99.9, "close": 100.1, "ma5": 100.1}
    snapshot.curr_k = {"open": 100.1, "high": 100.15, "low": 99.7, "close": 99.8, "ma5": 99.95}

    should_exit, reason, meta = ExitGate.evaluate(pos, snapshot, current_price=99.8)
    assert should_exit is False
    assert reason == "VETO_HOLD"
    assert meta.get("early_pullback_shield") is True
    assert "開倉後回踩不平倉鐵律" in meta.get("cause", "")


def test_exit_gate_strictly_holds_on_early_bounce_after_entry_short():
    """
    [開倉後反抽不平空鐵律] 空單剛開倉進場後，價格反彈小陽線甚至抽回 MA5，
    未達實質展開 (peak_roe < 0.75% 且探底展開 < 0.5*ATR)，
    100% 強制觸發 VETO_HOLD，early_pullback_shield=True，絕對不平空！
    """
    pos = {"side": "SHORT", "entry_price": 100.0, "peak_roe": 0.002}
    df = _build_mock_df(30, start_price=100.0, step=-0.05)
    df.loc[df.index[-2], ["open", "high", "low", "close"]] = [100.0, 100.1, 99.8, 99.9]
    df.loc[df.index[-1], ["open", "high", "low", "close"]] = [99.9, 100.3, 99.85, 100.2]
    snapshot = DataPipeline.calculate_snapshot("LOBSTER/USDT", df, current_price=100.2)
    snapshot.atr = 1.0
    snapshot.kc_middle = 100.5
    snapshot.kc_upper = 102.0  # 確保反抽在通道內，不觸發反側外軌破位
    snapshot.kc_middle_slope = -0.001
    snapshot.prev_k = {"open": 100.0, "high": 100.1, "low": 99.8, "close": 99.9, "ma5": 99.9}
    snapshot.curr_k = {"open": 99.9, "high": 100.3, "low": 99.85, "close": 100.2, "ma5": 100.05}

    should_exit, reason, meta = ExitGate.evaluate(pos, snapshot, current_price=100.2)
    assert should_exit is False
    assert reason == "VETO_HOLD"
    assert meta.get("early_pullback_shield") is True
    assert "開倉後反抽不平空鐵律" in meta.get("cause", "")


def test_entry_gate_veto_short_when_ma3_sloping_up():
    """
    [MA3 短均線動能鐵律] MA3 明顯向上勾頭 (curr_ma3 > prev_ma3)，
    短期反彈動能抬頭，100% 強制觸發 VETO_SHORT_MA3_SLOPING_UP 否決開空！
    """
    df = _build_mock_df(30, start_price=100.0, step=-0.05)
    snapshot = DataPipeline.calculate_snapshot("LOBSTER/USDT", df, current_price=98.0)
    snapshot.atr = 1.0
    snapshot.kc_middle = 100.0
    snapshot.kc_upper = 101.5
    snapshot.kc_lower = 98.5
    snapshot.kc_middle_slope = -0.01
    snapshot.prev_k = {"open": 98.5, "close": 97.8, "ma3": 98.0, "ma5": 98.2, "ma15": 99.5}
    # 當前棒 MA3 = 98.2 > 98.0，向上勾頭 (同向黑棒)
    snapshot.curr_k = {"open": 98.1, "close": 98.0, "ma3": 98.2, "ma5": 98.1, "ma15": 99.3}

    should_entry, reason, meta = EntryGate.evaluate("BREAKDOWN_SHORT", snapshot)
    assert should_entry is False
    assert reason == "VETO_SHORT_MA3_SLOPING_UP"
    assert "MA3 明顯向上勾頭" in meta["cause"]


def test_entry_gate_veto_short_when_price_above_ma3():
    """
    [MA3 短均線動能鐵律] 價格收在 MA3 之上 (close > ma3)，
    短期多頭動能占優，迎面撞上反彈波，100% 強制觸發 VETO_SHORT_PRICE_ABOVE_MA3 否決開空！
    """
    df = _build_mock_df(30, start_price=100.0, step=-0.05)
    snapshot = DataPipeline.calculate_snapshot("LOBSTER/USDT", df, current_price=98.5)
    snapshot.atr = 1.0
    snapshot.kc_middle = 100.0
    snapshot.kc_upper = 101.5
    snapshot.kc_lower = 98.5
    snapshot.kc_middle_slope = -0.01
    snapshot.prev_k = {"open": 98.5, "close": 98.0, "ma3": 98.5, "ma5": 98.7, "ma15": 99.5}
    # 當前棒 MA3 = 98.2 (走平向下)，但 close = 98.5 > ma3 = 98.2 (同向黑棒)
    snapshot.curr_k = {"open": 98.6, "close": 98.5, "ma3": 98.2, "ma5": 98.3, "ma15": 99.3}

    should_entry, reason, meta = EntryGate.evaluate("BREAKDOWN_SHORT", snapshot)
    assert should_entry is False
    assert reason == "VETO_SHORT_PRICE_ABOVE_MA3"
    assert "價格站上 MA3 之上" in meta["cause"]


def test_entry_gate_veto_long_when_ma3_sloping_down():
    """
    [MA3 短均線動能鐵律] MA3 明顯向下下拐 (curr_ma3 < prev_ma3)，
    短期動能向下，100% 強制觸發 VETO_LONG_MA3_SLOPING_DOWN 否決開多！
    """
    df = _build_mock_df(30, start_price=100.0, step=0.05)
    snapshot = DataPipeline.calculate_snapshot("LOBSTER/USDT", df, current_price=102.0)
    snapshot.atr = 1.0
    snapshot.kc_middle = 100.0
    snapshot.kc_upper = 101.5
    snapshot.kc_lower = 98.5
    snapshot.kc_middle_slope = 0.01
    snapshot.prev_k = {"open": 101.5, "close": 102.2, "ma3": 102.0, "ma5": 101.8, "ma15": 100.5}
    # 當前棒 MA3 = 101.8 < 102.0，向下下拐 (同向紅棒)
    snapshot.curr_k = {"open": 101.9, "close": 102.0, "ma3": 101.8, "ma5": 101.9, "ma15": 100.8}

    should_entry, reason, meta = EntryGate.evaluate("BREAKOUT_LONG", snapshot)
    assert should_entry is False
    assert reason == "VETO_LONG_MA3_SLOPING_DOWN"
    assert "MA3 明顯向下下拐" in meta["cause"]


def test_entry_gate_veto_long_when_price_below_ma3():
    """
    [MA3 短均線動能鐵律] 價格被 MA3 壓制 (close < ma3)，
    短期多頭未確立，100% 強制觸發 VETO_LONG_PRICE_BELOW_MA3 否決開多！
    """
    df = _build_mock_df(30, start_price=100.0, step=0.05)
    snapshot = DataPipeline.calculate_snapshot("LOBSTER/USDT", df, current_price=101.5)
    snapshot.atr = 1.0
    snapshot.kc_middle = 100.0
    snapshot.kc_upper = 101.5
    snapshot.kc_lower = 98.5
    snapshot.kc_middle_slope = 0.01
    snapshot.prev_k = {"open": 101.0, "close": 101.8, "ma3": 101.5, "ma5": 101.2, "ma15": 100.5}
    # 當前棒 MA3 = 101.8 (向上)，但 close = 101.5 < ma3 = 101.8 (同向紅棒)
    snapshot.curr_k = {"open": 101.4, "close": 101.5, "ma3": 101.8, "ma5": 101.6, "ma15": 100.8}

    should_entry, reason, meta = EntryGate.evaluate("BREAKOUT_LONG", snapshot)
    assert should_entry is False
    assert reason == "VETO_LONG_PRICE_BELOW_MA3"
    assert "價格被 MA3 壓制" in meta["cause"]


def test_entry_gate_veto_kc_squeeze_dead():
    """
    [死魚盤硬性鎖死 1] 肯特納通道極度收縮成一直線 (Bandwidth < 0.008 或 歷史極端壓縮)，
    波動率徹底乾涸歸零，100% 強制觸發 VETO_KC_SQUEEZE_DEAD 阻斷任何進場！
    """
    df = _build_mock_df(30, start_price=100.0, step=0.0)
    snapshot = DataPipeline.calculate_snapshot("LOBSTER/USDT", df, current_price=100.0)
    snapshot.kc_middle = 100.0
    snapshot.kc_upper = 100.2  # bw = 0.4 / 100.0 = 0.004 < 0.008
    snapshot.kc_lower = 99.8
    snapshot.atr = 0.5
    snapshot.curr_k = {"open": 100.0, "close": 99.98, "high": 100.05, "low": 99.95}
    snapshot.prev_k = {"open": 100.0, "close": 100.01, "high": 100.04, "low": 99.98}

    should_entry, reason, meta = EntryGate.evaluate("ENTRY_SHORT_EMA_CROSS_MID", snapshot)
    assert should_entry is False
    assert reason == "VETO_KC_SQUEEZE_DEAD"
    assert "死魚盤硬性鎖死" in meta["cause"]


def test_entry_gate_veto_ma15_long_flat():
    """
    [死魚盤硬性鎖死 2] 過去 8 根 K 棒內 MA15 極差 < 0.25 ATR，
    均線實質水平走平，100% 強制觸發 VETO_MA15_LONG_FLAT 阻斷開倉！
    """
    df = _build_mock_df(30, start_price=100.0, step=0.001)
    df["ma15"] = 100.0  # 完全平走
    snapshot = DataPipeline.calculate_snapshot("LOBSTER/USDT", df, current_price=100.0)
    snapshot.atr = 1.0
    snapshot.kc_middle = 100.0
    snapshot.kc_upper = 101.5  # bw 正常
    snapshot.kc_lower = 98.5
    snapshot.curr_k = {"open": 100.0, "close": 99.95, "high": 100.05, "low": 99.9}
    snapshot.prev_k = {"open": 100.0, "close": 99.98, "high": 100.05, "low": 99.9}
    snapshot.raw_df = df

    should_entry, reason, meta = EntryGate.evaluate("ENTRY_SHORT_EMA_CROSS_MID", snapshot)
    assert should_entry is False
    assert reason == "VETO_MA15_LONG_FLAT"
    assert "過去8根MA15波動極差" in meta["cause"]


def test_entry_gate_veto_ma_entanglement():
    """
    [死魚盤硬性鎖死 3] 過去 5 根 K 棒 MA3 與 MA15 平均差距 < 0.15 ATR，
    兩線高度黏合持續纏繞，100% 強制觸發 VETO_MA_ENTANGLEMENT 阻斷開倉！
    """
    df = _build_mock_df(30, start_price=100.0, step=0.01)
    # 構造 MA15 有微幅波動避免觸發 MA15_LONG_FLAT，但 MA3 與 MA15 極度黏合 (<0.15 ATR)
    df["ma15"] = [100.0 + i * 0.05 for i in range(len(df))]  # range 8 根 = 0.4 > 0.25 ATR (1.0*0.25=0.25)
    df["ma3"] = df["ma15"] + 0.02  # gap = 0.02 < 0.15 * 1.0
    snapshot = DataPipeline.calculate_snapshot("LOBSTER/USDT", df, current_price=101.5)
    snapshot.atr = 1.0
    snapshot.kc_middle = 101.5
    snapshot.kc_upper = 103.0  # bw 正常
    snapshot.kc_lower = 100.0
    snapshot.curr_k = {"open": 101.5, "close": 101.4, "high": 101.6, "low": 101.3}
    snapshot.prev_k = {"open": 101.4, "close": 101.45, "high": 101.6, "low": 101.3}
    snapshot.raw_df = df

    should_entry, reason, meta = EntryGate.evaluate("ENTRY_SHORT_EMA_CROSS_MID", snapshot)
    assert should_entry is False
    assert reason == "VETO_MA_ENTANGLEMENT"
    assert "過去5根MA3與MA15平均差距" in meta["cause"]


# ==============================================================================
# 測試 8：順勢動能一致性鐵律 (當根反向棒 100% 一票否決)
# ==============================================================================
def test_entry_gate_veto_buy_on_opposite_bearish_bar():
    """
    [順勢動能一致性 1] 買多時若當根 K 棒為黑棒 (Close <= Open)，
    賣方力量佔據主導，嚴禁接下墜飛刀，100% 一票否決 VETO_BUY_OPPOSITE_BAR！
    """
    snapshot = {
        "current_price": 102.0,
        "atr": 1.0,
        "kc_middle": 100.0,
        "kc_middle_slope": 0.02,
        "kc_upper": 103.0,
        "kc_lower": 97.0,
        "ema_fast": 102.5,
        "ema_slow": 100.5,
        "curr_k": {"open": 102.5, "close": 102.0, "high": 102.8, "low": 101.8},
        "prev_k": {"open": 101.5, "close": 102.5, "high": 102.6, "low": 101.2},
    }
    should_entry, reason, meta = EntryGate.evaluate("BREAKOUT_LONG", snapshot)
    assert should_entry is False
    assert reason == "VETO_BUY_OPPOSITE_BAR"
    assert "當根為黑棒/十字星" in meta["cause"]


def test_entry_gate_veto_sell_on_opposite_bullish_bar():
    """
    [順勢動能一致性 2] 賣空時若當根 K 棒為紅棒 (Close >= Open)，
    買方力量向上反彈，嚴禁迎面撞上反抽，100% 一票否決 VETO_SELL_OPPOSITE_BAR！
    """
    snapshot = {
        "current_price": 98.0,
        "atr": 1.0,
        "kc_middle": 100.0,
        "kc_middle_slope": -0.02,
        "kc_upper": 103.0,
        "kc_lower": 97.0,
        "ema_fast": 97.5,
        "ema_slow": 99.5,
        "curr_k": {"open": 97.5, "close": 98.0, "high": 98.2, "low": 97.2},
        "prev_k": {"open": 98.5, "close": 97.5, "high": 98.8, "low": 97.4},
    }
    should_entry, reason, meta = EntryGate.evaluate("BREAKDOWN_SHORT", snapshot)
    assert should_entry is False
    assert reason == "VETO_SELL_OPPOSITE_BAR"
    assert "當根為紅棒/十字星" in meta["cause"]


def test_entry_gate_veto_buy_on_fake_bullish_long_upper_wick():
    """
    [順勢動能一致性 3] 買多時雖為陽線但帶有長上影線（衝高被大幅砸回開盤價，收在下半部），
    買盤動能衰竭受阻，100% 一票否決 VETO_WICK_REJECTION！
    """
    snapshot = {
        "current_price": 100.3,
        "atr": 1.0,
        "kc_middle": 98.0,
        "kc_middle_slope": 0.02,
        "kc_upper": 102.0,
        "kc_lower": 96.0,
        "ema_fast": 100.0,
        "ema_slow": 98.5,
        # open=100.0, high=102.0, low=99.9, close=100.3
        # body=0.3, upper_wick=1.7 >= 1.5*0.3 且 >= 0.25*1.0，close 落在下半部
        "curr_k": {"open": 100.0, "close": 100.3, "high": 102.0, "low": 99.9},
        "prev_k": {"open": 99.0, "close": 100.0, "high": 100.2, "low": 98.8},
    }
    should_entry, reason, meta = EntryGate.evaluate("BREAKOUT_LONG", snapshot)
    assert should_entry is False
    assert reason == "VETO_WICK_REJECTION"
    assert "長上影線" in meta["cause"]


def test_entry_gate_veto_sell_on_fake_bearish_long_lower_wick():
    """
    [順勢動能一致性 4] 賣空時雖為陰線但帶有長下影線（探底被強力抽回開盤價，收在上半部），
    賣盤動能衰竭見底，100% 一票否決 VETO_WICK_REJECTION！
    """
    snapshot = {
        "current_price": 99.7,
        "atr": 1.0,
        "kc_middle": 102.0,
        "kc_middle_slope": -0.02,
        "kc_upper": 104.0,
        "kc_lower": 98.0,
        "ema_fast": 100.0,
        "ema_slow": 101.5,
        # open=100.0, high=100.1, low=98.0, close=99.7
        # body=0.3, lower_wick=1.7 >= 1.5*0.3 且 >= 0.25*1.0，close 落在上半部
        "curr_k": {"open": 100.0, "close": 99.7, "high": 100.1, "low": 98.0},
        "prev_k": {"open": 101.0, "close": 100.0, "high": 101.2, "low": 99.8},
    }
    should_entry, reason, meta = EntryGate.evaluate("BREAKDOWN_SHORT", snapshot)
    assert should_entry is False
    assert reason == "VETO_WICK_REJECTION"
    assert "長下影線" in meta["cause"]


def test_evaluate_entry_gate_strict_vetoes():
    """驗證 evaluate_entry_gate 的各項防呆一票否決"""
    from run_phase3_controlled_experiments import evaluate_entry_gate

    # 構造基準行情
    data = []
    base = 100.0
    for i in range(12):
        data.append({
            "open": base, "high": base + 1.0, "low": base - 1.0, "close": base + 0.5,
            "atr": 1.0, "ma3": base + 0.2, "ma15": base, "kc_middle": base,
            "kc_upper": base + 1.5, "kc_lower": base - 1.5,
        })
    df = pd.DataFrame(data)

    # 1. 買多時收黑線否決
    df.loc[df.index[-1], ["open", "close", "high", "low"]] = [101.0, 100.5, 101.2, 100.4]
    ok, reason = evaluate_entry_gate(df, "BUY", -1)
    assert ok is False
    assert "VETO_BUY_OPPOSITE_BAR" in reason

    # 2. 買多時長上影線否決
    df.loc[df.index[-1], ["open", "close", "high", "low"]] = [100.0, 100.2, 102.0, 99.9]
    ok, reason = evaluate_entry_gate(df, "BUY", -1)
    assert ok is False
    assert "VETO_WICK_REJECTION" in reason

    # 3. 買多時 MA3 下彎否決
    df.loc[df.index[-1], ["open", "close", "high", "low", "ma3"]] = [100.0, 100.8, 101.0, 99.9, 100.1]
    df.loc[df.index[-2], "ma3"] = 100.3  # ma3_slope = -0.2 <= 0
    ok, reason = evaluate_entry_gate(df, "BUY", -1)
    assert ok is False
    assert "VETO_MA3_NOT_UP" in reason

    # 4. 買多時價格在中軌下方否決 (MA3向上但價格在KC中軌下方)
    df.loc[df.index[-1], ["open", "close", "high", "low", "ma3", "kc_middle"]] = [98.0, 98.8, 99.0, 97.9, 100.5, 100.0]
    df.loc[df.index[-2], "ma3"] = 100.0  # ma3_slope = +0.5 > 0
    ok, reason = evaluate_entry_gate(df, "BUY", -1)
    assert ok is False
    assert "VETO_BELOW_KC_MIDDLE" in reason

    # 5. 賣空時收紅線否決
    df.loc[df.index[-1], ["open", "close", "high", "low"]] = [99.0, 99.5, 99.6, 98.9]
    ok, reason = evaluate_entry_gate(df, "SELL", -1)
    assert ok is False
    assert "VETO_SELL_OPPOSITE_BAR" in reason

    # 6. 賣空時 MA3 抬頭否決
    df.loc[df.index[-1], ["open", "close", "high", "low", "ma3"]] = [100.5, 99.8, 100.6, 99.7, 100.2]
    df.loc[df.index[-2], "ma3"] = 100.0  # ma3_slope = +0.2 >= 0
    ok, reason = evaluate_entry_gate(df, "SELL", -1)
    assert ok is False
    assert "VETO_MA3_RISING" in reason


def test_evaluate_entry_gate_passes():
    """驗證 evaluate_entry_gate 條件完全滿足時正確放行"""
    from run_phase3_controlled_experiments import evaluate_entry_gate

    data = []
    base = 100.0
    for i in range(12):
        data.append({
            "open": base, "high": base + 1.0, "low": base - 1.0, "close": base + 0.5,
            "atr": 1.0, "ma3": base + i * 0.1, "ma15": base, "kc_middle": base,
            "kc_upper": base + 2.0, "kc_lower": base - 2.0,
        })
    df = pd.DataFrame(data)

    # 做多放行
    df.loc[df.index[-1], ["open", "close", "high", "low", "ma3", "kc_middle"]] = [100.0, 101.0, 101.2, 99.8, 101.5, 100.0]
    df.loc[df.index[-2], "ma3"] = 101.0
    ok, reason = evaluate_entry_gate(df, "BUY", -1)
    assert ok is True
    assert "PASS_LONG" in reason

    # 做空放行
    df.loc[df.index[-1], ["open", "close", "high", "low", "ma3", "kc_middle"]] = [100.0, 99.0, 100.2, 98.8, 98.5, 100.0]
    df.loc[df.index[-2], "ma3"] = 99.0
    ok, reason = evaluate_entry_gate(df, "SELL", -1)
    assert ok is True
    assert "PASS_SHORT" in reason







