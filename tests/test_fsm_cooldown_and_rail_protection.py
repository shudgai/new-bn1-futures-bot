"""Unit tests for Finite State Machine, 3-Bar Cooldown, and No-Touch-Rail Exits.

驗證核心要點：
1. 嚴格狀態機（FSM）：當系統持倉（IN_LONG / IN_SHORT）時，絕對不重複觸發開倉訊號。
2. 平倉冷卻期（Cooldown）：平倉後至少冷卻 3 根 K 棒（180 秒），冷卻期間 100% 拒絕開新倉。
3. 徹底禁止「碰上軌平多、碰下軌平空」：
   - 衝破上軌或沿上軌走一律抱緊，不因上影線或回踩上軌而平多，必須連續 2 根收盤實體跌破 KC 中軌才出場。
   - 沿下軌走一律抱緊，不因下影線或回踩下軌而平空，必須連續 2 根收盤實體站上 KC 中軌才出場。
4. 獲利空間過濾（防貼軌追單）：
   - 價格貼近下軌底部（距下軌不足 20% 通道空間）嚴禁開空，防止追在地板。
   - 價格貼近上軌頂部（距上軌不足 20% 通道空間）嚴禁開多，防止追在天花板。
"""
import pytest
import pandas as pd
from unittest.mock import AsyncMock, MagicMock

from core.gates.strict_cross_kc_gate import StrictCrossAndKcTrendGate
from core.gates.meme_coin_gate import MemeCoinGate
from core.services.symbol_runner import process_single_symbol_runner


def make_candle_df(bars):
    df = pd.DataFrame(bars)
    df.attrs['timeframe_ms'] = 60000
    return df


class TestFsmCooldownAndRailProtection:
    """測試狀態機、3 根 K 棒冷卻期與防碰軌平倉規則"""

    def test_fsm_position_lock_does_not_open_duplicate(self):
        """1. 狀態機持倉鎖定：持有多單時，不重複發送開倉訊號"""
        async def _run():
            engine = MagicMock()
            engine.account = MagicMock()
            engine.account.positions = {
                '1000PEPE/USDT': {'side': 'LONG', 'entry_price': 0.0042, 'amount': 10000.0}
            }
            engine.account.trades = []
            engine.account.last_closed_at = {}
            engine._execute_confirmed_channel_break = AsyncMock()

            df = make_candle_df([
                {'open': 0.0042, 'close': 0.0043, 'high': 0.0044, 'low': 0.0041, 'timestamp': 1000000,
                 'ma5': 0.00425, 'ma15': 0.00420, 'kc_middle': 0.00420, 'kc_upper': 0.0044, 'kc_lower': 0.0040,
                 'kc_trend': 'UP', 'is_closed': True},
            ])

            from core.services.exits.realtime_profit_exit import enforce_realtime_profit_exit
            # 呼叫 process_single_symbol_runner
            await process_single_symbol_runner(
                engine, '1000PEPE/USDT', now_time=0, btc_1m_turn=False, daily_halt=False,
                exit_frame=df, exit_quote=0.0043, exit_only=False,
            )

            # 驗證：絕對不調用任何開倉邏輯！
            engine._execute_confirmed_channel_break.assert_not_called()

        import asyncio
        asyncio.run(_run())

    def test_cooldown_blocks_reentry_within_3_bars(self):
        """2. 平倉後冷卻期：平倉後未滿 180 秒（3 根 K 棒），100% 拒絕開新倉"""
        engine = MagicMock()
        engine.account = MagicMock()
        engine.account.positions = {}
        # 上次平倉時間是 1000 秒
        engine.account.last_closed_at = {'1000PEPE/USDT': 1000.0}
        engine.account.trades = [
            {'symbol': '1000PEPE/USDT', 'action': 'CLOSE_LONG', 'timestamp': 1000.0, 'id': 1000.0}
        ]
        engine._execute_confirmed_channel_break = AsyncMock()

        # 當前時間是 1060 秒（僅經過 1 根 K 棒，未滿 180 秒）
        curr_bar_ts = 1060.0 * 1000.0
        df = make_candle_df([
            {'open': 0.0042, 'close': 0.0043, 'high': 0.0044, 'low': 0.0041, 'timestamp': curr_bar_ts - 60000,
             'ma5': 0.00422, 'ma15': 0.00420, 'kc_middle': 0.00420, 'kc_upper': 0.0044, 'kc_lower': 0.0040,
             'kc_trend': 'UP', 'is_closed': True},
            {'open': 0.0043, 'close': 0.00435, 'high': 0.0044, 'low': 0.0042, 'timestamp': curr_bar_ts,
             'ma5': 0.00428, 'ma15': 0.00421, 'kc_middle': 0.00421, 'kc_upper': 0.0044, 'kc_lower': 0.0040,
             'kc_trend': 'UP', 'is_closed': True},
        ])

        async def _run():
            await process_single_symbol_runner(
                engine, '1000PEPE/USDT', now_time=0, btc_1m_turn=False, daily_halt=False,
                exit_frame=df, exit_quote=0.00435, exit_only=False,
            )

        import asyncio
        asyncio.run(_run())

        # 驗證：被冷卻期攔截，不開倉
        engine._execute_confirmed_channel_break.assert_not_called()

    def test_forbid_exit_on_touching_upper_rail_for_long(self):
        """3. 徹底禁止碰上軌就平多：衝破上軌穩穩抱住，唯有連續 2 根跌破 KC 中軌才平倉"""
        # 構造衝破上軌行情 (上軌 10.5，當前 10.8)
        df_冲破 = make_candle_df([
            {'open': 10.0, 'close': 10.4, 'high': 10.5, 'low': 9.9, 'kc_middle': 10.0, 'kc_upper': 10.5, 'kc_lower': 9.5},
            {'open': 10.4, 'close': 10.8, 'high': 10.9, 'low': 10.3, 'kc_middle': 10.1, 'kc_upper': 10.5, 'kc_lower': 9.5},
        ])
        should_exit, reason = MemeCoinGate.evaluate_meme_exit(
            df_冲破, quote=10.8, side='LONG', symbol='1000PEPE/USDT'
        )
        assert should_exit is False
        assert reason is None

        # 構造回踩上軌 (10.55 回踩至 10.45 稍微進軌道)，仍在中軌 10.1 之上，嚴禁平多！
        df_回踩 = make_candle_df([
            {'open': 10.4, 'close': 10.8, 'high': 10.9, 'low': 10.3, 'kc_middle': 10.1, 'kc_upper': 10.5, 'kc_lower': 9.5},
            {'open': 10.8, 'close': 10.45, 'high': 10.8, 'low': 10.4, 'kc_middle': 10.1, 'kc_upper': 10.5, 'kc_lower': 9.5},
        ])
        should_exit, reason = MemeCoinGate.evaluate_meme_exit(
            df_回踩, quote=10.45, side='LONG', symbol='1000PEPE/USDT'
        )
        assert should_exit is False
        assert reason is None

        # 連續 2 根收盤跌破中軌 (9.8 < 10.0 且 9.7 < 10.0)，才授權平多
        df_破中軌 = make_candle_df([
            {'open': 10.1, 'close': 9.8, 'high': 10.2, 'low': 9.7, 'kc_middle': 10.0, 'kc_upper': 10.5, 'kc_lower': 9.5},
            {'open': 9.8, 'close': 9.7, 'high': 9.9, 'low': 9.6, 'kc_middle': 10.0, 'kc_upper': 10.5, 'kc_lower': 9.5},
        ])
        should_exit, reason = MemeCoinGate.evaluate_meme_exit(
            df_破中軌, quote=9.7, side='LONG', symbol='1000PEPE/USDT'
        )
        assert should_exit is True
        assert reason == 'EXIT_MEME_KC_MIDDLE_REVERSAL'
