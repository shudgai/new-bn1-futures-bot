"""
tests/test_unified_pipeline.py
==============================
驗證極簡「三層流水線架構」：
1. 數據層 (DataPipeline): 純數值物件計算 (EMA15, EMA40, ma_spread_pct)
2. 裁決層 (ExitGate & EntryGate): 依 2026/10/21 盤面覆盤精確門禁
3. 執行層 (ExecutionManager): 單一通道執行
"""
import pytest
import pandas as pd
import numpy as np

from core.services.data_pipeline import DataPipeline, MarketDataSnapshot
from core.gates.unified_pipeline_gates import ExitGate, EntryGate
from core.services.execution_manager import ExecutionManager


def _generate_sample_df(n=50, trend="UP"):
    data = []
    base = 100.0
    for i in range(n):
        if trend == "UP":
            base += 0.5 + (i * 0.05)
        elif trend == "DOWN":
            base -= 0.5 + (i * 0.05)
        else:
            base += 0.05 if i % 2 == 0 else -0.05
        high = base + 0.3
        low = base - 0.3
        open_p = base - 0.1 if trend == "UP" else base + 0.1
        close_p = base + 0.1 if trend == "UP" else base - 0.1
        data.append({
            "timestamp": 1700000000000 + i * 60000,
            "open": open_p,
            "high": high,
            "low": low,
            "close": close_p,
            "volume": 100.0 + i,
        })
    return pd.DataFrame(data)


def test_data_pipeline_snapshot_calculation():
    """測試數據層能回傳純數值物件，不帶任何決策"""
    df = _generate_sample_df(30, trend="UP")
    snapshot = DataPipeline.calculate_snapshot(
        symbol="LOBSTER/USDT",
        df=df,
        current_price=125.0,
        position={"side": "LONG", "entry_price": 100.0, "peak_roe": 0.20},
    )
    assert snapshot is not None
    assert isinstance(snapshot, MarketDataSnapshot)
    assert snapshot.symbol == "LOBSTER/USDT"
    assert snapshot.current_price == 125.0
    assert snapshot.atr > 0
    assert snapshot.kc_upper > snapshot.kc_middle > snapshot.kc_lower
    assert snapshot.ema_fast > 0
    assert snapshot.ema_slow > 0
    assert snapshot.peak_roe >= 0.20


def test_entry_gate_veto_over_extension():
    """測試進場門禁 1-A：距 EMA40 乖離過大一票否決 (|close - ema_slow| > 0.8*ATR)"""
    df = _generate_sample_df(30, trend="UP")
    snapshot = DataPipeline.calculate_snapshot("LOBSTER/USDT", df, current_price=135.0)
    # 人工拉大乖離
    snapshot.ema_slow = 100.0
    snapshot.atr = 5.0
    snapshot.curr_k["close"] = 135.0  # diff=35 > 0.8*5=4.0
    should_entry, reason, meta = EntryGate.evaluate("BREAKOUT_LONG", snapshot)
    assert not should_entry
    assert reason == "VETO_OVER_EXTENSION_FROM_SLOW_MA"


def test_entry_gate_veto_long_upper_wick():
    """測試進場門禁 1-B：長上影線一票否決 (防 09:12 買在急衝頂部)"""
    df = _generate_sample_df(30, trend="UP")
    snapshot = DataPipeline.calculate_snapshot("LOBSTER/USDT", df, current_price=110.0)
    snapshot.ema_slow = 109.8
    snapshot.atr = 1.0
    # 衝高留長上影線
    snapshot.curr_k = {"open": 109.9, "high": 111.0, "low": 109.8, "close": 110.0}
    should_entry, reason, meta = EntryGate.evaluate("BREAKOUT_LONG", snapshot)
    assert not should_entry
    assert reason == "VETO_WICK_REJECTION"


def test_entry_gate_0945_breakout_main_wave_start():
    """測試進場門禁 2：09:45 實體紅K衝破KC上軌且發散放行主升浪起點"""
    df = _generate_sample_df(30, trend="UP")
    snapshot = DataPipeline.calculate_snapshot("LOBSTER/USDT", df, current_price=125.0)
    snapshot.kc_upper = 124.0
    snapshot.ema_fast = 123.0
    snapshot.ema_slow = 122.0
    snapshot.ma_spread_pct = 0.15
    snapshot.atr = 3.0
    # 飽滿紅陽線突破上軌
    snapshot.curr_k = {"open": 123.5, "high": 125.2, "low": 123.4, "close": 125.0}
    should_entry, reason, meta = EntryGate.evaluate("BREAKOUT_LONG", snapshot)
    assert should_entry
    assert reason == "BREAKOUT_MAIN_WAVE_START"


def test_entry_gate_1000_pullback_reclaim_continuation():
    """測試進場門禁 3：10:00 回踩均線帶收紅K延續開倉放行 (第二波主升段)"""
    df = _generate_sample_df(30, trend="UP")
    snapshot = DataPipeline.calculate_snapshot("LOBSTER/USDT", df, current_price=121.0)
    snapshot.ema_fast = 120.0
    snapshot.ema_slow = 118.0
    snapshot.kc_upper = 124.0
    snapshot.atr = 3.0
    # 前一根回踩至 119.5 (在 EMA15~40 之間)，當前棒收紅站上 EMA15 (121.0 > 120.0)
    snapshot.prev_k = {"open": 121.0, "high": 121.5, "low": 119.5, "close": 120.0}
    snapshot.curr_k = {"open": 120.0, "high": 121.2, "low": 119.8, "close": 121.0}
    should_entry, reason, meta = EntryGate.evaluate("BREAKOUT_LONG", snapshot)
    assert should_entry
    assert reason == "PULLBACK_RECLAIM_CONTINUATION"


def test_exit_gate_hold_through_consolidation():
    """測試出場門禁 1：盤整黏合嚴禁平多 (未達連續兩根收破 EMA40 強制 VETO_HOLD)"""
    df = _generate_sample_df(30, trend="UP")
    # 設定最近兩根為在 EMA40 (118.0) 之上的正常盤整洗盤小K線 (非大黑吞噬)
    df.loc[df.index[-2], ["open", "high", "low", "close"]] = [118.1, 118.6, 118.0, 118.3]
    df.loc[df.index[-1], ["open", "high", "low", "close"]] = [118.6, 118.8, 118.3, 118.5]
    snapshot = DataPipeline.calculate_snapshot("LOBSTER/USDT", df, current_price=118.5)
    snapshot.ema_slow = 118.0
    # 價格在 EMA40 附近洗盤但未連續兩根破位
    pos = {"side": "LONG", "entry_price": 115.0, "peak_roe": 0.05}
    should_exit, reason, meta = ExitGate.evaluate(pos, snapshot, current_price=118.5)
    assert not should_exit
    assert reason == "VETO_HOLD"
    assert "未達連續兩根收破EMA40慢線" in meta.get("cause", "")


def test_exit_gate_10pct_lock_7pct_profit():
    """測試出場門禁 2：達標 10% 鎖 7% 階梯保護"""
    df = _generate_sample_df(30, trend="UP")
    snapshot = DataPipeline.calculate_snapshot("LOBSTER/USDT", df, current_price=106.0)
    # entry=100, 最高達 112 (+12%), 當前跌到 106 (+6%), 跌破 70% 保證線 (12% * 0.7 = 8.4%)
    pos = {"side": "LONG", "entry_price": 100.0, "peak_roe": 0.12}
    should_exit, reason, meta = ExitGate.evaluate(pos, snapshot, current_price=106.0)
    assert should_exit
    assert reason == "PROFIT_LOCK"
    assert "10%鎖7%" in meta.get("cause", "")


def test_execution_manager_pipeline_flow():
    """測試執行層單一通道標準流水線協同"""
    import asyncio

    async def _runner():
        class MockAccount:
            def __init__(self):
                self.positions = {}
                self.closed = []
                self.opened = []

            async def close_position(self, symbol, current_price, close_reason):
                self.closed.append((symbol, current_price, close_reason))
                self.positions.pop(symbol, None)
                return True

            async def open_position(self, symbol, side, price, reason):
                self.opened.append((symbol, side, price, reason))
                self.positions[symbol] = {"side": side, "entry_price": price, "qty": 1.0}
                return True

        account = MockAccount()
        df = _generate_sample_df(30, trend="UP")

        # 1. 無持倉時問 EntryGate
        res_entry = await ExecutionManager.process_symbol_pipeline(
            symbol="CAP/USDT",
            engine_or_account=account,
            klines_df=df,
            quote=125.0,
        )
        assert res_entry["status"] in ("ENTRY_EXECUTED", "VETO", "IDLE")

        # 2. 有持倉且趨勢良好時問 ExitGate -> HOLD
        account.positions["CAP/USDT"] = {"side": "LONG", "entry_price": 120.0, "qty": 1.0}
        res_exit = await ExecutionManager.process_symbol_pipeline(
            symbol="CAP/USDT",
            engine_or_account=account,
            klines_df=df,
            quote=125.0,
        )
        assert res_exit["status"] == "HOLD"
        assert len(account.closed) == 0

    asyncio.run(_runner())
