import asyncio
import hashlib
from types import SimpleNamespace

import pandas as pd
import pytest

from core.engine import TradingEngine
from core.services.wait_entry_authority import WaitEntryAuthority


class _Account:
    def __init__(self):
        self.positions = {}
        self.pending_limit_orders = {}
        self.closing_lock = set()
        self.calls = []
        self.logs = []

    def get_wallet_balance(self):
        return 200.0

    def get_available_balance(self):
        return 180.0

    def log(self, message, level):
        self.logs.append((message, level))

    async def open_position(self, **kwargs):
        self.calls.append(kwargs)
        self.positions[kwargs["symbol"]] = {
            "side": kwargs["side"],
            "qty": 1.0,
        }
        return True


def _engine(authority):
    engine = TradingEngine.__new__(TradingEngine)
    engine.account = _Account()
    engine.wait_entry_authority = authority
    engine.symbol_rotation = SimpleNamespace(
        get_dynamic_leverage=lambda _symbol, _score: 5,
    )
    return engine


def _claimed_trigger(authority, symbol="CAP/USDT", side="LONG"):
    first = {
        "timestamp": 60_000, "open": 100.0, "high": 100.5, "low": 99.5,
        "close": 100.0, "atr": 2.0, "is_closed": True,
    }
    setup = {
        "timestamp": 120_000, "open": 100.0,
        "high": max(100.2, 100.5 if side == "SHORT" else 100.2), "low": 99.4,
        "close": 99.6 if side == "LONG" else 100.4, "atr": 2.0,
        "is_closed": True,
    }
    live = {
        "timestamp": 180_000, "open": 100.0,
        "high": 101.2 if side == "LONG" else 100.2,
        "low": 99.8 if side == "LONG" else 98.8,
        "close": 100.2, "atr": 2.0, "is_closed": False,
    }
    authority.observe(
        symbol, [first], {**first, "timestamp": 120_000, "is_closed": False},
        100.0, quote_fresh=True, position_open=False,
        authoritative_position_state=True,
    )
    candidate = authority.observe(
        symbol, [first, setup], live, 101.0 if side == "LONG" else 99.0,
        quote_fresh=True, position_open=False,
        authoritative_position_state=True,
    )
    assert candidate["status"] == "TRIGGER_AVAILABLE"
    assert authority.claim(candidate["trigger_id"])
    client_order_id = "WAIT" + candidate["trigger_id"][:32]
    assert authority.bind_order_identity(candidate["trigger_id"], client_order_id)
    candidate["client_order_id"] = client_order_id
    return candidate


def test_wait_entry_uses_shared_submit_lock_and_half_wallet_limit(tmp_path):
    authority = WaitEntryAuthority(tmp_path / "wait.json")
    engine = _engine(authority)
    trigger = _claimed_trigger(authority)
    result = asyncio.run(engine.execute_dual_tf_entry(
        symbol="CAP/USDT",
        frame=pd.DataFrame([{"timestamp": 120_000, "atr": 2.0}]),
        price=101.0,
        side="LONG",
        mode_name="WAIT_LIVE_BIG_TRIGGER",
        meta_dict={
            "entry_authority": "WAIT",
            "wait_trigger": trigger,
            "wait_trigger_id": trigger["trigger_id"],
            "wait_client_order_id": trigger["client_order_id"],
            "entry_authority_claim_id": trigger["trigger_id"],
            "entry_client_order_id": trigger["client_order_id"],
            "position_uuid": authority.position_uuid_for_trigger(trigger["trigger_id"]),
        },
        entry_mode="CHANNEL_SWING",
    ))

    assert result is True
    assert engine.account.calls
    submitted = engine.account.calls[0]
    assert submitted["amount_usdt"] <= 100.0
    assert submitted["entry_context"]["source"] == "WAIT_1M"
    assert submitted["entry_context"]["entry_authority"] == "WAIT"
    assert submitted["entry_context"]["is_manual"] is False
    assert engine._account_entry_submit_lock is not None


def test_wait_engine_rejects_mismatched_trigger_before_account_submit(tmp_path):
    authority = WaitEntryAuthority(tmp_path / "wait.json")
    engine = _engine(authority)
    trigger = _claimed_trigger(authority, side="SHORT")

    with pytest.raises(ValueError, match="Invalid WAIT direct-entry authority"):
        asyncio.run(engine.execute_dual_tf_entry(
            symbol="CAP/USDT",
            frame=pd.DataFrame([{"timestamp": 120_000, "atr": 2.0}]),
            price=99.0,
            side="LONG",
            mode_name="WAIT_LIVE_BIG_TRIGGER",
            meta_dict={
                "entry_authority": "WAIT",
                "wait_trigger": trigger,
                "wait_trigger_id": trigger["trigger_id"],
                "wait_client_order_id": trigger["client_order_id"],
                "entry_authority_claim_id": trigger["trigger_id"],
                "entry_client_order_id": trigger["client_order_id"],
                "position_uuid": authority.position_uuid_for_trigger(trigger["trigger_id"]),
            },
            entry_mode="CHANNEL_SWING",
        ))

    assert engine.account.calls == []


def test_kc2bar_engine_entry_uses_durable_claim_and_shared_capital_limit(tmp_path):
    authority = WaitEntryAuthority(tmp_path / "wait.json")
    engine = _engine(authority)
    signal = {
        "side": "LONG",
        "type": "KC_2BAR_CONFIRM_LONG",
        "entry_phase": "KC_2BAR_CLOSED_CONFIRM",
        "breakout_bar_id": 120_000,
        "pair_confirmation_bar_id": 180_000,
        "confirmation_bar_id": 240_000,
        "entry_atr": 2.0,
    }
    claim_id = hashlib.sha256(
        b"KC2BAR|CAP/USDT|LONG|120000|180000|240000",
    ).hexdigest()
    client_order_id = "KC2BAR" + claim_id[:30]
    assert authority.claim_external(
        claim_id,
        symbol="CAP/USDT",
        side="LONG",
        authority="KC2BAR",
        live_bar_id=240_000,
        setup_bar_id=120_000,
        client_order_id=client_order_id,
    )
    signal.update({
        "action": "ENTER",
        "price": 101.0,
    })
    result = asyncio.run(engine.execute_dual_tf_entry(
        symbol="CAP/USDT",
        frame=pd.DataFrame([{"timestamp": 240_000, "atr": 2.0}]),
        price=101.0,
        side="LONG",
        mode_name=signal["type"],
        meta_dict={
            "entry_authority": "KC2BAR",
            "kc2bar_signal": signal,
            "kc2bar_signal_id": claim_id,
            "entry_authority_claim_id": claim_id,
            "entry_client_order_id": client_order_id,
            "position_uuid": authority.position_uuid_for_trigger(claim_id),
        },
        entry_mode="CHANNEL_SWING",
    ))

    assert result is True
    submitted = engine.account.calls[0]
    assert submitted["amount_usdt"] <= 100.0
    assert submitted["entry_context"]["source"] == "KC2BAR_1M"
    assert submitted["entry_context"]["entry_authority"] == "KC2BAR"
