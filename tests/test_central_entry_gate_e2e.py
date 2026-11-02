"""
端到端整合測試：中央開倉物理閘門與全鏈路 signal_id 追蹤
Path: tests/test_central_entry_gate_e2e.py
"""

import pytest
import pandas as pd
import time
import json

from core.paper_account import PaperAccount
from core.services.entry_methods import KC_TWO_BAR_BREAKOUT, evaluate_entry_method
from test_entry_methods import method_context, post_close_market


@pytest.fixture(autouse=True)
def isolated_account_state(monkeypatch, tmp_path):
    monkeypatch.setattr("core.paper_account.STATE_FILE", str(tmp_path / "paper.json"))
    monkeypatch.setattr(
        "core.services.order_audit_trail.AUDIT_LOG_FILE",
        str(tmp_path / "audit.jsonl"),
    )


def _create_mock_candles(n=30, side="LONG", closed=True):
    now_ms = int(time.time() // 60) * 60_000 - 60_000
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
            "kc_middle": 99.0 + i * 0.02,
            "kc_middle_slope": 0.3 if side == "LONG" else -0.3,
            "ma5": 100.5,
            "ma15": 98.0 + i * 0.01,
            "ma3": 100.2 + i * 0.1,
            "macro_trend": "BULLISH" if side == "LONG" else "BEARISH",
        })
    # Keep unrelated direction/chop checks valid before exercising the target gate.
    if side == "LONG":
        rows[-2]["ma5"] = 100.5
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
            "ma5": 101.5,
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
    frame = pd.DataFrame(rows)
    frame.attrs.update(
        timeframe_ms=60_000, snapshot_ms=now_ms + 60_000,
        entry_finality_verified=closed,
    )
    return frame


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
@pytest.mark.parametrize("side", ["LONG", "SHORT"])
async def test_open_position_success_records_5_stages_audit(side, tmp_path):
    """驗證開倉路徑：合規開倉通過並完整寫入 5 大階段 signal_id 審計"""
    account = PaperAccount()
    account.balance = 1000.0
    account.available_balance = 1000.0
    
    valid_df = post_close_market(side)
    now_ms = int(time.time() // 60) * 60_000
    valid_df["timestamp"] += now_ms - 60_000 - int(valid_df.iloc[-1]["timestamp"])
    valid_df.loc[valid_df.index[-1], "volume"] = 150.0
    valid_df.attrs.update(
        entry_finality_verified=True, timeframe_ms=60_000, snapshot_ms=now_ms,
    )
    async def mock_provider(s, **kwargs):
        return valid_df
    account.entry_frame_provider = mock_provider
    
    bar_id = float(valid_df.iloc[-1]["timestamp"])
    price = float(valid_df.iloc[-1]["close"])
    signal = evaluate_entry_method(
        valid_df, symbol="LOBSTER/USDT", quote=price, expected_method=KC_TWO_BAR_BREAKOUT,
    )
    assert signal is not None
    entry_context = {
        **method_context(signal),
        "frame": valid_df,
        "entry_gate_frame": valid_df,
        "entry_timeframe_ms": 60_000,
        "entry_mode": "WILD_SWING",
        "source": "UNIFIED_PIPELINE",
        "entry_pattern": "BREAKOUT",
        "structure_confirmation": "CONFIRMED_2BAR_BREAKOUT",
        "channel_confirmation_bar_id": bar_id,
        "bar_id": bar_id,
        "dual_tf_meta": {"bar_id": bar_id},
        "is_intrabar": False,
        "is_outer_entry": True,
    }
    
    # 清空或標記測試前的 audit log
    opened = await account.open_position(
        symbol="LOBSTER/USDT",
        side=side,
        price=price,
        amount_usdt=50.0,
        sl=0.0,
        tp=0.0,
        reason="TEST_VALID_ENTRY",
        entry_context=entry_context,
    )
    assert opened is True
    assert "LOBSTER/USDT" in account.positions
    
    events = [json.loads(line) for line in (tmp_path / "audit.jsonl").read_text().splitlines()]
    assert [event["stage"] for event in events] == [
        "1_SIGNAL_GEN", "2_ENTRY_GATE_EVAL", "3_ORDER_DISPATCH",
        "4_EXCHANGE_RETURN", "5_POSITION_SYNC",
    ]
    assert len({event["signal_id"] for event in events}) == 1
    assert all(event["symbol"] == "LOBSTER/USDT" and event["side"] == side for event in events)
    assert events[1]["data"]["passed"] is True
    assert events[3]["data"]["filled_qty"] > 0
    assert events[3]["data"]["status"] == "PAPER_FILLED"
    assert events[3]["data"]["order_id"].startswith("PAPER_")
    assert account.positions["LOBSTER/USDT"]["side"] == side
    saved = json.loads((tmp_path / "paper.json").read_text())
    assert saved["positions"]["LOBSTER/USDT"]["side"] == side
    from core.services.exits.hard_stop_service import NEW_POSITION_LOSS_POLICY

    assert saved["positions"]["LOBSTER/USDT"]["position_loss_policy"] == NEW_POSITION_LOSS_POLICY
    assert saved["position_meta"]["LOBSTER/USDT"]["position_loss_policy"] == NEW_POSITION_LOSS_POLICY
    restored = PaperAccount()
    assert restored.positions["LOBSTER/USDT"]["position_loss_policy"] == NEW_POSITION_LOSS_POLICY


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
    
    # 真正手動平倉不受開倉門禁阻擋；數值硬止損不再是有效出口。
    closed = await account.close_position(
        symbol="LOBSTER/USDT",
        current_price=95.0,
        close_reason="手動平倉",
        is_manual=True,
    )
    assert closed is True
    assert "LOBSTER/USDT" not in account.positions
