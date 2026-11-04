import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

import core.gates.master_gate_funnel as master_gate_funnel
from core.gates.ultimate_short_exit_gatekeeper import UltimateShortExitGatekeeper
from core.services.exits import entry_atr_protection


@pytest.mark.parametrize(
    ("side", "reason", "reverse_side"),
    [
        ("LONG", "EXIT_LONG_KC_MIDDLE_MA15_BREAK", "SHORT"),
        ("SHORT", "EXIT_SHORT_KC_MIDDLE_MA15_BREAK", "LONG"),
    ],
)
def test_authorized_structure_exit_bypasses_legacy_trend_lock_and_records_reverse(
    monkeypatch, side, reason, reverse_side,
):
    symbol = "1000PEPE/USDT"
    position = {
        "symbol": symbol,
        "side": side,
        "entry_price": 100.0,
        "qty": 1.0,
        "open_timestamp": 1_700_000_000.0,
    }
    frame = object()
    account = SimpleNamespace(
        positions={symbol: position},
        position_meta={symbol: {}},
        channel_exit_frame_provider=lambda _symbol: frame,
        _channel_exit_frames={},
        close_position=AsyncMock(),
        log=Mock(),
    )

    async def close_position(_symbol, _price, close_reason, *, is_manual):
        assert is_manual is True
        master_gate_funnel.record_position_close(_symbol, side, close_reason)
        return True

    account.close_position.side_effect = close_position
    monkeypatch.setattr(
        UltimateShortExitGatekeeper,
        "structure_exit_candidate",
        classmethod(lambda cls, *_args, **_kwargs: reason),
    )
    monkeypatch.setattr(
        entry_atr_protection,
        "check_trend_lock_exit_permission",
        lambda *_args, **_kwargs: pytest.fail("legacy Trend Lock must not veto this exit"),
    )
    monkeypatch.setattr(master_gate_funnel, "_STRUCTURE_EXITS", {})

    assert asyncio.run(entry_atr_protection.enforce_atr_protection(account, symbol, 99.0))
    account.close_position.assert_awaited_once_with(
        symbol, 99.0, reason, is_manual=True,
    )
    assert master_gate_funnel.reverse_window_side(symbol) == reverse_side


def test_failed_structure_close_does_not_record_reverse_window(monkeypatch):
    symbol = "1000PEPE/USDT"
    position = {
        "symbol": symbol,
        "side": "LONG",
        "entry_price": 100.0,
        "qty": 1.0,
        "open_timestamp": 1_700_000_000.0,
    }
    account = SimpleNamespace(
        positions={symbol: position},
        position_meta={symbol: {}},
        channel_exit_frame_provider=lambda _symbol: object(),
        _channel_exit_frames={},
        close_position=AsyncMock(return_value=False),
        log=Mock(),
    )
    monkeypatch.setattr(
        UltimateShortExitGatekeeper,
        "structure_exit_candidate",
        classmethod(
            lambda cls, *_args, **_kwargs: "EXIT_LONG_KC_MIDDLE_MA15_BREAK"
        ),
    )
    monkeypatch.setattr(master_gate_funnel, "_STRUCTURE_EXITS", {})

    assert not asyncio.run(entry_atr_protection.enforce_atr_protection(account, symbol, 99.0))
    account.close_position.assert_awaited_once()
    assert master_gate_funnel.reverse_window_side(symbol) is None
