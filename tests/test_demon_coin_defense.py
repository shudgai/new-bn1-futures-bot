# -*- coding: utf-8 -*-
"""
測試妖幣專用 5 大短線防護機制 (DemonCoinDefenseController)
1. 訊號冷卻期機制 (Cooldown Period)
2. 多/空門檻不對稱與量能確認 (Asymmetric Volume Confirmation)
3. 爆量插針/超買過熱過濾 (ATR Extreme Filter)
4. 主動式動態追蹤止盈 (ATR Trailing Stop)
5. 時間停損與無效盤整平倉 (Time-Based Exit)
"""

import time
import pytest
from core.services.demon_coin_defense import (
    DemonCoinDefenseController,
    DemonCoinDefenseConfig,
)


@pytest.fixture(autouse=True)
def reset_defense_state():
    """每次測試前重置防護控制器狀態並一鍵啟用全部防護"""
    DemonCoinDefenseController.reset_state()
    DemonCoinDefenseController.enable_all()
    DemonCoinDefenseController.config.cooldown_bars = 4
    DemonCoinDefenseController.config.cooldown_seconds = 240.0
    DemonCoinDefenseController.config.vol_ma_period = 20
    DemonCoinDefenseController.config.long_vol_ratio = 1.5
    DemonCoinDefenseController.config.fast_short_enabled = True
    DemonCoinDefenseController.config.atr_extreme_mult = 2.5
    DemonCoinDefenseController.config.trailing_arm_atr = 1.5
    DemonCoinDefenseController.config.trailing_pullback_atr = 1.0
    DemonCoinDefenseController.config.max_hold_bars = 8
    DemonCoinDefenseController.config.consolidation_atr_threshold = 0.3
    yield
    DemonCoinDefenseController.disable_all()
    DemonCoinDefenseController.reset_state()


# =============================================================================
# 1. 測試訊號冷卻期機制 (Cooldown Period)
# =============================================================================
def test_cooldown_blocks_reverse_entries():
    symbol = "LOBSTERUSDT"
    now = time.time()

    # 1. 模擬剛平空 (Close Short)
    DemonCoinDefenseController.record_exit(
        symbol=symbol,
        closed_side="SHORT",
        exit_price=0.01950,
        timestamp=now,
    )

    # 剛平空後 10 秒內：嘗試開多 -> 必須被凍結否決！
    passed, reason = DemonCoinDefenseController.check_entry_cooldown(
        symbol=symbol,
        target_side="LONG",
        current_time=now + 10,
    )
    assert passed is False
    assert "VETO_COOLDOWN_LONG_FROZEN" in reason

    # 嘗試順向開空 -> 允許開空 (同向不被反手冷卻限制)
    passed_short, _ = DemonCoinDefenseController.check_entry_cooldown(
        symbol=symbol,
        target_side="SHORT",
        current_time=now + 10,
    )
    assert passed_short is True

    # 超過 240 秒 (4 根 1m K 棒) 後：開多解凍！
    passed_after, _ = DemonCoinDefenseController.check_entry_cooldown(
        symbol=symbol,
        target_side="LONG",
        current_time=now + 250,
    )
    assert passed_after is True


def test_cooldown_blocks_short_after_long_exit():
    symbol = "1000PEPEUSDT"
    now = time.time()

    # 模擬剛平多 (Close Long)
    DemonCoinDefenseController.record_exit(
        symbol=symbol,
        closed_side="LONG",
        exit_price=0.00500,
        timestamp=now,
    )

    # 剛平多後：嘗試開空 -> 必須被凍結否決！
    passed, reason = DemonCoinDefenseController.check_entry_cooldown(
        symbol=symbol,
        target_side="SHORT",
        current_time=now + 30,
    )
    assert passed is False
    assert "VETO_COOLDOWN_SHORT_FROZEN" in reason

    # 超過冷卻期後：開空解凍！
    passed_after, _ = DemonCoinDefenseController.check_entry_cooldown(
        symbol=symbol,
        target_side="SHORT",
        current_time=now + 300,
    )
    assert passed_after is True


# =============================================================================
# 2. 測試多/空門檻不對稱與量能確認 (Asymmetric Volume Confirmation)
# =============================================================================
def test_asymmetric_volume_confirmation():
    vol_ma20 = 1000.0

    # 1. 開多量能不足 (curr_vol = 1200 < 1.5 * 1000 = 1500) -> 否決！
    passed_long_fail, reason = DemonCoinDefenseController.check_volume_confirmation(
        side="LONG",
        curr_volume=1200.0,
        volume_ma20=vol_ma20,
    )
    assert passed_long_fail is False
    assert "VETO_LONG_INSUFFICIENT_VOLUME" in reason

    # 2. 開多放量達標 (curr_vol = 1600 >= 1.5 * 1000) -> 通過！
    passed_long_ok, _ = DemonCoinDefenseController.check_volume_confirmation(
        side="LONG",
        curr_volume=1600.0,
        volume_ma20=vol_ma20,
    )
    assert passed_long_ok is True

    # 3. 開空保持敏捷，無需高量能限制 (curr_vol = 800 < 1000) -> 通過！
    passed_short_ok, _ = DemonCoinDefenseController.check_volume_confirmation(
        side="SHORT",
        curr_volume=800.0,
        volume_ma20=vol_ma20,
    )
    assert passed_short_ok is True


# =============================================================================
# 3. 測試爆量插針/超買過熱過濾 (ATR Extreme Filter)
# =============================================================================
def test_atr_extreme_overheat_filter():
    atr = 10.0  # 門檻 = 2.5 * 10 = 25.0

    # 1. 單棒震幅 30.0 (High 130, Low 100, Close 128, Open 105) >= 25.0
    # 且當前價 128 尚未拉回均線 (MA5 = 115) -> 否決追多！
    curr_k_spike = {"open": 105.0, "high": 130.0, "low": 100.0, "close": 128.0}
    passed_spike, reason = DemonCoinDefenseController.check_atr_extreme_overheat(
        side="LONG",
        curr_k=curr_k_spike,
        current_atr=atr,
        ma5_curr=115.0,
        kc_upper=125.0,
    )
    assert passed_spike is False
    assert "VETO_ATR_EXTREME_OVERHEATED" in reason

    # 2. 價格拉回均線下方 (Close 112 <= MA5 115) -> 解除過熱警報！
    curr_k_pulled_back = {"open": 128.0, "high": 130.0, "low": 110.0, "close": 112.0}
    passed_pulled, _ = DemonCoinDefenseController.check_atr_extreme_overheat(
        side="LONG",
        curr_k=curr_k_pulled_back,
        current_atr=atr,
        ma5_curr=115.0,
        kc_upper=125.0,
    )
    assert passed_pulled is True

    # 3. 正常棒 (震幅 15.0 < 25.0) -> 放行
    curr_k_normal = {"open": 100.0, "high": 115.0, "low": 100.0, "close": 110.0}
    passed_normal, _ = DemonCoinDefenseController.check_atr_extreme_overheat(
        side="LONG",
        curr_k=curr_k_normal,
        current_atr=atr,
        ma5_curr=105.0,
    )
    assert passed_normal is True


# =============================================================================
# 4. 測試主動式動態追蹤止盈 (ATR Trailing Stop)
# =============================================================================
def test_atr_trailing_stop_long():
    atr = 5.0
    # 啟動門檻: 1.5 * 5 = 7.5; 回撤門檻: 1.0 * 5 = 5.0
    pos_long = {
        "side": "LONG",
        "entry_price": 100.0,
        "highest_price": 112.0,  # 浮盈 = 12.0 >= 7.5 (已啟動)
    }

    # 1. 價格從最高 112 回撤至 106 (回撤 6.0 >= 5.0) -> 觸發 Trailing Stop！
    should_exit, reason, meta = DemonCoinDefenseController.check_atr_trailing_stop(
        pos=pos_long,
        current_quote=106.0,
        current_atr=atr,
    )
    assert should_exit is True
    assert reason == "EXIT_BY_ATR_TRAILING_STOP"
    assert meta["trigger"] == "LONG_ATR_TRAILING_STOP"

    # 2. 價格從最高 112 僅微幅回撤至 109 (回撤 3.0 < 5.0) -> 繼續續抱！
    should_hold, _, _ = DemonCoinDefenseController.check_atr_trailing_stop(
        pos=pos_long,
        current_quote=109.0,
        current_atr=atr,
    )
    assert should_hold is False


def test_atr_trailing_stop_short():
    atr = 5.0
    # 啟動門檻: 7.5; 回彈門檻: 5.0
    pos_short = {
        "side": "SHORT",
        "entry_price": 100.0,
        "lowest_price": 88.0,  # 最大跌幅 = 12.0 >= 7.5 (已啟動)
    }

    # 1. 價格從最低 88 反彈至 94 (反彈 6.0 >= 5.0) -> 觸發 Trailing Stop 平空！
    should_exit, reason, _ = DemonCoinDefenseController.check_atr_trailing_stop(
        pos=pos_short,
        current_quote=94.0,
        current_atr=atr,
    )
    assert should_exit is True
    assert reason == "EXIT_BY_ATR_TRAILING_STOP"

    # 2. 價格在 90 (反彈 2.0 < 5.0) -> 繼續抱空
    should_hold, _, _ = DemonCoinDefenseController.check_atr_trailing_stop(
        pos=pos_short,
        current_quote=90.0,
        current_atr=atr,
    )
    assert should_hold is False


# =============================================================================
# 5. 測試時間停損與無效盤整平倉 (Time-Based Exit)
# =============================================================================
def test_time_based_consolidation_exit():
    atr = 10.0
    # 成本區間: 0.3 * 10 = 3.0
    pos = {
        "side": "LONG",
        "entry_price": 100.0,
    }

    # 1. 持倉已達 8 根 K 棒，但價格卡在 101.5 (位移 1.5 <= 3.0) -> 觸發時間停損！
    should_exit, reason, meta = DemonCoinDefenseController.check_time_based_exit(
        pos=pos,
        current_quote=101.5,
        current_atr=atr,
        bars_held=8,
    )
    assert should_exit is True
    assert reason == "EXIT_BY_TIME_BASED_CONSOLIDATION"
    assert meta["bars_held"] == 8

    # 2. 持倉僅 4 根 K 棒 (< 8) -> 不觸發時間停損
    should_hold_bars, _, _ = DemonCoinDefenseController.check_time_based_exit(
        pos=pos,
        current_quote=101.5,
        current_atr=atr,
        bars_held=4,
    )
    assert should_hold_bars is False

    # 3. 持倉 9 根 K 棒，但價格已大幅脫離成本區 (Quote 108.0, 位移 8.0 > 3.0) -> 不觸發盤整停損
    should_hold_trend, _, _ = DemonCoinDefenseController.check_time_based_exit(
        pos=pos,
        current_quote=108.0,
        current_atr=atr,
        bars_held=9,
    )
    assert should_hold_trend is False
