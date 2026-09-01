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


def test_every_production_entry_mode_is_registered_in_the_test_matrix():
    root = Path(__file__).resolve().parents[1]
    source = "\n".join(
        (root / relative).read_text(encoding="utf-8")
        for relative in (
            "core/engine.py",
            "core/strategy.py",
            "services/api.py",
        )
    )
    literal_modes = set(re.findall(
        r'"entry_mode"\s*:\s*"([A-Z][A-Z0-9_]+)"', source,
    ))
    literal_modes.update(re.findall(
        r'entry_mode\s*=\s*"([A-Z][A-Z0-9_]+)"', source,
    ))

    discovered = literal_modes | DYNAMIC_MODE_NAMES

    assert discovered == ACTIVE_TRADE_MODES


@pytest.mark.anyio
@pytest.mark.parametrize("entry_mode", sorted(ALL_TRADE_MODES))
@pytest.mark.parametrize("side", ["LONG", "SHORT"])
async def test_every_trade_mode_completes_open_reload_close_lifecycle(
    tmp_path, monkeypatch, entry_mode, side,
):
    state_file = tmp_path / f"{entry_mode.lower()}-{side.lower()}.json"
    monkeypatch.setattr(pa_module, "STATE_FILE", str(state_file))
    account = PaperAccount()
    close_price = 101.0 if side == "LONG" else 99.0
    sl = 95.0 if side == "LONG" else 105.0
    tp = 105.0 if side == "LONG" else 95.0

    opened = await account.open_position(
        "MODE/USDT", side, 100.0, 25.0, sl, tp,
        f"{entry_mode} lifecycle", leverage=1, signal_score=80,
        apply_slippage=False,
        entry_context={"entry_mode": entry_mode},
    )

    assert opened is True
    assert account.positions["MODE/USDT"]["side"] == side
    assert account.positions["MODE/USDT"]["entry_mode"] == entry_mode

    duplicate = await account.open_position(
        "MODE/USDT", "SHORT" if side == "LONG" else "LONG",
        100.0, 25.0, tp, sl, "duplicate must fail",
        leverage=1, signal_score=80, apply_slippage=False,
        entry_context={"entry_mode": entry_mode},
    )
    assert duplicate is False

    reloaded = PaperAccount()
    await reloaded.initialize()

    assert reloaded.positions["MODE/USDT"]["side"] == side
    assert reloaded.positions["MODE/USDT"]["entry_mode"] == entry_mode

    closed = await reloaded.close_position(
        "MODE/USDT", close_price, f"{entry_mode} lifecycle close",
        is_manual=True,
    )

    assert closed is True
    assert "MODE/USDT" not in reloaded.positions
    assert reloaded.trades[0]["action"] == f"CLOSE_{side}"
    assert any(trade["action"] == f"OPEN_{side}" for trade in reloaded.trades)


@pytest.mark.anyio
@pytest.mark.parametrize("entry_mode", sorted(ALL_TRADE_MODES))
@pytest.mark.parametrize("side", ["LONG", "SHORT"])
async def test_every_trade_mode_rejects_zero_amount(
    tmp_path, monkeypatch, entry_mode, side,
):
    monkeypatch.setattr(
        pa_module, "STATE_FILE",
        str(tmp_path / f"zero-{entry_mode.lower()}-{side.lower()}.json"),
    )
    account = PaperAccount()

    opened = await account.open_position(
        "MODE/USDT", side, 100.0, 0.0, 95.0, 105.0,
        "zero amount must fail", leverage=1, signal_score=80,
        apply_slippage=False,
        entry_context={"entry_mode": entry_mode},
    )

    assert opened is False
    assert account.positions == {}
