"""
tests/test_unified_pipeline.py
==============================
驗證極簡「三層流水線架構」：
1. 數據層 (DataPipeline): 純數值物件計算
2. 裁決層 (ExitGate & EntryGate): 純數學裁決官
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
    assert snapshot.kc_middle_slope > 0
    assert snapshot.peak_roe >= 0.20


def test_exit_gate_veto_hold_on_intact_trend():
    """測試出場裁決：趨勢結構未破時強制 VETO_HOLD 抱牢"""
    df = _generate_sample_df(30, trend="UP")
    snapshot = DataPipeline.calculate_snapshot(
        symbol="LOBSTER/USDT",
        df=df,
        current_price=120.0,
        position={"side": "LONG", "entry_price": 115.0, "peak_roe": 0.05},
    )
    should_exit, reason, meta = ExitGate.evaluate(
        pos={"side": "LONG", "entry_price": 115.0},
        market_data=snapshot,
    )
    assert not should_exit
    assert reason == "VETO_HOLD"
    assert "強制抱牢" in meta.get("cause", "")


def test_exit_gate_profit_lock_on_deep_giveback():
    """測試出場裁決：高額利潤大幅回撤時觸發 PROFIT_LOCK 階梯鎖利"""
    df = _generate_sample_df(30, trend="UP")
    snapshot = DataPipeline.calculate_snapshot(
        symbol="LOBSTER/USDT",
        df=df,
        current_price=110.0,
    )
    # entry=100, 曾獲利至 125 (+25%), 當前跌到 110 (+10%), 回撤達 15% (超過 30% of peak)
    pos = {"side": "LONG", "entry_price": 100.0, "peak_roe": 0.25}
    should_exit, reason, meta = ExitGate.evaluate(
        pos=pos,
        market_data=snapshot,
        current_price=110.0,
    )
    assert should_exit
    assert reason == "PROFIT_LOCK"
    assert "階梯鎖利" in meta.get("cause", "")


def test_exit_gate_true_peak_three_point_pivot():
    """測試出場裁決：純數學三點樞紐確立時觸發 TRUE_PEAK"""
    df = _generate_sample_df(30, trend="UP")
    # 人工構造最後三棒形成確定性頂部樞紐
    df.loc[df.index[-3], ["open", "high", "low", "close"]] = [118.0, 120.0, 117.0, 119.0]
    df.loc[df.index[-2], ["open", "high", "low", "close"]] = [119.0, 125.0, 118.5, 124.0] # 峰頂
    df.loc[df.index[-1], ["open", "high", "low", "close"]] = [124.0, 124.5, 119.0, 120.0] # 跌破中點
    snapshot = DataPipeline.calculate_snapshot("LOBSTER/USDT", df, current_price=120.0)

    pos = {"side": "LONG", "entry_price": 110.0, "peak_roe": 0.12}
    should_exit, reason, meta = ExitGate.evaluate(pos=pos, market_data=snapshot)
    assert should_exit
    assert reason == "TRUE_PEAK"
    assert "純數學真峰頂確立" in meta.get("cause", "")


def test_entry_gate_fast_track_breakout():
    """測試進場裁決：同色K強勢破軌快車道放行"""
    df = _generate_sample_df(30, trend="UP")
    snapshot = DataPipeline.calculate_snapshot("LOBSTER/USDT", df, current_price=130.0)
    should_entry, reason, meta = EntryGate.evaluate(
        signal_type="BREAKOUT_LONG",
        market_data=snapshot,
    )
    assert should_entry
    assert reason in ("FAST_TRACK_CONTINUATION", "STANDARD_TREND_ENTRY")


def test_entry_gate_veto_on_long_wick():
    """測試進場裁決：避震門禁（長影線一票否決）"""
    df = _generate_sample_df(30, trend="UP")
    # 最新 K 棒留巨大上影線
    df.loc[df.index[-1], ["open", "high", "low", "close"]] = [120.0, 128.0, 119.5, 120.5]
    snapshot = DataPipeline.calculate_snapshot("LOBSTER/USDT", df, current_price=120.5)
    should_entry, reason, meta = EntryGate.evaluate(
        signal_type="BREAKOUT_LONG",
        market_data=snapshot,
    )
    assert not should_entry
    assert reason == "VETO_WICK_REJECTION"


def test_entry_gate_veto_on_chop_convergence():
    """測試進場裁決：結構門禁（均線走平/黏合一票否決）"""
    df = _generate_sample_df(30, trend="CHOP")
    snapshot = DataPipeline.calculate_snapshot("LOBSTER/USDT", df, current_price=100.0)
    # 人工模擬黏合走平
    snapshot.ma5 = 100.0
    snapshot.ma15 = 100.01
    snapshot.kc_middle_slope = 0.0
    should_entry, reason, meta = EntryGate.evaluate(
        signal_type="BREAKOUT_LONG",
        market_data=snapshot,
    )
    assert not should_entry
    assert reason == "VETO_CHOP_OR_CONVERGENCE"


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
