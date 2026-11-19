import asyncio
import time
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from core.services.exits.authorized_position_exit import automated_position_exit_allowed
from core.services.closed_breakout_entry import matched_reentry_close


@pytest.mark.parametrize(
    ("side", "reason", "evidence"),
    [
        (
            "LONG",
            "PRIORITY_EXIT_P1_NET_ROE_PROFIT_LOCK",
            {
                "trigger": "DYNAMIC_NET_ROE_LOCK",
                "current_net_pnl_usdt": 1.0,
                "max_net_pnl_usdt": 2.0,
                "current_net_roi": 6.7,
                "max_net_roi": 14.36,
                "current_stop_roi": 11.36,
            },
        ),
        (
            "LONG",
            "PRIORITY_EXIT_P1_CONSECUTIVE_REVERSAL_PROFIT",
            {
                "trigger": "TWO_CLOSED_REVERSAL_CANDLES",
                "current_net_pnl_usdt": 1.0,
                "max_net_pnl_usdt": 2.0,
                "consecutive_opposite_candles": 2,
                "close_progression_confirmed": True,
                "first_bar_timestamp": 1_700_000_000_000,
                "bar_timestamp": 1_700_000_060_000,
            },
        ),
        (
            "SHORT",
            "PRIORITY_EXIT_P1_STRONG_REVERSAL_PROFIT",
            {
                "trigger": "SINGLE_STRONG_CLOSED_REVERSAL",
                "current_net_pnl_usdt": 1.0,
                "max_net_pnl_usdt": 2.0,
                "body": 0.6,
                "reference_atr": 1.0,
                "sequence_structure_level": 101.0,
                "bar_timestamp": 1_700_000_060_000,
            },
        ),
    ],
)
def test_verified_priority_profit_exit_closes_and_creates_normal_reentry(
    side, reason, evidence, monkeypatch,
):
    from core.paper_account import PaperAccount
    import pandas as pd
    from core.engine import TradingEngine
    from core.services.strategies.unified_entry_strategy import UnifiedEntryStrategy

    symbol = "LOBSTER/USDT"
    quote = 101.0 if side == "LONG" else 99.0
    opened = time.time() - 300.0
    position_uid = f"position-{side}-{reason}"
    position = {
        "symbol": symbol,
        "side": side,
        "entry_mode": "CHANNEL_SWING",
        "position_uid": position_uid,
        "open_timestamp": opened,
        "entry_price": 100.0,
        "qty": 1.0,
        "margin": 100.0,
        "leverage": 1.0,
        "peak_trailing_state": {"peak_price": quote},
    }
    evidence = {**evidence, "position_uid": position_uid}
    account = object.__new__(PaperAccount)
    account.positions = {symbol: position}
    account.position_meta = {symbol: {}}
    account.position_meta[symbol]["priority_exit_authorization"] = {
        "reason": reason,
        "side": side,
        "position_uid": position_uid,
        "quote": quote,
        "position_open_timestamp": opened,
        "issued_at": time.time(),
        "evidence": evidence,
    }
    account.pending_limit_orders = {}
    account.closing_lock = set()
    account.balance = 1_000.0
    account.realized_pnl = 0.0
    account.last_closed_at = {}
    account.last_closed_side = {}
    account.channel_profit_reentries = {}
    account.entry_filter_last = {}
    account.trades = []
    account._channel_exit_frames = {}
    account._auto_close_reject_logged_at = {}
    account.save_state = Mock()
    account.log = Mock()
    account.on_trade_closed = None

    assert asyncio.run(account.close_position(symbol, quote, reason)) is True
    assert symbol not in account.positions
    assert account.trades[0]["exit_type"] == "PROFIT_LOCK"
    ticket = account.channel_profit_reentries[symbol]
    assert ticket["side"] == side
    assert ticket["mode"] == "outer_cycle"
    assert ticket["requires_pullback"] is False
    closed_at_ms = matched_reentry_close(account, symbol, ticket)
    assert closed_at_ms
    monkeypatch.setattr(
        UnifiedEntryStrategy,
        "evaluate_entry",
        lambda *_args, **_kwargs: (True, "NORMAL_ENTRY_READY"),
    )
    next_bar = (int(closed_at_ms // 60_000) + 1) * 60_000
    assert TradingEngine._profit_reentry_ready(
        SimpleNamespace(account=account),
        symbol,
        ticket,
        pd.DataFrame([{"timestamp": next_bar}]),
        quote,
    )


def test_priority_profit_exit_requires_matching_fresh_position_authority():
    from core.services.math_execution_gate import validate_exit_at_execution

    symbol = "LOBSTER/USDT"
    reason = "PRIORITY_EXIT_P1_NET_ROE_PROFIT_LOCK"
    quote = 101.0
    opened = time.time() - 300.0
    position = {
        "side": "LONG",
        "position_uid": "position-current",
        "open_timestamp": opened,
        "entry_price": 100.0,
        "qty": 1.0,
    }
    evidence = {
        "trigger": "DYNAMIC_NET_ROE_LOCK",
        "position_uid": "position-current",
        "current_net_pnl_usdt": 1.0,
        "max_net_pnl_usdt": 2.0,
        "current_net_roi": 6.0,
        "max_net_roi": 14.0,
        "current_stop_roi": 11.0,
    }
    metadata = {
        "priority_exit_authorization": {
            "reason": reason,
            "side": "LONG",
            "position_uid": "position-current",
            "quote": quote,
            "position_open_timestamp": opened,
            "issued_at": time.time(),
            "evidence": evidence,
        },
    }
    account = SimpleNamespace(position_meta={symbol: metadata})

    assert validate_exit_at_execution(account, symbol, position, quote, reason)[0]
    metadata["priority_exit_authorization"]["position_uid"] = "stale-position"
    assert not validate_exit_at_execution(account, symbol, position, quote, reason)[0]
    metadata["priority_exit_authorization"]["position_uid"] = "position-current"
    evidence["current_net_roi"] = 3.0
    evidence["current_stop_roi"] = 4.0
    assert not validate_exit_at_execution(account, symbol, position, quote, reason)[0]


def test_exitgate_profit_lock_label_alone_cannot_authorize_close():
    # Arrange
    position = {"side": "LONG", "entry_price": 100.0, "qty": 1.0}

    # Act
    allowed = automated_position_exit_allowed(
        "TEST/USDT", position, {}, "ExitGate:EXIT_BY_RATCHET_PROFIT_LOCK", 102.0,
    )

    # Assert
    assert allowed is False


def test_verified_profit_lock_cannot_bypass_kc_reversal_only_policy(monkeypatch):
    import core.gates.holding_protection_gate as holding_gate

    # Arrange
    position = {
        "side": "LONG",
        "entry_price": 100.0,
        "qty": 1.0,
        "breakeven_locked": True,
        "breakeven_price": 101.0,
    }
    monkeypatch.setattr(
        holding_gate,
        "_verified_profit_lock_details",
        lambda *_args, **_kwargs: {"lock_type": "VERIFIED_TEST_LOCK"},
    )

    # Neither a verified lock nor a pivot is independent close authority.
    no_pivot = automated_position_exit_allowed(
        "TEST/USDT", position, {}, "ExitGate:EXIT_BY_RATCHET_PROFIT_LOCK", 100.5,
    )
    monkeypatch.setattr(
        holding_gate,
        "has_confirmed_true_pivot",
        lambda *_args, **_kwargs: (True, "TRUE_PEAK", {"confirmed": True}),
    )
    with_pivot = automated_position_exit_allowed(
        "TEST/USDT", position, {}, "ExitGate:EXIT_BY_RATCHET_PROFIT_LOCK", 100.5,
        frame=object(),
    )

    # Assert
    assert no_pivot is False
    assert with_pivot is False
