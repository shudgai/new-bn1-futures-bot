"""Unit tests for StrictCrossAndKcTrendGate (真均線交叉、通道共振與斜率防彎折閘門).

涵蓋四大核心要求與邊界檢測：
1. 金叉後 MA5 走平 / 下折被 100% 攔截 (REJECT_ENTRY: MA5_HOOKING_DOWN_AFTER_CROSS)。
2. 未死叉時 MA5 在上方（ma5 >= ma15）回落被 100% 絕對否決攔截 (REJECT_ENTRY: NO_DEATH_CROSS_MA5_ABOVE_MA15)。
3. 死叉後 MA5 走平 / 上拐被 100% 攔截 (REJECT_ENTRY: MA5_HOOKING_UP_AFTER_CROSS)。
4. 符合真穿越、通道共振且順向強發散時正常放行 (PASSED)。
5. KC 通道方向相反時一票否決 (REJECT_ENTRY: KC_TREND_NOT_UP / KC_TREND_NOT_DOWN)。
"""
import pytest
import pandas as pd
from core.gates.strict_cross_kc_gate import (
    StrictCrossAndKcTrendGate,
    has_recent_golden_cross,
    has_recent_death_cross,
)
from core.services.gates.entry_gate import EntryGate


def make_candle_df(bars):
    df = pd.DataFrame(bars)
    df.attrs['timeframe_ms'] = 60000
    return df


class TestStrictCrossAndKcTrendGate:
    """測試 StrictCrossAndKcTrendGate 各項硬檢核與放行規則"""

    # ─────────────────────────────────────────────────────────────
    # 一、 多單進場硬門檻（Long Gate Criteria）測試
    # ─────────────────────────────────────────────────────────────

    def test_long_kc_trend_down_rejected(self):
        """1. 通道大方向共振：kc_trend == 'DOWN' 一票否決開多"""
        df = make_candle_df([
            {'open': 100, 'close': 101, 'high': 102, 'low': 99, 'ma5': 100.0, 'ma15': 101.0, 'kc_middle': 105.0, 'kc_trend': 'DOWN', 'is_closed': True},
            {'open': 101, 'close': 103, 'high': 104, 'low': 100, 'ma5': 102.0, 'ma15': 101.5, 'kc_middle': 104.5, 'kc_trend': 'DOWN', 'is_closed': True},
        ])
        passed, reason = StrictCrossAndKcTrendGate.evaluate(df, 'LONG')
        assert passed is False
        assert reason == "REJECT_ENTRY: KC_TREND_NOT_UP"

    def test_long_no_golden_cross_ma5_below_ma15_rejected(self):
        """2. 當前 ma5 <= ma15 一票否決開多"""
        df = make_candle_df([
            {'open': 100, 'close': 101, 'high': 102, 'low': 99, 'ma5': 98.0, 'ma15': 101.0, 'kc_middle': 100.0, 'kc_trend': 'UP', 'is_closed': True},
            {'open': 101, 'close': 102, 'high': 103, 'low': 100, 'ma5': 99.0, 'ma15': 101.0, 'kc_middle': 101.0, 'kc_trend': 'UP', 'is_closed': True},
        ])
        passed, reason = StrictCrossAndKcTrendGate.evaluate(df, 'LONG')
        assert passed is False
        assert reason == "REJECT_ENTRY: NO_GOLDEN_CROSS_MA5_BELOW_MA15"

    def test_long_no_recent_golden_cross_rejected(self):
        """2. 雖 ma5 > ma15 但過去 3 根內未發生實質向上穿越一票否決"""
        df = make_candle_df([
            {'open': 100, 'close': 101, 'high': 102, 'low': 99, 'ma5': 105.0, 'ma15': 100.0, 'kc_middle': 100.0, 'kc_trend': 'UP', 'is_closed': True},
            {'open': 101, 'close': 102, 'high': 103, 'low': 100, 'ma5': 106.0, 'ma15': 100.5, 'kc_middle': 101.0, 'kc_trend': 'UP', 'is_closed': True},
            {'open': 102, 'close': 103, 'high': 104, 'low': 101, 'ma5': 107.0, 'ma15': 101.0, 'kc_middle': 102.0, 'kc_trend': 'UP', 'is_closed': True},
            {'open': 103, 'close': 104, 'high': 105, 'low': 102, 'ma5': 108.0, 'ma15': 101.5, 'kc_middle': 103.0, 'kc_trend': 'UP', 'is_closed': True},
        ])
        # 過去 3 根 ma5 一直 > ma15，無穿越事件
        passed, reason = StrictCrossAndKcTrendGate.evaluate(df, 'LONG')
        assert passed is False
        assert reason == "REJECT_ENTRY: NO_VALID_GOLDEN_CROSS_FOUND"

    def test_long_golden_cross_ma5_flat_rejected(self):
        """3. 金叉後 MA5 走平 (ma5_slope <= 0) 100% 絕對禁止開多"""
        df = make_candle_df([
            {'open': 100, 'close': 101, 'high': 102, 'low': 99, 'ma5': 100.0, 'ma15': 101.0, 'kc_middle': 100.0, 'kc_trend': 'UP', 'is_closed': True},
            {'open': 101, 'close': 103, 'high': 104, 'low': 100, 'ma5': 102.0, 'ma15': 101.5, 'kc_middle': 101.0, 'kc_trend': 'UP', 'is_closed': True},
            # 發生金叉後，MA5 走平 (102.0 -> 102.0)
            {'open': 103, 'close': 102.5, 'high': 103.5, 'low': 102, 'ma5': 102.0, 'ma15': 101.6, 'kc_middle': 101.5, 'kc_trend': 'UP', 'is_closed': True},
        ])
        passed, reason = StrictCrossAndKcTrendGate.evaluate(df, 'LONG')
        assert passed is False
        assert reason == "REJECT_ENTRY: MA5_HOOKING_DOWN_AFTER_CROSS"

    def test_long_golden_cross_ma5_hooking_down_rejected(self):
        """3. 金叉後 MA5 向下彎折 (ma5_curr < ma5_prev) 100% 絕對禁止開多"""
        df = make_candle_df([
            {'open': 100, 'close': 101, 'high': 102, 'low': 99, 'ma5': 100.0, 'ma15': 101.0, 'kc_middle': 100.0, 'kc_trend': 'UP', 'is_closed': True},
            {'open': 101, 'close': 103, 'high': 104, 'low': 100, 'ma5': 103.0, 'ma15': 101.5, 'kc_middle': 101.0, 'kc_trend': 'UP', 'is_closed': True},
            # 金叉後 MA5 向下勾頭 (103.0 -> 102.5，仍在 MA15 101.7 之上)
            {'open': 103, 'close': 101.5, 'high': 103.2, 'low': 101.0, 'ma5': 102.5, 'ma15': 101.7, 'kc_middle': 101.5, 'kc_trend': 'UP', 'is_closed': True},
        ])
        passed, reason = StrictCrossAndKcTrendGate.evaluate(df, 'LONG')
        assert passed is False
        assert reason == "REJECT_ENTRY: MA5_HOOKING_DOWN_AFTER_CROSS"

    def test_long_valid_golden_cross_strong_expansion_passed(self):
        """放行條件：KC向上、近3根實質金叉、當前MA5>MA15且MA5向上發散"""
        df = make_candle_df([
            {'open': 100, 'close': 101, 'high': 102, 'low': 99, 'ma5': 100.0, 'ma15': 101.0, 'kc_middle': 100.0, 'kc_trend': 'UP', 'is_closed': True},
            # 實質穿越金叉且 MA5 向上堅定發散 (100.0 -> 102.5 > 101.5)
            {'open': 101, 'close': 104, 'high': 105, 'low': 101, 'ma5': 102.5, 'ma15': 101.5, 'kc_middle': 101.0, 'kc_trend': 'UP', 'is_closed': True},
        ])
        passed, reason = EntryGate.evaluate(df, 'LONG')
        assert passed is True
        assert reason == "PASSED"

    # ─────────────────────────────────────────────────────────────
    # 二、 空單進場硬門檻（Short Gate Criteria）測試
    # ─────────────────────────────────────────────────────────────

    def test_short_kc_trend_up_rejected(self):
        """1. 通道大方向共振：kc_trend == 'UP' 一票否決開空"""
        df = make_candle_df([
            {'open': 105, 'close': 104, 'high': 106, 'low': 103, 'ma5': 104.0, 'ma15': 103.0, 'kc_middle': 100.0, 'kc_trend': 'UP', 'is_closed': True},
            {'open': 104, 'close': 101, 'high': 104, 'low': 100, 'ma5': 101.0, 'ma15': 102.0, 'kc_middle': 101.0, 'kc_trend': 'UP', 'is_closed': True},
        ])
        passed, reason = StrictCrossAndKcTrendGate.evaluate(df, 'SHORT')
        assert passed is False
        assert reason == "REJECT_ENTRY: KC_TREND_NOT_DOWN"

    def test_short_no_death_cross_ma5_above_ma15_rejected(self):
        """2. 【絕對否決鐵律】：當前 MA5 仍在 MA15 上方（ma5 >= ma15），無論 MA5 當根如何向下勾頭，100% 絕對禁止放行開空"""
        df = make_candle_df([
            {'open': 108, 'close': 107, 'high': 109, 'low': 106, 'ma5': 108.0, 'ma15': 102.0, 'kc_middle': 105.0, 'kc_trend': 'DOWN', 'is_closed': True},
            # MA5 急速向下回落勾頭 (108.0 -> 104.0)，但仍在 MA15 (103.0) 上方，判定為多頭回踩雜訊
            {'open': 107, 'close': 103, 'high': 107, 'low': 102, 'ma5': 104.0, 'ma15': 103.0, 'kc_middle': 104.0, 'kc_trend': 'DOWN', 'is_closed': True},
        ])
        passed, reason = StrictCrossAndKcTrendGate.evaluate(df, 'SHORT')
        assert passed is False
        assert reason == "REJECT_ENTRY: NO_DEATH_CROSS_MA5_ABOVE_MA15"

    def test_short_no_recent_death_cross_rejected(self):
        """2. 雖 ma5 < ma15 但過去 3 根內未發生實質向下貫穿一票否決"""
        df = make_candle_df([
            {'open': 100, 'close': 99, 'high': 101, 'low': 98, 'ma5': 92.0, 'ma15': 98.0, 'kc_middle': 99.0, 'kc_trend': 'DOWN', 'is_closed': True},
            {'open': 99, 'close': 98, 'high': 100, 'low': 97, 'ma5': 91.0, 'ma15': 97.5, 'kc_middle': 98.0, 'kc_trend': 'DOWN', 'is_closed': True},
            {'open': 98, 'close': 97, 'high': 99, 'low': 96, 'ma5': 90.0, 'ma15': 97.0, 'kc_middle': 97.0, 'kc_trend': 'DOWN', 'is_closed': True},
            {'open': 97, 'close': 96, 'high': 98, 'low': 95, 'ma5': 89.0, 'ma15': 96.5, 'kc_middle': 96.0, 'kc_trend': 'DOWN', 'is_closed': True},
        ])
        passed, reason = StrictCrossAndKcTrendGate.evaluate(df, 'SHORT')
        assert passed is False
        assert reason == "REJECT_ENTRY: NO_VALID_DEATH_CROSS_FOUND"

    def test_short_death_cross_ma5_flat_rejected(self):
        """3. 死叉後 MA5 走平 (ma5_slope >= 0) 100% 絕對禁止開空"""
        df = make_candle_df([
            {'open': 102, 'close': 100, 'high': 103, 'low': 99, 'ma5': 102.0, 'ma15': 101.0, 'kc_middle': 102.0, 'kc_trend': 'DOWN', 'is_closed': True},
            {'open': 100, 'close': 98, 'high': 100, 'low': 97, 'ma5': 99.0, 'ma15': 100.5, 'kc_middle': 101.0, 'kc_trend': 'DOWN', 'is_closed': True},
            # 死叉後 MA5 走平 (99.0 -> 99.0)
            {'open': 98, 'close': 98.5, 'high': 99, 'low': 97.5, 'ma5': 99.0, 'ma15': 100.2, 'kc_middle': 100.5, 'kc_trend': 'DOWN', 'is_closed': True},
        ])
        passed, reason = StrictCrossAndKcTrendGate.evaluate(df, 'SHORT')
        assert passed is False
        assert reason == "REJECT_ENTRY: MA5_HOOKING_UP_AFTER_CROSS"

    def test_short_death_cross_ma5_hooking_up_rejected(self):
        """3. 死叉後 MA5 向上折起勾頭 (ma5_curr > ma5_prev) 100% 絕對禁止開空"""
        df = make_candle_df([
            {'open': 102, 'close': 100, 'high': 103, 'low': 99, 'ma5': 102.0, 'ma15': 101.0, 'kc_middle': 102.0, 'kc_trend': 'DOWN', 'is_closed': True},
            {'open': 100, 'close': 98, 'high': 100, 'low': 97, 'ma5': 99.0, 'ma15': 100.5, 'kc_middle': 101.0, 'kc_trend': 'DOWN', 'is_closed': True},
            # 死叉後 MA5 向上折起 (99.0 -> 99.8)
            {'open': 98, 'close': 99.5, 'high': 100, 'low': 98, 'ma5': 99.8, 'ma15': 100.3, 'kc_middle': 100.5, 'kc_trend': 'DOWN', 'is_closed': True},
        ])
        passed, reason = StrictCrossAndKcTrendGate.evaluate(df, 'SHORT')
        assert passed is False
        assert reason == "REJECT_ENTRY: MA5_HOOKING_UP_AFTER_CROSS"

    def test_short_valid_death_cross_strong_descent_passed(self):
        """放行條件：KC向下、近3根實質死叉、當前MA5<MA15且MA15走跌、MA5向下俯衝發散"""
        df = make_candle_df([
            {'open': 102, 'close': 100, 'high': 103, 'low': 99, 'ma5': 102.0, 'ma15': 101.0, 'kc_middle': 102.0, 'kc_trend': 'DOWN', 'is_closed': True},
            # 實質貫穿死叉且 MA5 向下俯衝 (102.0 -> 99.0 < 100.5)，MA15 亦向下 (101.0 -> 100.5)
            {'open': 100, 'close': 96, 'high': 100, 'low': 95, 'ma5': 99.0, 'ma15': 100.5, 'kc_middle': 101.0, 'kc_trend': 'DOWN', 'is_closed': True},
        ])
        passed, reason = EntryGate.evaluate(df, 'SHORT')
        assert passed is True
        assert reason == "PASSED"

    def test_narrow_kc_bandwidth_rejected(self):
        """4. 通道帶寬不足 1.2% (死水震盪) 100% 絕對禁止開倉"""
        df = make_candle_df([
            {'open': 100, 'close': 100.2, 'high': 100.3, 'low': 99.8, 'ma5': 100.0, 'ma15': 100.1,
             'kc_upper': 100.5, 'kc_middle': 100.0, 'kc_lower': 99.5, 'kc_trend': 'UP', 'is_closed': True},
            # 帶寬 (100.4 - 99.8) / 100.1 = 0.6% < 1.2%
            {'open': 100.2, 'close': 100.3, 'high': 100.4, 'low': 99.8, 'ma5': 100.2, 'ma15': 100.1,
             'kc_upper': 100.4, 'kc_middle': 100.1, 'kc_lower': 99.8, 'kc_trend': 'UP', 'is_closed': True},
        ])
        passed, reason = StrictCrossAndKcTrendGate.evaluate(df, 'LONG')
        assert passed is False
        assert reason == "REJECT_ENTRY: CHOP_BANDWIDTH_TOO_NARROW"

    def test_long_too_close_to_upper_rail_rejected(self):
        """5. 獲利空間防護：價格貼近 KC 上軌頂部 (剩餘空間 < 20%) 嚴禁追多"""
        # 通道：中軌 100，上軌 110，下軌 90（帶寬 20%）。頂部 20% 區域為 >= 108
        df = make_candle_df([
            {'open': 100, 'close': 102, 'high': 103, 'low': 99, 'ma5': 101.0, 'ma15': 102.0,
             'kc_upper': 110.0, 'kc_middle': 100.0, 'kc_lower': 90.0, 'kc_trend': 'UP', 'is_closed': True},
            # 價格 108.5，已經衝到頂部 20% 貼軌超買區
            {'open': 102, 'close': 108.5, 'high': 109.0, 'low': 102.0, 'ma5': 105.0, 'ma15': 103.0,
             'kc_upper': 110.0, 'kc_middle': 100.5, 'kc_lower': 91.0, 'kc_trend': 'UP', 'is_closed': True},
        ])
        passed, reason = StrictCrossAndKcTrendGate.evaluate(df, 'LONG', quote=108.5)
        assert passed is False
        assert reason == "REJECT_ENTRY: LONG_TOO_CLOSE_TO_UPPER_RAIL"

    def test_short_too_close_to_lower_rail_rejected(self):
        """6. 獲利空間防護：價格貼近 KC 下軌底部 (剩餘空間 < 20%) 嚴禁在地板追空"""
        # 通道：中軌 100，上軌 110，下軌 90。底部 20% 區域為 <= 92
        df = make_candle_df([
            {'open': 100, 'close': 98, 'high': 101, 'low': 97, 'ma5': 99.0, 'ma15': 98.0,
             'kc_upper': 110.0, 'kc_middle': 100.0, 'kc_lower': 90.0, 'kc_trend': 'DOWN', 'is_closed': True},
            # 價格 91.5，已經跌到底部 20% 貼軌地板區
            {'open': 98, 'close': 91.5, 'high': 98.0, 'low': 91.0, 'ma5': 95.0, 'ma15': 97.0, 'ma15_diff': -1.0,
             'kc_upper': 109.0, 'kc_middle': 99.5, 'kc_lower': 90.0, 'kc_trend': 'DOWN', 'is_closed': True},
        ])
        passed, reason = StrictCrossAndKcTrendGate.evaluate(df, 'SHORT', quote=91.5)
        assert passed is False
        assert reason == "REJECT_ENTRY: SHORT_TOO_CLOSE_TO_LOWER_RAIL"
