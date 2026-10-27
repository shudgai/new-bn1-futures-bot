"""
tests/test_agile_dual_entry_triggers.py
======================================
驗證敏捷雙重開倉觸發點 (Dual Agile Entry Triggers) 與平多反手開空機制：
1. 多頭觸發點 A (MA3 穿中軌即開 ENTRY_LONG_CROSS_MID)
2. 多頭觸發點 B (實體衝破 KC 上軌即開 ENTRY_LONG_BREAKOUT)
3. 空頭觸發點 A (MA3 跌破中軌即開 ENTRY_SHORT_CROSS_MID)
4. 空頭觸發點 B (實體跌破 KC 下軌即開 ENTRY_SHORT_BREAKDOWN)
5. 底層物理閘門 CentralEntryGate 對 direction_override 豁免落後斜率與空間限制
6. 平多後滿足做空條件，立即反手開空 (豁免冷卻期)
"""
import time
import pytest
import pandas as pd
import numpy as np

from core.gates.central_entry_gate import CentralEntryGate
from core.services.execution_manager import ExecutionManager


class DummyAccount:
    def __init__(self):
        self.positions = {}
        self.closing_lock = set()
        self.balance = 1000.0
        self.available_balance = 1000.0
        self.entry_filter_last = {}

    def get_wallet_balance(self):
        return 1000.0

    def get_available_balance(self):
        return 1000.0

    def save_state(self, *args, **kwargs):
        pass


def _create_base_df(n=35, base_p=100.0):
    data = []
    for i in range(n):
        p = base_p + np.sin(i / 3.0) * 0.1
        o = p - 0.1
        c = p + 0.1
        data.append({
            "timestamp": (1700000000 + i * 60) * 1000,
            "open": o,
            "high": p + 0.3,
            "low": p - 0.3,
            "close": c,
            "volume": 100.0,
            "is_closed": True,
            "atr": 0.5,
            "kc_middle": base_p,
            "kc_upper": base_p + 1.0,
            "kc_lower": base_p - 1.0,
        })
    return pd.DataFrame(data)


def test_central_entry_gate_exempts_slope_and_room_on_direction_override():
    """驗證 CentralEntryGate 收到 direction_override 時豁免落後的 KC 斜率與通道空間限制"""
    df = _create_base_df(35, 100.0)
    df.loc[df.index[-1], "kc_middle_slope"] = -0.2

    # 當沒有 direction_override 時，做多必定被阻斷
    df_no_override = df.copy()
    df_no_override.attrs = {}
    res_no = CentralEntryGate.verify_and_authorize(
        symbol="BTC/USDT",
        side="LONG",
        price=102.0,
        klines_or_snapshot=df_no_override,
        source="UNIFIED_PIPELINE|BREAKOUT",
    )
    assert not res_no.passed

    # 當有 direction_override 且 K 棒與 MA3 向上時，斜率門檻被順暢豁免
    df.loc[df.index[-1], "close"] = 103.0
    df.loc[df.index[-1], "open"] = 101.0
    df.loc[df.index[-1], "high"] = 103.2
    df.loc[df.index[-1], "low"] = 100.9
    df.loc[df.index[-1], "ma3"] = 102.5
    df.loc[df.index[-2], "ma3"] = 101.0
    df.loc[df.index[-1], "ma5"] = 101.8
    df.loc[df.index[-1], "ma15"] = 100.5
    df.loc[df.index[-1], "kc_middle"] = 100.0
    df.loc[df.index[-1], "kc_middle_slope"] = -0.15 # 模擬落後的負斜率
    df.attrs["direction_override"] = True

    res_override = CentralEntryGate.verify_and_authorize(
        symbol="BTC/USDT",
        side="LONG",
        price=103.0,
        klines_or_snapshot=df,
        source="UNIFIED_PIPELINE|AGILE_ENTRY_LONG_CROSS_MID",
    )
    assert res_override.passed is True
    assert res_override.checks["kc_middle_slope"]["status"] == "PASS"
    assert res_override.checks["kc_middle_slope"]["required_slope"] == "OVERRIDE"


@pytest.mark.anyio
async def test_execution_manager_agile_trigger_long_breakout():
    """驗證 ExecutionManager 在大綠 K 衝破 KC 上軌時第一時間觸發 ENTRY_LONG_BREAKOUT 開多"""
    df = _create_base_df(35, 100.0)
    # 最後一根大紅陽線衝破上軌
    df.loc[df.index[-1], "open"] = 100.5
    df.loc[df.index[-1], "close"] = 103.5
    df.loc[df.index[-1], "high"] = 103.8
    df.loc[df.index[-1], "low"] = 100.4

    account = DummyAccount()

    opened_calls = []
    async def mock_open_position(engine_or_account, symbol, side, price, reason, meta=None):
        opened_calls.append({"side": side, "price": price, "reason": reason, "meta": meta})
        return True

    original_open = ExecutionManager.open_position
    ExecutionManager.open_position = mock_open_position
    try:
        ExecutionManager._last_trade_bar_time.clear()
        ExecutionManager._last_exit_record.clear()

        result = await ExecutionManager.process_symbol_pipeline(
            symbol="BTC/USDT",
            engine_or_account=account,
            klines_df=df,
            quote=103.5,
        )
        assert result.get("status") == "ENTRY_EXECUTED"
        assert result.get("side") == "LONG"
        assert len(opened_calls) == 1
        call = opened_calls[0]
        assert call["side"] == "LONG"
        assert call["meta"].get("direction_override") is True
        assert call["meta"].get("is_agile_trigger") is True
    finally:
        ExecutionManager.open_position = original_open


@pytest.mark.anyio
async def test_execution_manager_agile_trigger_long_cross_mid():
    """驗證 ExecutionManager 在 MA3 穿中軌時第一時間觸發 ENTRY_LONG_CROSS_MID 開多"""
    df = _create_base_df(35, 100.0)
    # 讓前一根在 99.5，最後一根放量穿過中軌站上 100.8
    df.loc[df.index[-2], "open"] = 99.8
    df.loc[df.index[-2], "close"] = 99.5
    df.loc[df.index[-2], "high"] = 99.9
    df.loc[df.index[-2], "low"] = 99.3

    df.loc[df.index[-1], "open"] = 99.6
    df.loc[df.index[-1], "close"] = 100.95
    df.loc[df.index[-1], "high"] = 101.0
    df.loc[df.index[-1], "low"] = 99.5

    account = DummyAccount()

    opened_calls = []
    async def mock_open_position(engine_or_account, symbol, side, price, reason, meta=None):
        opened_calls.append({"side": side, "price": price, "reason": reason, "meta": meta})
        return True

    original_open = ExecutionManager.open_position
    ExecutionManager.open_position = mock_open_position
    try:
        ExecutionManager._last_trade_bar_time.clear()
        ExecutionManager._last_exit_record.clear()

        result = await ExecutionManager.process_symbol_pipeline(
            symbol="BTC/USDT",
            engine_or_account=account,
            klines_df=df,
            quote=100.95,
        )
        assert result.get("status") == "ENTRY_EXECUTED"
        assert result.get("side") == "LONG"
        assert len(opened_calls) == 1
        call = opened_calls[0]
        assert call["side"] == "LONG"
        assert "CROSS_MID" in call["reason"] or "CROSS_MID" in call["meta"].get("candidate_signal_type", "")
        assert call["meta"].get("direction_override") is True
        assert call["meta"].get("is_agile_trigger") is True
    finally:
        ExecutionManager.open_position = original_open


@pytest.mark.anyio
async def test_reverse_position_after_close_long():
    """驗證平多後若滿足開空條件，立即豁免冷卻期反手開空"""
    df = _create_base_df(35, 100.0)
    # 最後一根大黑陰線向下擊穿 KC 下軌
    df.loc[df.index[-1], "open"] = 99.5
    df.loc[df.index[-1], "close"] = 96.5
    df.loc[df.index[-1], "high"] = 99.6
    df.loc[df.index[-1], "low"] = 96.2

    account = DummyAccount()

    # 模擬剛平多倉（5 秒前平多）
    acc_key = ExecutionManager._get_account_key(account, "BTC/USDT")
    ExecutionManager._last_exit_record[acc_key] = {
        "side": "LONG",
        "timestamp": time.time() - 5.0,  # 距平多僅 5 秒 (< 180 秒冷卻)
        "price": 100.0,
        "exit_price": 100.0,
    }

    opened_calls = []
    async def mock_open_position(engine_or_account, symbol, side, price, reason, meta=None):
        opened_calls.append({"side": side, "price": price, "reason": reason, "meta": meta})
        return True

    original_open = ExecutionManager.open_position
    ExecutionManager.open_position = mock_open_position
    try:
        ExecutionManager._last_trade_bar_time.clear()

        result = await ExecutionManager.process_symbol_pipeline(
            symbol="BTC/USDT",
            engine_or_account=account,
            klines_df=df,
            quote=96.5,
        )
        assert result.get("status") == "ENTRY_EXECUTED"
        assert result.get("side") == "SHORT"
        assert len(opened_calls) == 1
        assert opened_calls[0]["side"] == "SHORT"
    finally:
        ExecutionManager.open_position = original_open


def test_central_entry_gate_v_shape_recovery_momentum_override():
    """驗證 CentralEntryGate 即使未顯式帶有 direction_override，只要符合 V 轉條件也自動豁免負中軌斜率"""
    rows = []
    for i in range(25):
        kc_m = 101.0 - i * (1.0 / 24.0)  # 真實下跌趨勢：從 101.0 跌至 100.0
        rows.append({
            "timestamp": 1700000000000 + i * 60_000,
            "open": kc_m - 0.5,
            "high": kc_m + 0.2,
            "low": kc_m - 0.8,
            "close": kc_m - 0.4,
            "volume": 1000.0,
            "is_closed": True,
            "atr": 2.0,
            "kc_upper": kc_m + 4.0,
            "kc_lower": kc_m - 4.0,
            "kc_middle": kc_m,
            "kc_middle_slope": -0.25,
            "ma5": kc_m - 0.2,
            "ma3": kc_m - 0.3,
            "ma15": kc_m + 0.5,
            "macro_trend": "NEUTRAL",
        })
    # 最後一根：底部 V 轉強勢大陽線收盤 100.8 站上 KC 中軌 (100.0)，MA3 100.5 穿過中軌
    rows[-1] = {
        "timestamp": 1700000000000 + 24 * 60_000,
        "open": 99.6,
        "high": 101.0,
        "low": 99.5,
        "close": 100.8,  # 實體 1.2 >= 0.4 * 2.0 = 0.8
        "volume": 1500.0,
        "is_closed": True,
        "atr": 2.0,
        "kc_upper": 104.0,
        "kc_lower": 96.0,
        "kc_middle": 100.0,
        "kc_middle_slope": -0.25,  # 前 20 根下跌導致的負斜率
        "ma5": 100.2,
        "ma3": 100.5,  # prev is 99.4, delta > 0 且站上中軌
        "ma15": 99.8,
        "macro_trend": "NEUTRAL",
    }
    df = pd.DataFrame(rows)
    df.attrs = {}

    res = CentralEntryGate.verify_and_authorize(
        symbol="BTC/USDT",
        side="LONG",
        price=100.8,
        klines_or_snapshot=df,
        source="UNIFIED_PIPELINE|ENTRY_LONG_CROSS_MID",
    )
    assert res.passed is True
    assert res.checks["kc_middle_slope"]["status"] == "PASS"
    assert res.checks["kc_middle_slope"]["required_slope"] == "V_SHAPE_OVERRIDE"


def test_central_entry_gate_v_shape_breakdown_momentum_override():
    """驗證 CentralEntryGate 空頭頂部 V 轉大黑 K 跌破中軌時自動豁免正中軌斜率"""
    rows = []
    for i in range(25):
        kc_m = 99.0 + i * (1.0 / 24.0)  # 真實上漲趨勢：從 99.0 漲至 100.0
        rows.append({
            "timestamp": 1700000000000 + i * 60_000,
            "open": kc_m + 0.4,
            "high": kc_m + 0.8,
            "low": kc_m - 0.2,
            "close": kc_m + 0.5,
            "volume": 1000.0,
            "is_closed": True,
            "atr": 2.0,
            "kc_upper": kc_m + 4.0,
            "kc_lower": kc_m - 4.0,
            "kc_middle": kc_m,
            "kc_middle_slope": 0.25,
            "ma5": kc_m + 0.2,
            "ma3": kc_m + 0.3,
            "ma15": kc_m - 0.5,
            "macro_trend": "NEUTRAL",
        })
    # 最後一根：頂部 V 轉強勢大陰線收盤 99.2 跌破 KC 中軌 (100.0)，MA3 99.5 跌破中軌
    rows[-1] = {
        "timestamp": 1700000000000 + 24 * 60_000,
        "open": 100.4,
        "high": 100.5,
        "low": 99.0,
        "close": 99.2,  # 實體 1.2 >= 0.4 * 2.0 = 0.8
        "volume": 1500.0,
        "is_closed": True,
        "atr": 2.0,
        "kc_upper": 104.0,
        "kc_lower": 96.0,
        "kc_middle": 100.0,
        "kc_middle_slope": 0.25,  # 前 20 根上漲導致的正斜率
        "ma5": 99.8,
        "ma3": 99.5,  # prev is 100.6, delta < 0 且跌破中軌
        "ma15": 100.2,
        "macro_trend": "NEUTRAL",
    }
    df = pd.DataFrame(rows)
    df.attrs = {}

    res = CentralEntryGate.verify_and_authorize(
        symbol="BTC/USDT",
        side="SHORT",
        price=99.2,
        klines_or_snapshot=df,
        source="UNIFIED_PIPELINE|ENTRY_SHORT_CROSS_MID",
    )
    assert res.passed is True
    assert res.checks["kc_middle_slope"]["status"] == "PASS"
    assert res.checks["kc_middle_slope"]["required_slope"] == "V_SHAPE_OVERRIDE"

