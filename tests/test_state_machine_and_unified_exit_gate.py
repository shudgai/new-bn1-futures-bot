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
