"""
tests/test_state_machine_and_unified_exit_gate.py
=================================================
驗證用戶重構規範之 PositionStateMachine (狀態追蹤器) 與 UnifiedExitGate (裁決官)：
1. PositionStateMachine:
   - 冷卻期約束 (cooldown_bars)
   - 原地追單約束 (多單未破前高 / 空單未破前低 一票否決)
2. UnifiedExitGate:
   - 硬止損 (ROE <= -15%)
   - 雙階梯鎖利 (15% 鎖 80%, 10% 鎖 70%)
   - 健康回踩 (HOLD_HEALTHY_PULLBACK) 一票否決
   - 趨勢衰退平倉 (MA15 破位 / 三點樞紐真峰谷)
"""
import pytest
import pandas as pd
from core.gates.unified_pipeline_gates import (
    PositionStateMachine,
    UnifiedExitGate,
    ExitEventRecord,
)


def _build_test_df(n_bars=30, start_price=100.0, step=0.1, ma5_step=0.1, ma15_step=0.05):
    bars = []
    curr = start_price
    for i in range(n_bars):
        c = curr + (i * step)
        bars.append({
            "open": c - 0.05,
            "high": c + 0.1,
            "low": c - 0.1,
            "close": c,
            "ma5": c,
            "ma15": c - 0.5,
            "atr": 1.0,
        })
    return pd.DataFrame(bars)


# ==============================================================================
# 測試 1：PositionStateMachine 冷卻與原地追單防護
# ==============================================================================
def test_state_machine_cooldown_and_chasing_veto():
    sm = PositionStateMachine(cooldown_bars=3)

    # 1. 無平倉紀錄放行
    can_enter, msg = sm.validate_entry_cooldown("LONG", current_bar_index=10, current_price=105.0)
    assert can_enter is True

    # 2. 記錄平倉事件 (在 bar 10 多單平倉，平倉價 105.0，波段最高價 106.0)
    sm.record_exit(
        side="LONG",
        bar_index=10,
        exit_price=105.0,
        peak_price=106.0,
        reason="PROFIT_LOCK",
    )

    # 3. 冷卻期未滿 (bar 11, 只過 1 根 < 3 根)
    can_enter, msg = sm.validate_entry_cooldown("LONG", current_bar_index=11, current_price=107.0)
    assert can_enter is False
    assert "COOLDOWN_VETO" in msg

    # 4. 冷卻期已滿 (bar 13, 已過 3 根)，但現價 105.5 尚未突破前高 106.0 -> CHASING_VETO
    can_enter, msg = sm.validate_entry_cooldown("LONG", current_bar_index=13, current_price=105.5)
    assert can_enter is False
    assert "CHASING_VETO" in msg

    # 5. 冷卻期已滿，現價 106.5 突破前高 106.0 -> 放行！
    can_enter, msg = sm.validate_entry_cooldown("LONG", current_bar_index=13, current_price=106.5)
    assert can_enter is True

    # 6. 冷卻期已滿，方向相反 (做空) 不受多單前高限制 -> 放行！
    can_enter, msg = sm.validate_entry_cooldown("SHORT", current_bar_index=13, current_price=104.0)
    assert can_enter is True


def test_state_machine_short_chasing_veto():
    sm = PositionStateMachine(cooldown_bars=3)

    # 空單在 bar 20 平倉，波段最低價 95.0
    sm.record_exit(
        side="SHORT",
        bar_index=20,
        exit_price=96.0,
        peak_price=95.0,
        reason="PROFIT_LOCK",
    )

    # 冷卻已滿 (bar 24)，但空單現價 95.5 未跌破前低 95.0 -> CHASING_VETO
    can_enter, msg = sm.validate_entry_cooldown("SHORT", current_bar_index=24, current_price=95.5)
    assert can_enter is False
    assert "CHASING_VETO" in msg

    # 空單現價 94.5 跌破前低 95.0 -> 放行！
    can_enter, msg = sm.validate_entry_cooldown("SHORT", current_bar_index=24, current_price=94.5)
    assert can_enter is True


# ==============================================================================
# 測試 2：UnifiedExitGate 硬止損 (-15% ROE)
# ==============================================================================
def test_unified_exit_gate_hard_stop_loss():
    gate = UnifiedExitGate(leverage=5.0)
    df = _build_test_df(30, start_price=100.0, step=0.1)
    df.loc[df.index[-1], "close"] = 96.0  # -4% 價格跌幅 -> 5x 槓桿 ROE = -20% <= -15%

    pos = {"side": "LONG", "entry_price": 100.0, "peak_price": 100.5}
    should_exit, code, reason = gate.evaluate_exit(pos, df, current_bar_index=30)
    assert should_exit is True
    assert code == "HARD_STOP_LOSS"


# ==============================================================================
# 測試 3：UnifiedExitGate 雙階梯鎖利 (15% 鎖 80%, 10% 鎖 70%)
# ==============================================================================
def test_unified_exit_gate_profit_lock_tiers():
    gate = UnifiedExitGate(leverage=5.0)

    # Tier 2: 峰值 ROE 20% (>= 15%)，鎖 80% (16%)。當前 ROE 回吐至 14% -> 觸發 PROFIT_LOCK_T2
    df = _build_test_df(30, start_price=100.0)
    df.loc[df.index[-1], "close"] = 102.8  # +2.8% 漲幅 -> 5x ROE = 14%
    pos = {"side": "LONG", "entry_price": 100.0, "peak_price": 104.0}  # peak +4% -> peak ROE 20%

    should_exit, code, reason = gate.evaluate_exit(pos, df, current_bar_index=30)
    assert should_exit is True
    assert code == "PROFIT_LOCK_T2"

    # Tier 1: 峰值 ROE 12% (>= 10%)，鎖 70% (8.4%)。當前 ROE 回吐至 7.5% -> 觸發 PROFIT_LOCK_T1
    df.loc[df.index[-1], "close"] = 101.5  # +1.5% -> 5x ROE = 7.5%
    pos = {"side": "LONG", "entry_price": 100.0, "peak_price": 102.4}  # peak +2.4% -> peak ROE 12%

    should_exit, code, reason = gate.evaluate_exit(pos, df, current_bar_index=30)
    assert should_exit is True
    assert code == "PROFIT_LOCK_T1"


# ==============================================================================
# 測試 4：UnifiedExitGate 健康回踩強制續抱 (一票否決日常平倉)
# ==============================================================================
def test_unified_exit_gate_healthy_pullback_veto():
    gate = UnifiedExitGate(leverage=5.0)
    df = _build_test_df(30, start_price=100.0)
    # MA5 向上: prev=102.0, now=102.5
    # close=102.3 守在 MA15=101.5 之上
    df.loc[df.index[-2], "ma5"] = 102.0
    df.loc[df.index[-1], "ma5"] = 102.5
    df.loc[df.index[-1], "ma15"] = 101.5
    df.loc[df.index[-1], "close"] = 102.3
    df.loc[df.index[-1], "atr"] = 1.0

    pos = {"side": "LONG", "entry_price": 102.0, "peak_price": 102.6}
    should_exit, code, reason = gate.evaluate_exit(pos, df, current_bar_index=30)
    assert should_exit is False
    assert code == "HOLD_HEALTHY_PULLBACK"


# ==============================================================================
# 測試 5：UnifiedExitGate 均線實質破位衰退平倉 (收盤跌破 MA15 且 MA5 轉平/向下)
# ==============================================================================
def test_unified_exit_gate_ma15_lost_exit():
    gate = UnifiedExitGate(leverage=5.0)
    df = _build_test_df(30, start_price=100.0)
    # MA5 向下: prev=102.5, now=101.8
    # close=101.0 跌破 MA15=101.5
    df.loc[df.index[-2], "ma5"] = 102.5
    df.loc[df.index[-1], "ma5"] = 101.8
    df.loc[df.index[-1], "ma15"] = 101.5
    df.loc[df.index[-1], "close"] = 101.0
    df.loc[df.index[-1], "atr"] = 1.0

    pos = {"side": "LONG", "entry_price": 102.0, "peak_price": 102.6}
    should_exit, code, reason = gate.evaluate_exit(pos, df, current_bar_index=30)
    assert should_exit is True
    assert code == "LONG_EXIT_MA15_LOST"


# ==============================================================================
# 測試 6：強勢大確認棒判定與冷卻豁免機制 (can_open_short / can_open_long)
# ==============================================================================
from core.gates.unified_pipeline_gates import (
    is_strong_bearish_breakout,
    is_strong_bullish_breakout,
    can_open_short,
    can_open_long,
)


def test_is_strong_bearish_breakout_criteria():
    prev_bar = {"low": 100.0}
    # 1. 符合標準強勢大黑K：實體 1.5 ATR (>=1.2), 下影線 0.2 (<=0.5*body), 跌破前低 100, 灌破下軌 99.5
    strong_bar = {
        "open": 100.2,
        "close": 98.7,    # body = 1.5 >= 1.2 * 1.0 ATR
        "high": 100.3,
        "low": 98.5,      # lower_shadow = 0.2 <= 0.75
        "kc_lower": 99.5,
    }
    assert is_strong_bearish_breakout(strong_bar, prev_bar, avg_atr=1.0) is True

    # 2. 實體動能不足 (< 1.2 ATR) -> False
    weak_bar = {**strong_bar, "open": 99.6, "close": 98.7}  # body = 0.9 < 1.2
    assert is_strong_bearish_breakout(weak_bar, prev_bar, avg_atr=1.0) is False

    # 3. 下影線過長 (插針假跌破，下影線 > 0.5 * body) -> False
    pin_bar = {**strong_bar, "low": 97.0}  # lower_shadow = 1.7 > 0.75
    assert is_strong_bearish_breakout(pin_bar, prev_bar, avg_atr=1.0) is False


def test_can_open_short_cooldown_exemption():
    df = _build_test_df(30, start_price=100.0)
    # 構造最後兩根：前一根收在 99.0，下軌 99.5
    df.loc[df.index[-2], ["open", "high", "low", "close", "ma5", "kc_lower"]] = [100.0, 100.2, 98.8, 99.0, 99.5, 99.5]

    # 場景 A：冷卻期內 (平倉在 bar 28，當前 bar 29，僅隔 1 根 <= 3)
    # A-1: 普通小碎步陰線 (body=0.5 < 1.2 ATR)
    df.loc[df.index[-1], ["open", "high", "low", "close", "ma5", "kc_lower"]] = [99.0, 99.1, 98.4, 98.5, 99.2, 99.5]
    res = can_open_short(df, current_bar_index=29, avg_atr=1.0, last_exit_idx=28, min_cooldown=3)
    assert res is False, "冷卻期內普通碎步陰線必須被阻擋！"

    # A-2: 強勢大黑K灌破下軌 (open=100.0, close=98.5, body=1.5 >= 1.2 ATR, 下破前低 98.8)
    df.loc[df.index[-1], ["open", "high", "low", "close", "ma5", "kc_lower"]] = [100.0, 100.1, 98.3, 98.5, 99.2, 99.5]
    res_strong = can_open_short(df, current_bar_index=29, avg_atr=1.0, last_exit_idx=28, min_cooldown=3)
    assert res_strong is True, "強勢大黑K灌破下軌，必須立刻豁免冷卻期直接開空！"

    # 場景 B：已過冷卻期 (平倉在 bar 20，當前 bar 25)
    # 普通兩根破軌小碎步 -> 順利放行！
    df.loc[df.index[-1], ["open", "high", "low", "close", "ma5", "kc_lower"]] = [99.0, 99.1, 98.4, 98.5, 99.2, 99.5]
    res_passed = can_open_short(df, current_bar_index=25, avg_atr=1.0, last_exit_idx=20, min_cooldown=3)
    assert res_passed is True, "已過冷卻期且兩根破下軌，必須放行！"


def test_can_open_long_cooldown_exemption():
    df = _build_test_df(30, start_price=100.0)
    # 前一根
    df.loc[df.index[-2], ["open", "high", "low", "close", "ma5", "kc_upper"]] = [100.0, 101.2, 99.8, 101.0, 100.5, 100.5]

    # 冷卻期內強勢大陽K衝破上軌 (open=100.0, close=101.5, body=1.5 >= 1.2 ATR)
    df.loc[df.index[-1], ["open", "high", "low", "close", "ma5", "kc_upper"]] = [100.0, 101.7, 99.9, 101.5, 100.8, 100.5]
    res_strong_long = can_open_long(df, current_bar_index=29, avg_atr=1.0, last_exit_idx=28, min_cooldown=3)
    assert res_strong_long is True, "強勢大陽K衝破上軌，必須立刻豁免冷卻期直接開多！"
