import copy
import asyncio
from unittest.mock import AsyncMock

import pytest

from core.services.entry_contract import evaluate_entry_contract
from core.services.entry_firewall import validate_account_entry
from core.services.exits.peak_trailing_exit import (
    ABNORMAL_REASON,
    PEAK_REASON,
    evaluate_peak_trailing,
    three_point_pivot_exit,
)
from core.services.exits.profit_exit_telemetry import ProfitExitTelemetry
from test_breakout_only_entry import breakout_frame


@pytest.fixture(autouse=True)
def disable_exit_telemetry(monkeypatch):
    monkeypatch.setattr(ProfitExitTelemetry, "ENABLED", False)


def position(side):
    return dict(
        side=side,
        entry_price=100.,
        qty=1.,
        open_timestamp=60.,
        entry_mode="CHANNEL_SWING",
        entry_atr=1.,
    )


def live_breakout_position(side):
    held = position(side)
    held.update(
        entry_signal_code=f"KC_LIVE_BODY_BREAKOUT_{side}",
        entry_snapshot={"signal_code": f"KC_LIVE_BODY_BREAKOUT_{side}"},
    )
    return held


def channel_snapshot(price_ms=181_000.):
    return dict(
        reason=None,
        quote_ms=price_ms,
        snapshot_bar_id=120_000.,
        closed_bar_ms=120_000.,
        live_bar_ms=180_000.,
        live_kc_lower=99.,
        live_kc_upper=101.,
    )


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_channel_peak_reversal_pullback_does_not_close(side):
    sign = 1 if side == "LONG" else -1
    held = position(side)

    assert evaluate_peak_trailing(
        held, 100. + sign * 1.0, 61_000, fee=0., slippage=0.
    ) is None
    assert evaluate_peak_trailing(
        held, 100. + sign * 2.0, 62_000, fee=0., slippage=0.
    ) is None

    result = evaluate_peak_trailing(
        held, 100. + sign * 1.5, 63_000, fee=0., slippage=0.
    )

    assert result is None
    assert not held["peak_trailing_state"].get("pending")


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_first_live_breakout_stays_open_when_quote_returns_inside_kc(side):
    held = live_breakout_position(side)
    result = evaluate_peak_trailing(
        held, 100., channel_snapshot(), fee=0., slippage=0.
    )

    assert result is None
    assert not held["peak_trailing_state"].get("pending")


@pytest.mark.parametrize(
    ("side", "price"),
    [("LONG", 101.), ("SHORT", 99.)],
)
def test_first_live_breakout_stays_open_until_quote_enters_kc(side, price):
    held = live_breakout_position(side)
    result = evaluate_peak_trailing(
        held, price, channel_snapshot(), fee=0., slippage=0.
    )

    assert result is None
    assert not held["peak_trailing_state"].get("pending")


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_first_live_breakout_keeps_confirmed_pivot_exit(side, monkeypatch):
    held = live_breakout_position(side)
    snapshot = channel_snapshot()
    monkeypatch.setattr(
        "core.services.exits.peak_trailing_exit.three_point_pivot_exit",
        lambda *_: {"trigger": "THREE_POINT_PIVOT"},
    )
    monkeypatch.setattr(
        "core.services.exits.peak_trailing_exit.live_ma5_reversal_exit",
        lambda *_: None,
    )

    result = evaluate_peak_trailing(
        held, 98., snapshot, fee=0., slippage=0.
    )

    assert result["trigger"] == "THREE_POINT_PIVOT"
    assert held["peak_trailing_state"]["pending"] == ABNORMAL_REASON


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_channel_return_exit_does_not_apply_to_other_breakouts(side):
    held = position(side)
    held["entry_snapshot"] = {"signal_code": f"KC_2BAR_CONFIRM_{side}"}
    result = evaluate_peak_trailing(
        held, 100., channel_snapshot(), fee=0., slippage=0.
    )

    assert result is None
    assert not held["peak_trailing_state"].get("pending")


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_legacy_pullback_ticket_is_revoked(side):
    held = position(side)
    held["peak_trailing_state"] = {
        "pending": PEAK_REASON,
        "trigger": "CHANNEL_PEAK_PULLBACK_REVERSAL",
    }

    result = evaluate_peak_trailing(held, 101., 63_000, fee=0., slippage=0.)

    assert result is None
    assert not held["peak_trailing_state"].get("pending")


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
@pytest.mark.parametrize(
    "trigger",
    [
        "EXIT_PEAK_PULLBACK_PRESSURE",
        "CHANNEL_PEAK_PULLBACK_REVERSAL",
        "KC_CHANNEL_RETURN",
    ],
)
def test_restart_revokes_every_disabled_channel_pullback_ticket(side, trigger):
    held = position(side)
    state = {
        "policy": "abnormal_body_only_v2",
        "identity": [side, 60.0, 100.0, 1.0],
        "peak_price": 102.0 if side == "LONG" else 98.0,
        "pending": PEAK_REASON,
        "trigger": trigger,
    }
    held["peak_trailing_state"] = copy.deepcopy(state)
    meta = {"peak_trailing_state": copy.deepcopy(state)}

    from core.services.exits.peak_trailing_exit import migrate_peak_state
    migrated = migrate_peak_state(held, meta)

    result = evaluate_peak_trailing(
        held, 101.0 if side == "LONG" else 99.0, 63_000, fee=0., slippage=0.
    )

    assert result is None
    assert not migrated.get("pending")
    assert not meta["peak_trailing_state"].get("pending")


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_subthreshold_pullback_keeps_position_open(side):
    sign = 1 if side == "LONG" else -1
    held = position(side)
    evaluate_peak_trailing(held, 100. + sign * 2.0, 61_000, fee=0., slippage=0.)

    result = evaluate_peak_trailing(
        held, 100. + sign * 1.51, 62_000, fee=0., slippage=0.
    )

    assert result is None


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_inner_channel_run_exits_only_after_confirmed_three_bar_pivot(side):
    base_ms = 1_800_000_000_000
    position = {"side": side, "open_timestamp": (base_ms - 60_000) / 1000}
    bars = [
        {"ms": base_ms, "o": 10., "h": 11., "l": 9., "c": 10.5},
        {"ms": base_ms + 60_000, "o": 10.5, "h": 12., "l": 9.5, "c": 11.5},
        {"ms": base_ms + 120_000, "o": 11.5, "h": 11.8, "l": 9.8, "c": 10.2},
    ]
    if side == "SHORT":
        bars = [
            {"ms": base_ms, "o": 10., "h": 11., "l": 9., "c": 9.5},
            {"ms": base_ms + 60_000, "o": 9.5, "h": 10.5, "l": 8., "c": 8.5},
            {"ms": base_ms + 120_000, "o": 8.5, "h": 10.2, "l": 8.2, "c": 9.8},
        ]
    snapshot = {
        "reason": None,
        "history_outer_pivots": bars,
        "quote_ms": base_ms + 180_000,
        "snapshot_bar_id": base_ms + 120_000,
    }
    result = three_point_pivot_exit(position, snapshot)
    assert result["trigger"] == "THREE_POINT_PIVOT"
    assert result["trigger_price"] == bars[1]["h" if side == "LONG" else "l"]

    # Before the confirming candle, a visually apparent extremum is not actionable.
    snapshot["history_outer_pivots"] = bars[:2]
    assert three_point_pivot_exit(position, snapshot) is None


def reversal_frame(side):
    frame = breakout_frame(side)
    row = frame.index[-1]
    opened = float(frame.loc[row, "open"])
    edge = float(frame.loc[row, "kc_upper" if side == "LONG" else "kc_lower"])
    if side == "LONG":
        frame.loc[row, "high"] = opened + 0.8
        quote = opened - 0.4
        frame.loc[row, "low"] = min(opened, edge, quote)
    else:
        frame.loc[row, "low"] = opened - 0.8
        quote = opened + 0.4
        frame.loc[row, "high"] = max(opened, edge, quote)
    frame.loc[row, "close"] = opened
    frame.attrs["entry_finality_verified"] = True
    return frame, quote


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_impulse_reversal_blocks_entry_for_rest_of_same_candle(side):
    account = type("Account", (), {
        "positions": {}, "trades": [], "last_closed_at": {},
        "breakout_qualification": {}, "save_state": lambda self: None,
    })()
    frame, quote = reversal_frame(side)
    symbol = "LOBSTER/USDT"
    diagnostics = {}

    assert evaluate_entry_contract(
        frame, quote, account=account, symbol=symbol, diagnostics=diagnostics
    ) is None
    assert diagnostics["reason"] in (
        "BLOCKED_ENTRY_REQUIRES_INNER_CHANNEL_PRESSURE",
        "WAIT_LIVE_BODY_BREAKOUT",
        "KC_PENDING_CANCELLED_INSIDE_RAIL",
    )
    assert not account.breakout_qualification


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_next_candle_requires_observed_pullback_then_resume(side):
    account = type("Account", (), {
        "positions": {}, "trades": [], "last_closed_at": {},
        "breakout_qualification": {}, "save_state": lambda self: None,
    })()
    frame, quote = reversal_frame(side)
    symbol = "LOBSTER/USDT"
    diagnostics = {}
    assert evaluate_entry_contract(
        frame, quote, account=account, symbol=symbol, diagnostics=diagnostics
    ) is None

    assert evaluate_entry_contract(
        frame, quote, account=account, symbol=symbol, diagnostics=diagnostics
    ) is None
    assert diagnostics["reason"] in (
        "BLOCKED_ENTRY_REQUIRES_INNER_CHANNEL_PRESSURE",
        "WAIT_LIVE_BODY_BREAKOUT",
        "KC_PENDING_CANCELLED_INSIDE_RAIL",
    )
