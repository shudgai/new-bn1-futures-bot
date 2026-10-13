"""Unit tests for Gate 4: Continuation Re-entry & MA5 Momentum Filter.

測試重點：
1. MA5 逆勢過濾禁令：
   - 禁開多單：若當前 MA5 斜率向下（或走弱下拐/走平，live_ma5 <= prev_ma5），一律嚴禁開多單。
   - 禁開空單：若當前 MA5 斜率向上（或走強上拐/走平，live_ma5 >= prev_ma5），一律嚴禁開空單。
   - 價格位置檢驗：
     * 多單：價格必須站上 MA5（quote > live_ma5），低於或等於均線嚴禁開多。
     * 空單：價格必須壓制在 MA5 之下（quote < live_ma5），高於或等於均線嚴禁開空。
2. 平倉後的「延續開倉（Re-entry）」與「破軌沒開倉後面延續開倉」情境：
   - 在部位平倉出場後，如果大趨勢未變且後續再次出現順勢擴張/突破，允許觸發延續開倉。
   - 破軌沒開倉，後續大趨勢順向且價格在軌外/MA5 外，允許延續開倉。
   - 鐵律：必須等到短期均線方向與主趨勢同步（多頭 MA5 向上、空頭 MA5 向下）時，才允許執行延續開倉。
3. Pipeline 整合測試：
   - 確認 CONTINUATION_MA5_FILTER 正式加入 self.gates 成為第四道開倉閘門。
"""
import pytest
import pandas as pd
import numpy as np

from core.gates.continuation_ma5_gate import ContinuationMa5Gate
from core.gates.pipeline import EntryGatePipeline, pipeline


def make_frame(rows):
    df = pd.DataFrame(rows)
    df.attrs['timeframe_ms'] = 60000
    return df


class TestGate4ContinuationMa5:
    """測試 Gate 4 核心邏輯"""

    def test_long_blocked_when_ma5_slopes_down(self):
        # prev_ma5 = 100.0, live_ma5 = 99.0 -> downward slope -> BLOCKED
        df = make_frame([
            {'open': 99.0, 'close': 100.0, 'high': 101.0, 'low': 98.0,
             'ma5': 100.0, 'ma15': 95.0, 'is_closed': True},
            {'open': 100.0, 'close': 99.0, 'high': 101.0, 'low': 98.0,
             'ma5': 99.0, 'ma15': 95.0, 'is_closed': True},
        ])
        passed, reason = ContinuationMa5Gate.evaluate(df, quote=102.0, side='LONG')
        assert passed is False
        assert reason == 'BLOCKED_BY_MA5_DOWNWARD_SLOPE'

    def test_long_blocked_when_ma5_flat(self):
        # prev_ma5 = 100.0, live_ma5 = 100.0 -> flat slope -> BLOCKED
        df = make_frame([
            {'open': 99.0, 'close': 100.0, 'high': 101.0, 'low': 98.0,
             'ma5': 100.0, 'ma15': 95.0, 'is_closed': True},
            {'open': 100.0, 'close': 100.0, 'high': 101.0, 'low': 98.0,
             'ma5': 100.0, 'ma15': 95.0, 'is_closed': True},
        ])
        passed, reason = ContinuationMa5Gate.evaluate(df, quote=102.0, side='LONG')
        assert passed is False
        assert reason == 'BLOCKED_BY_MA5_DOWNWARD_SLOPE'

    def test_long_blocked_when_quote_below_or_equal_ma5(self):
        # prev_ma5 = 98.0, live_ma5 = 100.0, but quote = 99.5 <= ma5 -> BLOCKED
        df = make_frame([
            {'open': 97.0, 'close': 98.0, 'high': 99.0, 'low': 96.0,
             'ma5': 98.0, 'ma15': 95.0, 'is_closed': True},
            {'open': 98.0, 'close': 100.0, 'high': 101.0, 'low': 97.0,
             'ma5': 100.0, 'ma15': 95.0, 'is_closed': True},
        ])
        passed, reason = ContinuationMa5Gate.evaluate(df, quote=99.5, side='LONG')
        assert passed is False
        assert reason == 'BLOCKED_BY_PRICE_BELOW_MA5'

    def test_long_passed_when_ma5_up_and_quote_above(self):
        # prev_ma5 = 98.0, live_ma5 = 100.0, quote = 103.0 > ma5 -> PASSED
        df = make_frame([
            {'open': 97.0, 'close': 98.0, 'high': 99.0, 'low': 96.0,
             'ma5': 98.0, 'ma15': 95.0, 'is_closed': True},
            {'open': 98.0, 'close': 100.0, 'high': 104.0, 'low': 97.0,
             'ma5': 100.0, 'ma15': 95.0, 'is_closed': True},
        ])
        passed, reason = ContinuationMa5Gate.evaluate(df, quote=103.0, side='LONG')
        assert passed is True
        assert reason is None

    def test_short_blocked_when_ma5_slopes_up(self):
        # prev_ma5 = 100.0, live_ma5 = 101.0 -> upward slope -> BLOCKED
        df = make_frame([
            {'open': 101.0, 'close': 100.0, 'high': 102.0, 'low': 99.0,
             'ma5': 100.0, 'ma15': 105.0, 'is_closed': True},
            {'open': 100.0, 'close': 101.0, 'high': 102.0, 'low': 99.0,
             'ma5': 101.0, 'ma15': 105.0, 'is_closed': True},
        ])
        passed, reason = ContinuationMa5Gate.evaluate(df, quote=98.0, side='SHORT')
        assert passed is False
        assert reason == 'BLOCKED_BY_MA5_UPWARD_SLOPE'

    def test_short_blocked_when_ma5_flat(self):
        # prev_ma5 = 100.0, live_ma5 = 100.0 -> flat slope -> BLOCKED
        df = make_frame([
            {'open': 101.0, 'close': 100.0, 'high': 102.0, 'low': 99.0,
             'ma5': 100.0, 'ma15': 105.0, 'is_closed': True},
            {'open': 100.0, 'close': 100.0, 'high': 102.0, 'low': 99.0,
             'ma5': 100.0, 'ma15': 105.0, 'is_closed': True},
        ])
        passed, reason = ContinuationMa5Gate.evaluate(df, quote=98.0, side='SHORT')
        assert passed is False
        assert reason == 'BLOCKED_BY_MA5_UPWARD_SLOPE'

    def test_short_blocked_when_quote_above_or_equal_ma5(self):
        # prev_ma5 = 102.0, live_ma5 = 100.0, quote = 100.5 >= ma5 -> BLOCKED
        df = make_frame([
            {'open': 103.0, 'close': 102.0, 'high': 104.0, 'low': 101.0,
             'ma5': 102.0, 'ma15': 105.0, 'is_closed': True},
            {'open': 102.0, 'close': 100.0, 'high': 103.0, 'low': 99.0,
             'ma5': 100.0, 'ma15': 105.0, 'is_closed': True},
        ])
        passed, reason = ContinuationMa5Gate.evaluate(df, quote=100.5, side='SHORT')
        assert passed is False
        assert reason == 'BLOCKED_BY_PRICE_ABOVE_MA5'

    def test_short_passed_when_ma5_down_and_quote_below(self):
        # prev_ma5 = 102.0, live_ma5 = 100.0, quote = 97.0 < ma5 -> PASSED
        df = make_frame([
            {'open': 103.0, 'close': 102.0, 'high': 104.0, 'low': 101.0,
             'ma5': 102.0, 'ma15': 105.0, 'is_closed': True},
            {'open': 102.0, 'close': 100.0, 'high': 103.0, 'low': 96.0,
             'ma5': 100.0, 'ma15': 105.0, 'is_closed': True},
        ])
        passed, reason = ContinuationMa5Gate.evaluate(df, quote=97.0, side='SHORT')
        assert passed is True
        assert reason is None


class TestContinuationReentryScenario:
    """測試延續開倉情境（平倉後或破軌沒開倉）"""

    def test_reentry_long_trend_aligned_passes(self):
        df = make_frame([
            {'open': 98.0, 'close': 100.0, 'high': 101.0, 'low': 97.0,
             'ma5': 99.0, 'ma15': 95.0, 'kc_middle': 96.0, 'kc_upper': 102.0,
             'channel_state': 'KC_TREND_UP', 'is_closed': True},
            {'open': 100.0, 'close': 103.0, 'high': 105.0, 'low': 99.5,
             'ma5': 101.5, 'ma15': 96.5, 'kc_middle': 98.0, 'kc_upper': 103.0,
             'channel_state': 'KC_TREND_UP', 'is_closed': True},
        ])
        passed, reason = ContinuationMa5Gate.evaluate_continuation_reentry(
            df, quote=104.0, side='LONG', is_reentry=True
        )
        assert passed is True
        assert reason is None

    def test_reentry_long_pullback_inside_fails(self):
        # 價格已退回 KC 通道內且跌破 MA5 -> 退回防護觸發
        df = make_frame([
            {'open': 98.0, 'close': 100.0, 'high': 101.0, 'low': 97.0,
             'ma5': 99.0, 'ma15': 95.0, 'kc_middle': 96.0, 'kc_upper': 105.0,
             'channel_state': 'KC_TREND_UP', 'is_closed': True},
            {'open': 100.0, 'close': 103.0, 'high': 105.0, 'low': 99.5,
             'ma5': 101.5, 'ma15': 96.5, 'kc_middle': 98.0, 'kc_upper': 105.0,
             'channel_state': 'KC_TREND_UP', 'is_closed': True},
        ])
        passed, reason = ContinuationMa5Gate.evaluate_continuation_reentry(
            df, quote=101.0, side='LONG', is_reentry=True
        )
        assert passed is False


class TestPipelineGate4Integration:
    """測試 EntryPipeline 的 Gate 4 整合"""

    def test_pipeline_has_gate4_registered(self):
        pipe = EntryGatePipeline()
        gate_names = [name for name, _ in pipe.gates]
        assert 'CONTINUATION_MA5_FILTER' in gate_names
        assert gate_names[3] == 'CONTINUATION_MA5_FILTER'

    def test_pipeline_detect_trend_continuation_vetoes_ma5_slope_down_long(self):
        # MA5 is sloping down -> detect_trend_continuation returns None for LONG
        df = make_frame([
            {'open': 99.0, 'close': 100.0, 'high': 101.0, 'low': 98.0,
             'ma5': 101.0, 'ma15': 95.0, 'kc_middle': 98.0, 'kc_upper': 102.0,
             'is_closed': True, 'timestamp': 1000},
            {'open': 100.0, 'close': 99.5, 'high': 102.0, 'low': 99.0,
             'ma5': 100.0, 'ma15': 96.0, 'kc_middle': 98.5, 'kc_upper': 102.5,
             'is_closed': True, 'timestamp': 61000},
        ])
        decision = pipeline.detect_trend_continuation(df, quote=100.5, side='LONG')
        assert decision is None

    def test_pipeline_detect_trend_continuation_vetoes_ma5_slope_up_short(self):
        # MA5 is sloping up -> detect_trend_continuation returns None for SHORT
        df = make_frame([
            {'open': 101.0, 'close': 100.0, 'high': 102.0, 'low': 99.0,
             'ma5': 99.0, 'ma15': 105.0, 'kc_middle': 102.0, 'kc_lower': 98.0,
             'is_closed': True, 'timestamp': 1000},
            {'open': 100.0, 'close': 100.5, 'high': 101.0, 'low': 98.5,
             'ma5': 100.0, 'ma15': 104.0, 'kc_middle': 101.5, 'kc_lower': 97.5,
             'is_closed': True, 'timestamp': 61000},
        ])
        decision = pipeline.detect_trend_continuation(df, quote=99.5, side='SHORT')
        assert decision is None
