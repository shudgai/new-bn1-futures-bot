"""Test Suite for Emergency Fix: Direction Consistency and Reversal Gate Guards.

驗證項目：
1. KC_UP 時普通空單（突破、延續、均線交叉）必須被阻擋 (BLOCKED_SHORT_ON_KC_UPWARD_TREND)。
2. KC_DOWN 時普通多單（突破、延續、均線交叉）必須被阻擋 (BLOCKED_LONG_ON_KC_DOWNWARD_TREND)。
3. 真正峰頂反轉 (Mode A / Peak Reversal) 可以依獨立條件評估空單。
4. 真正谷底反轉 (Mode A / Valley Reversal) 可以依獨立條件評估多單。
5. 正常趨勢回踩不得產生反向開倉。
6. UI 方向標籤 (getChartCkTrend) 與實際下單方向授權嚴格一致。
"""

import pytest
import pandas as pd
from core.services.entry_methods import (
    revalidate_entry_method,
    MEME_GATE_ENTRY,
    KC_TWO_BAR_BREAKOUT,
    MA5_MA15_CROSS,
)
from core.gates.master_gate_funnel import (
    MarketSnapshot,
    MasterGateFunnel,
    MasterOpenGatekeeper,
)


def _base_snapshot(**overrides) -> MarketSnapshot:
    defaults = dict(
        symbol="1000PEPE/USDT",
        quote=0.00502,
        open_price=0.00500,
        close_price=0.00502,
        high_price=0.00504,
        low_price=0.00499,
        ma3_curr=0.00502,
        ma3_prev=0.00500,
        ma5_curr=0.00501,
        ma5_prev=0.00499,
        ma15_curr=0.00498,
        ma15_prev=0.00497,
        kc_upper=0.00510,
        kc_middle=0.00498,
        kc_middle_prev=0.00496,
        kc_lower=0.00486,
        atr=0.0001,
        macro_trend="BULLISH",
        ma_cross_count_8=0,
        timeframe_ms=60000.0,
        is_closed=True,
    )
    defaults.update(overrides)
    return MarketSnapshot(**defaults)


def _make_bar(ts, o, h, l, c, ma5, ma15, kc_u, kc_m, kc_l, atr=0.0001, is_closed=True):
    return {
        "timestamp": ts,
        "open": o,
        "high": h,
        "low": l,
        "close": c,
        "ma5": ma5,
        "ma15": ma15,
        "kc_upper": kc_u,
        "kc_middle": kc_m,
        "kc_lower": kc_l,
        "atr": atr,
        "volume": 1000.0,
        "is_closed": is_closed,
    }


def test_kc_up_blocks_normal_short_entry():
    """KC_UP（中軌向上，界面標頭顯示『KC 向上 (只評估多單)』）時，普通趨勢或突破空單 100% 必須被阻擋。"""
    import time
    now_ms = time.time() * 1000
    base_t = int(now_ms / 60000) * 60 - 600
    bars = []
    base_mid = 0.00490
    for i in range(10):
        t = (base_t + i * 60) * 1000
        mid = base_mid + i * 0.00002  # 明確向上
        bars.append(_make_bar(t, mid, mid + 0.0001, mid - 0.0001, mid + 0.00005, mid, mid - 0.00002, mid + 0.0002, mid, mid - 0.0002))
    df = pd.DataFrame(bars)
    df.attrs["snapshot_ms"] = now_ms
    df.attrs["timeframe_ms"] = 60000.0

    context = {
        "entry_method_authority": MEME_GATE_ENTRY,
        "entry_method_signal": {"event_id": "test_event", "trigger": "TRIGGER_RELAXED_KC_BREAKOUT_SHORT"},
        "entry_method_bar_id": bars[-1]["timestamp"],
        "entry_method_event_id": "test_event",
    }

    allowed, reason = revalidate_entry_method(
        df,
        symbol="1000PEPE/USDT",
        side="SHORT",
        quote=0.00475,
        context=context,
        final_submission=True,
    )
    # 必須被阻擋，不能在 KC_UP 下開空
    assert allowed is False
    assert "BLOCKED_SHORT_ON_KC_UPWARD_TREND" in reason or "BLOCKED_SHORT_ON_BULLISH_TREND" in reason


def test_kc_down_blocks_normal_long_entry():
    """KC_DOWN（中軌向下，界面標頭顯示『KC 向下 (只評估空單)』）時，普通趨勢或突破多單 100% 必須被阻擋。"""
    import time
    now_ms = time.time() * 1000
    base_t = int(now_ms / 60000) * 60 - 600
    bars = []
    base_mid = 0.00510
    for i in range(10):
        t = (base_t + i * 60) * 1000
        mid = base_mid - i * 0.00002  # 明確向下
        bars.append(_make_bar(t, mid, mid + 0.0001, mid - 0.0001, mid - 0.00005, mid, mid + 0.00002, mid + 0.0002, mid, mid - 0.0002))
    df = pd.DataFrame(bars)
    df.attrs["snapshot_ms"] = now_ms
    df.attrs["timeframe_ms"] = 60000.0

    context = {
        "entry_method_authority": MEME_GATE_ENTRY,
        "entry_method_signal": {"event_id": "test_event", "trigger": "TRIGGER_RELAXED_KC_BREAKOUT_LONG"},
        "entry_method_bar_id": bars[-1]["timestamp"],
        "entry_method_event_id": "test_event",
    }

    allowed, reason = revalidate_entry_method(
        df,
        symbol="1000PEPE/USDT",
        side="LONG",
        quote=0.00535,
        context=context,
        final_submission=True,
    )
    assert allowed is False
    assert "BLOCKED_LONG_ON_KC_DOWNWARD_TREND" in reason or "BLOCKED_LONG_ON_BEARISH_TREND" in reason


def test_true_peak_reversal_allowed_independently():
    """真正的峰頂反轉 (Mode A / Peak Reversal)，在具備完整證據時，允許評估逆勢空單。"""
    peak_short = _base_snapshot(
        open_price=0.00515,
        close_price=0.00505,
        quote=0.00505,
        high_price=0.00518,
        low_price=0.00504,
        ma5_curr=0.00508,
        ma5_prev=0.00512,  # MA5 拐頭向下
        ma15_curr=0.00506,
        ma15_prev=0.00505,
        kc_upper=0.00512,
        kc_middle=0.00505,
        kc_middle_prev=0.00504,
        kc_lower=0.00498,
        current_swing_high=0.00518,
        prev_swing_high=0.00525,  # LH 結構
    )
    ok_s, trig_s = MasterGateFunnel.evaluate_mode_a_reversal_entry(peak_short, "SHORT")
    assert ok_s is True
    assert trig_s in ("TRIGGER_TOP_PIVOT_CROSS", "TRIGGER_MODE_A_PEAK_REVERSAL_SHORT")


def test_true_valley_reversal_allowed_independently():
    """真正的谷底反轉 (Mode A / Valley Reversal)，在具備完整證據時，允許評估逆勢多單。"""
    valley_long = _base_snapshot(
        open_price=0.00495,
        close_price=0.00505,
        quote=0.00505,
        high_price=0.00506,
        low_price=0.00494,
        ma5_curr=0.00502,
        ma5_prev=0.00498,  # MA5 拐頭向上
        ma15_curr=0.00504,
        ma15_prev=0.00505,
        kc_upper=0.00512,
        kc_middle=0.00505,
        kc_middle_prev=0.00506,
        kc_lower=0.00498,
        current_swing_low=0.00494,
        prev_swing_low=0.00490,  # HL 結構
    )
    ok_l, trig_l = MasterGateFunnel.evaluate_mode_a_reversal_entry(valley_long, "LONG")
    assert ok_l is True
    assert trig_l in ("TRIGGER_BOTTOM_PIVOT_CROSS", "TRIGGER_MODE_A_VALLEY_REVERSAL_LONG")


def test_normal_pullback_does_not_open_opposite_side():
    """正常趨勢回踩（如多頭趨勢中的單根小陰線）不得被誤判為峰頂反轉而開空。"""
    pullback_snap = _base_snapshot(
        open_price=0.00503,
        close_price=0.00501,  # 普通回踩小陰線
        quote=0.00501,
        high_price=0.00504,
        low_price=0.00500,
        ma5_curr=0.00502,
        ma5_prev=0.00500,
        ma15_curr=0.00498,
        ma15_prev=0.00496,
        kc_upper=0.00515,
        kc_middle=0.00500,
        kc_middle_prev=0.00498,  # 中軌向上多頭趨勢中
        current_swing_high=0.00504,
        prev_swing_high=0.00502,  # 仍在創高，無 LH 反轉結構
    )
    is_mode_a, _ = MasterGateFunnel.evaluate_mode_a_reversal_entry(pullback_snap, "SHORT")
    assert is_mode_a is False, "正常回踩絕不可誤判為峰頂反轉！"


def test_lobster_historical_breakdown_1456_pattern():
    """驗證類似 14:56 龍蝦跌破結構：
    - MA5 死叉 MA15 且雙雙陡峭向下
    - KC 中軌向下
    - 收盤跌破 KC 下軌 (同側破軌確認)
    - 系統應順利放行順勢空單授權，且無逆向或平線阻礙。
    """
    breakdown_snap = _base_snapshot(
        symbol="龙虾/USDT",
        open_price=0.01676,
        high_price=0.01677,
        low_price=0.01669,
        close_price=0.01669,
        quote=0.01669,
        ma3_curr=0.01671,
        ma3_prev=0.01678,
        ma5_curr=0.016814,
        ma5_prev=0.016850,  # 陡峭向下
        ma15_curr=0.016828,
        ma15_prev=0.016838,  # 向下且 MA5 < MA15
        kc_upper=0.016931,
        kc_middle=0.016834,
        kc_middle_prev=0.016849,  # 中軌向下
        kc_lower=0.016736,  # 收盤 0.01669 跌破 KC 下軌
        atr=0.00008,
        macro_trend="BEARISH",
        is_closed=True,
    )
    dec = MasterOpenGatekeeper.validate_open_signal(breakdown_snap, "SHORT")
    assert dec.can_execute is True, f"14:56 類型跌破空單應授權執行，但被拒絕: {dec.reason}"
    assert "TRIGGER" in dec.reason
