import asyncio
import hashlib
import time
from types import SimpleNamespace

import pandas as pd
import pytest

from core.services import entry_firewall, entry_contract, symbol_runner
from core.services.entry_firewall import validate_account_entry
from core.services.wait_entry_authority import WaitEntryAuthority
from core.testnet_account import BinanceTestnetAccount


def _signal(side="LONG"):
    return {
        "action": "ENTER",
        "side": side,
        "type": f"KC_2BAR_CONFIRM_{side}",
        "entry_phase": "KC_2BAR_CLOSED_CONFIRM",
        "breakout_bar_id": 120_000,
        "pair_confirmation_bar_id": 180_000,
        "confirmation_bar_id": 240_000,
        "pending_signal_id": f"{side}:120000:180000",
        "entry_atr": 2.0,
        "price": 101.0 if side == "LONG" else 99.0,
    }


def _claim(authority, symbol="CAP/USDT", side="LONG", signal=None):
    signal = signal or _signal(side)
    canonical = authority.canonical_symbol(symbol)
    claim_id = hashlib.sha256(
        (
            f"KC2BAR|{canonical}|{side}|{signal['breakout_bar_id']}|"
            f"{signal['pair_confirmation_bar_id']}|"
            f"{signal['confirmation_bar_id']}"
        ).encode("utf-8"),
    ).hexdigest()
    client_order_id = "KC2BAR" + claim_id[:30]
    assert authority.claim_external(
        claim_id,
        symbol=symbol,
        side=side,
        authority="KC2BAR",
        live_bar_id=signal["confirmation_bar_id"],
        setup_bar_id=signal["breakout_bar_id"],
        client_order_id=client_order_id,
    )
    return claim_id, client_order_id


def test_authority_arbitration_fails_closed_opposite_and_selects_one_same_side():
    same_side = WaitEntryAuthority.arbitrate([
        {"authority": "WAIT", "side": "LONG", "trigger_id": "wait"},
        {"authority": "KC2BAR", "side": "LONG", "trigger_id": "kc"},
    ])
    assert same_side["status"] == "ONE_ORDER"
    assert same_side["winner"]["authority"] == "KC2BAR"

    opposite = WaitEntryAuthority.arbitrate([
        {"authority": "WAIT", "side": "LONG"},
        {"authority": "KC2BAR", "side": "SHORT"},
    ])
    assert opposite["status"] == "BLOCKED_OPPOSITE_AUTHORITIES"


def test_kc2bar_producer_has_stable_identity_and_rejects_non_authorized_shape(
    monkeypatch,
):
    signal = _signal()
    monkeypatch.setattr(
        entry_contract,
        "evaluate_entry_contract",
        lambda frame, **kwargs: signal if kwargs["code"] == signal["type"] else None,
    )
    frame = pd.DataFrame([{"timestamp": 240_000}])
    assert symbol_runner._kc2bar_candidates(frame, "CAP/USDT", 101.0) == []
    frame.attrs.update({
        "entry_finality_verified": True,
        "timeframe_ms": 60_000,
    })
    candidates = symbol_runner._kc2bar_candidates(frame, "龙虾/USDT", 101.0)
    assert len(candidates) == 1
    candidate = candidates[0]
    expected = hashlib.sha256(
        b"KC2BAR|LOBSTER/USDT|LONG|120000|180000|240000",
    ).hexdigest()
    assert candidate["trigger_id"] == expected
    assert candidate["authority"] == "KC2BAR"

    malformed = {**signal, "confirmation_bar_id": 300_000}
    monkeypatch.setattr(
        entry_contract,
        "evaluate_entry_contract",
        lambda _frame, **_kwargs: malformed,
    )
    assert symbol_runner._kc2bar_candidates(frame, "CAP/USDT", 101.0) == []


def test_external_claim_restart_quarantine_and_durable_outbox(tmp_path):
    path = tmp_path / "wait.json"
    authority = WaitEntryAuthority(path)
    signal = _signal()
    claim_id, client_order_id = _claim(authority, signal=signal)
    assert authority.is_external_claim_valid(
        claim_id, "CAP/USDT", "LONG", 240_000, 120_000, client_order_id,
    )
    assert authority.pending_events("CAP/USDT")

    restarted = WaitEntryAuthority(path)
    assert restarted.unknown_claims("CAP/USDT")[0]["trigger_id"] == claim_id
    assert not restarted.is_external_claim_valid(
        claim_id, "CAP/USDT", "LONG", 240_000, 120_000, client_order_id,
    )
    pending = restarted.pending_events("CAP/USDT")
    assert any(event["type"] == "CLAIM_QUARANTINED_RESTART" for event in pending)
    assert restarted.acknowledge_events([event["event_id"] for event in pending])
    assert restarted.pending_events("CAP/USDT") == []


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_kc2bar_final_firewall_revalidates_signal_and_claim(
    tmp_path, monkeypatch, side,
):
    from core import config

    monkeypatch.setattr(config, "ENTRY_DISABLED_SYMBOLS", ())
    signal = _signal(side)
    authority = WaitEntryAuthority(tmp_path / f"{side}.json")
    claim_id, client_order_id = _claim(
        authority, symbol="龍蝦/USDT", side=side, signal=signal,
    )
    frame = pd.DataFrame([{"timestamp": 240_000, "is_closed": False}])
    frame.attrs.update({
        "entry_finality_verified": True,
        "timeframe_ms": 60_000,
    })
    account = SimpleNamespace(
        positions={},
        closing_lock=set(),
        pending_limit_orders={},
        wait_entry_authority=authority,
        entry_frame_provider=lambda _symbol: _resolved(frame),
        wait_quote_provider=lambda _symbol: {
            "price": signal["price"],
            "observed_at": time.monotonic(),
        },
    )
    monkeypatch.setattr(entry_firewall, "evaluate_entry_contract", lambda *args, **kwargs: signal)

    result = asyncio.run(validate_account_entry(
        account,
        "龍蝦/USDT",
        side,
        {
            "entry_mode": "CHANNEL_SWING",
            "entry_authority": "KC2BAR",
            "source": "KC2BAR_1M",
            "kc2bar_signal": signal,
            "kc2bar_signal_id": claim_id,
            "entry_authority_claim_id": claim_id,
            "entry_client_order_id": client_order_id,
            "position_uuid": authority.position_uuid_for_trigger(claim_id),
        },
    ))
    assert result["type"] == signal["type"]
    assert result["entry_atr"] == 2.0


async def _resolved(value):
    return value


@pytest.mark.parametrize("position_uuid_matches", [True, False])
def test_testnet_restart_reconciliation_requires_matching_position_identity(
    tmp_path, position_uuid_matches,
):
    path = tmp_path / "wait.json"
    authority = WaitEntryAuthority(path)
    signal = _signal()
    claim_id, client_order_id = _claim(authority, signal=signal)
    claim = authority._data["claims"][claim_id]
    restarted = WaitEntryAuthority(path)
    position_uuid = (
        claim["position_uuid"]
        if position_uuid_matches
        else "different-position"
    )
    calls = []

    class Exchange:
        async def fetch_order(self, order_id, symbol, params):
            calls.append((order_id, symbol, params))
            return {"status": "closed", "filled": 1.0}

    account = SimpleNamespace(
        exchange=Exchange(),
        positions={
            "CAP/USDT": {
                "entry_authority_claim_id": claim_id,
                "position_uuid": position_uuid,
            },
        },
        refresh=_refresh,
        log=lambda *_args: None,
    )
    asyncio.run(
        BinanceTestnetAccount.reconcile_wait_entry_claims(
            account, restarted, "CAP/USDT",
        ),
    )

    assert calls == [
        (None, "CAP/USDT", {"origClientOrderId": client_order_id}),
    ]
    if position_uuid_matches:
        assert restarted._data["claims"][claim_id]["status"] == "CONSUMED"
    else:
        assert restarted._data["claims"][claim_id]["status"] == "UNKNOWN"


async def _refresh(*, force):
    assert force is True
