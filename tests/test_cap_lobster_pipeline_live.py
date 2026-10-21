import pytest
import pandas as pd
from core.services.data_pipeline import DataPipeline, MarketDataSnapshot
from core.gates.unified_pipeline_gates import EntryGate
from core.services.execution_manager import ExecutionManager


class MockAccount:
    def __init__(self, balance=100.0):
        self.balance = balance
        self.positions = {}
        self.closing_lock = set()
        self.opened_records = []

    def get_wallet_balance(self):
        return self.balance

    def get_available_balance(self):
        return self.balance

    async def open_position(
        self, symbol, side, price, amount_usdt, sl, tp, reason,
        atr=0.0, leverage=None, signal_score=None, entry_context=None,
    ):
        self.opened_records.append({
            "symbol": symbol,
            "side": side,
            "price": price,
            "amount_usdt": amount_usdt,
            "reason": reason,
            "entry_context": entry_context,
        })
        self.positions[symbol] = {
            "symbol": symbol,
            "side": side,
            "entry_price": price,
            "amount": amount_usdt,
            "qty": (amount_usdt * (leverage or 5)) / price,
        }
        return True


def test_cap_downward_breakdown_opens_short():
    """驗證 CAP/USDT 盤整結束向下破軌主跌浪起點，三層流水線精確判定做空並成功開倉"""
    import asyncio
    async def _test():
        account = MockAccount()
        # 前 55 根在 0.0650 盤整，第 56-60 根跌破下軌（在 1 ATR 內）
        closes = [0.0650] * 55 + [0.0649, 0.0648, 0.0647, 0.0646, 0.0645]
        df = pd.DataFrame({
            "timestamp": [1700000000 + i * 60 for i in range(60)],
            "open": [c + 0.00005 for c in closes],
            "high": [c + 0.00006 for c in closes],
            "low": [c - 0.00002 for c in closes],
            "close": closes,
            "volume": [1000.0 for _ in range(60)],
        })
        quote = closes[-1]
        res = await ExecutionManager.process_symbol_pipeline(
            symbol="CAP/USDT",
            engine_or_account=account,
            klines_df=df,
            quote=quote,
        )
        assert res["status"] == "ENTRY_EXECUTED"
        assert res["side"] == "SHORT"
        assert res["success"] is True
        assert len(account.opened_records) == 1
        assert account.opened_records[0]["symbol"] == "CAP/USDT"
        assert account.opened_records[0]["side"] == "SHORT"
    asyncio.run(_test())


def test_lobster_upward_breakout_opens_long():
    """驗證 龙虾/USDT 盤整結束向上破軌主升浪起點，三層流水線精確判定做多並成功開倉"""
    import asyncio
    async def _test():
        account = MockAccount()
        # 前 55 根在 0.0210 盤整，第 56-60 根衝破上軌（在 1 ATR 內）
        closes = [0.0210] * 55 + [0.0211, 0.0212, 0.0213, 0.0214, 0.0215]
        df = pd.DataFrame({
            "timestamp": [1700000000 + i * 60 for i in range(60)],
            "open": [c - 0.00005 for c in closes],
            "high": [c + 0.00002 for c in closes],
            "low": [c - 0.00006 for c in closes],
            "close": closes,
            "volume": [1000.0 for _ in range(60)],
        })
        quote = closes[-1]
        res = await ExecutionManager.process_symbol_pipeline(
            symbol="龙虾/USDT",
            engine_or_account=account,
            klines_df=df,
            quote=quote,
        )
        assert res["status"] == "ENTRY_EXECUTED"
        assert res["side"] == "LONG"
        assert res["success"] is True
        assert len(account.opened_records) == 1
        assert account.opened_records[0]["symbol"] == "龙虾/USDT"
        assert account.opened_records[0]["side"] == "LONG"
    asyncio.run(_test())
