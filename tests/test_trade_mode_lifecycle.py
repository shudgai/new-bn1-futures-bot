import re
from pathlib import Path

import pytest

import core.paper_account as pa_module
from core.paper_account import PaperAccount


ACTIVE_TRADE_MODES = {
    "BREAKOUT",
    "CHANNEL_SWING",
    "CURRENT_MAKER",
    "EXHAUSTION_SNIPER",
    "MA3_MA15_MARKET",
    "MA3_PIVOT",
    "MA5_BOTTOM_LIMIT",
    "MA5_CROSS_PIVOT",
    "MA5_REVERSAL",
    "MOMENTUM_CROSS",
    "PIVOT_TURN",
    "PULLBACK",
    "SHORT_FAST_ENTRY",
    "STRONG_LONG_BURST",
    "SUPPORT_PULLBACK",
}

# Pending orders created by an older version can still be loaded and managed.
COMPATIBILITY_TRADE_MODES = {"MA3_MA15_LIMIT"}
ALL_TRADE_MODES = ACTIVE_TRADE_MODES | COMPATIBILITY_TRADE_MODES

DYNAMIC_MODE_NAMES = {
    "MA5_BOTTOM_LIMIT",
    "MA5_CROSS_PIVOT",
    "MA5_REVERSAL",
}










@pytest.mark.anyio
async def test_market_entry_rejects_existing_pending_order(tmp_path, monkeypatch):
    monkeypatch.setattr(pa_module, "STATE_FILE", str(tmp_path / "pending.json"))
    account = PaperAccount()
    account.pending_limit_orders["MODE/USDT"] = {"side": "LONG"}

    opened = await account.open_position(
        "MODE/USDT", "LONG", 100.0, 25.0, 95.0, 105.0, "market conflict",
        leverage=1, signal_score=80, apply_slippage=False,
        entry_context={"entry_mode": "BREAKOUT"},
    )

    assert opened is False
    assert account.positions == {}
    assert "MODE/USDT" in account.pending_limit_orders








