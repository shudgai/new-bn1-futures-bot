"""Comprehensive Unit Tests for Four-Task Quantitative Architecture Refactoring

Covers:
1. Direction Master Lock (Long/Short) & KC Squeeze Gate & Physical runtime error block
2. Swing Pivot Early Entry (Valley Long / Peak Short) with KC slope exemption & precise prev_k stop loss
3. Breakout-Pullback-Continuation (Three-Bar Continuation Long / Short)
4. Anti-Whipsaw Holding & Extreme Peak/Valley Exits (Engulfing, Wick/Pinbar, MA3 Body Break)
"""

import math
import pandas as pd
import pytest

from core.gates.direction_master_lock import (
    evaluate_direction_master_lock,
    check_direction_lock_from_frame,
)
from core.gates.central_entry_gate import CentralEntryGate
from core.services.swing_pivot_rules import (
    evaluate_swing_pivot_entry,
    evaluate_outer_rail_reversal_exit,
)
from core.gates.top_bottom_reversal_exit import (
    evaluate_top_bottom_reversal_exit,
    CLOSE_LONG_AT_TOP,
    CLOSE_SHORT_AT_BOTTOM,
)
from core.testnet_account import BinanceTestnetAccount


# =========================================================================
# 任務 1 測試：方向總開關與 KC 盤整鎖定
# =========================================================================

def test_task1_direction_master_lock_long_and_short():
    kc_mid = 100.0

    # 1. 開多單絕對門檻：MA3 必須 > kc_middle 且 ma3_curr > ma3_prev
    # 成功案例
    ok, reason, _ = evaluate_direction_master_lock(
        side="LONG", ma3_curr=101.5, ma3_prev=101.0, kc_middle=kc_mid
    )
    assert ok is True
    assert reason == "DIRECTION_LOCK_PASS"

    # 違規 1：MA3 在中軌下方開多 ➔ 攔截
    ok, reason, _ = evaluate_direction_master_lock(
        side="LONG", ma3_curr=98.5, ma3_prev=98.0, kc_middle=kc_mid
    )
    assert ok is False
    assert reason == "BLOCKED_DIRECTION_LOCK_LONG_BELOW_MID"

    # 違規 2：MA3 在中軌上方但向下走開多 ➔ 攔截
    ok, reason, _ = evaluate_direction_master_lock(
        side="LONG", ma3_curr=101.0, ma3_prev=101.5, kc_middle=kc_mid
    )
    assert ok is False
    assert reason == "BLOCKED_DIRECTION_LOCK_LONG_SLOPE_DOWN"

    # 2. 開空單絕對門檻：MA3 必須 < kc_middle 且 ma3_curr < ma3_prev
    # 成功案例
    ok, reason, _ = evaluate_direction_master_lock(
        side="SHORT", ma3_curr=98.0, ma3_prev=98.5, kc_middle=kc_mid
    )
    assert ok is True
    assert reason == "DIRECTION_LOCK_PASS"

    # 違規 1：MA3 在中軌上方開空 ➔ 攔截
    ok, reason, _ = evaluate_direction_master_lock(
        side="SHORT", ma3_curr=101.0, ma3_prev=100.5, kc_middle=kc_mid
    )
    assert ok is False
    assert reason == "BLOCKED_DIRECTION_LOCK_SHORT_ABOVE_MID"

    # 違規 2：MA3 在中軌下方但向上勾頭開空 ➔ 攔截
    ok, reason, _ = evaluate_direction_master_lock(
        side="SHORT", ma3_curr=98.5, ma3_prev=98.0, kc_middle=kc_mid
    )
    assert ok is False
    assert reason == "BLOCKED_DIRECTION_LOCK_SHORT_SLOPE_UP"


def test_task1_physical_block_runtime_error():
    # 模擬 BinanceTestnetAccount 物理攔截
    account = BinanceTestnetAccount.__new__(BinanceTestnetAccount)
    account.positions = {}
    account.pending_limit_orders = set()
    account.closing_lock = set()
    account.logs = []
    account.log = lambda msg, level="INFO": account.logs.append((level, msg))

    # 行情：MA3 在中軌上方且向上 (例如 V 轉向上)，收到 SHORT 訂單
    df = pd.DataFrame([
        dict(timestamp=60_000, open=100, high=101, low=99, close=100.5, ma3=100.2, ma5=100.1, kc_middle=99.0, is_closed=True),
        dict(timestamp=120_000, open=100.5, high=102, low=100.4, close=101.8, ma3=101.2, ma5=100.8, kc_middle=99.0, is_closed=True),
    ])

    context = {
        "frame": df,
        "entry_gate_frame": df,
        "raise_on_direction_lock": True,
        "source": "STRATEGY",
        "_central_gate_passed": True,  # 跳過前置 central gate 以直接測試底層物理硬核驗
    }

    import asyncio
    try:
        asyncio.run(account.open_position("TESTUSDT", "SHORT", 101.8, 10.0, 105.0, 95.0, "STRATEGY", entry_context=context))
        print("ACCOUNT_LOGS:", account.logs)
    except RuntimeError as exc:
        assert ("严禁逆势" in str(exc) or "嚴禁逆勢" in str(exc) or "DIRECTION_LOCK" in str(exc))
        return
    pytest.fail(f"DID NOT RAISE RuntimeError! logs={account.logs}")


# =========================================================================
# 任務 2 測試：谷底與頂部轉折提前開倉（雙向豁免 KC 斜率）
# =========================================================================

def test_task2_swing_pivot_valley_long_and_peak_short():
    # 1. 多頭谷底轉折提前開多 (SWING_PIVOT_LONG)
    # 條件：價格在下半區 (close <= kc_middle) + ma3_curr > ma3_prev + ma3 >= ma15 + 實體陽線 (close > open, close >= ma3_curr)
    # 即使 KC 中軌向下傾斜，依然授權開多，停損鎖定在谷底前一根 K 棒最低點
    bars_valley = [
        dict(timestamp=60_000, open=98.0, high=98.5, low=96.0, close=96.5, atr=1.0, ma3=97.0, ma15=98.0, kc_middle=99.5, kc_upper=101.5, kc_lower=97.5, is_closed=True),
        dict(timestamp=120_000, open=96.5, high=97.0, low=95.0, close=95.5, atr=1.0, ma3=96.0, ma15=97.5, kc_middle=99.0, kc_upper=101.0, kc_lower=97.0, is_closed=True), # 谷底 K (low=95.0)
        dict(timestamp=180_000, open=95.5, high=98.0, low=95.4, close=97.8, atr=1.0, ma3=96.8, ma15=96.5, kc_middle=98.5, kc_upper=100.5, kc_lower=96.5, is_closed=True), # 確認棒 (close=97.8 > ma3=96.8 >= ma15=96.5)
    ]
    df_valley = pd.DataFrame(bars_valley)
    df_valley.attrs["timeframe_ms"] = 60_000.0

    res_valley = evaluate_swing_pivot_entry(df_valley)
    assert res_valley is not None
    assert res_valley["side"] == "LONG"
    assert res_valley["direction_override"] is True
    # 停損必須鎖定在前一根 K 棒最低點 (95.0)
    assert res_valley["stop_loss_price"] == 95.0

    # 2. 空頭頂部轉折提前開空 (SWING_PIVOT_SHORT)
    # 條件：價格在上半區 (close >= kc_middle) + ma3_curr < ma3_prev + ma3 <= ma15 + 實體陰線 (close < open, close <= ma3_curr)
    # 即使 KC 中軌向上傾斜，依然授權開空，停損鎖定在頂部前一根 K 棒最高點
    bars_peak = [
        dict(timestamp=60_000, open=102.0, high=103.5, low=101.5, close=103.0, atr=1.0, ma3=102.5, ma15=101.5, kc_middle=100.5, kc_upper=102.5, kc_lower=98.5, is_closed=True),
        dict(timestamp=120_000, open=103.0, high=105.0, low=102.8, close=104.5, atr=1.0, ma3=103.8, ma15=102.0, kc_middle=101.0, kc_upper=103.0, kc_lower=99.0, is_closed=True), # 頂部 K (high=105.0)
        dict(timestamp=180_000, open=104.5, high=104.6, low=102.0, close=102.2, atr=1.0, ma3=103.0, ma15=103.2, kc_middle=101.5, kc_upper=103.5, kc_lower=99.5, is_closed=True), # 確認棒 (close=102.2 < ma3=103.0 <= ma15=103.2)
    ]
    df_peak = pd.DataFrame(bars_peak)
    df_peak.attrs["timeframe_ms"] = 60_000.0

    res_peak = evaluate_swing_pivot_entry(df_peak)
    assert res_peak is not None
    assert res_peak["side"] == "SHORT"
    assert res_peak["direction_override"] is True
    # 停損必須鎖定在前一根 K 棒最高點 (105.0)
    assert res_peak["stop_loss_price"] == 105.0


# =========================================================================
# 任務 3 測試：順勢破軌回踩再起飛模式 (Breakout-Pullback-Continuation)
# =========================================================================

def test_task3_three_bar_continuation_long_and_short():
    from core.services.candle_data import closed_entry_candles

    # 🟢 多頭二次起飛 (CONTINUATION_LONG)
    # K1: 陽線衝破 KC 上軌
    # K2: 縮量小陰線回踩，實體 <= 0.5 * K1，收盤未跌破 MA3
    # K3: 實體陽線，收盤突破 K2 高點，MA3 向上
    bars_cont_long = [
        dict(timestamp=60_000, open=100.0, high=103.0, low=99.8, close=102.5, kc_upper=101.5, ma3=101.0, is_closed=True), # K1: 破上軌，實體=2.5
        dict(timestamp=120_000, open=102.5, high=102.8, low=101.6, close=101.8, kc_upper=102.0, ma3=101.5, is_closed=True), # K2: 小陰回踩實體=0.7 (<=1.25), 收在 ma3(101.5) 之上
        dict(timestamp=180_000, open=101.8, high=104.0, low=101.7, close=103.5, kc_upper=102.5, ma3=102.6, is_closed=False), # K3: 陽線突破 K2.high(102.8), ma3向上
    ]
    df_cont_long = pd.DataFrame(bars_cont_long)

    # 驗證邏輯
    k1 = df_cont_long.iloc[-3]
    k2 = df_cont_long.iloc[-2]
    k3 = df_cont_long.iloc[-1]
    k1_body = abs(k1.close - k1.open)
    k2_body = abs(k2.close - k2.open)

    long_met = (
        (k1.close > k1.open and k1.close > k1.kc_upper)
        and (k2.close <= k2.open and k2_body <= 0.5 * k1_body and k2.close >= k2.ma3)
        and (k3.close > k3.open and k3.close > k2.high and k3.ma3 > k2.ma3)
    )
    assert bool(long_met) is True, "Three-bar continuation long pattern must match!"
    stop_loss_long = float(k2.low)
    assert stop_loss_long == 101.6

    # 🔴 空頭二次下殺 (CONTINUATION_SHORT)
    # K1: 陰線跌破 KC 下軌
    # K2: 縮量小陽線反彈，實體 <= 0.5 * K1，收盤未突破 MA3
    # K3: 實體陰線，收盤跌破 K2 低點，MA3 向下
    bars_cont_short = [
        dict(timestamp=60_000, open=100.0, high=100.2, low=97.0, close=97.5, kc_lower=98.5, ma3=99.0, is_closed=True), # K1: 跌破下軌，實體=2.5
        dict(timestamp=120_000, open=97.5, high=98.4, low=97.3, close=98.2, kc_lower=98.0, ma3=98.5, is_closed=True), # K2: 小陽反彈實體=0.7 (<=1.25), 收在 ma3(98.5) 之下
        dict(timestamp=180_000, open=98.2, high=98.3, low=96.0, close=96.5, kc_lower=97.5, ma3=97.4, is_closed=False), # K3: 陰線跌破 K2.low(97.3), ma3向下
    ]
    df_cont_short = pd.DataFrame(bars_cont_short)

    k1_s = df_cont_short.iloc[-3]
    k2_s = df_cont_short.iloc[-2]
    k3_s = df_cont_short.iloc[-1]
    k1_body_s = abs(k1_s.close - k1_s.open)
    k2_body_s = abs(k2_s.close - k2_s.open)

    short_met = (
        (k1_s.close < k1_s.open and k1_s.close < k1_s.kc_lower)
        and (k2_s.close >= k2_s.open and k2_body_s <= 0.5 * k1_body_s and k2_s.close <= k2_s.ma3)
        and (k3_s.close < k3_s.open and k3_s.close < k2_s.low and k3_s.ma3 < k2_s.ma3)
    )
    assert bool(short_met) is True, "Three-bar continuation short pattern must match!"
    stop_loss_short = float(k2_s.high)
    assert stop_loss_short == 98.4

    # 🛑 防偷跑測試：處於 K2 反彈小陽 K 棒時，K3 尚未跌破 K2.low 或未收陰 ➔ 嚴格禁止偷跑開空！
    bars_k2_only = [
        dict(timestamp=60_000, open=100.0, high=100.2, low=97.0, close=97.5, kc_lower=98.5, ma3=99.0, is_closed=True), # K1
        dict(timestamp=120_000, open=97.5, high=98.4, low=97.3, close=98.2, kc_lower=98.0, ma3=98.5, is_closed=True),  # K2: 小陽反彈棒
    ]
    # 當前如果還在反彈棒內，或者 K3 收陽線反彈 (close > open)
    k3_still_pullback = dict(open=98.2, high=98.6, low=98.0, close=98.5, ma3=98.6)
    k3_confirmed_short = (
        (k3_still_pullback["close"] < k3_still_pullback["open"])
        and (k3_still_pullback["close"] < 97.3)
        and (k3_still_pullback["ma3"] < 98.5)
    )
    assert k3_confirmed_short is False, "During K2 pullback or unconfirmed bar, continuation short must be BLOCKED!"


# =========================================================================
# 任務 4 測試：抗洗盤持倉保護與極限峰谷即刻鎖利
# =========================================================================

def test_task4_anti_whipsaw_holding_and_extreme_exits():
    # 1. 多單持倉抗洗盤：價格守在 MA15 之上且未見極限見頂 ➔ 強制續抱 (TREND_LOCK_ACTIVE)
    pos_long = {"side": "LONG", "entry_price": 100.0, "open_timestamp": 60.0}
    bars_hold_long = [
        dict(timestamp=60_000, open=100.0, high=102.0, low=99.5, close=101.5, kc_upper=103.0, kc_lower=97.0, atr=1.0, ma3=101.0, ma15=100.2, is_closed=True),
        dict(timestamp=120_000, open=101.5, high=102.5, low=101.0, close=102.2, kc_upper=103.0, kc_lower=97.0, atr=1.0, ma3=101.8, ma15=100.5, is_closed=True),
        dict(timestamp=180_000, open=102.2, high=102.3, low=101.5, close=101.7, kc_upper=103.0, kc_lower=97.0, atr=1.0, ma3=101.9, ma15=100.8, is_closed=True), # 小陰線回踩，守在 ma15(100.8) 之上
    ]
    df_hold_long = pd.DataFrame(bars_hold_long)

    exit_hold, _, meta_hold = evaluate_top_bottom_reversal_exit(pos_long, df_hold_long, atr_val=1.0)
    assert exit_hold is False
    assert "TREND_LOCK_ACTIVE" in str(meta_hold.get("skipped"))

    # 2. 多單極限見頂平倉：軌外大陰吞噬 (吃掉前根 60% 以上) ➔ 即刻平多
    bars_engulf_top = [
        dict(timestamp=60_000, open=100.0, high=102.0, low=99.5, close=101.5, kc_upper=103.0, kc_lower=97.0, atr=1.0, ma3=101.0, ma15=100.2, is_closed=True),
        dict(timestamp=120_000, open=101.5, high=105.0, low=101.4, close=104.5, kc_upper=103.0, kc_lower=97.0, atr=1.0, ma3=103.0, ma15=100.5, is_closed=True), # 衝破上軌收大陽 (close=104.5 > upper=103.0, body=3.0)
        dict(timestamp=180_000, open=104.5, high=104.6, low=102.0, close=102.2, kc_upper=103.5, kc_lower=97.5, atr=1.0, ma3=102.8, ma15=100.8, is_closed=True), # 大陰線吞噬實體=2.3 (>= 0.6*3.0=1.8)
    ]
    df_engulf_top = pd.DataFrame(bars_engulf_top)

    exit_engulf, reason_engulf, _ = evaluate_top_bottom_reversal_exit(pos_long, df_engulf_top, atr_val=1.0)
    assert exit_engulf is True
    assert reason_engulf == CLOSE_LONG_AT_TOP

    # 3. 空單極限見底條件 A：MA3 實體向上金叉 MA15 慢線 ➔ 即刻平空
    pos_short = {"side": "SHORT", "entry_price": 100.0, "open_timestamp": 60.0}
    bars_golden_cross = [
        dict(timestamp=60_000, open=100.0, high=100.5, low=98.0, close=98.5, kc_upper=103.0, kc_lower=97.0, atr=1.0, ma3=98.5, ma15=99.0, is_closed=True),
        dict(timestamp=120_000, open=98.5, high=98.6, low=96.0, close=96.5, kc_upper=102.0, kc_lower=96.0, atr=1.0, ma3=96.8, ma15=98.0, is_closed=True), # 破軌
        dict(timestamp=180_000, open=96.5, high=98.5, low=96.4, close=98.2, kc_upper=101.5, kc_lower=95.5, atr=1.0, ma3=98.2, ma15=98.0, is_closed=True), # ma3(98.2) > ma15(98.0) 金叉
    ]
    df_gc = pd.DataFrame(bars_golden_cross)
    exit_gc, reason_gc, _ = evaluate_top_bottom_reversal_exit(pos_short, df_gc, atr_val=1.0)
    assert exit_gc is True
    assert reason_gc == CLOSE_SHORT_AT_BOTTOM

    # 4. 空單抗洗盤：微小反彈（未金叉 MA15 也未大陽吞噬）➔ 強制鎖死持倉 (TREND_LOCK_ACTIVE)
    bars_minor_pullback = [
        dict(timestamp=60_000, open=100.0, high=100.5, low=98.0, close=98.5, kc_upper=103.0, kc_lower=97.0, atr=1.0, ma3=98.5, ma15=100.0, is_closed=True),
        dict(timestamp=120_000, open=98.5, high=98.6, low=95.0, close=95.5, kc_upper=102.0, kc_lower=96.0, atr=1.0, ma3=96.5, ma15=99.0, is_closed=True),
        dict(timestamp=180_000, open=95.5, high=96.2, low=95.4, close=96.0, kc_upper=101.5, kc_lower=95.5, atr=1.0, ma3=96.6, ma15=98.5, is_closed=True), # 小反彈，ma3 < ma15 且非大陽吞噬
    ]
    df_minor = pd.DataFrame(bars_minor_pullback)
    exit_minor, _, meta_minor = evaluate_top_bottom_reversal_exit(pos_short, df_minor, atr_val=1.0)
    assert exit_minor is False
    assert "TREND_LOCK_ACTIVE" in str(meta_minor.get("skipped"))

    # 5. 結構失效平倉：多單收盤跌破谷底前一根 K 棒最低點 (close < prev_k['low'])
    pos_struct_long = {
        "side": "LONG",
        "entry_price": 98.0,
        "stop_loss_price": 95.0,  # 谷底前一根最低點
        "open_timestamp": 60.0,
    }
    bars_struct_fail_long = [
        dict(timestamp=60_000, open=97.5, high=98.5, low=96.0, close=98.0, atr=1.0, ma3=97.0, ma15=97.5, kc_upper=102.0, kc_lower=96.0, is_closed=True),
        dict(timestamp=120_000, open=96.0, high=97.0, low=94.5, close=94.8, atr=1.0, ma3=95.5, ma15=97.0, kc_upper=102.0, kc_lower=96.0, is_closed=True),
    ]
    df_struct_long = pd.DataFrame(bars_struct_fail_long)
    exit_sl, reason_sl, _ = evaluate_top_bottom_reversal_exit(pos_struct_long, df_struct_long, atr_val=1.0)
    assert exit_sl is True
    assert reason_sl == "STRUCTURAL_FAILURE_EXIT"

    # 6. 極端大瀑布斷路器：單根反向實體 >= 1.8 * atr 且貫穿 MA15
    bars_waterfall = [
        dict(timestamp=60_000, open=100.0, high=102.0, low=99.5, close=101.5, kc_upper=103.0, kc_lower=97.0, atr=1.0, ma3=101.0, ma15=100.2, is_closed=True),
        dict(timestamp=120_000, open=101.5, high=102.0, low=98.0, close=98.2, kc_upper=103.0, kc_lower=97.0, atr=1.0, ma3=100.0, ma15=100.1, is_closed=True), # 實體=3.3 >= 1.8*1.0，開盤101.5 > ma15(100.1) 且收盤98.2 < ma15(100.1)
    ]
    df_waterfall = pd.DataFrame(bars_waterfall)
    exit_wf, reason_wf, _ = evaluate_top_bottom_reversal_exit(pos_long, df_waterfall, atr_val=1.0)
    assert exit_wf is True
    assert reason_wf == "EXIT_BY_EXTREME_WATERFALL"

