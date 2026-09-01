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


@pytest.mark.anyio
@pytest.mark.parametrize("entry_mode", sorted(ALL_TRADE_MODES))
@pytest.mark.parametrize("side", ["LONG", "SHORT"])
async def test_every_trade_mode_rejects_same_side_duplicate(
    tmp_path, monkeypatch, entry_mode, side,
):
    monkeypatch.setattr(
        pa_module, "STATE_FILE",
        str(tmp_path / f"duplicate-{entry_mode.lower()}-{side.lower()}.json"),
    )
    account = PaperAccount()
    sl = 95.0 if side == "LONG" else 105.0
    tp = 105.0 if side == "LONG" else 95.0
    assert await account.open_position(
        "MODE/USDT", side, 100.0, 25.0, sl, tp, "first",
        leverage=1, signal_score=80, apply_slippage=False,
        entry_context={"entry_mode": entry_mode},
    )
    qty_before = account.positions["MODE/USDT"]["qty"]

    duplicate = await account.open_position(
        "MODE/USDT", side, 100.0, 25.0, sl, tp, "duplicate",
        leverage=1, signal_score=80, apply_slippage=False,
        entry_context={"entry_mode": entry_mode},
    )

    assert duplicate is False
    assert account.positions["MODE/USDT"]["qty"] == qty_before
    assert len([t for t in account.trades if t["action"] == f"OPEN_{side}"]) == 1


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


@pytest.mark.anyio
async def test_explicit_enabled_dca_is_the_only_allowed_top_up(tmp_path, monkeypatch):
    monkeypatch.setattr(pa_module, "STATE_FILE", str(tmp_path / "dca.json"))
    monkeypatch.setattr(pa_module, "ENABLE_DCA_LIMIT", True)
    account = PaperAccount()
    assert await account.open_position(
        "MODE/USDT", "LONG", 100.0, 30.0, 95.0, 105.0, "DCA stage 1",
        leverage=1, signal_score=80, apply_slippage=False,
        entry_context={"entry_mode": "BREAKOUT", "dca_stage": 1},
    )
    qty_before = account.positions["MODE/USDT"]["qty"]

    topped_up = await account.open_position(
        "MODE/USDT", "LONG", 99.0, 10.0, 94.0, 104.0, "DCA stage 2",
        leverage=1, signal_score=80, apply_slippage=False,
        entry_context={"entry_mode": "BREAKOUT", "dca_stage": 2},
    )

    assert topped_up is True
    assert account.positions["MODE/USDT"]["qty"] > qty_before


@pytest.mark.anyio
@pytest.mark.parametrize("side", ["LONG", "SHORT"])
@pytest.mark.parametrize("fraction", [0.0, 1.0, 1.2])
async def test_partial_close_rejects_non_partial_fraction(
    tmp_path, monkeypatch, side, fraction,
):
    monkeypatch.setattr(
        pa_module, "STATE_FILE", str(tmp_path / f"partial-{side}-{fraction}.json"),
    )
    account = PaperAccount()
    sl = 95.0 if side == "LONG" else 105.0
    tp = 105.0 if side == "LONG" else 95.0
    assert await account.open_position(
        "MODE/USDT", side, 100.0, 25.0, sl, tp, "partial guard",
        leverage=1, signal_score=80, apply_slippage=False,
        entry_context={"entry_mode": "BREAKOUT"},
    )
    qty_before = account.positions["MODE/USDT"]["qty"]

    closed = await account.partial_close_position(
        "MODE/USDT", 101.0, "invalid partial", fraction=fraction,
    )

    assert closed is False
    assert account.positions["MODE/USDT"]["qty"] == qty_before
    assert not any(t["action"].startswith("PARTIAL_CLOSE") for t in account.trades)


@pytest.mark.anyio
@pytest.mark.parametrize("partial", [False, True])
async def test_close_paths_remove_same_symbol_pending_top_up(
    tmp_path, monkeypatch, partial,
):
    monkeypatch.setattr(pa_module, "STATE_FILE", str(tmp_path / f"close-pending-{partial}.json"))
    account = PaperAccount()
    assert await account.open_position(
        "MODE/USDT", "LONG", 100.0, 25.0, 95.0, 105.0, "open",
        leverage=1, signal_score=80, apply_slippage=False,
        entry_context={"entry_mode": "BREAKOUT"},
    )
    account.pending_limit_orders["MODE/USDT"] = {
        "side": "LONG", "entry_context": {"dca_stage": 2},
    }

    if partial:
        closed = await account.partial_close_position(
            "MODE/USDT", 101.0, "partial", fraction=0.5,
        )
    else:
        closed = await account.close_position(
            "MODE/USDT", 101.0, "full", is_manual=True,
        )

    assert closed is True
    assert "MODE/USDT" not in account.pending_limit_orders


@pytest.mark.anyio
async def test_dca_pending_top_up_requires_stage_two_same_side_and_non_channel(
    tmp_path, monkeypatch,
):
    monkeypatch.setattr(pa_module, "STATE_FILE", str(tmp_path / "dca-pending.json"))
    monkeypatch.setattr(pa_module, "ENABLE_DCA_LIMIT", True)
    account = PaperAccount()
    assert await account.open_position(
        "MODE/USDT", "LONG", 100.0, 30.0, 95.0, 105.0, "open",
        leverage=1, signal_score=80, apply_slippage=False,
        entry_context={"entry_mode": "BREAKOUT", "dca_stage": 1},
    )

    stage_one = await account.place_limit_entry(
        "MODE/USDT", "LONG", 99.0, 10.0, 94.0, 104.0, "bad stage",
        leverage=1, signal_score=80,
        entry_context={"entry_mode": "BREAKOUT", "dca_stage": 1},
    )
    opposite = await account.place_limit_entry(
        "MODE/USDT", "SHORT", 101.0, 10.0, 106.0, 96.0, "bad side",
        leverage=1, signal_score=80,
        entry_context={"entry_mode": "BREAKOUT", "dca_stage": 2},
    )
    valid = await account.place_limit_entry(
        "MODE/USDT", "LONG", 99.0, 10.0, 94.0, 104.0, "valid DCA",
        leverage=1, signal_score=80,
        entry_context={"entry_mode": "BREAKOUT", "dca_stage": 2},
    )

    assert stage_one is False
    assert opposite is False
    assert valid is True
    assert account.pending_limit_orders["MODE/USDT"]["side"] == "LONG"

    channel = PaperAccount()
    assert await channel.open_position(
        "SWING/USDT", "LONG", 100.0, 30.0, 0.0, 0.0, "swing",
        leverage=1, signal_score=100, apply_slippage=False,
        entry_context={"entry_mode": "CHANNEL_SWING"},
    )
    channel_dca = await channel.place_limit_entry(
        "SWING/USDT", "LONG", 99.0, 10.0, 94.0, 104.0, "bad swing DCA",
        leverage=1, signal_score=100,
        entry_context={"entry_mode": "CHANNEL_SWING", "dca_stage": 2},
    )
    assert channel_dca is False
