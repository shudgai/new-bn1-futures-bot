import asyncio
import time
from types import SimpleNamespace

import pandas as pd
import pytest

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
        "kc_upper": 110.0,
        "kc_lower": 90.0,
        "kc_middle": 100.0,
        "ma15": 100.0,
        "is_closed": is_closed,
    }


def _wait_account(tmp_path, symbol="CAP/USDT", side="LONG"):
    authority = WaitEntryAuthority(tmp_path / "wait.json")
    first_time, setup_time, live_time = 840_000, 900_000, 960_000
    first = _candle(first_time, 100, 100)
    setup = _candle(setup_time, 100, 99.6 if side == "LONG" else 100.4)
    live = _candle(live_time, 100, 100.2, span=2.4, is_closed=False)
    if side == "LONG":
        first.update(ma5=99.0, ma15=100.0, kc_middle=100.0)
        setup.update(ma5=100.5, ma15=100.0, kc_middle=100.1)
        kc_lower, kc_upper = 90.1, 110.1
        history_middle = lambda index: 98.0 + 0.15 * index
    else:
        first.update(ma5=101.0, ma15=100.2, kc_middle=100.1)
        setup.update(ma5=99.5, ma15=100.0, kc_middle=99.9)
        kc_lower, kc_upper = 89.9, 109.9
        history_middle = lambda index: 101.4 - 0.1 * index
    live.update(kc_lower=kc_lower, kc_upper=kc_upper)
    live["kc_middle"] = setup["kc_middle"]
    quote = 101.0 if side == "LONG" else 99.0
    history = []
    for index in range(13):
        timestamp = (index + 1) * 60_000
        middle = history_middle(index)
        candle = _candle(timestamp, 100, 100)
        candle.update(
            ma5=86.0 + index if side == "LONG" else 114.0 - index,
            ma15=87.0 + index if side == "LONG" else 113.0 - index,
            kc_middle=middle,
            kc_lower=middle - 10.0,
            kc_upper=middle + 10.0,
        )
        history.append(candle)
    first.update(kc_lower=kc_lower, kc_upper=kc_upper)
    setup.update(kc_lower=kc_lower, kc_upper=kc_upper)
    authority.observe(
        symbol, [first], _candle(setup_time, 100, 100, is_closed=False), 100,
        quote_fresh=True, position_open=False,
        authoritative_position_state=True,
    )
    candidate = authority.observe(
        symbol, [first, setup], live, quote,
        quote_fresh=True, position_open=False,
        authoritative_position_state=True,
    )
    assert candidate["status"] == "TRIGGER_AVAILABLE"
    assert authority.claim(candidate["trigger_id"])
    client_order_id = "WAIT" + candidate["trigger_id"][:32]
    assert authority.bind_order_identity(candidate["trigger_id"], client_order_id)

    frame = pd.DataFrame([
        *history,
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
            "price": quote,
            "observed_at": time.monotonic(),
        },
    )
    return account, candidate, frame


async def _async_frame(frame):
    return frame


@pytest.mark.parametrize(
    ("symbol", "side"),
    [
        ("LOBSTER/USDT", "LONG"),
        ("LOBSTER/USDT", "SHORT"),
        ("CAP/USDT", "LONG"),
        ("CAP/USDT", "SHORT"),
    ],
)
def test_final_wait_firewall_revalidates_claimed_live_trigger(
    tmp_path, monkeypatch, symbol, side,
):
    from core import config

    monkeypatch.setattr(config, "ENTRY_DISABLED_SYMBOLS", ())
    account, candidate, _ = _wait_account(tmp_path, symbol=symbol, side=side)
    result = asyncio.run(validate_account_entry(
        account,
        symbol,
        side,
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
    assert result["side"] == side
    assert result["confirmation_bar_id"] == 960_000


@pytest.mark.parametrize(
    ("mutation", "expected_reason"),
    [
        ("stale_cross", "BLOCKED_LONG_MA5_GOLDEN_CROSS_NOT_FRESH"),
        ("down_slope", "VETO_LONG_MA_CONDITION_NOT_MET"),
        ("below_middle", "BLOCKED_LONG_PRICE_BELOW_KC_MIDDLE"),
        ("kc_down", "BLOCKED_LONG_KC_NOT_UP"),
    ],
)
def test_final_wait_firewall_blocks_long_without_all_new_gates(
    tmp_path, monkeypatch, mutation, expected_reason,
):
    from core import config

    monkeypatch.setattr(config, "ENTRY_DISABLED_SYMBOLS", ())
    account, candidate, frame = _wait_account(tmp_path)
    if mutation == "stale_cross":
        frame.loc[frame.index[-3], "ma5"] = 99.5
        frame.loc[frame.index[-3], "ma15"] = 99.0
    elif mutation == "down_slope":
        frame.loc[frame.index[-3], "ma5"] = 101.0
        frame.loc[frame.index[-3], "ma15"] = 102.0
        frame.loc[frame.index[-2], "ma5"] = 100.5
    elif mutation == "below_middle":
        account.wait_quote_provider = lambda _symbol: {
            "price": 100.0,
            "observed_at": time.monotonic(),
        }
    else:
        frame.loc[frame.index[-1], "channel_state"] = "KC向下"

    with pytest.raises(ValueError, match=expected_reason):
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


def test_final_wait_firewall_blocks_short_when_ma5_turns_up(
    tmp_path, monkeypatch,
):
    from core import config

    monkeypatch.setattr(config, "ENTRY_DISABLED_SYMBOLS", ())
    account, candidate, frame = _wait_account(tmp_path, side="SHORT")
    frame.loc[frame.index[-2], "ma5"] = 101.5
    frame.loc[frame.index[-2], "ma15"] = 102.0

    with pytest.raises(ValueError, match="VETO_SHORT_MA_CONDITION_NOT_MET"):
        asyncio.run(validate_account_entry(
            account,
            "CAP/USDT",
            "SHORT",
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


def test_final_wait_firewall_applies_macro_structure_gate(tmp_path, monkeypatch):
    from core import config
    from core.gates.macro_candle_structure_gate import MacroCandleStructureGate

    monkeypatch.setattr(config, "ENTRY_DISABLED_SYMBOLS", ())
    account, candidate, _ = _wait_account(tmp_path)
    calls = []

    def veto(*args, **kwargs):
        calls.append((args, kwargs))
        return False, "TEST_MACRO_VETO"

    monkeypatch.setattr(MacroCandleStructureGate, "can_open", veto)

    with pytest.raises(ValueError, match="MACRO_CANDLE_STRUCTURE_GATE: TEST_MACRO_VETO"):
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

    assert calls[0][1]["entry_pattern"] == "CONTINUATION"


def test_final_wait_firewall_rejects_stale_live_quote(tmp_path, monkeypatch):
    from core import config

    monkeypatch.setattr(config, "ENTRY_DISABLED_SYMBOLS", ())
    account, candidate, _ = _wait_account(tmp_path)
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
