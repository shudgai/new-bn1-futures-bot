"""Unit tests for Same-Bar Holding Protection and Profit-Lock Rules.

鐵律：
開倉後不要當根平倉，除非剛好是真峰谷，否則除了鎖利外都不平倉。
"""
import pytest
import pandas as pd
import numpy as np

from core.gates.holding_protection_gate import (
    HoldingProtectionExitGate,
    is_same_bar_as_entry,
    has_confirmed_true_pivot,
)


def make_frame(rows, timeframe_ms=60000):
    df = pd.DataFrame(rows)
    df.attrs['timeframe_ms'] = timeframe_ms
    return df


class TestSameBarAndProfitLockProtection:
    """測試開倉後不要當根平倉，除非剛好是真峰谷，否則除了鎖利外都不平倉"""

    def test_same_bar_veto_ordinary_exit(self):
        """當根 K 棒內普通平倉（如 MA 破位、回調）必須 100% 否決"""
        live_ts = 1791941640000.0  # 1m K 棒開盤時間
        df = make_frame([
            {'open': 10.0, 'close': 10.2, 'high': 10.3, 'low': 9.9, 'timestamp': live_ts - 120000,
             'ma5': 10.1, 'ma15': 9.8, 'kc_upper': 10.4, 'kc_lower': 9.6, 'kc_middle': 10.0, 'atr': 0.2},
            {'open': 10.2, 'close': 10.5, 'high': 10.6, 'low': 10.1, 'timestamp': live_ts - 60000,
             'ma5': 10.3, 'ma15': 9.9, 'kc_upper': 10.5, 'kc_lower': 9.7, 'kc_middle': 10.1, 'atr': 0.2},
            {'open': 10.5, 'close': 10.4, 'high': 10.7, 'low': 10.3, 'timestamp': live_ts,
             'ma5': 10.45, 'ma15': 10.0, 'kc_upper': 10.6, 'kc_lower': 9.8, 'kc_middle': 10.2, 'atr': 0.2},
        ])

        # 持倉在同一根 K 棒內進場 (live_ts + 5000ms)
        position = {
            'symbol': '1000PEPE/USDT',
            'side': 'LONG',
            'entry_price': 10.5,
            'qty': 1000.0,
            'open_timestamp': (live_ts + 5000) / 1000.0,
        }

        assert is_same_bar_as_entry(position, df) is True

        # 當根普通回調平倉 -> 必須被否決
        can_exit, veto_reason, _ = HoldingProtectionExitGate.validate_exit(
            position, df, quote=10.4, candidate_reason='EXIT_BY_PULLBACK'
        )
        assert can_exit is False
        assert veto_reason == 'VETO_SAME_BAR_EXIT_UNLESS_PIVOT_OR_PROFIT_LOCK'

    def test_same_bar_allow_true_pivot_exit(self):
        """當根 K 棒內若剛好成型真峰谷，允許平倉"""
        live_ts = 1791941640000.0
        # 構造前棒高點 11.0，當前棒未創新高 (high 10.6)，且已跌破前棒實體中點 (mid 10.7) 且自峰頂回落達標
        df = make_frame([
            {'open': 10.0, 'close': 10.3, 'high': 10.4, 'low': 9.9, 'timestamp': live_ts - 120000,
             'ma5': 10.1, 'ma15': 9.8, 'kc_upper': 10.5, 'kc_lower': 9.7, 'kc_middle': 10.1, 'atr': 0.3},
            {'open': 10.5, 'close': 10.9, 'high': 11.0, 'low': 10.4, 'timestamp': live_ts - 60000,
             'ma5': 10.5, 'ma15': 10.0, 'kc_upper': 10.8, 'kc_lower': 9.8, 'kc_middle': 10.3, 'atr': 0.3},
            {'open': 10.8, 'close': 10.5, 'high': 10.8, 'low': 10.4, 'timestamp': live_ts,
             'ma5': 10.6, 'ma15': 10.1, 'kc_upper': 10.9, 'kc_lower': 9.9, 'kc_middle': 10.4, 'atr': 0.3},
        ])

        position = {
            'symbol': '龙虾/USDT',
            'side': 'LONG',
            'entry_price': 10.8,
            'qty': 100.0,
            'open_timestamp': (live_ts + 2000) / 1000.0,
        }

        assert is_same_bar_as_entry(position, df) is True

        # 檢驗真峰頂成型
        has_pivot, p_type, _ = has_confirmed_true_pivot(position, df, quote=10.5)
        assert has_pivot is True

        # 授權真峰頂出場
        can_exit, reason, _ = HoldingProtectionExitGate.validate_exit(
            position, df, quote=10.5, candidate_reason='EXIT_BY_CONFIRMED_TRUE_PEAK'
        )
        assert can_exit is True
        assert reason == 'EXIT_BY_CONFIRMED_TRUE_PEAK'

    def test_same_bar_allow_profit_lock_exit(self):
        """當根 K 棒內若達到保本鎖利或階梯鎖利，允許平倉"""
        live_ts = 1791941640000.0
        df = make_frame([
            {'open': 10.0, 'close': 10.2, 'high': 10.3, 'low': 9.9, 'timestamp': live_ts - 120000,
             'ma5': 10.1, 'ma15': 9.8, 'kc_upper': 10.4, 'kc_lower': 9.6, 'kc_middle': 10.0, 'atr': 0.2},
            {'open': 10.2, 'close': 10.5, 'high': 10.6, 'low': 10.1, 'timestamp': live_ts - 60000,
             'ma5': 10.3, 'ma15': 9.9, 'kc_upper': 10.5, 'kc_lower': 9.7, 'kc_middle': 10.1, 'atr': 0.2},
            {'open': 10.5, 'close': 10.9, 'high': 11.2, 'low': 10.5, 'timestamp': live_ts,
             'ma5': 10.7, 'ma15': 10.1, 'kc_upper': 10.8, 'kc_lower': 9.8, 'kc_middle': 10.3, 'atr': 0.2},
        ])

        position = {
            'symbol': '1000PEPE/USDT',
            'side': 'LONG',
            'entry_price': 10.5,
            'qty': 1000.0,
            'open_timestamp': (live_ts + 2000) / 1000.0,
        }

        # 鎖利原因（如保本鎖定 / 階梯鎖利）在當根允許平倉
        can_exit, reason, _ = HoldingProtectionExitGate.validate_exit(
            position, df, quote=10.9, candidate_reason='RATCHET_BREAKEVEN_LOCK'
        )
        assert can_exit is True
        assert reason == 'RATCHET_BREAKEVEN_LOCK'

    def test_cross_bar_veto_ordinary_pullback_allow_profit_lock(self):
        """跨根後（非當根）：普通回調未成真峰谷禁止平倉，但鎖利允許平倉"""
        live_ts = 1791941640000.0
        df = make_frame([
            {'open': 10.0, 'close': 10.2, 'high': 10.3, 'low': 9.9, 'timestamp': live_ts - 120000,
             'ma5': 10.1, 'ma15': 9.8, 'kc_upper': 10.4, 'kc_lower': 9.6, 'kc_middle': 10.0, 'atr': 0.2},
            {'open': 10.2, 'close': 10.5, 'high': 10.6, 'low': 10.1, 'timestamp': live_ts - 60000,
             'ma5': 10.3, 'ma15': 9.9, 'kc_upper': 10.5, 'kc_lower': 9.7, 'kc_middle': 10.1, 'atr': 0.2},
            {'open': 10.5, 'close': 10.6, 'high': 10.7, 'low': 10.4, 'timestamp': live_ts,
             'ma5': 10.5, 'ma15': 10.1, 'kc_upper': 10.8, 'kc_lower': 9.8, 'kc_middle': 10.3, 'atr': 0.2},
        ])

        # 持倉在兩根前進場
        position = {
            'symbol': '龙虾/USDT',
            'side': 'LONG',
            'entry_price': 10.1,
            'qty': 100.0,
            'open_timestamp': (live_ts - 120000 + 5000) / 1000.0,
        }

        assert is_same_bar_as_entry(position, df) is False

        # 1. 普通回調（非鎖利、非真峰谷） -> 100% 否決
        can_exit, veto_reason, _ = HoldingProtectionExitGate.validate_exit(
            position, df, quote=10.5, candidate_reason='EXIT_BY_PULLBACK'
        )
        assert can_exit is False
        assert 'VETO' in veto_reason

        # 2. 鎖利（如保本鎖定 / 階梯鎖利） -> 允許平倉
        can_exit, reason, _ = HoldingProtectionExitGate.validate_exit(
            position, df, quote=10.5, candidate_reason='RATCHET_BREAKEVEN_LOCK'
        )
        assert can_exit is True
        assert reason == 'RATCHET_BREAKEVEN_LOCK'
