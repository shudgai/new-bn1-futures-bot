"""Unit tests for Initial Holding Protection, Fast Peak/Valley Exit, and Seamless Short Entry."""
import pytest
import pandas as pd
from unittest.mock import Mock

from core.services.exits.dual_track_exit_service import DualTrackExitStrategy
from core.services.entry_contract import (
    opposite_side_close_cooldown_problem,
    check_entry_gates,
)


def test_initial_holding_protection_long_on_ma5():
    """【第一部分】多頭初生期強制抱緊：在多頭結構中，只要 1m K 棒收盤價仍穩穩踩在 MA5 上方，一票否決平多。"""
    strategy = DualTrackExitStrategy()
    entry = 100.0
    atr = 1.0
    position = {
        'symbol': 'CAP/USDT',
        'side': 'LONG',
        'entry_price': entry,
        'entry_atr': atr,
        'highest_price': 100.8,  # 微幅浮盈，未達 1.0 ATR
        'open_timestamp': 1000,
        'qty': 10.0,
    }

    stamp = 60000
    # 行情踩在 MA5 上方（ma5=100.3, close=100.5 >= ma5），MA5 > MA15，KC向上
    frame = pd.DataFrame([
        dict(timestamp=stamp-60000, is_closed=True, open=100.0, high=100.6, low=99.8, close=100.4,
             ma5=100.2, ma15=99.8, kc_middle=100.0, atr=atr, channel_state="KC向上"),
        dict(timestamp=stamp, is_closed=True, open=100.4, high=100.7, low=100.2, close=100.5,
             ma5=100.3, ma15=99.9, kc_middle=100.1, atr=atr, channel_state="KC向上"),
    ])

    exit_signal = strategy.evaluate_exit(position, frame=frame, current_price=100.5, is_closed=True)
    # 強制鎖死禁止平多（None）
    assert exit_signal is None


def test_initial_holding_protection_short_below_ma5():
    """【第一部分】空頭初生期強制抱緊：在空頭結構中，只要 1m K 棒收盤價仍壓制在 MA5 下方，一票否決平空。"""
    strategy = DualTrackExitStrategy()
    entry = 100.0
    atr = 1.0
    position = {
        'symbol': 'CAP/USDT',
        'side': 'SHORT',
        'entry_price': entry,
        'entry_atr': atr,
        'lowest_price': 99.2,  # 微幅浮盈，未達 1.0 ATR
        'open_timestamp': 1000,
        'qty': 10.0,
    }

    stamp = 60000
    # 行情壓制在 MA5 下方（ma5=99.7, close=99.5 <= ma5），MA5 < MA15，KC向下
    frame = pd.DataFrame([
        dict(timestamp=stamp-60000, is_closed=True, open=100.0, high=100.2, low=99.4, close=99.6,
             ma5=99.8, ma15=100.2, kc_middle=100.0, atr=atr, channel_state="KC向下"),
        dict(timestamp=stamp, is_closed=True, open=99.6, high=99.8, low=99.3, close=99.5,
             ma5=99.7, ma15=100.1, kc_middle=99.9, atr=atr, channel_state="KC向下"),
    ])

    exit_signal = strategy.evaluate_exit(position, frame=frame, current_price=99.5, is_closed=True)
    # 強制鎖死禁止平空（None）
    assert exit_signal is None




def test_hard_breakeven_floor_1_0_atr():
    """【第二部分】絕對保本底線：浮盈曾達 1.0 ATR，回撤碰觸 entry_price + 0.05% 時強制平手離場。"""
    strategy = DualTrackExitStrategy()
    entry = 100.0
    atr = 1.0
    position = {
        'symbol': '龙虾/USDT',
        'side': 'LONG',
        'entry_price': entry,
        'entry_atr': atr,
        'highest_price': 101.05,  # 浮盈曾達 1.05 ATR >= 1.0 ATR
        'open_timestamp': 1000,
        'qty': 10.0,
    }

    # 跌回 100.04 <= 100.0 * 1.0005 = 100.05
    exit_signal = strategy.evaluate_exit(position, frame=None, current_price=100.04, is_closed=False)
    assert exit_signal == 'EXIT_BY_BREAKEVEN_LOCK'


def test_seamless_short_entry_waives_cooldown():
    """【第三部分】多單平倉後，若盤面確立為空頭排列，立即解除冷卻（opposite_cooldown == None），無縫開空。"""
    symbol = "TEST_SEAMLESS"
    account = Mock()
    account.last_closed_at = {symbol: 1000.0}
    account.last_closed_side = {symbol: "LONG"}
    account.trades = [
        {'symbol': symbol, 'action': 'CLOSE_LONG', 'reason': 'EXIT_BY_FAST_PEAK_REVERSAL', 'timestamp': 1000000}
    ]

    # 當前盤面已確立為空頭結構（MA5 < MA15 且 KC向下）
    frame = pd.DataFrame([
        dict(timestamp=1000000, open=100, high=101, low=98, close=98.5,
             ma5=99.0, ma15=100.5, kc_middle=100.0, atr=1.0, kc_trend='DOWN'),
        dict(timestamp=1060000, open=98.5, high=98.8, low=96.0, close=96.5,
             ma5=97.5, ma15=100.0, kc_middle=99.5, atr=1.0, kc_trend='DOWN'),
    ])

    # 檢測 opposite_side_close_cooldown_problem
    problem = opposite_side_close_cooldown_problem(account, symbol, frame, side="SHORT", cooldown_bars=3)
    # 確立為空頭結構，冷卻應當被立即豁免！
    assert problem is None
