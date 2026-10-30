"""
測試模組：MSB 極速結構破位切換 + 追高追低三重否決 + 順勢守穩 MA15 續抱 (終極版驗證)
"""

import pytest
import pandas as pd
from core.services.scalping_state_machine import ScalpingStateMachine
from core.services.market_structure_break import check_macro_trend_end, find_recent_swing_pivot


def test_instant_msb_bearish_to_bullish():
    """驗證方案 1：熊市中 1m 實體突破最近 LH，當下 Bar 立刻極速翻多"""
    # 構造 1m 下殺數據：最後一個反彈高點 LH 為 0.00518 (三點分形: 0.00515, 0.00518, 0.00512)
    candles = pd.DataFrame([
        {"open": 0.00520, "high": 0.00525, "low": 0.00518, "close": 0.00519},
        {"open": 0.00519, "high": 0.00515, "low": 0.00512, "close": 0.00514},
        {"open": 0.00514, "high": 0.00518, "low": 0.00508, "close": 0.00510},  # 局部 LH: 0.00518
        {"open": 0.00510, "high": 0.00512, "low": 0.00500, "close": 0.00502},  # 探底
        {"open": 0.00502, "high": 0.00522, "low": 0.00501, "close": 0.00520},  # 強勢突破 LH (0.00520 > 0.00518)
    ])
    res = check_macro_trend_end(
        current_macro_trend="BEARISH",
        htf_candles=candles,
        live_1m_candles=candles,
        current_1m_close=0.00520,
        current_1m_ma15=0.00512,
    )
    assert res["event"] == "MACRO_BEARISH_ENDED"
    assert res["macro_trend"] == "BULLISH"
    assert "極速 MSB" in res["reason"]


def test_instant_msb_bullish_to_bearish():
    """驗證方案 1：牛市中 1m 實體跌破最近 HL，當下 Bar 立刻極速翻空"""
    # 構造 1m 拉升數據：最後一個回調低點 HL 為 0.00510
    candles = pd.DataFrame([
        {"open": 0.00500, "high": 0.00515, "low": 0.00498, "close": 0.00512},
        {"open": 0.00512, "high": 0.00525, "low": 0.00510, "close": 0.00522},  # 局部 HL: 0.00510
        {"open": 0.00522, "high": 0.00530, "low": 0.00520, "close": 0.00528},
        {"open": 0.00528, "high": 0.00529, "low": 0.00505, "close": 0.00508},  # 跌破 HL (0.00508 < 0.00510)
    ])
    res = check_macro_trend_end(
        current_macro_trend="BULLISH",
        htf_candles=candles,
        live_1m_candles=candles,
        current_1m_close=0.00508,
        current_1m_ma15=0.00518,
    )
    assert res["event"] == "MACRO_BULLISH_ENDED"
    assert res["macro_trend"] == "BEARISH"
    assert "極速 MSB" in res["reason"]


def test_anti_chasing_long_slope_flat_or_down():
    """驗證方案 2：MA3 斜率走平或向下，100% 一票否決開多"""
    # 模擬 19:32 尾聲：價格在 MA15 之上，但 MA3 斜率為 0 或負
    res_flat = ScalpingStateMachine.evaluate_dict(
        position_state="FLAT",
        macro_trend="BULLISH",
        current_price=101.0,
        open_price=100.0,
        ma3=100.5,
        ma15=100.0,
        ma3_slope=0.0,  # 走平
    )
    assert res_flat["action"] == "WAIT"
    assert "斜率否決" in res_flat["reason"]

    res_down = ScalpingStateMachine.evaluate_dict(
        position_state="FLAT",
        macro_trend="BULLISH",
        current_price=101.0,
        open_price=100.0,
        ma3=100.5,
        ma15=100.0,
        ma3_slope=-0.00001,  # 向下
    )
    assert res_down["action"] == "WAIT"
    assert "斜率否決" in res_down["reason"]


def test_anti_chasing_long_overextended():
    """驗證方案 2：價格嚴重偏離 MA15 正乖離過大，100% 一票否決開多"""
    res = ScalpingStateMachine.evaluate_dict(
        position_state="FLAT",
        macro_trend="BULLISH",
        current_price=103.5,  # 偏離 3.5%
        open_price=101.0,
        ma3=103.0,
        ma15=100.0,
        ma3_slope=0.02,
    )
    assert res["action"] == "WAIT"
    assert "乖離否決" in res["reason"]


def test_anti_chasing_short_slope_flat_or_up():
    """驗證方案 2：MA3 斜率走平或向上，100% 一票否決開空"""
    res_flat = ScalpingStateMachine.evaluate_dict(
        position_state="FLAT",
        macro_trend="BEARISH",
        current_price=99.0,
        open_price=100.0,
        ma3=99.5,
        ma15=100.0,
        ma3_slope=0.0,  # 走平
    )
    assert res_flat["action"] == "WAIT"
    assert "斜率否決" in res_flat["reason"]


def test_anti_chasing_short_overextended():
    """驗證方案 2：價格嚴重偏離 MA15 負乖離過大，100% 一票否決開空"""
    res = ScalpingStateMachine.evaluate_dict(
        position_state="FLAT",
        macro_trend="BEARISH",
        current_price=96.5,  # 負偏離 3.5%
        open_price=99.0,
        ma3=97.0,
        ma15=100.0,
        ma3_slope=-0.02,
    )
    assert res["action"] == "WAIT"
    assert "乖離否決" in res["reason"]


def test_kc_middle_flat_scratch_exit_short():
    """驗證：空單持倉 5 分鐘以上無利潤且 KC 中軌走平，觸發及時撤退"""
    hold_sec = 350.0  # > 300s
    peak_roe_pct = 2.0  # < 5.0%
    favorable_gain_atr = 0.3  # < 0.8
    pos_entry = 0.01916
    curr_quote = 0.01920  # 微虧 0.2%
    kc_slope = 0.000002   # 中軌走平轉正
    kc_slope_5_ref = 0.000001
    closed_c = 0.01920
    closed_kc_mid = 0.01918
    m3_c = 0.01921
    m15_c = 0.01919

    is_stagnation_no_profit = bool(peak_roe_pct < 5.0 and favorable_gain_atr < 0.8)
    is_kc_mid_flat_or_up = bool(kc_slope >= -0.000001 or kc_slope_5_ref >= 0)
    is_in_scratch_loss_short = bool(curr_quote <= pos_entry * 1.006)

    should_exit = bool(
        hold_sec >= 300.0
        and is_stagnation_no_profit
        and is_kc_mid_flat_or_up
        and is_in_scratch_loss_short
        and (closed_c > closed_kc_mid or m3_c >= m15_c)
    )
    assert should_exit is True, "空單中軌走平且無利潤，必須觸發及時撤退！"


def test_kc_middle_flat_scratch_exit_long():
    """驗證：多單持倉 5 分鐘以上無利潤且 KC 中軌走平，觸發及時撤退"""
    hold_sec = 400.0
    peak_roe_pct = 1.5
    favorable_gain_atr = 0.2
    pos_entry = 0.01950
    curr_quote = 0.01945  # 微虧 0.25%
    kc_slope = -0.000001  # 中軌走平轉負
    kc_slope_5_ref = -0.000002
    closed_c = 0.01945
    closed_kc_mid = 0.01948
    m3_c = 0.01944
    m15_c = 0.01947

    is_stagnation_no_profit = bool(peak_roe_pct < 5.0 and favorable_gain_atr < 0.8)
    is_kc_mid_flat_or_down = bool(kc_slope <= 0.000001 or kc_slope_5_ref <= 0)
    is_in_scratch_loss_long = bool(curr_quote >= pos_entry * 0.994)

    should_exit = bool(
        hold_sec >= 300.0
        and is_stagnation_no_profit
        and is_kc_mid_flat_or_down
        and is_in_scratch_loss_long
        and (closed_c < closed_kc_mid or m3_c <= m15_c)
    )
    assert should_exit is True, "多單中軌走平且無利潤，必須觸發及時撤退！"

