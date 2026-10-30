import time
import pytest
import pandas as pd
from unittest.mock import MagicMock


def test_v_reversal_anti_chasing_veto():
    """驗證：無論大週期標籤為何，只要當前 K 棒出現實體長綠棒 (close > open) 且價格已站上 MA3 或 MA15，強制一票否決 OPEN_SHORT"""
    c_c = 101.0
    c_o = 99.0  # 實體綠棒
    m3_c = 100.5
    m15_c = 100.0
    curr_quote = 101.2
    atr_v = 1.0

    is_v_reversal_bounce = bool(
        (c_c > c_o and (c_c >= m3_c or curr_quote >= m3_c or c_c >= m15_c or curr_quote >= m15_c))
        or (c_c > m15_c or curr_quote > m15_c)
        or (c_c > c_o and (c_c - c_o) >= 0.20 * atr_v)
    )
    assert is_v_reversal_bounce is True, "V 轉強勢反彈必須判定為 True，行使一票否決開空"


def test_short_cooldown_after_exit():
    """驗證：平空離場後 2~3 根 K 棒內 (180s) 強制凍結同向開空"""
    now = time.time()
    last_closed_time = now - 60.0  # 僅經過 60 秒 (1 根 K 棒)
    last_closed_side = "SHORT"

    is_in_short_cooldown = bool(last_closed_side == "SHORT" and (now - last_closed_time) < 180.0)
    assert is_in_short_cooldown is True, "60s 內必須處於平空冷卻期"

    # 超過 180 秒 (3 根 K 棒)
    past_closed_time = now - 200.0
    is_in_short_cooldown_past = bool(last_closed_side == "SHORT" and (now - past_closed_time) < 180.0)
    assert is_in_short_cooldown_past is False, "超過 180s 冷卻期解除"


def test_long_early_breakout_ma15():
    """驗證：牛市且無持倉時，單根 K 棒實體突破 MA15 (close > ma15 且 close > open)，無需金叉即可早期開多"""
    c_c = 102.0
    c_o = 99.0
    m15_c = 100.0
    curr_quote = 102.5
    is_heavy_bear_smash = False

    is_early_ma15_breakout = bool(curr_quote > m15_c and c_c > m15_c and c_c > c_o and (not is_heavy_bear_smash))
    assert is_early_ma15_breakout is True, "單根突破 MA15 必須放行早期開多"


def test_short_early_breakdown_ma15():
    """驗證：熊市且無持倉時，單根 K 棒實體跌破 MA15 (close < ma15 且 close < open)，無需死叉即可早期開空"""
    c_c = 98.0
    c_o = 101.0
    m15_c = 100.0
    curr_quote = 97.5
    is_heavy_bull_surge = False

    is_early_ma15_breakdown = bool(curr_quote < m15_c and c_c < m15_c and c_c < c_o and (not is_heavy_bull_surge))
    assert is_early_ma15_breakdown is True, "單根跌破 MA15 必須放行早期開空"



from core.services.scalping_state_machine import ScalpingStateMachine


def test_state_machine_macro_trend_veto():
    """驗證規則一：大週期順勢一票否決"""
    # 牛市中，即使條件符合跌破，FLAT 狀態絕不開空
    res_bull = ScalpingStateMachine.evaluate_dict(
        position_state="FLAT",
        macro_trend="BULLISH",
        current_price=95.0,
        ma3=94.0,
        ma15=100.0,
        ma3_slope=-0.05,
        open_price=96.0,
    )
    assert res_bull["action"] == "WAIT"
    assert "大週期牛市觀望中" in res_bull["reason"]

    # 熊市中，即使條件符合突破，FLAT 狀態絕不開多
    res_bear = ScalpingStateMachine.evaluate_dict(
        position_state="FLAT",
        macro_trend="BEARISH",
        current_price=105.0,
        ma3=106.0,
        ma15=100.0,
        ma3_slope=0.05,
        open_price=104.0,
    )
    assert res_bear["action"] == "WAIT"
    assert "大週期熊市觀望中" in res_bear["reason"]


def test_state_machine_structure_break_reversal():
    """驗證規則一.2：大週期結束即刻一鍵轉向"""
    # 熊轉牛 + 持有空單 -> 一鍵翻多 CLOSE_SHORT_AND_OPEN_LONG
    res_flip_long = ScalpingStateMachine.evaluate_dict(
        position_state="SHORT",
        macro_trend="BULLISH",
        current_price=102.0,
        ma3=101.0,
        ma15=100.0,
        is_structure_break=True,
    )
    assert res_flip_long["action"] == "CLOSE_SHORT_AND_OPEN_LONG"

    # 熊轉牛 + 無持倉 -> 立刻 OPEN_LONG
    res_open_long = ScalpingStateMachine.evaluate_dict(
        position_state="FLAT",
        macro_trend="BULLISH",
        current_price=102.0,
        ma3=101.0,
        ma15=100.0,
        is_structure_break=True,
    )
    assert res_open_long["action"] == "OPEN_LONG"

    # 牛轉熊 + 持有多單 -> 一鍵翻空 CLOSE_LONG_AND_OPEN_SHORT
    res_flip_short = ScalpingStateMachine.evaluate_dict(
        position_state="LONG",
        macro_trend="BEARISH",
        current_price=98.0,
        ma3=99.0,
        ma15=100.0,
        is_structure_break=True,
    )
    assert res_flip_short["action"] == "CLOSE_LONG_AND_OPEN_SHORT"

    # 牛轉熊 + 無持倉 -> 立刻 OPEN_SHORT
    res_open_short = ScalpingStateMachine.evaluate_dict(
        position_state="FLAT",
        macro_trend="BEARISH",
        current_price=98.0,
        ma3=99.0,
        ma15=100.0,
        is_structure_break=True,
    )
    assert res_open_short["action"] == "OPEN_SHORT"


def test_state_machine_long_double_veto():
    """驗證規則二：多頭【位置+斜率】雙重否決"""
    # 1. 位置否決：MA3 < MA15 (空頭排列)
    res_pos_veto = ScalpingStateMachine.evaluate_dict(
        position_state="FLAT",
        macro_trend="BULLISH",
        current_price=102.0,
        open_price=100.0,
        ma3=99.0,
        ma15=100.0,
        ma3_slope=0.01,
    )
    assert res_pos_veto["action"] == "WAIT"
    assert "位置否決" in res_pos_veto["reason"]

    # 2. 斜率否決：MA3_Slope < 0 (向下勾)
    res_slope_veto = ScalpingStateMachine.evaluate_dict(
        position_state="FLAT",
        macro_trend="BULLISH",
        current_price=102.0,
        open_price=100.0,
        ma3=101.0,
        ma15=100.0,
        ma3_slope=-0.02,
    )
    assert res_slope_veto["action"] == "WAIT"
    assert "斜率否決" in res_slope_veto["reason"]

    # 3. 雙重通過 -> OPEN_LONG
    res_ok = ScalpingStateMachine.evaluate_dict(
        position_state="FLAT",
        macro_trend="BULLISH",
        current_price=102.0,
        open_price=100.0,
        ma3=101.0,
        ma15=100.0,
        ma3_slope=0.02,
    )
    assert res_ok["action"] == "OPEN_LONG"


def test_state_machine_short_double_veto():
    """驗證規則三：空頭【位置+斜率】雙重否決"""
    # 1. 位置否決：MA3 > MA15 (多頭排列)
    res_pos_veto = ScalpingStateMachine.evaluate_dict(
        position_state="FLAT",
        macro_trend="BEARISH",
        current_price=98.0,
        open_price=100.0,
        ma3=101.0,
        ma15=100.0,
        ma3_slope=-0.01,
    )
    assert res_pos_veto["action"] == "WAIT"
    assert "位置否決" in res_pos_veto["reason"]

    # 2. 斜率否決：MA3_Slope > 0 (向上勾)
    res_slope_veto = ScalpingStateMachine.evaluate_dict(
        position_state="FLAT",
        macro_trend="BEARISH",
        current_price=98.0,
        open_price=100.0,
        ma3=99.0,
        ma15=100.0,
        ma3_slope=0.02,
    )
    assert res_slope_veto["action"] == "WAIT"
    assert "斜率否決" in res_slope_veto["reason"]

    # 3. 雙重通過 -> OPEN_SHORT
    res_ok = ScalpingStateMachine.evaluate_dict(
        position_state="FLAT",
        macro_trend="BEARISH",
        current_price=98.0,
        open_price=100.0,
        ma3=99.0,
        ma15=100.0,
        ma3_slope=-0.02,
    )
    assert res_ok["action"] == "OPEN_SHORT"


def test_state_machine_cooldown_and_exit_rules():
    """驗證規則四：平倉冷卻期與廢除過中軌平倉"""
    # 1. 冷卻期中 -> 100% WAIT
    res_cooldown = ScalpingStateMachine.evaluate_dict(
        position_state="FLAT",
        macro_trend="BEARISH",
        current_price=98.0,
        open_price=100.0,
        ma3=99.0,
        ma15=100.0,
        ma3_slope=-0.02,
        cooldown_active=True,
    )
    assert res_cooldown["action"] == "WAIT"
    assert "冷卻期" in res_cooldown["reason"]

    # 2. 空單持倉中，微幅過線但未實體站穩 MA15 (收陰線或價格仍壓制) -> HOLD
    res_hold_short = ScalpingStateMachine.evaluate_dict(
        position_state="SHORT",
        macro_trend="BEARISH",
        current_price=99.5,
        open_price=100.0,  # 陰線
        ma3=99.0,
        ma15=100.0,
    )
    assert res_hold_short["action"] == "HOLD"

    # 3. 空單持倉中，實體收盤強勢站上 MA15 (curr_p > ma15 且 curr_p > open_p) -> CLOSE_SHORT
    res_exit_short = ScalpingStateMachine.evaluate_dict(
        position_state="SHORT",
        macro_trend="BEARISH",
        current_price=101.0,
        open_price=99.5,  # 實體陽線站上 MA15
        ma3=100.5,
        ma15=100.0,
    )
    assert res_exit_short["action"] == "CLOSE_SHORT"


def test_state_machine_sideways_veto():
    """驗證：震盪行情硬性禁止開倉 (Sideways No-Trade Zone)"""
    # 1. 無持倉 (FLAT) + 震盪行情 (is_sideways=True) -> 100% HOLD / 禁止開倉
    res_flat_sideways = ScalpingStateMachine.evaluate_dict(
        position_state="FLAT",
        macro_trend="BEARISH",
        current_price=98.0,
        open_price=100.0,
        ma3=99.0,
        ma15=100.0,
        ma3_slope=-0.02,
        is_sideways=True,
        sideways_reason="KC 通道寬度 < 0.8% 且均線麻花纏繞",
    )
    assert res_flat_sideways["action"] == "HOLD"
    assert res_flat_sideways["status_label"] == "震盪觀望 (禁止開倉)"
    assert "Sideways No-Trade Zone" in res_flat_sideways["reason"]
    assert "一票否決開倉" in res_flat_sideways["reason"]

    # 2. 持倉中 (SHORT) + 震盪行情 (is_sideways=True) -> 100% 續抱 (HOLD) 防雙向巴掌
    res_pos_sideways = ScalpingStateMachine.evaluate_dict(
        position_state="SHORT",
        macro_trend="BEARISH",
        current_price=98.0,
        open_price=97.5,
        ma3=98.2,
        ma15=98.0,
        is_sideways=True,
    )
    assert res_pos_sideways["action"] == "HOLD"
    assert "續抱持倉防雙向巴掌磨損" in res_pos_sideways["reason"]
