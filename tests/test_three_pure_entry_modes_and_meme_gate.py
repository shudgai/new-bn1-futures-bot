"""Unit tests for Three Pure Entry Modes & MemeSharedProtectionGate.

測試重點：
1. 妖幣共用防禦閘門 (Gate: MemeSharedProtectionGate)：
   - 長影線十字星 (body_ratio < 0.35) 否決開倉 (REJECT_ENTRY: DOMINATED_BY_WICKS)
   - 多單上影線超標 (> 0.6 body) 否決開倉 (REJECT_ENTRY: EXCESSIVE_UPPER_WICK)
   - 空單下影線超標 (> 0.6 body) 否決開倉 (REJECT_ENTRY: EXCESSIVE_LOWER_WICK)
   - MA5 乖離過大 (> 1.2%) 天花板/地板防追高追空否決 (LONG_MA5_DEVIATION_CAP / SHORT_MA5_DEVIATION_CAP)
2. 模式一：一般 KC 收線破軌開倉 (Breakout Close Entry)
   - 收線確認、實體 > 軌道、實體 >= 0.4 ATR、KC 趨勢共振且 MA5 順向放行，空間豁免 (bypass_spatial_check=True)
3. 模式二：MA5 / MA15 交叉開倉 (Strict Cross Entry)
   - 剛金叉/死叉 1~3 根內、KC 方向共振、價格在 MA5 順側放行
   - 金叉後 MA5 走平/下折 100% 否決
   - 空頭未死叉勾頭 100% 否決
4. 模式三：斷頭開倉 (Decisive Momentum Guillotine Entry)
   - 實體 >= 0.85 ATR、貫穿均線/中軌、body_ratio >= 0.70 放行
"""
import pytest
import pandas as pd
import numpy as np

from core.gates.strict_cross_kc_gate import (
    StrictCrossAndKcTrendGate,
    MemeSharedProtectionGate,
)
from core.services.strategies.unified_entry_strategy import evaluate_closed_entry
from core.services.entry_contract import detect_raw_triggers


def make_candle_df(bars, timeframe_ms=60000):
    data = []
    base_ts = 1700000000000
    for i, r in enumerate(bars):
        d = dict(r)
        d.setdefault('timestamp', base_ts + i * timeframe_ms)
        d.setdefault('ma3', d.get('ma5', d.get('close', 100.0)))
        d.setdefault('atr', 2.0)
        d.setdefault('kc_upper', 110.0)
        d.setdefault('kc_lower', 90.0)
        d.setdefault('kc_middle', (d['kc_upper'] + d['kc_lower']) / 2.0)
        d.setdefault('is_closed', True)
        data.append(d)
    df = pd.DataFrame(data)
    df.attrs['timeframe_ms'] = timeframe_ms
    return df



class TestThreePureEntryModesAndMemeGate:
    """測試三大純粹開倉模式與妖幣共用防禦閘門"""

    # ══════════════════════════════════════════════════════════════════════
    # 一、 妖幣共用防禦閘門 (MemeSharedProtectionGate) 測試
    # ══════════════════════════════════════════════════════════════════════

    def test_gate_rejects_dominated_by_wicks(self):
        """實體佔比 < 0.35 之十字星、紡錘線一票否決"""
        # open=100, close=100.5 (body=0.5), high=103, low=98 (range=5.0) -> body_ratio = 0.10 < 0.35
        df = make_candle_df([
            {'open': 99, 'close': 100, 'high': 101, 'low': 98, 'ma5': 100, 'ma15': 99, 'is_closed': True},
            {'open': 100, 'close': 100.5, 'high': 103, 'low': 98, 'ma5': 100.2, 'ma15': 99.2, 'is_closed': True},
        ])
        passed, reason = MemeSharedProtectionGate.evaluate(df, 'LONG')
        assert passed is False
        assert reason == "REJECT_ENTRY: DOMINATED_BY_WICKS"

    def test_gate_does_not_veto_single_upper_wick_for_long(self):
        """單根上影線沒有量價確認時不單獨否決多單。"""
        # open=100, close=102 (body=2.0), high=103.5 (upper_wick=1.5 > 0.6*2.0=1.2), low=99.8
        # range = 3.7, body_ratio = 2/3.7 = 0.54 > 0.35
        df = make_candle_df([
            {'open': 98, 'close': 100, 'high': 101, 'low': 97, 'ma5': 100, 'ma15': 98, 'is_closed': True},
            {'open': 100, 'close': 102, 'high': 103.5, 'low': 99.8, 'ma5': 101.5, 'ma15': 99, 'is_closed': True},
        ])
        passed, reason = MemeSharedProtectionGate.evaluate(df, 'LONG')
        assert passed is True
        assert reason == "PASSED"

    def test_gate_does_not_veto_single_lower_wick_for_short(self):
        """單根下影線沒有量價確認時不單獨否決空單。"""
        # open=102, close=100 (body=2.0), low=98.5 (lower_wick=1.5 > 1.2), high=102.2
        # range = 3.7, body_ratio = 0.54
        df = make_candle_df([
            {'open': 104, 'close': 102, 'high': 105, 'low': 101, 'ma5': 102, 'ma15': 103, 'is_closed': True},
            {'open': 102, 'close': 100, 'high': 102.2, 'low': 98.5, 'ma5': 100.5, 'ma15': 102.5, 'is_closed': True},
        ])
        passed, reason = MemeSharedProtectionGate.evaluate(df, 'SHORT')
        assert passed is True
        assert reason == "PASSED"

    def test_gate_rejects_ma5_deviation_cap_overextended(self):
        """防天花板與地板乖離限制：偏離 MA5 > 1.2% 一票否決"""
        # 多單：price = 102.0, MA5 = 100.0 -> deviation = 2.0% > 1.2%
        df_long = make_candle_df([
            {'open': 99, 'close': 100, 'high': 100.5, 'low': 98.5, 'ma5': 99.5, 'ma15': 98, 'is_closed': True},
            {'open': 100, 'close': 102, 'high': 102.2, 'low': 99.9, 'ma5': 100.0, 'ma15': 98.5, 'is_closed': True},
        ])
        passed, reason = MemeSharedProtectionGate.evaluate(df_long, 'LONG', quote=102.0)
        assert passed is False
        assert reason == "REJECT_ENTRY: LONG_MA5_DEVIATION_CAP"

        # 空單：price = 98.0, MA5 = 100.0 -> deviation = (100 - 98) / 100 = 2.0% > 1.2%
        df_short = make_candle_df([
            {'open': 101, 'close': 100, 'high': 101.5, 'low': 99.5, 'ma5': 100.5, 'ma15': 102, 'is_closed': True},
            {'open': 100, 'close': 98, 'high': 100.1, 'low': 97.8, 'ma5': 100.0, 'ma15': 101.5, 'is_closed': True},
        ])
        passed, reason = MemeSharedProtectionGate.evaluate(df_short, 'SHORT', quote=98.0)
        assert passed is False
        assert reason == "REJECT_ENTRY: SHORT_MA5_DEVIATION_CAP"

    # ══════════════════════════════════════════════════════════════════════
    # 二、 模式一：一般 KC 收線破軌開倉 (Breakout Close Entry)
    # ══════════════════════════════════════════════════════════════════════

    def test_mode1_breakout_close_long_passed_with_spatial_bypass(self):
        """模式一多單：收線破上軌、實體充足、KC向上、MA5>MA15，放行且豁免25%空間門檻"""
        # 價格已破上軌 (close 111 > kc_upper 110)，此時距上軌空間為負，若未豁免會觸發貼軌拒絕
        rows = [
            {'open': 100, 'close': 101, 'high': 102, 'low': 99, 'kc_upper': 108, 'kc_lower': 92, 'kc_middle': 100, 'ma5': 101, 'ma15': 99, 'atr': 2.0, 'kc_trend': 'UP', 'is_closed': True},
            {'open': 101, 'close': 102, 'high': 103, 'low': 100, 'kc_upper': 108.5, 'kc_lower': 92.5, 'kc_middle': 100.5, 'ma5': 102, 'ma15': 99.5, 'atr': 2.0, 'kc_trend': 'UP', 'is_closed': True},
            {'open': 102, 'close': 104, 'high': 105, 'low': 101, 'kc_upper': 109, 'kc_lower': 93, 'kc_middle': 101, 'ma5': 103.5, 'ma15': 100, 'atr': 2.0, 'kc_trend': 'UP', 'is_closed': True},
            {'open': 104, 'close': 106, 'high': 107, 'low': 103.5, 'kc_upper': 109.5, 'kc_lower': 93.5, 'kc_middle': 101.5, 'ma5': 105, 'ma15': 100.5, 'atr': 2.0, 'kc_trend': 'UP', 'is_closed': True},
            # 破軌 K 棒：open 109, close 110.5 > kc_upper 110.0, body 1.5 >= 0.4*2.0=0.8, upper_wick = 0.3 <= 0.6*1.5=0.9
            {'open': 109, 'close': 110.5, 'high': 110.8, 'low': 108.8, 'kc_upper': 110.0, 'kc_lower': 94.0, 'kc_middle': 102.0, 'ma5': 109.5, 'ma15': 101.0, 'atr': 2.0, 'kc_trend': 'UP', 'is_closed': True},
        ]
        df = make_candle_df(rows)
        passed, code, data = evaluate_closed_entry(df, side='LONG', price=110.5, symbol='LOBSTER/USDT')
        assert passed is True
        assert code == "CLOSED_TREND_BREAKOUT_LONG"
        assert data['bypass_spatial_check'] is True

    # ══════════════════════════════════════════════════════════════════════
    # 三、 模式二：MA5 / MA15 交叉開倉 (Strict Cross Entry)
    # ══════════════════════════════════════════════════════════════════════

    def test_mode2_strict_golden_cross_long_passed(self):
        """模式二多單：KC向上、剛實質金叉、MA5順向發散、價格站上MA5，放行開多"""
        rows = [
            {'open': 100, 'close': 101, 'high': 102, 'low': 99, 'kc_upper': 115, 'kc_lower': 85, 'kc_middle': 100, 'ma5': 100.0, 'ma15': 101.0, 'atr': 2.0, 'kc_trend': 'UP', 'is_closed': True},
            {'open': 101, 'close': 102, 'high': 102.5, 'low': 100, 'kc_upper': 115.5, 'kc_lower': 85.5, 'kc_middle': 100.5, 'ma5': 100.5, 'ma15': 101.0, 'atr': 2.0, 'kc_trend': 'UP', 'is_closed': True},
            # 金叉棒：ma5 102.0 > ma15 101.2，ma5堅定發散 (100.5 -> 102.0)，收盤 103 站上 MA5
            {'open': 102, 'close': 103, 'high': 103.3, 'low': 101.8, 'kc_upper': 116, 'kc_lower': 86, 'kc_middle': 101.0, 'ma5': 102.0, 'ma15': 101.2, 'atr': 2.0, 'kc_trend': 'UP', 'is_closed': True},
        ]
        df = make_candle_df(rows)
        passed, code, data = evaluate_closed_entry(df, side='LONG', price=103.0, symbol='LOBSTER/USDT')
        assert passed is True
        assert code == "CLOSED_MA_CROSS_FAST_LONG"

    def test_mode2_golden_cross_hooking_down_rejected(self):
        """金叉後 MA5 向下彎折，100% 否決開多"""
        rows = [
            {'open': 100, 'close': 101, 'high': 102, 'low': 99, 'kc_upper': 115, 'kc_lower': 85, 'kc_middle': 100, 'ma5': 100.0, 'ma15': 101.0, 'atr': 2.0, 'kc_trend': 'UP', 'is_closed': True},
            {'open': 101, 'close': 104, 'high': 104.5, 'low': 101, 'kc_upper': 115.5, 'kc_lower': 85.5, 'kc_middle': 100.5, 'ma5': 102.5, 'ma15': 101.2, 'atr': 2.0, 'kc_trend': 'UP', 'is_closed': True},
            # MA5 向下勾頭 (102.5 -> 102.0)
            {'open': 104, 'close': 102.2, 'high': 104.2, 'low': 102.0, 'kc_upper': 116, 'kc_lower': 86, 'kc_middle': 101.0, 'ma5': 102.0, 'ma15': 101.4, 'atr': 2.0, 'kc_trend': 'UP', 'is_closed': True},
        ]
        df = make_candle_df(rows)
        passed, reason, _ = evaluate_closed_entry(df, side='LONG', price=102.2, symbol='LOBSTER/USDT')
        assert passed is False
        assert "MA5_HOOKING_DOWN" in reason

    def test_mode2_short_rejected_when_ma5_above_ma15(self):
        """MA5 仍在 MA15 上方，即使向下勾頭，100% 嚴禁開空"""
        rows = [
            {'open': 109, 'close': 108, 'high': 110, 'low': 107, 'kc_upper': 116, 'kc_lower': 86, 'kc_middle': 106, 'ma5': 109.0, 'ma15': 101.5, 'atr': 2.0, 'kc_trend': 'DOWN', 'is_closed': True},
            {'open': 108, 'close': 107, 'high': 109, 'low': 106, 'kc_upper': 115, 'kc_lower': 85, 'kc_middle': 105, 'ma5': 108.0, 'ma15': 102.0, 'atr': 2.0, 'kc_trend': 'DOWN', 'is_closed': True},
            # MA5 向下勾頭 (108.0 -> 104.0)，但仍在 MA15 103.0 之上
            {'open': 107, 'close': 103.5, 'high': 107, 'low': 103, 'kc_upper': 114, 'kc_lower': 84, 'kc_middle': 104, 'ma5': 104.0, 'ma15': 103.0, 'atr': 2.0, 'kc_trend': 'DOWN', 'is_closed': True},
        ]
        df = make_candle_df(rows)
        passed, reason, _ = evaluate_closed_entry(df, side='SHORT', price=103.5, symbol='LOBSTER/USDT')
        assert passed is False
        assert "MA_ALIGNMENT_MISMATCH" in reason or "NO_DEATH_CROSS_MA5_ABOVE_MA15" in reason

    # ══════════════════════════════════════════════════════════════════════
    # 四、 模式三：斷頭開倉 (Decisive Momentum Guillotine Entry)
    # ══════════════════════════════════════════════════════════════════════

    def test_mode3_guillotine_momentum_burst_passed(self):
        """模式三多單：大實體爆發 >= 0.85 ATR、貫穿均線/中軌、body_ratio >= 0.70 放行"""
        # 金叉早在前幾根就已完成 (ma5 > ma15 已維持多根)，此時不符合模式二的剛交叉時效
        # 當根 K 棒大陽線貫穿 KC 中軌 (open 99 <= 100.0, close 102.5 > 100.0)，body 3.5 >= 0.85 * 2 = 1.70，觸發模式三
        rows = [
            {'open': 98, 'close': 99, 'high': 99.5, 'low': 97.5, 'kc_upper': 112, 'kc_lower': 88, 'kc_middle': 99.5, 'ma5': 100.5, 'ma15': 99.0, 'atr': 2.0, 'kc_trend': 'UP', 'is_closed': True},
            {'open': 98.5, 'close': 99.0, 'high': 99.5, 'low': 98.0, 'kc_upper': 112, 'kc_lower': 88, 'kc_middle': 99.5, 'ma5': 101.0, 'ma15': 99.2, 'atr': 2.0, 'kc_trend': 'UP', 'is_closed': True},
            {'open': 99.0, 'close': 102.0, 'high': 102.2, 'low': 98.8, 'kc_upper': 113, 'kc_lower': 89, 'kc_middle': 100.0, 'ma5': 101.5, 'ma15': 99.5, 'atr': 2.0, 'kc_trend': 'UP', 'is_closed': True},
        ]
        df = make_candle_df(rows)
        passed, code, data = evaluate_closed_entry(df, side='LONG', price=102.0, symbol='LOBSTER/USDT')
        assert passed is True
        assert code == "CLOSED_MOMENTUM_GUILLOTINE_LONG"
        assert data['bypass_spatial_check'] is True
