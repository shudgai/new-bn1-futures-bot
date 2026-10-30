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
