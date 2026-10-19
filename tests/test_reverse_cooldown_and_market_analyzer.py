"""測試反向開單冷卻鎖與市場信號分析器。"""
import pytest
import pandas as pd
from core.services.market_signal_analyzer import analyze_market_signal, evaluate_frame_market_signal
from core.services.reverse_entry_cooldown_lock import check_reverse_cooldown_lock, evaluate_reverse_cooldown_guard


def test_market_signal_analyzer_strong_bullish():
    # Close > 5MA > 15MA > 60MA 且高於 VWAP
    candle = {"close": 105.0}
    res = analyze_market_signal(candle, vwap=100.0, ma5=103.0, ma15=101.0, ma60=98.0)
    assert res["action"] == "ALLOW_LONG"
    assert res["status"] == "【做多】"


def test_market_signal_analyzer_strong_bearish():
    # Close < 5MA < 15MA < 60MA 且低於 VWAP
    candle = {"close": 90.0}
    res = analyze_market_signal(candle, vwap=100.0, ma5=92.0, ma15=95.0, ma60=98.0)
    assert res["action"] == "ALLOW_SHORT"
    assert res["status"] == "【做空】"


def test_market_signal_analyzer_hold_long_on_pullback():
    # 多頭結構內的正常回踩：close > ma60 且 close >= min(ma15, vwap)
    # 此時即使 close < ma5 (短線下拐回調)，依然堅定 HOLD_LONG！
    candle = {"close": 101.5}
    res = analyze_market_signal(candle, vwap=101.0, ma5=103.0, ma15=102.0, ma60=98.0)
    assert res["action"] == "HOLD_LONG"
    assert "多頭回踩" in res["status"]


def test_market_signal_analyzer_hold_short_on_rebound():
    # 空頭結構內的正常反彈：close < ma60 且 close <= max(ma15, vwap)
    # 此時即使 close > ma5 (短線上拐反彈)，依然堅定 HOLD_SHORT！
    candle = {"close": 94.0}
    res = analyze_market_signal(candle, vwap=95.0, ma5=92.0, ma15=96.0, ma60=99.0)
    assert res["action"] == "HOLD_SHORT"
    assert "空頭反彈" in res["status"]


def test_reverse_cooldown_lock_blocks_short_after_long():
    # 剛平多單 (last_side='long')，若想開空 (target_action='OPEN_SHORT')：
    # 1. 小於 3 根 K 棒被擋
    last_trade = {"side": "long", "bar_index": 100}
    action, reason = check_reverse_cooldown_lock(
        symbol="CAP/USDT",
        target_action="OPEN_SHORT",
        current_price=95.0,
        current_bar_index=101,  # 只過了 1 根
        last_closed_trade=last_trade,
        ma60=98.0,
    )
    assert action == "WAIT"
    assert "平多冷卻中" in reason

    # 2. 已過 3 根 K 棒，但價格高於 60MA (尚未穩穩跌破)，依然被擋
    action, reason = check_reverse_cooldown_lock(
        symbol="CAP/USDT",
        target_action="OPEN_SHORT",
        current_price=99.0,  # 99.0 > 60MA (98.0)
        current_bar_index=105,  # 已過 5 根
        last_closed_trade=last_trade,
        ma60=98.0,
    )
    assert action == "WAIT"
    assert "價格未收在 60MA 下方" in reason

    # 3. 已過 3 根且跌破 60MA，放行！
    action, reason = check_reverse_cooldown_lock(
        symbol="CAP/USDT",
        target_action="OPEN_SHORT",
        current_price=96.0,  # 96.0 < 60MA (98.0)
        current_bar_index=105,
        last_closed_trade=last_trade,
        ma60=98.0,
    )
    assert action == "OPEN_SHORT"
    assert reason is None


def test_reverse_cooldown_lock_blocks_long_after_short():
    # 剛平空單 (last_side='short')，若想開多 (target_action='OPEN_LONG')：
    # 1. 小於 3 根 K 棒被擋
    last_trade = {"side": "short", "bar_index": 100}
    action, reason = check_reverse_cooldown_lock(
        symbol="CAP/USDT",
        target_action="OPEN_LONG",
        current_price=105.0,
        current_bar_index=102,  # 只過了 2 根
        last_closed_trade=last_trade,
        ma60=100.0,
    )
    assert action == "WAIT"
    assert "平空冷卻中" in reason

    # 2. 已過 3 根 K 棒，但價格低於 60MA (尚未站上)，依然被擋
    action, reason = check_reverse_cooldown_lock(
        symbol="CAP/USDT",
        target_action="OPEN_LONG",
        current_price=98.0,  # 98.0 < 60MA (100.0)
        current_bar_index=105,
        last_closed_trade=last_trade,
        ma60=100.0,
    )
    assert action == "WAIT"
    assert "價格未收在 60MA 上方" in reason

    # 3. 已過 3 根且站上 60MA，放行！
    action, reason = check_reverse_cooldown_lock(
        symbol="CAP/USDT",
        target_action="OPEN_LONG",
        current_price=102.0,  # 102.0 > 60MA (100.0)
        current_bar_index=105,
        last_closed_trade=last_trade,
        ma60=100.0,
    )
    assert action == "OPEN_LONG"
    assert reason is None
