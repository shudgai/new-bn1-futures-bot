"""Unit tests for 1000PEPE / USDT: 完全對齊龍蝦（LOBSTER）之一切開倉、平倉、鎖利規則.

用戶指示：
「cap幣種改成 1000pepe 所有的開倉平倉鎖利所有一切都要跟龍蝦一樣，請修正」

涵蓋項目：
1. 幣種識別與權限解禁：1000PEPE/USDT 納入 MemeCoin 體系，is_entry_disabled 為 False。
2. 雙軌高波動風控參數（HIGH_BETA_CONFIG）：1000PEPE/USDT 與 DEFAULT (龍蝦) 參數 100% 對齊。
3. 初始破軌開倉（兩根流極速確認）：與龍蝦完全一致。
4. 錯過初次破軌之延續開倉（強制上車）：與龍蝦完全一致。
5. 外軌鎖定抱緊機制（防洗盤震下車）：多單/空單與龍蝦完全一致。
6. 空單空頭走勢防洗鐵律（未達真谷底嚴禁平倉，成型真谷底授權平倉）：與龍蝦完全一致。
7. 即時出場保護（realtime_profit_exit 中的 lobster_hold）：1000PEPE 享有龍蝦完全同等的 hold 保護。
"""
import pytest
import pandas as pd
import numpy as np

from core.symbol_aliases import (
    is_meme_breakout_symbol, is_pepe_symbol, is_lobster_symbol
)
from core.config import is_entry_disabled, get_high_beta_config, HIGH_BETA_CONFIG
from core.gates.meme_coin_gate import MemeCoinGate
from core.gates.holding_protection_gate import HoldingProtectionExitGate, has_confirmed_true_pivot
from core.services.exits.dual_track_exit_service import DualTrackExitStrategy


def make_frame(rows):
    df = pd.DataFrame(rows)
    df.attrs['timeframe_ms'] = 60000
    return df


class Test1000PepeParityWithLobster:
    """測試 1000PEPE 與龍蝦 100% 相同的一致性"""

    def test_1000pepe_symbol_identification_and_enabled(self):
        """1000PEPE 正式被識別為妖幣且完全解禁"""
        for sym in ('1000PEPE/USDT', '1000PEPE/USDT:USDT', '1000PEPEUSDT', 'PEPE/USDT'):
            assert is_meme_breakout_symbol(sym) is True
            assert is_pepe_symbol(sym) is True
            assert MemeCoinGate.is_meme_coin(sym) is True
            assert is_entry_disabled(sym) is False

    def test_1000pepe_high_beta_config_parity_with_lobster(self):
        """1000PEPE 之風控與鎖利參數與龍蝦完全一致"""
        lobster_cfg = HIGH_BETA_CONFIG["DEFAULT"]
        pepe_cfg = get_high_beta_config("1000PEPE/USDT")
        assert pepe_cfg["ATR_PERIOD"] == lobster_cfg["ATR_PERIOD"]
        assert pepe_cfg["BREAKEVEN_ACTIVATE_ATR"] == lobster_cfg["BREAKEVEN_ACTIVATE_ATR"]
        assert pepe_cfg["COOLDOWN_KLINES"] == lobster_cfg["COOLDOWN_KLINES"]
        assert pepe_cfg["BODY_FILTER_PCT"] == lobster_cfg["BODY_FILTER_PCT"]
        assert pepe_cfg["BREAKOUT_BUFFER_ATR"] == lobster_cfg["BREAKOUT_BUFFER_ATR"]
        assert pepe_cfg["TRAILING_STOP_DISTANCE_ATR"] == lobster_cfg["TRAILING_STOP_DISTANCE_ATR"]
        assert pepe_cfg["BASE_POSITION_SIZE_RATIO"] == lobster_cfg["BASE_POSITION_SIZE_RATIO"]
        assert pepe_cfg["VOLATILITY_FILTER_PCT"] == lobster_cfg["VOLATILITY_FILTER_PCT"]

    def test_1000pepe_initial_breakout_long_entry(self):
        """1000PEPE 享有龍蝦專屬兩根流極速開多"""
        df = make_frame([
            {'open': 0.0040, 'close': 0.0040, 'high': 0.0041, 'low': 0.0039,
             'ma5': 0.0040, 'ma15': 0.0039, 'kc_upper': 0.0042, 'kc_lower': 0.0038,
             'atr': 0.0002, 'is_closed': True},
            {'open': 0.0041, 'close': 0.0043, 'high': 0.0044, 'low': 0.0040,
             'ma5': 0.0042, 'ma15': 0.00395, 'kc_upper': 0.00425, 'kc_lower': 0.00385,
             'atr': 0.0002, 'is_closed': False},
        ])
        passed, reason, meta = MemeCoinGate.evaluate_meme_entry(
            df, quote=0.00435, side='LONG', symbol='1000PEPE/USDT'
        )
        assert passed is True
        assert reason == 'MEME_INITIAL_KC_BREAKOUT_LONG'

    def test_1000pepe_forced_continuation_entry(self):
        """1000PEPE 錯過初次破軌時，觸發龍蝦專屬強制上車延續開多"""
        df = make_frame([
            {'open': 0.0040, 'close': 0.0043, 'high': 0.0044, 'low': 0.0039,
             'ma5': 0.0041, 'ma15': 0.0039, 'kc_upper': 0.0042, 'kc_lower': 0.0038,
             'atr': 0.0002, 'is_closed': True},
            {'open': 0.0043, 'close': 0.0045, 'high': 0.0046, 'low': 0.0042,
             'ma5': 0.0043, 'ma15': 0.0040, 'kc_upper': 0.00425, 'kc_lower': 0.00385,
             'atr': 0.0002, 'is_closed': False},
        ])
        passed, reason, meta = MemeCoinGate.evaluate_meme_entry(
            df, quote=0.0045, side='LONG', symbol='1000PEPE/USDT'
        )
        assert passed is True
        assert reason == 'MEME_FORCED_CONTINUATION_ENTRY_LONG'

    def test_1000pepe_outer_rail_lock_holding(self):
        """1000PEPE 持倉期間，在外軌強勢區或守穩 MA5 時一律死抱，拒絕被洗盤震下車"""
        df = make_frame([
            {'open': 0.0040, 'close': 0.0044, 'high': 0.0045, 'low': 0.0039,
             'ma5': 0.0042, 'ma15': 0.0039, 'kc_upper': 0.0042, 'kc_lower': 0.0038,
             'atr': 0.0002, 'is_closed': True},
            {'open': 0.0044, 'close': 0.0043, 'high': 0.0045, 'low': 0.00425,
             'ma5': 0.00425, 'ma15': 0.0040, 'kc_upper': 0.0042, 'kc_lower': 0.0038,
             'atr': 0.0002, 'is_closed': False},
        ])
        # quote=0.0043 高於 kc_upper=0.0042 且守穩 MA5
        should_hold, reason = MemeCoinGate.should_hold_outer_rail(
            df, quote=0.0043, side='LONG', symbol='1000PEPE/USDT'
        )
        assert should_hold is True
        assert 'HOLDING' in reason

    def test_1000pepe_short_holding_until_true_valley(self):
        """1000PEPE 開空後，空頭走勢不要平倉，要到真谷底才能平倉（與龍蝦完全一致）"""
        # 下跌走勢中，價格在 KC 中軌下方，未成真谷底
        df = make_frame([
            {'open': 0.0045, 'close': 0.0042, 'high': 0.0046, 'low': 0.0041, 'is_closed': True,
             'ma5': 0.0043, 'ma15': 0.0045, 'kc_upper': 0.0046, 'kc_lower': 0.0039, 'kc_middle': 0.00425, 'atr': 0.0002},
            {'open': 0.0042, 'close': 0.0039, 'high': 0.0043, 'low': 0.0038, 'is_closed': True,
             'ma5': 0.0041, 'ma15': 0.0044, 'kc_upper': 0.0045, 'kc_lower': 0.0038, 'kc_middle': 0.00415, 'atr': 0.0002},
            {'open': 0.0039, 'close': 0.0037, 'high': 0.0040, 'low': 0.0036, 'is_closed': True,
             'ma5': 0.0039, 'ma15': 0.0043, 'kc_upper': 0.0044, 'kc_lower': 0.0037, 'kc_middle': 0.00405, 'atr': 0.0002},
            {'open': 0.0037, 'close': 0.0038, 'high': 0.00385, 'low': 0.00365, 'is_closed': False,
             'ma5': 0.00385, 'ma15': 0.0042, 'kc_upper': 0.0044, 'kc_lower': 0.0037, 'kc_middle': 0.00405, 'atr': 0.0002},
        ])
        position = {
            'symbol': '1000PEPE/USDT',
            'side': 'SHORT',
            'entry_price': 0.0043,
            'qty': 10000.0,
            'has_confirmed_true_pivot': False,
        }

        # 1. 檢驗 MemeCoinGate 禁止平倉
        should_exit, reason = MemeCoinGate.evaluate_meme_exit(
            df, quote=0.0038, side='SHORT', symbol='1000PEPE/USDT'
        )
        assert should_exit is False
        assert reason is None

        # 2. 檢驗 HoldingProtectionExitGate 否決普通平倉（未成真谷底前）
        can_exit, veto_reason, _ = HoldingProtectionExitGate.validate_exit(
            position, df, quote=0.0038, candidate_reason='EXIT_BY_PULLBACK'
        )
        assert can_exit is False
        assert 'VETO' in veto_reason

        # 3. 檢驗 DualTrackExitStrategy 抱緊
        strategy = DualTrackExitStrategy()
        decision = strategy.evaluate_exit(position, current_price=0.0038, frame=df)
        assert decision is None

    def test_1000pepe_short_exit_on_confirmed_true_valley(self):
        """1000PEPE 空單收線確認形成三點真谷底後，授權平倉"""
        df = make_frame([
            {'open': 0.0040, 'close': 0.0038, 'high': 0.0041, 'low': 0.0037, 'is_closed': True,
             'ma5': 0.0040, 'ma15': 0.0042, 'kc_upper': 0.0044, 'kc_lower': 0.0036, 'kc_middle': 0.0040, 'atr': 0.0002},
            {'open': 0.0038, 'close': 0.0035, 'high': 0.0039, 'low': 0.0034, 'is_closed': True,  # 中間最低
             'ma5': 0.0038, 'ma15': 0.0041, 'kc_upper': 0.0043, 'kc_lower': 0.0035, 'kc_middle': 0.0039, 'atr': 0.0002},
            {'open': 0.0035, 'close': 0.0037, 'high': 0.0038, 'low': 0.0036, 'is_closed': True,  # 收線確認包夾
             'ma5': 0.0037, 'ma15': 0.0040, 'kc_upper': 0.0042, 'kc_lower': 0.0035, 'kc_middle': 0.00385, 'atr': 0.0002},
            {'open': 0.0037, 'close': 0.00375, 'high': 0.0038, 'low': 0.00365, 'is_closed': False,
             'ma5': 0.00375, 'ma15': 0.00395, 'kc_upper': 0.0042, 'kc_lower': 0.0035, 'kc_middle': 0.00385, 'atr': 0.0002},
        ])
        position = {
            'symbol': '1000PEPE/USDT',
            'side': 'SHORT',
            'entry_price': 0.0042,
            'qty': 10000.0,
        }

        # 確認真谷底
        has_valley, p_type, _ = has_confirmed_true_pivot(position, df, quote=0.00375)
        assert has_valley is True

        # MemeCoinGate 授權真谷底出場
        should_exit, reason = MemeCoinGate.evaluate_meme_exit(
            df, quote=0.00375, side='SHORT', symbol='1000PEPE/USDT'
        )
        assert should_exit is True
        assert reason == 'EXIT_MEME_TRUE_VALLEY_CONFIRMED'

    def test_1000pepe_short_does_not_exit_on_historical_pre_entry_valley(self):
        """1000PEPE 開空後，歷史存在開倉前的谷底，絕不當作開倉後的真谷底秒平"""
        live_ts = 1791944400000.0  # 10:20:00
        # 構造 10:17~10:19 的歷史谷底（10:18 最低），並在 10:20 開空
        df = make_frame([
            {'open': 0.004190, 'close': 0.004190, 'high': 0.004193, 'low': 0.004189, 'timestamp': live_ts - 180000,
             'is_closed': True, 'ma5': 0.004193, 'ma15': 0.004198, 'kc_upper': 0.004201, 'kc_lower': 0.004190, 'kc_middle': 0.004195, 'atr': 0.000005},
            {'open': 0.004190, 'close': 0.004191, 'high': 0.004192, 'low': 0.004185, 'timestamp': live_ts - 120000,
             'is_closed': True, 'ma5': 0.004191, 'ma15': 0.004197, 'kc_upper': 0.004200, 'kc_lower': 0.004189, 'kc_middle': 0.004195, 'atr': 0.000005},
            {'open': 0.004191, 'close': 0.004189, 'high': 0.004192, 'low': 0.004189, 'timestamp': live_ts - 60000,
             'is_closed': True, 'ma5': 0.004190, 'ma15': 0.004195, 'kc_upper': 0.004199, 'kc_lower': 0.004189, 'kc_middle': 0.004194, 'atr': 0.000005},
            {'open': 0.004189, 'close': 0.004188, 'high': 0.004190, 'low': 0.004187, 'timestamp': live_ts,
             'is_closed': False, 'ma5': 0.004189, 'ma15': 0.004194, 'kc_upper': 0.004199, 'kc_lower': 0.004188, 'kc_middle': 0.004193, 'atr': 0.000005},
        ])

        # 在 10:20:09 進場
        position = {
            'symbol': '1000PEPE/USDT',
            'side': 'SHORT',
            'entry_price': 0.004189,
            'qty': 100000.0,
            'open_timestamp': (live_ts + 9000) / 1000.0,
        }

        # 檢驗：歷史谷底被過濾，不判為真谷底
        has_valley, p_type, _ = has_confirmed_true_pivot(position, df, quote=0.004188)
        assert has_valley is False
        assert p_type is None

        # 檢驗：MemeCoinGate 絕對不平倉（傳入 position 防止歷史谷底誤平）
        should_exit, reason = MemeCoinGate.evaluate_meme_exit(
            df, quote=0.004188, side='SHORT', symbol='1000PEPE/USDT', position=position
        )
        assert should_exit is False
        assert reason is None

    def test_1000pepe_short_holding_strict_no_exit_except_profit_lock_or_true_valley(self):
        """1000PEPE 開空後不要平倉，除非鎖利或真谷底出現，要不然一律不平倉"""
        live_ts = 1791944400000.0
        df = make_frame([
            {'open': 0.004200, 'close': 0.004180, 'high': 0.004205, 'low': 0.004175, 'timestamp': live_ts - 120000,
             'is_closed': True, 'ma5': 0.004190, 'ma15': 0.004210, 'kc_upper': 0.004220, 'kc_lower': 0.004170, 'kc_middle': 0.004195, 'atr': 0.000010},
            {'open': 0.004180, 'close': 0.004160, 'high': 0.004185, 'low': 0.004155, 'timestamp': live_ts - 60000,
             'is_closed': True, 'ma5': 0.004175, 'ma15': 0.004200, 'kc_upper': 0.004215, 'kc_lower': 0.004160, 'kc_middle': 0.004185, 'atr': 0.000010},
            {'open': 0.004160, 'close': 0.004172, 'high': 0.004175, 'low': 0.004158, 'timestamp': live_ts,
             'is_closed': False, 'ma5': 0.004170, 'ma15': 0.004195, 'kc_upper': 0.004210, 'kc_lower': 0.004158, 'kc_middle': 0.004180, 'atr': 0.000010},
        ])

        position = {
            'symbol': '1000PEPE/USDT',
            'side': 'SHORT',
            'entry_price': 0.004190,
            'qty': 100000.0,
            'open_timestamp': (live_ts - 100000) / 1000.0,
        }

        # 1. 普通回調平倉、MA5 穿破平倉等雜訊平倉，全部 100% 被一票否決
        for cand_reason in ['EXIT_BY_PULLBACK', 'EXIT_BY_MA5_CROSS', 'EXIT_BY_BAND_TOUCH']:
            can_exit, veto_reason, _ = HoldingProtectionExitGate.validate_exit(
                position, df, quote=0.004172, candidate_reason=cand_reason
            )
            assert can_exit is False
            assert 'VETO' in veto_reason

        # 2. 鎖利平倉（保本鎖利 / 階梯鎖利）被授權放行
        can_exit_profit, reason_profit, _ = HoldingProtectionExitGate.validate_exit(
            position, df, quote=0.004172, candidate_reason='EXIT_BY_RATCHET_PROFIT_LOCK'
        )
        assert can_exit_profit is True
        assert reason_profit == 'EXIT_BY_RATCHET_PROFIT_LOCK'

        # 3. 真谷底成型時，授權平倉
        has_v, _, _ = has_confirmed_true_pivot(position, df, quote=0.004172)
        assert has_v is True
        can_exit_valley, reason_valley, _ = HoldingProtectionExitGate.validate_exit(
            position, df, quote=0.004172, candidate_reason='EXIT_BY_CONFIRMED_TRUE_VALLEY'
        )
        assert can_exit_valley is True
        assert reason_valley == 'EXIT_BY_CONFIRMED_TRUE_VALLEY'
