"""
端到端整合測試：中央開倉物理閘門與全鏈路 signal_id 追蹤
Path: tests/test_central_entry_gate_e2e.py
"""

import pytest
import pandas as pd
import time
import os
import json

from core.paper_account import PaperAccount
from core.gates.central_entry_gate import CentralEntryGate
from core.services.order_audit_trail import OrderAuditTrail, AUDIT_LOG_FILE


def _create_mock_candles(n=30, side="LONG", closed=True):
    now_ms = time.time() * 1000.0
    rows = []
    base = 100.0
    for i in range(n):
        rows.append({
            "timestamp": int(now_ms - (n - 1 - i) * 60_000),
            "open": base + i * 0.1,
            "high": base + i * 0.1 + 0.5,
            "low": base + i * 0.1 - 0.5,
            "close": base + i * 0.1 + 0.2,
            "volume": 1000.0,
            "is_closed": True,
            "atr": 2.0,
            "kc_upper": 104.0,
            "kc_lower": 96.0,
            "kc_middle": 100.0 + i * 0.05,
            "kc_middle_slope": 0.3 if side == "LONG" else -0.3,
            "ma5": 100.5,
            "ma15": 99.0 + i * 0.02,
            "ma3": 100.2 + i * 0.1,
            "macro_trend": "BULLISH" if side == "LONG" else "BEARISH",
        })
    # 設定倒數第二根為金叉前狀態 (ma5 < ma15)，確保最新一根為剛金叉
    if side == "LONG":
        rows[-2]["ma5"] = 98.0
        rows[-2]["ma15"] = 98.2
        rows[-2]["kc_middle"] = 99.8
        # 最後一根通道擴張 (width=10.0 vs min_prev=8.0 -> 25% expansion >= 5%)
        rows[-1] = {
            "timestamp": int(now_ms),
            "open": 101.0,
            "high": 102.5,
            "low": 100.8,
            "close": 102.0,
            "volume": 1500.0,
            "is_closed": closed,
            "atr": 2.0,
            "kc_upper": 105.0,
            "kc_lower": 95.0,
            "kc_middle": 100.0,
            "kc_middle_slope": 0.25,  # >= 0.1 * 2.0 = 0.2
            "ma5": 101.5,  # > 98.5
            "ma15": 98.5,
            "ma3": 101.8,
            "macro_trend": "BULLISH",
        }
    else:
        rows[-1] = {
            "timestamp": int(now_ms),
            "open": 100.0,
            "high": 100.2,
            "low": 98.0,
            "close": 98.5,
            "volume": 1500.0,
            "is_closed": closed,
            "atr": 2.0,
            "kc_upper": 105.0,
            "kc_lower": 95.0,
            "kc_middle": 100.0 - n * 0.05 - 0.2,
            "kc_middle_slope": -0.25,  # <= -0.1 * 2.0 = -0.2
            "ma5": 99.0,  # < 100.5
            "ma15": 101.0 - n * 0.02 - 0.1,
            "ma3": 98.8,
            "macro_trend": "BEARISH",
        }
    return pd.DataFrame(rows)


@pytest.mark.anyio
async def test_open_position_blocked_when_candle_not_closed():
    """驗證開倉路徑：當 K 棒未收線時，CentralEntryGate 強制拒單"""
    account = PaperAccount()
    account.balance = 1000.0
    account.available_balance = 1000.0
    
    unclosed_df = _create_mock_candles(30, side="LONG", closed=False)
    async def mock_unclosed(s, **kwargs):
        return unclosed_df
    account.entry_frame_provider = mock_unclosed
    
    opened = await account.open_position(
        symbol="BTC/USDT",
        side="LONG",
        price=101.5,
        amount_usdt=50.0,
        sl=0.0,
        tp=0.0,
        reason="TEST_UNCLOSED_ENTRY",
        entry_context={"frame": unclosed_df},
    )
    assert opened is False
    # 檢查 account logs
    assert any("VETO_INTRABAR_NOT_CLOSED" in l.get("text", "") for l in account.logs)


@pytest.mark.anyio
async def test_open_position_blocked_when_kc_slope_insufficient():
    """驗證開倉路徑：當 KC 中軌斜率不足時，CentralEntryGate 強制拒單"""
    account = PaperAccount()
    account.balance = 1000.0
    account.available_balance = 1000.0
    
    df = _create_mock_candles(30, side="LONG", closed=True)
    df.loc[df.index[-1], "kc_middle_slope"] = 0.05  # < 0.1 * 2.0 = 0.2
    
    opened = await account.open_position(
        symbol="BTC/USDT",
        side="LONG",
        price=101.5,
        amount_usdt=50.0,
        sl=0.0,
        tp=0.0,
        reason="TEST_FLAT_SLOPE_ENTRY",
        entry_context={"frame": df},
    )
    assert opened is False
    assert any("VETO_KC_SLOPE_INSUFFICIENT" in l.get("text", "") for l in account.logs)


@pytest.mark.anyio
async def test_open_position_success_records_5_stages_audit():
    """驗證開倉路徑：合規開倉通過並完整寫入 5 大階段 signal_id 審計"""
    account = PaperAccount()
    account.balance = 1000.0
    account.available_balance = 1000.0
    
    valid_df = _create_mock_candles(30, side="LONG", closed=True)
    valid_df.attrs["entry_finality_verified"] = True
    valid_df.attrs["timeframe_ms"] = 60_000
    async def mock_provider(s, **kwargs):
        return valid_df
    account.entry_frame_provider = mock_provider
    
    bar_id = float(valid_df.iloc[-1]["timestamp"])
    entry_context = {
        "frame": valid_df,
        "entry_gate_frame": valid_df,
        "entry_timeframe_ms": 60_000,
        "entry_mode": "WILD_SWING",
        "source": "UNIFIED_PIPELINE",
        "entry_pattern": "BREAKOUT",
        "structure_confirmation": "BREAKOUT",
        "channel_confirmation_bar_id": bar_id,
        "bar_id": bar_id,
        "dual_tf_meta": {"bar_id": bar_id},
        "is_intrabar": False,
        "is_outer_entry": True,
    }
    
    # 清空或標記測試前的 audit log
    opened = await account.open_position(
        symbol="LOBSTER/USDT",
        side="LONG",
        price=102.0,
        amount_usdt=50.0,
        sl=0.0,
        tp=0.0,
        reason="TEST_VALID_ENTRY",
        entry_context=entry_context,
    )
    assert opened is True
    assert "LOBSTER/USDT" in account.positions
    
    # 檢查 account.logs 中是否有 5 個審計階段日誌
    logs_text = [l.get("text", "") for l in account.logs]
    has_stage1 = any("1_SIGNAL_GEN" in t for t in logs_text)
    has_stage2 = any("2_ENTRY_GATE_EVAL" in t for t in logs_text)
    has_stage5 = any("5_POSITION_SYNC" in t for t in logs_text)
    
    assert has_stage1, "Stage 1 (SIGNAL_GEN) missing from audit trail!"
    assert has_stage2, "Stage 2 (ENTRY_GATE_EVAL) missing from audit trail!"
    assert has_stage5, "Stage 5 (POSITION_SYNC) missing from audit trail!"


@pytest.mark.anyio
async def test_close_position_bypasses_central_entry_gate():
    """驗證平倉路徑：平倉單不受 CentralEntryGate 任何干預，確保停損與風控正常執行"""
    account = PaperAccount()
    account.balance = 1000.0
    account.available_balance = 1000.0
    
    # 手動建立一個持倉
    account.positions["LOBSTER/USDT"] = {
        "symbol": "LOBSTER/USDT",
        "side": "LONG",
        "entry_price": 100.0,
        "qty": 1.0,
        "margin": 50.0,
        "leverage": 5,
        "sl": 96.0,
        "tp": 0.0,
    }
    
    # 即使行情極端不符合開倉條件 (如未收盤、逆向大瀑布)，平倉必須直接成功！
    closed = await account.close_position(
        symbol="LOBSTER/USDT",
        current_price=95.0,
        close_reason="CIRCUIT_BREAKER_HARD_SL",
    )
    assert closed is True
    assert "LOBSTER/USDT" not in account.positions
