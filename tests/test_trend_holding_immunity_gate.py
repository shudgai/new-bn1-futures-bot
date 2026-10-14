"""Unit tests for TrendHoldingImmunityGate and DualTrackExitStrategy trend immunity integration."""
import pytest
import pandas as pd
from core.services.gates.exit_gate import TrendHoldingImmunityGate
from core.services.exits.dual_track_exit_service import DualTrackExitStrategy


class TestTrendHoldingImmunityGate:
    """單邊趨勢全程護航閘門測試"""

    def test_long_holding_immunity_blocks_exit_on_ma5_pullback(self):
        """多頭爬升回踩 MA5 嘗試平多被 Gate 攔截：close >= ma15, ma5 > ma15"""
        gate = TrendHoldingImmunityGate()
        # 價格回踩 MA5 (105)，但仍在 MA15 (102) 之上，且均線多頭順向
        bar = {'close': 105.0, 'open': 106.0, 'high': 107.0, 'low': 104.5}
        indicators = {
            'ma5': 105.0,
            'ma15': 102.0,
            'ma15_slope': 0.5,
            'kc_middle': 100.0,
        }
        can_exit, reason = gate.can_exit('LONG', bar, indicators)
        assert can_exit is False
        assert "REJECT_EXIT: TREND_INTACT_ABOVE_MA15" in reason

    def test_short_holding_immunity_blocks_exit_on_ma5_rebound(self):
        """空頭下跌反抽 MA5 嘗試平空被 Gate 攔截：close <= ma15, ma5 < ma15"""
        gate = TrendHoldingImmunityGate()
        # 價格反彈碰觸 MA5 (95)，但仍在 MA15 (98) 之下，且均線空頭順向
        bar = {'close': 95.0, 'open': 94.0, 'high': 95.5, 'low': 93.5}
        indicators = {
            'ma5': 95.0,
            'ma15': 98.0,
            'ma15_slope': -0.4,
            'kc_middle': 100.0,
        }
        can_exit, reason = gate.can_exit('SHORT', bar, indicators)
        assert can_exit is False
        assert "REJECT_EXIT: TREND_INTACT_BELOW_MA15" in reason

    def test_long_exit_passed_when_piercing_ma15_or_kc_middle(self):
        """多單實體摜破 MA15 或跌破 KC 中軌，正常放行平倉"""
        gate = TrendHoldingImmunityGate()
        # 實體摜破 MA15 (close 99.5 < ma15 101.0)
        bar = {'close': 99.5, 'open': 102.0, 'high': 102.5, 'low': 99.0}
        indicators = {
            'ma5': 100.5,
            'ma15': 101.0,
            'ma15_slope': -0.1,
            'kc_middle': 100.0,
        }
        can_exit, reason = gate.can_exit('LONG', bar, indicators)
        assert can_exit is True
        assert reason == "PASSED_TREND_GATE"

        # 跌破 KC 中軌 (close 99.0 < kc_middle 100.0)
        bar2 = {'close': 99.0, 'open': 101.0, 'high': 101.5, 'low': 98.8}
        indicators2 = {
            'ma5': 100.0,
            'ma15': 100.5,
            'ma15_slope': -0.2,
            'kc_middle': 100.0,
        }
        can_exit2, reason2 = gate.can_exit('LONG', bar2, indicators2)
        assert can_exit2 is True
        assert reason2 == "PASSED_TREND_GATE"

    def test_short_exit_passed_when_piercing_ma15_or_kc_middle(self):
        """空單實體站上 MA15 或突破 KC 中軌，正常放行平倉"""
        gate = TrendHoldingImmunityGate()
        # 實體站上 MA15 (close 101.5 > ma15 100.0)
        bar = {'close': 101.5, 'open': 99.0, 'high': 102.0, 'low': 98.5}
        indicators = {
            'ma5': 100.5,
            'ma15': 100.0,
            'ma15_slope': 0.2,
            'kc_middle': 101.0,
        }
        can_exit, reason = gate.can_exit('SHORT', bar, indicators)
        assert can_exit is True
        assert reason == "PASSED_TREND_GATE"

        # 突破 KC 中軌 (close 102.5 > kc_middle 102.0)
        bar2 = {'close': 102.5, 'open': 101.0, 'high': 103.0, 'low': 100.5}
        indicators2 = {
            'ma5': 101.0,
            'ma15': 101.5,
            'ma15_slope': 0.1,
            'kc_middle': 102.0,
        }
        can_exit2, reason2 = gate.can_exit('SHORT', bar2, indicators2)
        assert can_exit2 is True
        assert reason2 == "PASSED_TREND_GATE"

    def test_dual_track_exit_strategy_vetoes_exit_when_trend_intact(self):
        """DualTrackExitStrategy 整合測試：即使觸發普通平倉條件，若趨勢未壞，Gate 100% 一票否決"""
        strategy = DualTrackExitStrategy()
        # 構造一個已開多倉，入場價 100，目前最高價 108，現價 105 (回吐 3 ATR，原本會觸發階梯鎖利或峰頂平倉)
        position = {
            'side': 'LONG',
            'entry_price': 100.0,
            'highest_price': 102.0,
            'peak_price': 102.0,
            'entry_atr': 2.0,
            'qty': 10.0,
            'margin': 100.0,
        }
        # 構造當前 K 棒：close 101.5 仍在 MA15 101 之上，MA5 101.8 > MA15 101，ma15_slope > 0
        df = pd.DataFrame([
            {
                'timestamp': 1000000.0, 'open': 100.5, 'high': 102.0, 'low': 100.0, 'close': 101.0,
                'ma5': 101.0, 'ma15': 100.5, 'atr': 2.0, 'kc_middle': 100.0, 'kc_upper': 110.0, 'kc_lower': 90.0,
                'is_closed': True,
            },
            {
                'timestamp': 1060000.0, 'open': 101.0, 'high': 102.0, 'low': 101.0, 'close': 101.5,
                'ma5': 101.8, 'ma15': 101.0, 'atr': 2.0, 'kc_middle': 100.5, 'kc_upper': 110.5, 'kc_lower': 90.5,
                'is_closed': True,
            },
        ])
        df.attrs['timeframe_ms'] = 60000

        # evaluate_exit 應被 TrendHoldingImmunityGate 一票否決，返回 None
        exit_result = strategy.evaluate_exit(position, frame=df, current_price=101.5, is_closed=True)
        assert exit_result is None

    def test_dual_track_exit_strategy_allows_exit_when_pierced_ma15(self):
        """DualTrackExitStrategy 整合測試：當收盤價實體摜破 MA15 時，正常放行平倉"""
        strategy = DualTrackExitStrategy()
        position = {
            'side': 'LONG',
            'entry_price': 100.0,
            'highest_price': 108.0,
            'peak_price': 108.0,
            'entry_atr': 2.0,
            'qty': 10.0,
            'margin': 100.0,
        }
        # 價格跌至 99，摜破 MA15 (101) 與 KC中軌 (100)
        df = pd.DataFrame([
            {
                'timestamp': 1000000.0, 'open': 104.0, 'high': 106.0, 'low': 102.0, 'close': 104.0,
                'ma5': 104.0, 'ma15': 102.0, 'atr': 2.0, 'kc_middle': 100.0, 'kc_upper': 110.0, 'kc_lower': 90.0,
                'is_closed': True,
            },
            {
                'timestamp': 1060000.0, 'open': 104.0, 'high': 104.5, 'low': 98.5, 'close': 99.0,
                'ma5': 100.0, 'ma15': 101.0, 'atr': 2.0, 'kc_middle': 100.0, 'kc_upper': 110.0, 'kc_lower': 90.0,
                'is_closed': True,
            },
        ])
        df.attrs['timeframe_ms'] = 60000

        exit_result = strategy.evaluate_exit(position, frame=df, current_price=99.0, is_closed=True)
        # 正常放行出場訊號
        assert exit_result is not None

    def test_holding_protection_exit_gate_vetoes_when_trend_intact(self):
        """HoldingProtectionExitGate 整合測試：當多單嘗試平倉但趨勢順向未破 MA15，Gate 100% 否決"""
        from core.gates.holding_protection_gate import HoldingProtectionExitGate
        position = {
            'symbol': 'BTC/USDT',
            'side': 'LONG',
            'entry_price': 100.0,
            'highest_price': 108.0,
            'peak_price': 108.0,
            'entry_atr': 2.0,
            'entry_timestamp': 1000000.0,
        }
        df = pd.DataFrame([
            {
                'timestamp': 1000000.0, 'open': 103.0, 'high': 106.0, 'low': 102.0, 'close': 104.0,
                'ma5': 104.0, 'ma15': 101.5, 'atr': 2.0, 'kc_middle': 100.0, 'kc_upper': 110.0, 'kc_lower': 90.0,
                'is_closed': True,
            },
            {
                'timestamp': 1060000.0, 'open': 104.0, 'high': 108.0, 'low': 103.5, 'close': 105.0,
                'ma5': 105.0, 'ma15': 102.0, 'atr': 2.0, 'kc_middle': 100.5, 'kc_upper': 110.5, 'kc_lower': 90.5,
                'is_closed': True,
            },
        ])
        df.attrs['timeframe_ms'] = 60000
        # 即使傳入峰頂平倉候選 EXIT_LONG_ON_FRACTAL_PEAK，但因 close 105 > ma15 102 且順向，Gate 一票否決
        passed, reason, _ = HoldingProtectionExitGate.validate_exit(
            position, df, quote=105.0, candidate_reason='EXIT_LONG_ON_FRACTAL_PEAK'
        )
        assert passed is False
        assert "TREND_INTACT_ABOVE_MA15" in reason
