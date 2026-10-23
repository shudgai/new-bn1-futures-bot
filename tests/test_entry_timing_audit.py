"""
tests/test_entry_timing_audit.py
================================
龍蝦 Binance Futures Bot 2.0：開倉時機深度稽核與回歸測試套件
嚴格涵蓋：
1. EMA5/EMA13 金叉剛發起時 (ma_gap < 0.2 ATR) 正常放行，不再被均線間距過窄阻斷。
2. 趨勢剛啟動時，不被中軌過濾誤阻。
3. 有效收盤破軌 (close > kc_upper) 能正確放行進場。
4. 盤中影線插針假突破 (high > kc_upper 但 close < kc_upper) 嚴格阻斷，不追天花板。
5. 過度延伸追價 (距外軌 > 0.5 ATR) 依策略一票否決。
6. 趨勢回踩守穩收陽重新站上 MA5 (模式二) 能正確放行。
7. 回踩尚未確認 (未站上 MA5) 嚴禁提早搶跑進場。
8. 多空條件嚴格對稱 (空單死叉、跌破、反抽受阻鏡像)。
9. 執行層呼叫鏈測試：確認 EntryGate 放行後會正確呼叫 open_position。
10. 平倉後冷卻期內，具備強動能實體破軌時正確豁免冷卻期。
"""

import pytest
import pandas as pd
import numpy as np
import asyncio
from unittest.mock import MagicMock, AsyncMock, patch

from core.gates.unified_pipeline_gates import EntryGate
from core.services.data_pipeline import MarketDataSnapshot, DataPipeline
from core.services.execution_manager import ExecutionManager


def _create_entry_snapshot(
    symbol="LOBSTERUSDT",
    quote=0.0210,
    c_open=0.0208,
    c_close=0.0210,
    c_high=None,
    c_low=None,
    prev_open=0.0207,
    prev_close=0.0208,
    prev_high=0.02085,
    prev_low=0.02065,
    kc_mid=0.0205,
    kc_up=0.0209,
    kc_low=0.0201,
    atr=0.0002,
    ma5=0.02095,
    ma5_prev=0.02085,
    ma15=0.02090,
    ema_fast=0.02090,
    ema_slow=0.02040,
    macro_trend="BULLISH",
):
    if c_high is None:
        c_high = max(c_open, c_close) + 0.00001
    if c_low is None:
        c_low = min(c_open, c_close) - 0.00001
    df_rows = [
        {"open": prev_open, "high": prev_high, "low": prev_low, "close": prev_close, "atr": atr, "kc_upper": kc_up, "kc_lower": kc_low, "kc_middle": kc_mid, "ma_slow": ema_slow, "bandwidth": 0.05},
        {"open": c_open, "high": c_high, "low": c_low, "close": c_close, "atr": atr, "kc_upper": kc_up, "kc_lower": kc_low, "kc_middle": kc_mid, "ma_slow": ema_slow, "bandwidth": 0.05},
    ]
    raw_df = pd.DataFrame(df_rows)
    curr_k = {"open": c_open, "high": c_high, "low": c_low, "close": c_close, "ma5": ma5, "ma15": ma15}
    prev_k = {"open": prev_open, "high": prev_high, "low": prev_low, "close": prev_close, "ma5": ma5_prev, "ma15": ma15}

    return MarketDataSnapshot(
        symbol=symbol,
        current_price=quote,
        curr_k=curr_k,
        prev_k=prev_k,
        ma5=ma5,
        ma15=ma15,
        ema_fast=ema_fast,
        ema_slow=ema_slow,
        ma_spread_pct=1.0,
        fast_slope=0.00001,
        slow_slope=0.00001,
        kc_upper=kc_up,
        kc_middle=kc_mid,
        kc_lower=kc_low,
        kc_middle_slope=0.00001,
        atr=atr,
        peak_roe=0.0,
        min_price=c_low,
        max_price=c_high,
        macro_trend=macro_trend,
        macro_slope=0.00001,
        slow_slope_5=0.00001,
        bandwidth=0.05,
        raw_df=raw_df,
    )


def test_1_fresh_ema_cross_start_not_blocked_by_gap_narrow():
    """情境 1：EMA5 剛金叉 EMA13 (間距 < 0.2 ATR)，趨勢啟動模式正常放行，不再被均線間距過窄阻斷"""
    # 構造剛金叉：ma5=0.02091, ma15=0.02090，間距 0.00001 < 0.2*0.0002=0.00004
    snap = _create_entry_snapshot(
        quote=0.02095, c_open=0.0208, c_close=0.02095,
        ma5=0.02091, ma5_prev=0.02085, ma15=0.02090,
        kc_mid=0.0207
    )
    should_entry, reason, meta = EntryGate.evaluate("ENTRY_LONG_EMA_CROSS_MID", snap)
    assert should_entry is True, f"剛金叉趨勢啟動應允許進場，實際原因: {reason}"
    assert reason == "ENTRY_LONG_EMA_CROSS_MID"


def test_2_trend_start_above_midline_allows_entry():
    """情境 2：趨勢啟動站穩 KC 中軌，不被中軌過濾 (VETO_AT_KC_MIDDLE) 誤阻"""
    snap = _create_entry_snapshot(
        quote=0.02075, c_open=0.02065, c_close=0.02075,
        kc_mid=0.02070, kc_low=0.0200, kc_up=0.0214 # 剛好在中軌 (KC%B 約 0.53)
    )
    should_entry, reason, _ = EntryGate.evaluate("ENTRY_LONG_EMA_CROSS_MID", snap)
    assert should_entry is True
    assert reason == "ENTRY_LONG_EMA_CROSS_MID"


def test_3_confirmed_closed_breakout_allows_entry():
    """情境 3：實體大陽 K 棒收盤確認衝破 KC 上軌，正常放行開多"""
    snap = _create_entry_snapshot(
        quote=0.02105, c_open=0.0208, c_close=0.02105,
        kc_up=0.02090, atr=0.0002 # 收盤在軌外 0.00015 = 0.75 ATR (符合首根突破 <= 3.0 ATR)
    )
    should_entry, reason, _ = EntryGate.evaluate("BREAKOUT_LONG", snap)
    assert should_entry is True
    assert reason in ("BREAKOUT_MAIN_WAVE_START", "CONFIRMED_2BAR_BREAKOUT")


def test_4_intrabar_wick_spike_without_closed_breakout_blocked():
    """情境 4：盤中影線插針 (high > kc_upper 但收盤 close < kc_upper) 嚴格阻斷，不追天花板"""
    # 影線衝高到 0.0211 > kc_up=0.0209，但收盤在軌道內 0.02085
    snap = _create_entry_snapshot(
        quote=0.02085, c_open=0.02080, c_close=0.02085, c_high=0.02110,
        kc_up=0.02090, atr=0.0002
    )
    should_entry, reason, _ = EntryGate.evaluate("BREAKOUT_LONG", snap)
    assert should_entry is False, f"收盤未突破外軌應阻斷開倉，實際原因: {reason}"


def test_5_late_chase_over_extension_strictly_blocked():
    """情境 5：過度延伸追價 (延續開倉距外軌 > 0.5 ATR 且非強動能大陽柱) 依策略一票否決"""
    snap = _create_entry_snapshot(
        quote=0.02120, c_open=0.02115, c_close=0.02120, # 實體僅 0.00005 < 0.8 ATR
        kc_up=0.02090, atr=0.0002 # 距外軌 0.00030 = 1.5 ATR > 0.5 ATR
    )
    should_entry, reason, _ = EntryGate.evaluate("CONTINUATION_OUTSIDE_KC_LONG", snap)
    assert should_entry is False
    assert "LATE_CHASE" in reason or "OVER_EXTENSION" in reason


def test_6_trend_pullback_reclaim_allows_entry():
    """情境 6：模式二回踩均線帶守穩收陽，正常放行開多"""
    # 回踩低點探至 0.02085，收盤收陽 0.02095 重新站回 MA5 (0.02090)
    snap = _create_entry_snapshot(
        quote=0.02095, c_open=0.02088, c_close=0.02095, c_low=0.02085,
        ma5=0.02090, ma5_prev=0.02088, ema_slow=0.02040, kc_mid=0.02060
    )
    should_entry, reason, _ = EntryGate.evaluate("TREND_PULLBACK_LONG", snap)
    assert should_entry is True, f"回踩守穩應放行開多，實際原因: {reason}"
    assert reason in ("TREND_PULLBACK_LONG", "PULLBACK_RECLAIM_CONTINUATION")


def test_7_pullback_unconfirmed_not_allowed():
    """情境 7：回踩尚未確認 (收黑陰線且跌破 MA5)，嚴禁提早搶跑進場"""
    # 收陰 0.02085 < open=0.02095，且跌破 MA5 (0.02090)
    snap = _create_entry_snapshot(
        quote=0.02085, c_open=0.02095, c_close=0.02085,
        ma5=0.02090, ma5_prev=0.02092
    )
    should_entry, reason, _ = EntryGate.evaluate("TREND_PULLBACK_LONG", snap)
    assert should_entry is False, f"未止跌轉強前嚴禁進場，實際原因: {reason}"


def test_8_short_side_mirror_symmetry():
    """情境 8：空單模式嚴格對稱 (死叉中軌啟動、收黑跌破外軌、反抽受阻)"""
    # 1. 空單死叉啟動 (中軌與慢線向下，價格受壓中軌)
    snap_s_cross = _create_entry_snapshot(
        quote=0.02030, c_open=0.02040, c_close=0.02030,
        kc_mid=0.02050, kc_low=0.0200, kc_up=0.0210,
        ma5=0.02035, ma5_prev=0.02040, ma15=0.02038,
        ema_fast=0.02038, ema_slow=0.02060,
        macro_trend="BEARISH"
    )
    snap_s_cross.kc_middle_slope = -0.00001
    snap_s_cross.slow_slope = -0.00001
    s1, r1, _ = EntryGate.evaluate("ENTRY_SHORT_EMA_CROSS_MID", snap_s_cross)
    assert s1 is True, f"空單死叉啟動應允許進場，實際原因: {r1}"
    assert r1 == "ENTRY_SHORT_EMA_CROSS_MID"

    # 2. 空單跌破外軌放行
    snap_s_break = _create_entry_snapshot(
        quote=0.01990, c_open=0.02010, c_close=0.01990,
        kc_low=0.02000, atr=0.0002,
        ma5=0.01995, ma5_prev=0.02010,
        macro_trend="BEARISH"
    )
    snap_s_break.kc_middle_slope = -0.00001
    snap_s_break.slow_slope = -0.00001
    s2, r2, _ = EntryGate.evaluate("BREAKDOWN_SHORT", snap_s_break)
    assert s2 is True, f"空單跌破應允許進場，實際原因: {r2}"
    assert r2 in ("BREAKOUT_MAIN_WAVE_START", "CONFIRMED_2BAR_BREAKOUT")


def test_9_strong_breakout_bypasses_cooldown():
    """情境 9：大陽柱強動能破軌 (body >= 0.8 ATR) 成功豁免平倉後冷卻期，及時上車"""
    last_exit = {"timestamp": 1000.0, "bars_since_exit": 1, "side": "LONG"}
    # 當前收出實體長度 0.00025 >= 0.8 * 0.0002 = 0.00016 的大陽柱衝破外軌
    snap = _create_entry_snapshot(
        quote=0.02105, c_open=0.02080, c_close=0.02105,
        kc_up=0.02090, atr=0.0002
    )
    ctx = {**last_exit, "current_time": 1030.0}
    should_entry, reason, _ = EntryGate.evaluate("BREAKOUT_LONG", snap, last_exit_context=ctx)
    assert should_entry is True, f"強動能破軌應豁免冷卻期，實際原因: {reason}"


def test_10_execution_manager_calls_open_position_on_entry_signal():
    """情境 10：Mock 測試執行呼叫鏈，確認候選信號通過 EntryGate 後會送往 open_position"""
    async def _run_async_entry():
        class MockAccount:
            positions = {}
        mock_account = MockAccount()

        snap = _create_entry_snapshot(
            quote=0.02105, c_open=0.0208, c_close=0.02105,
            kc_up=0.02090, atr=0.0002
        )

        with patch.object(DataPipeline, "calculate_snapshot", return_value=snap), \
             patch.object(ExecutionManager, "open_position", new_callable=AsyncMock) as mock_open:

            mock_open.return_value = True

            result = await ExecutionManager.process_symbol_pipeline(
                symbol="LOBSTERUSDT",
                engine_or_account=mock_account,
                quote=0.02105,
                klines_df=pd.DataFrame([{"close": 0.02105}]),
            )

            assert result["status"] == "ENTRY_EXECUTED"
            mock_open.assert_awaited_once()

    asyncio.run(_run_async_entry())
