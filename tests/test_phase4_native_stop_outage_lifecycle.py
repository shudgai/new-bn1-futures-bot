# -*- coding: utf-8 -*-
"""
測試第四階段整合修正：
1. 原生 STOP_MARKET 建立成功與生命週期管理
2. 孤兒保護不擅自掛出固定 TAKE_PROFIT_MARKET
3. 快照失敗時禁止新倉，但保護既有持倉
4. 平倉後撤單與防孤兒訂單
5. 多空鏡像對稱性與趨勢續抱、峰谷確認
"""
import pytest
import asyncio
import pandas as pd
import numpy as np
from unittest.mock import AsyncMock, MagicMock, patch

from core.services.execution_manager import ExecutionManager
from core.gates.unified_pipeline_gates import ExitGate
from core.services.data_pipeline import MarketDataSnapshot
from core.testnet_account import BinanceTestnetAccount


def test_native_stop_market_created_for_wild_swing():
    """驗證 WILD_SWING 開倉時確實向交易所掛出 STOP_MARKET，且不掛固定 TAKE_PROFIT_MARKET"""
    async def _test():
        with patch.object(BinanceTestnetAccount, "__init__", lambda self: None), \
             patch("core.services.entry_firewall.EntryFirewall.verify_can_open", lambda *args, **kwargs: None), \
             patch("core.services.entry_firewall.require_ma5_ma15_entry_gates", lambda *args, **kwargs: None), \
             patch("core.services.order_sizing.calculate_order_qty", return_value=5.0), \
             patch("core.testnet_account.ENABLE_EXCHANGE_INITIAL_STOP_LOSS", True), \
             patch("core.testnet_account.POSITION_HARD_STOPS_ENABLED", True):

            acc = BinanceTestnetAccount()
            acc.is_paper = False
            acc.exchange = MagicMock()
            acc.exchange.market = lambda s: {'linear': True, 'contractSize': 1, 'limits': {'cost': {'min': 5.0}, 'amount': {'min': 0.001}}, 'precision': {'amount': 0.001}}
            acc.exchange.price_to_precision = lambda s, p: f"{float(p):.2f}"
            acc.exchange.amount_to_precision = lambda s, a: f"{float(a):.3f}"
            acc.positions = {}
            acc.position_meta = {}
            acc.trades = []
            acc.closing_lock = set()
            acc.pending_limit_orders = {}
            acc.last_closed_at = {}
            acc.last_closed_side = {}
            acc.last_closed_price = {}
            acc.d0_in_flight_state = {}
            acc.d0_consumed_state = {}
            acc.d0_quarantine_state = {}
            acc.log = MagicMock(side_effect=lambda msg, lvl="INFO": print(f"[{lvl}] {msg}"))
            acc.save_state = MagicMock()
            acc.refresh = AsyncMock(return_value=0.0)
            acc._prepare_leverage = AsyncMock()
            acc._ensure_markets = AsyncMock()
            acc._cancel_all_orders = AsyncMock()
            acc._create_protection_order = AsyncMock(return_value={"id": "stop_123", "status": "NEW"})
            acc._send_order = AsyncMock(return_value={"id": "order_entry_1", "status": "FILLED", "average": 100.0})

            # 調用 open_position
            opened = await acc.open_position(
                symbol="BTCUSDT",
                side="LONG",
                price=100.0,
                amount_usdt=50.0,
                sl=95.5,
                tp=0.0,
                reason="CONFIRMED_2BAR_BREAKOUT",
                atr=1.5,
                leverage=10,
                entry_context={
                    "entry_mode": "WILD_SWING",
                    "source": "UNIFIED_PIPELINE",
                    "is_manual": True,
                }
            )

            assert opened is True
            # 驗證 _create_protection_order 被調用且類型為 STOP_MARKET
            calls = acc._create_protection_order.call_args_list
            assert len(calls) == 1, "應該僅建立 1 個保護單（STOP_MARKET）"
            sym, c_side, o_type, qty, sl_p = calls[0][0]
            assert sym == "BTCUSDT"
            assert c_side == "sell"
            assert o_type == "STOP_MARKET"
            assert sl_p == 99.5  # 依保證金風險上限收緊至 99.5 掛出原生止損單

    asyncio.run(_test())


def test_orphan_protection_does_not_create_tp_for_trend_positions():
    """驗證孤兒保護對 WILD_SWING 或 UNIFIED_PIPELINE 不會擅自建立固定 TAKE_PROFIT_MARKET"""
    async def _test():
        with patch.object(BinanceTestnetAccount, "__init__", lambda self: None), \
             patch("core.testnet_account.ENABLE_EXCHANGE_INITIAL_STOP_LOSS", True), \
             patch("core.testnet_account.POSITION_HARD_STOPS_ENABLED", True):

            acc = BinanceTestnetAccount()
            acc.positions = {
                "ETHUSDT": {
                    "symbol": "ETHUSDT",
                    "side": "LONG",
                    "entry_price": 2000.0,
                    "qty": 1.0,
                    "leverage": 10,
                    "entry_mode": "WILD_SWING",
                }
            }
            meta = {"source": "UNIFIED_PIPELINE", "entry_mode": "WILD_SWING", "atr": 20.0}
            acc.position_meta = {"ETHUSDT": meta}
            acc.exchange = MagicMock()
            acc.exchange.price_to_precision = lambda s, p: f"{p:.2f}"
            acc.exchange.amount_to_precision = lambda s, a: f"{a:.3f}"
            acc.log = MagicMock()
            acc.save_state = MagicMock()
            acc._cancel_all_orders = AsyncMock()
            acc._create_protection_order = AsyncMock(return_value={"id": "p_1"})

            # 調用孤兒保護
            await acc._create_orphan_protection("ETHUSDT", acc.positions["ETHUSDT"], meta)

            # 檢查建立的保護單
            calls = acc._create_protection_order.call_args_list
            created_types = [call[0][2] for call in calls]
            assert "STOP_MARKET" in created_types
            assert "TAKE_PROFIT_MARKET" not in created_types, "趨勢續抱倉位絕不得建立固定 TAKE_PROFIT_MARKET"

    asyncio.run(_test())


def test_snapshot_failure_forbids_new_entry_but_protects_existing_position():
    """驗證快照失敗時：無持倉時 Fail-Closed 禁止開倉；有持倉且觸發緊急損失時執行平倉"""
    async def _test():
        class MockAccount:
            def __init__(self):
                self.positions = {}
                self.close_position = AsyncMock(return_value=True)

        account = MockAccount()
        fake_df = pd.DataFrame([{"open": 100, "high": 105, "low": 95, "close": 100, "volume": 1000}])

        with patch("core.services.data_pipeline.DataPipeline.calculate_snapshot", return_value=None):
            # 1. 無持倉時：快照失敗一律禁止新倉
            res_no_pos = await ExecutionManager.process_symbol_pipeline(
                symbol="SOLUSDT",
                engine_or_account=account,
                klines_df=fake_df,
                quote=150.0,
            )
            assert res_no_pos["status"] == "SNAPSHOT_FAILED"

            # 2. 有持倉時：虧損觸發緊急硬止損 (100 -> 80, 損失 20%)
            account.positions = {
                "SOLUSDT": {
                    "symbol": "SOLUSDT",
                    "side": "LONG",
                    "entry_price": 100.0,
                    "qty": 10.0,
                    "amount": 10.0,
                    "leverage": 5,
                }
            }
            res_with_pos = await ExecutionManager.process_symbol_pipeline(
                symbol="SOLUSDT",
                engine_or_account=account,
                klines_df=fake_df,
                quote=80.0,  # 暴跌 20%
            )
            assert res_with_pos["status"] == "EMERGENCY_EXIT_EXECUTED"
            assert res_with_pos["success"] is True
            account.close_position.assert_awaited_once()

    asyncio.run(_test())


def test_close_position_cancels_all_lingering_orders():
    """驗證平倉成功後調用 _cancel_all_orders 徹底清除條件單"""
    async def _test():
        account = MagicMock()
        del account.account
        account.positions = {
            "BTCUSDT": {"symbol": "BTCUSDT", "side": "LONG", "entry_price": 60000.0, "qty": 0.1}
        }
        account.close_position = AsyncMock(return_value=True)
        account._cancel_all_orders = AsyncMock()

        success = await ExecutionManager.close_position(
            account=account,
            symbol="BTCUSDT",
            price=61000.0,
            reason="EARLY_PIVOT_PEAK_EXIT",
        )
        assert success is True
        account._cancel_all_orders.assert_awaited_once_with("BTCUSDT")

    asyncio.run(_test())


def test_long_short_mirror_three_point_pivot_symmetry():
    """驗證多頭三點峰頂與空頭三點谷底在鏡像序列上的對稱性"""
    # 建立多頭行情：左棒 100 -> 峰棒 110 (收陽碰上軌) -> 確認棒 102 (收陰跌破實體中點 105，且 ma5 下拐)
    # entry_p = 100.0, peak_high = 110.0 (衝高 10.0 = 5 ATR >= 0.45 ATR)
    df_long = pd.DataFrame([
        {"open": 98.0, "high": 102.0, "low": 97.0, "close": 101.0, "kc_upper": 108.0, "kc_lower": 92.0, "kc_middle": 100.0, "atr": 2.0, "ma5": 100.0},
        {"open": 101.0, "high": 110.0, "low": 100.0, "close": 109.0, "kc_upper": 108.0, "kc_lower": 92.0, "kc_middle": 100.0, "atr": 2.0, "ma5": 104.0},
        {"open": 106.0, "high": 106.5, "low": 101.0, "close": 102.0, "kc_upper": 108.0, "kc_lower": 92.0, "kc_middle": 100.0, "atr": 2.0, "ma5": 103.0},
    ])
    k_curr_l = df_long.iloc[-1].to_dict()
    k_prev_l = df_long.iloc[-2].to_dict()
    snap_long = MarketDataSnapshot(
        symbol="BTCUSDT",
        current_price=102.0,
        curr_k=k_curr_l,
        prev_k=k_prev_l,
        ma5=103.0,
        ma15=100.0,
        ema_fast=101.0,
        ema_slow=100.0,
        ma_spread_pct=1.0,
        fast_slope=0.1,
        slow_slope=0.05,
        kc_upper=108.0,
        kc_middle=100.0,
        kc_lower=92.0,
        kc_middle_slope=0.02,
        atr=2.0,
        peak_roe=0.08,
        macro_trend="BULLISH",
        raw_df=df_long,
    )
    pos_long = {"side": "LONG", "entry_price": 100.0, "qty": 1.0, "leverage": 10, "peak_roe": 0.08}
    exit_l, reason_l, _ = ExitGate.evaluate(pos_long, snap_long)
    assert exit_l is True
    assert reason_l == "EARLY_PIVOT_PEAK_EXIT"

    # 建立鏡像空頭行情：左棒 100 -> 谷棒 90 (收陰碰下軌) -> 確認棒 98 (收陽突破實體中點 95，且 ma5 上拐)
    # entry_p = 100.0, valley_low = 90.0 (探底 10.0 = 5 ATR >= 0.45 ATR)
    df_short = pd.DataFrame([
        {"open": 102.0, "high": 103.0, "low": 98.0, "close": 99.0, "kc_upper": 108.0, "kc_lower": 92.0, "kc_middle": 100.0, "atr": 2.0, "ma5": 100.0},
        {"open": 99.0, "high": 100.0, "low": 90.0, "close": 91.0, "kc_upper": 108.0, "kc_lower": 92.0, "kc_middle": 100.0, "atr": 2.0, "ma5": 96.0},
        {"open": 94.0, "high": 99.0, "low": 93.5, "close": 98.0, "kc_upper": 108.0, "kc_lower": 92.0, "kc_middle": 100.0, "atr": 2.0, "ma5": 97.0},
    ])
    k_curr_s = df_short.iloc[-1].to_dict()
    k_prev_s = df_short.iloc[-2].to_dict()
    snap_short = MarketDataSnapshot(
        symbol="BTCUSDT",
        current_price=98.0,
        curr_k=k_curr_s,
        prev_k=k_prev_s,
        ma5=97.0,
        ma15=100.0,
        ema_fast=99.0,
        ema_slow=100.0,
        ma_spread_pct=1.0,
        fast_slope=-0.1,
        slow_slope=-0.05,
        kc_upper=108.0,
        kc_middle=100.0,
        kc_lower=92.0,
        kc_middle_slope=-0.02,
        atr=2.0,
        peak_roe=0.08,
        macro_trend="BEARISH",
        raw_df=df_short,
    )
    pos_short = {"side": "SHORT", "entry_price": 100.0, "qty": 1.0, "leverage": 10, "peak_roe": 0.08}
    exit_s, reason_s, _ = ExitGate.evaluate(pos_short, snap_short)
    assert exit_s is True
    assert reason_s == "EARLY_PIVOT_VALLEY_EXIT"
