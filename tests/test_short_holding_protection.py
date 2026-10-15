"""Unit tests for Short Holding Protection (龍蝦 / CAP 空單走勢抱緊，直至真谷底才平倉).

規則要點：
1. 空頭走勢護航（Bearish Trend Holding）：只要收盤價或最新價在 KC 中軌下方（close <= kc_middle），
   或均線空頭排列（MA5 <= MA15 且 MA15 向下），盤中任何反彈、觸碰 MA5、回到下軌內均視為良性回測，嚴禁平倉！
2. 出場唯一條件：必須收線確認形成確定性「真谷底（Three-Point Valley Pivot 或 True Fractal Valley）」，
   或實體收盤明確站上 KC 中軌逆轉。
3. 極端安全風控（硬止損、反側破軌）除外。
"""
import pytest
import pandas as pd
import numpy as np

from core.gates.meme_coin_gate import MemeCoinGate
from core.gates.holding_protection_gate import HoldingProtectionExitGate, has_confirmed_true_pivot
from core.services.exits.dual_track_exit_service import DualTrackExitStrategy
from core.services.exits.peak_trailing_exit import evaluate_peak_trailing


def make_frame(rows):
    df = pd.DataFrame(rows)
    df.attrs['timeframe_ms'] = 60000
    return df


class TestShortHoldingProtection:
    """測試空頭走勢抱緊鐵律：不到真谷底不平倉"""

    def test_meme_coin_short_holding_below_kc_middle_no_valley(self):
        """空單在 KC 中軌下方且未成型真谷底，觸碰 MA5 或回到下軌內禁止平倉"""
        # 構造空頭下跌走勢的 K 線數據（價格由 10 跌至 8，KC 中軌 9.0，下軌 8.2）
        # 當前價格 8.3（稍微回到下軌內，且穿過 MA5 8.25），但仍在 KC 中軌 9.0 下方，且無真谷底
        df = make_frame([
            {'open': 9.5, 'close': 9.0, 'high': 9.6, 'low': 8.9, 'is_closed': True,
             'ma5': 9.3, 'ma15': 9.6, 'kc_upper': 9.8, 'kc_lower': 8.5, 'kc_middle': 9.15, 'atr': 0.5},
            {'open': 9.0, 'close': 8.5, 'high': 9.1, 'low': 8.4, 'is_closed': True,
             'ma5': 8.8, 'ma15': 9.4, 'kc_upper': 9.7, 'kc_lower': 8.3, 'kc_middle': 9.0, 'atr': 0.5},
            {'open': 8.5, 'close': 8.1, 'high': 8.6, 'low': 8.0, 'is_closed': True,
             'ma5': 8.4, 'ma15': 9.2, 'kc_upper': 9.6, 'kc_lower': 8.2, 'kc_middle': 8.9, 'atr': 0.5},
            {'open': 8.1, 'close': 8.3, 'high': 8.4, 'low': 8.05, 'is_closed': False,
             'ma5': 8.25, 'ma15': 9.0, 'kc_upper': 9.6, 'kc_lower': 8.2, 'kc_middle': 8.9, 'atr': 0.5},
        ])
        position = {
            'symbol': 'LOBSTER/USDT',
            'side': 'SHORT',
            'entry_price': 9.2,
            'qty': 100.0,
            'has_confirmed_true_pivot': False,
        }

        # 1. MemeCoinGate 檢驗
        should_exit, reason = MemeCoinGate.evaluate_meme_exit(
            df, quote=8.3, side='SHORT', symbol='LOBSTER/USDT'
        )
        assert should_exit is False
        assert reason is None

        # 2. HoldingProtectionExitGate 檢驗普通回調平倉在未成真谷底前被否決
        can_exit, veto_reason, _ = HoldingProtectionExitGate.validate_exit(
            position, df, quote=8.3, candidate_reason='EXIT_BY_PULLBACK'
        )
        assert can_exit is False
        assert 'VETO' in veto_reason

    def test_meme_coin_short_exit_when_true_valley_confirmed(self):
        """空單收線確認形成三點真谷底，授權平倉"""
        # 構造三點谷底：K0 low 8.2, K1 low 7.8 (中間最低), K2 low 8.1 (收線包夾確認)
        df = make_frame([
            {'open': 8.5, 'close': 8.3, 'high': 8.6, 'low': 8.2, 'is_closed': True,
             'ma5': 8.5, 'ma15': 9.0, 'kc_upper': 9.5, 'kc_lower': 8.0, 'kc_middle': 8.75, 'atr': 0.5},
            {'open': 8.3, 'close': 7.9, 'high': 8.4, 'low': 7.8, 'is_closed': True,
             'ma5': 8.3, 'ma15': 8.8, 'kc_upper': 9.4, 'kc_lower': 7.9, 'kc_middle': 8.65, 'atr': 0.5},
            {'open': 7.9, 'close': 8.2, 'high': 8.3, 'low': 8.1, 'is_closed': True,
             'ma5': 8.2, 'ma15': 8.6, 'kc_upper': 9.3, 'kc_lower': 7.8, 'kc_middle': 8.55, 'atr': 0.5},
            {'open': 8.2, 'close': 8.3, 'high': 8.4, 'low': 8.15, 'is_closed': False,
             'ma5': 8.25, 'ma15': 8.5, 'kc_upper': 9.3, 'kc_lower': 7.8, 'kc_middle': 8.55, 'atr': 0.5},
        ])
        position = {
            'symbol': 'CAP/USDT',
            'side': 'SHORT',
            'entry_price': 9.0,
            'qty': 100.0,
        }

        # 檢驗三點谷底確認
        has_valley, p_type, _ = has_confirmed_true_pivot(position, df, quote=8.3)
        assert has_valley is True
        assert p_type == 'TRUE_VALLEY_PIVOT_CONFIRMED'

        # MemeCoinGate 應授權真谷底出場
        should_exit, reason = MemeCoinGate.evaluate_meme_exit(
            df, quote=8.3, side='SHORT', symbol='CAP/USDT'
        )
        assert should_exit is True
        assert reason == 'EXIT_MEME_TRUE_VALLEY_CONFIRMED'

        # HoldingProtectionExitGate 也應允許平倉
        can_exit, veto_reason, _ = HoldingProtectionExitGate.validate_exit(
            position, df, quote=8.3, candidate_reason='EXIT_BY_CONFIRMED_TRUE_VALLEY'
        )
        assert can_exit is True

    def test_dual_track_exit_short_holds_in_bearish_trend(self):
        """DualTrackExitStrategy 在空頭走勢且無真谷底時，一票否決普通平倉維持 HOLD"""
        df = make_frame([
            {'open': 9.5, 'close': 9.0, 'high': 9.6, 'low': 8.9, 'is_closed': True,
             'ma5': 9.3, 'ma15': 9.6, 'kc_upper': 9.8, 'kc_lower': 8.5, 'kc_middle': 9.15, 'atr': 0.5},
            {'open': 9.0, 'close': 8.5, 'high': 9.1, 'low': 8.4, 'is_closed': True,
             'ma5': 8.8, 'ma15': 9.4, 'kc_upper': 9.7, 'kc_lower': 8.3, 'kc_middle': 9.0, 'atr': 0.5},
            {'open': 8.5, 'close': 8.1, 'high': 8.6, 'low': 8.0, 'is_closed': True,
             'ma5': 8.4, 'ma15': 9.2, 'kc_upper': 9.6, 'kc_lower': 8.2, 'kc_middle': 8.9, 'atr': 0.5},
            {'open': 8.1, 'close': 8.25, 'high': 8.35, 'low': 8.05, 'is_closed': False,
             'ma5': 8.3, 'ma15': 9.0, 'kc_upper': 9.6, 'kc_lower': 8.2, 'kc_middle': 8.9, 'atr': 0.5},
        ])
        position = {
            'symbol': 'LOBSTER/USDT',
            'side': 'SHORT',
            'entry_price': 9.2,
            'qty': 100.0,
            'trough_price': 8.0,
            'lowest_price': 8.0,
        }
        strategy = DualTrackExitStrategy()
        # 價格在 8.25（KC 中軌 8.9 下方，MA5 < MA15，空頭結構無真谷底）
        decision = strategy.evaluate_exit(position, current_price=8.25, frame=df)
        assert decision is None

    def test_peak_trailing_exit_veto_below_kc_middle(self):
        """evaluate_peak_trailing 在 KC 中軌下方一票否決非硬止損平倉"""
        position = {
            'symbol': 'CAP/USDT',
            'side': 'SHORT',
            'entry_price': 9.0,
            'qty': 100.0,
            'lowest_price': 8.0,
        }
        snapshot = {
            'quote_ms': 1700000060000,
            'kc_middle': 8.9,
            'ma5': 8.2,
            'ma15': 8.8,
            'last_ma15': 8.9,  # MA15 斜率向下
        }
        # price = 8.3 在 kc_middle 8.9 之下
        res = evaluate_peak_trailing(position, price=8.3, snapshot=snapshot, atr=0.5)
        assert res is None

