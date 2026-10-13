"""Unit tests for MemeCoinGate (妖幣專用：KC 破軌暴利與延續補倉手冊).

涵蓋五大核心規則：
1. 初始破軌與極速捕捉：大陽線爆出 KC 上軌（實體 >= 0.30 ATR，MA5 向上）極速捕捉進場。
2. 錯過初次破軌的延續開倉（強制上車）：價格維持在 KC 上軌外，MA5 依然昂揚向上，強制觸發延續上車。
3. KC 外軌鎖定（不被洗盤震下車）：只要實體維持在 KC 外軌外且守穩 MA5，一律不平倉、死抱利潤！
4. 極短線動能防護（MA5 嚴格禁令）：MA5 走跌一律嚴禁開多；MA5 走揚一律嚴禁開空。
5. 妖幣出場止盈點：實體砸回 KC 軌道內或跌破 MA5 關鍵防守線時果斷平倉獲利了結。
"""
import pytest
import pandas as pd
import numpy as np

from core.gates.meme_coin_gate import MemeCoinGate
from core.gates.holding_protection_gate import HoldingProtectionExitGate


def make_frame(rows):
    df = pd.DataFrame(rows)
    df.attrs['timeframe_ms'] = 60000
    return df


class TestMemeCoinGate:
    """測試妖幣專用閘門規則"""

    def test_meme_coin_symbol_identification(self):
        assert MemeCoinGate.is_meme_coin('CAP/USDT') is True
        assert MemeCoinGate.is_meme_coin('龍蝦/USDT') is True
        assert MemeCoinGate.is_meme_coin('LOBSTER/USDT') is True
        assert MemeCoinGate.is_meme_coin('BTC/USDT') is False

    def test_meme_initial_breakout_fast_capture(self):
        # 初始破軌：大陽線爆出 KC 上軌（開盤在軌內，價格在軌外，實體 >= 0.3 ATR，MA5 向上）
        df = make_frame([
            {'open': 10.0, 'close': 10.0, 'high': 10.2, 'low': 9.8,
             'ma5': 10.0, 'ma15': 9.8, 'kc_upper': 10.5, 'kc_lower': 9.5,
             'atr': 1.0, 'is_closed': True},
            {'open': 10.2, 'close': 10.4, 'high': 11.2, 'low': 10.1,
             'ma5': 10.3, 'ma15': 9.9, 'kc_upper': 10.6, 'kc_lower': 9.5,
             'atr': 1.0, 'is_closed': False},
        ])
        passed, reason, meta = MemeCoinGate.evaluate_meme_entry(
            df, quote=11.0, side='LONG', symbol='龍蝦/USDT'
        )
        assert passed is True
        assert reason == 'MEME_INITIAL_KC_BREAKOUT_LONG'
        assert meta['entry_type'] == 'MEME_INITIAL_BREAKOUT'

    def test_meme_missed_breakout_forced_continuation(self):
        # 錯過初次破軌（開盤已在軌外，價格持續在軌外狂奔，MA5 昂揚向上）-> 強制上車！
        df = make_frame([
            {'open': 10.0, 'close': 10.8, 'high': 11.0, 'low': 9.8,
             'ma5': 10.2, 'ma15': 9.8, 'kc_upper': 10.5, 'kc_lower': 9.5,
             'atr': 1.0, 'is_closed': True},
            {'open': 10.8, 'close': 11.2, 'high': 11.8, 'low': 10.7,
             'ma5': 10.8, 'ma15': 10.0, 'kc_upper': 10.6, 'kc_lower': 9.5,
             'atr': 1.0, 'is_closed': False},
        ])
        passed, reason, meta = MemeCoinGate.evaluate_meme_entry(
            df, quote=11.5, side='LONG', symbol='CAP/USDT'
        )
        assert passed is True
        assert reason == 'MEME_FORCED_CONTINUATION_ENTRY_LONG'
        assert meta['entry_type'] == 'MEME_FORCED_CONTINUATION'

    def test_meme_ma5_momentum_veto_blocks_long_at_peak(self):
        # 妖幣轉弱：MA5 下拐走跌，一律嚴禁開多！
        df = make_frame([
            {'open': 11.0, 'close': 11.5, 'high': 11.8, 'low': 10.8,
             'ma5': 11.2, 'ma15': 10.0, 'kc_upper': 10.6, 'kc_lower': 9.5,
             'atr': 1.0, 'is_closed': True},
            {'open': 11.5, 'close': 10.8, 'high': 11.6, 'low': 10.5,
             'ma5': 11.0, 'ma15': 10.1, 'kc_upper': 10.6, 'kc_lower': 9.5,
             'atr': 1.0, 'is_closed': True},
        ])
        passed, reason, meta = MemeCoinGate.evaluate_meme_entry(
            df, quote=10.9, side='LONG', symbol='龍蝦/USDT'
        )
        assert passed is False
        assert reason == 'BLOCKED_BY_MA5_DOWNWARD_SLOPE'

    def test_meme_outer_rail_holding_protection_against_shakeout(self):
        # KC 外軌鎖定：價格在 KC 上軌外狂奔，小陰線洗盤，只要在 KC 外軌外且守穩 MA5，一律死抱利潤！
        df = make_frame([
            {'open': 10.0, 'close': 10.8, 'high': 11.0, 'low': 9.8,
             'ma5': 10.2, 'ma15': 9.8, 'kc_upper': 10.5, 'kc_lower': 9.5,
             'atr': 1.0, 'is_closed': True},
            {'open': 11.5, 'close': 11.2, 'high': 11.6, 'low': 11.0,
             'ma5': 10.9, 'ma15': 10.0, 'kc_upper': 10.6, 'kc_lower': 9.5,
             'atr': 1.0, 'is_closed': False},
        ])
        should_hold, reason = MemeCoinGate.should_hold_outer_rail(
            df, quote=11.2, side='LONG', symbol='CAP/USDT'
        )
        assert should_hold is True
        assert reason == 'VETO_MEME_OUTER_RAIL_LOCK_HOLDING'

    def test_meme_exit_when_breaching_ma5_or_returning_inside_kc(self):
        # 妖幣出場止盈點：跌破 MA5 關鍵防守線或砸回 KC 軌道內
        df = make_frame([
            {'open': 10.0, 'close': 10.8, 'high': 11.0, 'low': 9.8,
             'ma5': 10.2, 'ma15': 9.8, 'kc_upper': 10.5, 'kc_lower': 9.5,
             'atr': 1.0, 'is_closed': True},
            {'open': 11.0, 'close': 10.3, 'high': 11.1, 'low': 10.2,
             'ma5': 10.6, 'ma15': 10.0, 'kc_upper': 10.5, 'kc_lower': 9.5,
             'atr': 1.0, 'is_closed': True},
        ])
        # 價格跌破 MA5 (10.3 < 10.6) 且砸回 KC 上軌內 (10.3 <= 10.5)
        should_exit, reason = MemeCoinGate.evaluate_meme_exit(
            df, quote=10.3, side='LONG', symbol='龍蝦/USDT'
        )
        assert should_exit is True
        assert reason in ('EXIT_MEME_MA5_DEFENSE_BREACH', 'EXIT_MEME_RETURN_INSIDE_KC_RAIL')

    def test_holding_protection_gate_integrates_meme_outer_lock(self):
        # 驗證 HoldingProtectionExitGate 自動識別妖幣並否決微利回吐偷跑平倉
        df = make_frame([
            {'open': 10.0, 'close': 10.8, 'high': 11.0, 'low': 9.8,
             'ma5': 10.2, 'ma15': 9.8, 'kc_upper': 10.5, 'kc_lower': 9.5,
             'atr': 1.0, 'is_closed': True},
            {'open': 11.5, 'close': 11.2, 'high': 11.6, 'low': 11.0,
             'ma5': 10.9, 'ma15': 10.0, 'kc_upper': 10.6, 'kc_lower': 9.5,
             'atr': 1.0, 'is_closed': False},
        ])
        position = {'symbol': '龍蝦/USDT', 'side': 'LONG', 'entry_price': 10.2}
        allowed, reason, _ = HoldingProtectionExitGate.validate_exit(
            position, df, quote=11.2, candidate_reason='STAGE1_TRAILING_GIVEBACK_EXIT'
        )
        assert allowed is False
        assert reason == 'VETO_MEME_OUTER_RAIL_LOCK_HOLDING'

    def test_meme_ambush_entry_long_conditions_met(self):
        # 模組一：提前開倉（30% 底倉埋伏）：軌道內、MA5 陡峭向上、站穩中軌與 MA5
        df = make_frame([
            {'open': 9.8, 'close': 10.0, 'high': 10.1, 'low': 9.7,
             'ma5': 9.9, 'ma15': 9.8, 'kc_middle': 10.0, 'kc_upper': 10.8, 'kc_lower': 9.2,
             'atr': 1.0, 'is_closed': True},
            {'open': 10.0, 'close': 10.3, 'high': 10.4, 'low': 9.9,
             'ma5': 10.2, 'ma15': 9.9, 'kc_middle': 10.05, 'kc_upper': 10.8, 'kc_lower': 9.2,
             'atr': 1.0, 'is_closed': False},
        ])
        passed, reason, meta = MemeCoinGate.evaluate_ambush_entry(
            df, quote=10.3, side='LONG', symbol='CAP/USDT'
        )
        assert passed is True
        assert reason == 'MEME_AMBUSH_ENTRY_LONG'
        assert meta['entry_type'] == 'MEME_AMBUSH'
        assert meta['suggested_size_ratio'] == 0.35

    def test_meme_ambush_entry_blocked_when_already_broke_kc_upper(self):
        # 已突破 KC 上軌時不應視為提前埋伏（應由破軌入口或加倉接手）
        df = make_frame([
            {'open': 9.8, 'close': 10.0, 'high': 10.1, 'low': 9.7,
             'ma5': 9.9, 'ma15': 9.8, 'kc_middle': 10.0, 'kc_upper': 10.8, 'kc_lower': 9.2,
             'atr': 1.0, 'is_closed': True},
            {'open': 10.0, 'close': 10.9, 'high': 11.0, 'low': 9.9,
             'ma5': 10.4, 'ma15': 9.9, 'kc_middle': 10.05, 'kc_upper': 10.8, 'kc_lower': 9.2,
             'atr': 1.0, 'is_closed': False},
        ])
        passed, reason, meta = MemeCoinGate.evaluate_ambush_entry(
            df, quote=10.9, side='LONG', symbol='CAP/USDT'
        )
        assert passed is False
        assert reason == 'ALREADY_BROKE_KC_OUTER_RAIL'

    def test_meme_pyramid_addition_on_breakout(self):
        # 模組二：破軌確認（70% 加倉做滿）
        df = make_frame([
            {'open': 10.0, 'close': 10.4, 'high': 10.5, 'low': 9.9,
             'ma5': 10.1, 'ma15': 9.8, 'kc_middle': 10.0, 'kc_upper': 10.6, 'kc_lower': 9.4,
             'atr': 1.0, 'is_closed': True},
            {'open': 10.4, 'close': 11.0, 'high': 11.2, 'low': 10.3,
             'ma5': 10.5, 'ma15': 9.9, 'kc_middle': 10.1, 'kc_upper': 10.6, 'kc_lower': 9.4,
             'atr': 1.0, 'is_closed': False},
        ])
        passed, reason, meta = MemeCoinGate.evaluate_pyramid_addition(
            df, quote=11.0, side='LONG', symbol='龍蝦/USDT'
        )
        assert passed is True
        assert reason == 'MEME_PYRAMID_ADDITION_LONG'
        assert meta['entry_type'] == 'MEME_PYRAMID_ADD'
        assert meta['suggested_size_ratio'] == 0.65

    def test_meme_ambush_failure_stop_on_kc_middle_breach(self):
        # 模組三：埋伏失敗認錯止損（跌破中軌）
        df = make_frame([
            {'open': 10.0, 'close': 10.2, 'high': 10.3, 'low': 9.9,
             'ma5': 10.1, 'ma15': 9.9, 'kc_middle': 10.0, 'kc_upper': 10.6, 'kc_lower': 9.4,
             'atr': 1.0, 'is_closed': True},
            {'open': 10.1, 'close': 9.8, 'high': 10.2, 'low': 9.7,
             'ma5': 9.9, 'ma15': 9.9, 'kc_middle': 10.0, 'kc_upper': 10.6, 'kc_lower': 9.4,
             'atr': 1.0, 'is_closed': True},
        ])
        should_stop, reason = MemeCoinGate.evaluate_ambush_failure_stop(
            df, quote=9.8, side='LONG', symbol='CAP/USDT'
        )
        assert should_stop is True
        assert reason == 'EXIT_MEME_AMBUSH_FAIL_KC_MID_BREACH'
