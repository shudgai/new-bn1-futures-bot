import asyncio
import time
from types import SimpleNamespace

import pandas as pd

from core.services.entry_firewall import validate_account_entry
from core.services.wait_entry_authority import WaitEntryAuthority


def _candle(timestamp, opening, closing, *, atr=2.0, span=1.0, is_closed=True):
    wick = (span - abs(closing - opening)) / 2
    return {
        "timestamp": timestamp,
        "open": opening,
        "high": max(opening, closing) + wick,
        "low": min(opening, closing) - wick,
        "close": closing,
        "atr": atr,
        "is_closed": is_closed,
    }


def _wait_account(tmp_path):
    authority = WaitEntryAuthority(tmp_path / "wait.json")
    first = _candle(60_000, 100, 100)
    setup = _candle(120_000, 100, 99.6)
    live = _candle(180_000, 100, 100.2, span=2.4, is_closed=False)
    authority.observe(
        "CAP/USDT", [first], _candle(120_000, 100, 100, is_closed=False), 100,
        quote_fresh=True, position_open=False,
        authoritative_position_state=True,
    )
    candidate = authority.observe(
        "CAP/USDT", [first, setup], live, 101,
        quote_fresh=True, position_open=False,
        authoritative_position_state=True,
    )
    assert candidate["status"] == "TRIGGER_AVAILABLE"
    assert authority.claim(candidate["trigger_id"])
    client_order_id = "WAIT" + candidate["trigger_id"][:32]
    assert authority.bind_order_identity(candidate["trigger_id"], client_order_id)

    frame = pd.DataFrame([
        {**first, "is_closed": True},
        {**setup, "is_closed": True},
        live,
    ])
    frame.attrs["entry_finality_verified"] = True
    frame.attrs["timeframe_ms"] = 60_000
    account = SimpleNamespace(
        positions={},
        closing_lock=set(),
        pending_limit_orders={},
        wait_entry_authority=authority,
        entry_frame_provider=lambda _symbol: _async_frame(frame),
        wait_quote_provider=lambda _symbol: {
            "price": 101.0,
            "observed_at": time.monotonic(),
        },
    )
    return account, candidate


async def _async_frame(frame):
    return frame


def test_final_wait_firewall_revalidates_claimed_live_trigger(tmp_path, monkeypatch):
    from core import config

    monkeypatch.setattr(config, "ENTRY_DISABLED_SYMBOLS", ())
    account, candidate = _wait_account(tmp_path)
    result = asyncio.run(validate_account_entry(
        account,
        "CAP/USDT",
        "LONG",
        {
            "entry_mode": "CHANNEL_SWING",
            "entry_authority": "WAIT",
            "source": "WAIT_1M",
            "wait_trigger": candidate,
            "wait_trigger_id": candidate["trigger_id"],
            "wait_client_order_id": "WAIT" + candidate["trigger_id"][:32],
            "entry_authority_claim_id": candidate["trigger_id"],
            "entry_client_order_id": "WAIT" + candidate["trigger_id"][:32],
            "position_uuid": account.wait_entry_authority.position_uuid_for_trigger(
                candidate["trigger_id"],
            ),
        },
    ))

    assert result["type"] == "WAIT_LIVE_BIG_TRIGGER"
    assert result["side"] == "LONG"
    assert result["confirmation_bar_id"] == 180_000


def test_final_wait_firewall_rejects_stale_live_quote(tmp_path, monkeypatch):
    from core import config

    monkeypatch.setattr(config, "ENTRY_DISABLED_SYMBOLS", ())
    account, candidate = _wait_account(tmp_path)
    account.wait_quote_provider = lambda _symbol: {
        "price": 100.9,
        "observed_at": time.monotonic(),
    }

    try:
        asyncio.run(validate_account_entry(
            account,
            "CAP/USDT",
            "LONG",
            {
                "entry_mode": "CHANNEL_SWING",
                "entry_authority": "WAIT",
                "source": "WAIT_1M",
                "wait_trigger": candidate,
                "wait_trigger_id": candidate["trigger_id"],
                "wait_client_order_id": "WAIT" + candidate["trigger_id"][:32],
                "entry_authority_claim_id": candidate["trigger_id"],
                "entry_client_order_id": "WAIT" + candidate["trigger_id"][:32],
                "position_uuid": account.wait_entry_authority.position_uuid_for_trigger(
                    candidate["trigger_id"],
                ),
            },
        ))
    except ValueError as exc:
        assert "WAIT_TRIGGER_STALE_OR_INVALID" in str(exc)
    else:
        raise AssertionError("A quote below the live trigger must be rejected")
