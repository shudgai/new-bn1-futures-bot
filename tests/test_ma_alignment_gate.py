"""Unit tests for strict MA alignment (MA5 vs MA15 relative position) entry gate.

Requirements:
1. 多單（LONG）方向鐵律：
   - 硬性門檻：必須滿足 MA5 > MA15（MA5 位於 MA15 上方，呈黃金交叉或多頭排列）。
   - 收線確認：當根 1m K 線收盤價必須站穩在均線之上（close > ma5）。
   - 一票否決：若 MA5 <= MA15，嚴禁開多單（REJECT: MA_ALIGNMENT_MISMATCH）。
   - 一票否決：若 close <= ma5，嚴禁開多單（REJECT: MA_ALIGNMENT_MISMATCH）。
2. 空單（SHORT）方向鐵律：
   - 硬性門檻：必須滿足 MA5 < MA15（MA5 位於 MA15 下方，呈死亡交叉或空頭排列）。
   - 收線確認：當根 1m K 線收盤價必須壓制在均線之下（close < ma5）。
   - 一票否決：若 MA5 >= MA15，嚴禁開空單（REJECT: MA_ALIGNMENT_MISMATCH）。
   - 一票否決：若 close >= ma5，嚴禁開空單（REJECT: MA_ALIGNMENT_MISMATCH）。
3. 三引擎統一遵循：
   - 引擎一（交叉敏捷開倉）：偵測到交叉瞬間，且收線當下多單嚴格為 MA5 > MA15、空單嚴格為 MA5 < MA15 時方可觸發。
   - 引擎二 & 三（破軌開倉 / 即時破軌）：即使盤中突破 KC 外軌，若均線關係反向（例如破上軌但 MA5 仍在 MA15 下方），
     同樣一票否決（REJECT: MA_ALIGNMENT_MISMATCH），不允許逆均線開倉！
"""
import pytest
import pandas as pd
import numpy as np

from core.services.strategies.unified_entry_strategy import (
    validate_ma_alignment,
    check_ma_cross_entry,
    UnifiedEntryStrategy,
)
from core.services.entry_contract import (
    evaluate_entry_contract,
    check_entry_gates,
    detect_raw_triggers,
    evaluate_realtime_short_rail_breach,
    evaluate_live_body_breakout,
)


def make_candle_frame(rows):
    df = pd.DataFrame(rows)
    df.attrs['timeframe_ms'] = 60000
    return df


class TestMAAlignmentCore:
    """測試核心 validate_ma_alignment 函式"""

    def test_long_valid(self):
        # MA5 > MA15 and close > MA5 -> PASSED
        df = make_candle_frame([{
            'open': 100.0, 'high': 106.0, 'low': 99.0, 'close': 105.0,
            'ma5': 103.0, 'ma15': 100.0, 'is_closed': True
        }])
        passed, reason = validate_ma_alignment(df, 'LONG')
        assert passed is True
        assert reason == "PASSED"

    def test_long_ma5_below_or_equal_ma15_rejected(self):
        # MA5 <= MA15 -> REJECT: MA_ALIGNMENT_MISMATCH
        df1 = make_candle_frame([{
            'open': 100.0, 'high': 106.0, 'low': 99.0, 'close': 105.0,
            'ma5': 99.0, 'ma15': 100.0, 'is_closed': True
        }])
        passed1, reason1 = validate_ma_alignment(df1, 'LONG')
        assert passed1 is False
        assert reason1 == "REJECT: MA_ALIGNMENT_MISMATCH"

        # Equal: MA5 == MA15
        df2 = make_candle_frame([{
            'open': 100.0, 'high': 106.0, 'low': 99.0, 'close': 105.0,
            'ma5': 100.0, 'ma15': 100.0, 'is_closed': True
        }])
        passed2, reason2 = validate_ma_alignment(df2, 'LONG')
        assert passed2 is False
        assert reason2 == "REJECT: MA_ALIGNMENT_MISMATCH"

    def test_long_close_below_or_equal_ma5_rejected(self):
        # close <= ma5 -> REJECT: MA_ALIGNMENT_MISMATCH
        df = make_candle_frame([{
            'open': 100.0, 'high': 106.0, 'low': 99.0, 'close': 102.0,
            'ma5': 103.0, 'ma15': 100.0, 'is_closed': True
        }])
        passed, reason = validate_ma_alignment(df, 'LONG')
        assert passed is False
        assert reason == "REJECT: MA_ALIGNMENT_MISMATCH"

    def test_short_valid(self):
        # MA5 < MA15 and close < MA5 -> PASSED
        df = make_candle_frame([{
            'open': 100.0, 'high': 101.0, 'low': 93.0, 'close': 94.0,
            'ma5': 96.0, 'ma15': 99.0, 'is_closed': True
        }])
        passed, reason = validate_ma_alignment(df, 'SHORT')
        assert passed is True
        assert reason == "PASSED"

    def test_short_ma5_above_or_equal_ma15_rejected(self):
        # MA5 >= MA15 -> REJECT: MA_ALIGNMENT_MISMATCH
        df1 = make_candle_frame([{
            'open': 100.0, 'high': 101.0, 'low': 93.0, 'close': 94.0,
            'ma5': 100.0, 'ma15': 99.0, 'is_closed': True
        }])
        passed1, reason1 = validate_ma_alignment(df1, 'SHORT')
        assert passed1 is False
        assert reason1 == "REJECT: MA_ALIGNMENT_MISMATCH"

        # Equal: MA5 == MA15
        df2 = make_candle_frame([{
            'open': 100.0, 'high': 101.0, 'low': 93.0, 'close': 94.0,
            'ma5': 99.0, 'ma15': 99.0, 'is_closed': True
        }])
        passed2, reason2 = validate_ma_alignment(df2, 'SHORT')
        assert passed2 is False
        assert reason2 == "REJECT: MA_ALIGNMENT_MISMATCH"

    def test_short_close_above_or_equal_ma5_rejected(self):
        # close >= ma5 -> REJECT: MA_ALIGNMENT_MISMATCH
        df = make_candle_frame([{
            'open': 100.0, 'high': 101.0, 'low': 93.0, 'close': 97.0,
            'ma5': 96.0, 'ma15': 99.0, 'is_closed': True
        }])
        passed, reason = validate_ma_alignment(df, 'SHORT')
        assert passed is False
        assert reason == "REJECT: MA_ALIGNMENT_MISMATCH"

    def test_forming_bar_intrabar_long(self):
        # Live forming bar with quote
        df = make_candle_frame([{
            'open': 100.0, 'high': 105.0, 'low': 99.0, 'close': 100.0,
            'ma5': 100.0, 'ma15': 100.0, 'is_closed': False
        }])
        # Quote = 105: live_ma5 = 100 + (105-100)/5 = 101, live_ma15 = 100 + (105-100)/15 = 100.33
        # live_ma5 > live_ma15 (101 > 100.33), quote > live_ma5 (105 > 101) -> PASSED
        passed, reason = validate_ma_alignment(df, 'LONG', quote=105.0)
        assert passed is True

        # Quote = 98: live_ma5 = 99.6, live_ma15 = 99.866
        # live_ma5 < live_ma15 -> REJECT
        passed_bear, reason_bear = validate_ma_alignment(df, 'LONG', quote=98.0)
        assert passed_bear is False
        assert reason_bear == "REJECT: MA_ALIGNMENT_MISMATCH"


class TestEngineOneFastCross:
    """測試引擎一：交叉敏捷開倉"""

    def test_golden_cross_triggers_when_aligned(self):
        prev = {'open': 99.0, 'close': 99.5, 'high': 100.0, 'low': 98.5,
                'ma5': 99.0, 'ma15': 99.5, 'is_closed': True}
        curr = {'open': 99.5, 'close': 101.5, 'high': 102.0, 'low': 99.0,
                'ma5': 100.5, 'ma15': 99.8, 'is_closed': True}
        df = make_candle_frame([prev, curr])
        signal = check_ma_cross_entry(df)
        assert signal is not None
        assert signal['side'] == 'LONG'
        assert signal['type'] == 'KC_GOLDEN_CROSS_FAST_LONG'

    def test_golden_cross_rejected_if_close_below_ma5(self):
        prev = {'open': 99.0, 'close': 99.5, 'high': 100.0, 'low': 98.5,
                'ma5': 99.0, 'ma15': 99.5, 'is_closed': True}
        # Close <= ma5
        curr = {'open': 99.5, 'close': 100.0, 'high': 102.0, 'low': 99.0,
                'ma5': 100.5, 'ma15': 99.8, 'is_closed': True}
        df = make_candle_frame([prev, curr])
        signal = check_ma_cross_entry(df)
        assert signal is None

    def test_death_cross_triggers_when_aligned(self):
        prev = {'open': 101.0, 'close': 100.5, 'high': 101.5, 'low': 100.0,
                'ma5': 101.0, 'ma15': 100.5, 'is_closed': True}
        curr = {'open': 100.5, 'close': 98.5, 'high': 101.0, 'low': 98.0,
                'ma5': 99.5, 'ma15': 100.2, 'is_closed': True}
        df = make_candle_frame([prev, curr])
        signal = check_ma_cross_entry(df)
        assert signal is not None
        assert signal['side'] == 'SHORT'
        assert signal['type'] == 'KC_DEATH_CROSS_FAST_SHORT'


class TestEngineTwoAndThreeBreakoutGate:
    """測試引擎二 & 三：破軌開倉 / 即時破軌反向均線一票否決"""

    def test_detect_raw_triggers_rejects_breakout_when_ma_misaligned(self):
        # 即使突破 KC 上軌（close > upper），但 MA5 <= MA15
        rows = [
            {'open': 99.0, 'close': 99.5, 'high': 100.0, 'low': 98.5,
             'kc_middle': 100.0, 'kc_upper': 101.0, 'kc_lower': 99.0,
             'ma5': 98.0, 'ma15': 100.0, 'atr': 1.0, 'is_closed': True},
            {'open': 99.5, 'close': 100.0, 'high': 100.5, 'low': 99.0,
             'kc_middle': 100.0, 'kc_upper': 101.0, 'kc_lower': 99.0,
             'ma5': 98.5, 'ma15': 100.0, 'atr': 1.0, 'is_closed': True},
            {'open': 100.0, 'close': 102.5, 'high': 102.6, 'low': 99.8,
             'kc_middle': 100.2, 'kc_upper': 101.2, 'kc_lower': 99.2,
             'ma5': 99.5, 'ma15': 100.0, 'atr': 1.0, 'is_closed': True}, # close > upper, but ma5 < ma15!
        ]
        df = make_candle_frame(rows)
        side, trigger = detect_raw_triggers(df)
        # Should not trigger long breakout because MA5 <= MA15
        assert trigger != "TRIGGER_A_KC_BREAKOUT"

    def test_check_entry_gates_rejects_when_ma_misaligned(self):
        rows = [
            {'open': 100.0, 'close': 100.0, 'high': 101.0, 'low': 99.0,
             'kc_middle': 100.0, 'kc_upper': 101.0, 'kc_lower': 99.0,
             'ma5': 99.0, 'ma15': 100.0, 'atr': 1.0, 'is_closed': True}
            for _ in range(15)
        ]
        # Current bar has close > ma5 but ma5 <= ma15
        rows[-1]['ma5'] = 99.5
        rows[-1]['ma15'] = 100.0
        rows[-1]['close'] = 102.0
        df = make_candle_frame(rows)

        passed, reason = check_entry_gates(None, "BTC/USDT", df, "LONG", "TRIGGER_A_KC_BREAKOUT")
        assert passed is False
        assert reason == "REJECT: MA_ALIGNMENT_MISMATCH"

    def test_evaluate_entry_contract_vetoes_misaligned_breakout(self):
        # 測試 evaluate_entry_contract 若均線不符，直接拒絕並回傳 REJECT: MA_ALIGNMENT_MISMATCH
        rows = [
            {'timestamp': 1000 + i * 60000, 'open': 100.0, 'close': 100.0,
             'high': 101.0, 'low': 99.0, 'kc_middle': 100.0,
             'kc_upper': 101.0, 'kc_lower': 99.0,
             'ma5': 99.0, 'ma15': 100.0, 'atr': 1.0, 'is_closed': True}
            for i in range(25)
        ]
        # LONG with MA5 < MA15
        rows[-1]['ma5'] = 98.0
        rows[-1]['ma15'] = 100.0
        rows[-1]['close'] = 102.0
        df = make_candle_frame(rows)

        diagnostics = {}
        res = evaluate_entry_contract(
            df, price=102.0, code="CLOSED_BODY_BREAKOUT_LONG",
            symbol="BTC/USDT", diagnostics=diagnostics
        )
        assert res is None
        assert diagnostics.get('reason') == "REJECT: MA_ALIGNMENT_MISMATCH"

    def test_unified_entry_strategy_evaluate_entry_vetoes(self):
        rows = [
            {'timestamp': 1000 + i * 60000, 'open': 100.0, 'close': 100.0,
             'high': 101.0, 'low': 99.0, 'kc_middle': 100.0,
             'kc_upper': 101.0, 'kc_lower': 99.0,
             'ma5': 101.0, 'ma15': 100.0, 'atr': 1.0, 'is_closed': True}
            for i in range(25)
        ]
        # SHORT with MA5 > MA15 (Wrong alignment)
        rows[-1]['ma5'] = 101.0
        rows[-1]['ma15'] = 100.0
        rows[-1]['close'] = 98.0
        df = make_candle_frame(rows)

        strategy = UnifiedEntryStrategy()
        ok, reason, details = strategy.evaluate_entry(df, 98.0, 'SHORT', symbol='BTC/USDT')
        assert ok is False
        assert reason == "REJECT: MA_ALIGNMENT_MISMATCH"
