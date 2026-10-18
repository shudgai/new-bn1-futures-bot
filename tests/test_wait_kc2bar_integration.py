import asyncio
import hashlib
import time
from types import SimpleNamespace

import pandas as pd
import pytest
import ccxt.async_support as ccxt

from core.services import entry_firewall, entry_contract, symbol_runner
from core.services.entry_firewall import validate_account_entry
from core.services.wait_entry_authority import WaitEntryAuthority
from core.services.symbol_runner import (
    _submit_kc2bar_entry,
    _submit_wait_entry,
)
from core.engine import TradingEngine
import core.paper_account as paper_account
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


def _add_market_structure_history(frame, side):
    frame = frame.copy()
    closed = frame.loc[frame["is_closed"].astype(bool)]
    if len(closed) >= 15 or len(closed) < 2:
        return frame
    previous = closed.iloc[-2]
    current = closed.iloc[-1]
    atr = float(current["atr"])
    ma15 = float(current["ma15"])
    ma5 = float(current["ma5"])
    sign = 1.0 if side == "LONG" else -1.0
    gap = ma5 - ma15 if side == "LONG" else ma15 - ma5
    if gap > 0 and gap < 0.30 * atr:
        frame.loc[current.name, "ma5"] = (
            ma15 + 0.30 * atr if side == "LONG" else ma15 - 0.30 * atr
        )
    count = 15 - len(closed)
    previous_middle = float(previous["kc_middle"])
    upper_offset = float(previous["kc_upper"] - previous["kc_middle"])
    lower_offset = float(previous["kc_middle"] - previous["kc_lower"])
    base_ma5 = float(previous["ma5"])
    base_ma15 = float(previous["ma15"])
    base_close = float(previous["close"])
    ma5_step = 0.5 if side == "LONG" else 1.0
    ma15_step = 0.25 if side == "LONG" else 0.4
    history = []
    first_timestamp = int(closed.iloc[0]["timestamp"])
    for offset in range(count, 0, -1):
        middle = previous_middle - sign * 0.1 * offset
        close = base_close - sign * 0.1 * offset
        history.append({
            "timestamp": first_timestamp - offset * 60_000,
            "open": close,
            "high": close + 0.5,
            "low": close - 0.5,
            "close": close,
            "atr": atr,
            "ma5": base_ma5 - sign * ma5_step * offset,
            "ma15": base_ma15 - sign * ma15_step * offset,
            "kc_middle": middle,
            "kc_upper": middle + upper_offset,
            "kc_lower": middle - lower_offset,
            "is_closed": True,
        })
    result = pd.concat([pd.DataFrame(history), frame], ignore_index=True)
    result.attrs.update(frame.attrs)
    return result


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


@pytest.mark.parametrize(
    ("previous", "current", "expected"),
    [(100.0, 101.0, "LONG"), (101.0, 100.0, "SHORT"), (100.0, 100.0, None)],
)
def test_conflicting_authorities_are_filtered_only_by_verified_kc_slope(
    previous, current, expected,
):
    frame = pd.DataFrame([
        {"timestamp": 60_000, "kc_middle": previous, "is_closed": True},
        {"timestamp": 120_000, "kc_middle": current, "is_closed": True},
    ])
    frame.attrs.update({"entry_finality_verified": True, "timeframe_ms": 60_000})
    authorities = [
        {"authority": "WILD_SWING", "side": "LONG"},
        {"authority": "KC2BAR", "side": "SHORT"},
    ]

    filtered, allowed_side = symbol_runner._filter_authorities_by_kc_direction(
        authorities, frame,
    )

    assert allowed_side == expected
    if expected is None:
        assert filtered == authorities
    else:
        assert filtered == [next(item for item in authorities if item["side"] == expected)]


def test_unverified_kc_frame_cannot_resolve_authority_conflict():
    frame = pd.DataFrame([
        {"timestamp": 60_000, "kc_middle": 100.0, "is_closed": True},
        {"timestamp": 120_000, "kc_middle": 101.0, "is_closed": True},
    ])
    frame.attrs["timeframe_ms"] = 60_000
    authorities = [{"side": "LONG"}, {"side": "SHORT"}]

    assert symbol_runner._filter_authorities_by_kc_direction(
        authorities, frame,
    ) == (authorities, None)


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
    if side == "LONG":
        rows = [
            {
                "timestamp": 120_000, "open": 99.2, "high": 100.7,
                "low": 99.0, "close": 100.6, "kc_upper": 100.5,
                "kc_lower": 98.5, "kc_middle": 99.5, "ma5": 99.8,
                "ma15": 100.0, "atr": 2.0, "is_closed": True,
            },
            {
                "timestamp": 180_000, "open": 100.0, "high": 101.3,
                "low": 99.9, "close": 101.2, "kc_upper": 101.0,
                "kc_lower": 99.0, "kc_middle": 100.0, "ma5": 100.5,
                "ma15": 100.2, "atr": 2.0, "is_closed": True,
            },
            {
                "timestamp": 240_000, "open": 100.8, "high": 101.3,
                "low": 100.8, "close": 101.0, "kc_upper": 100.5,
                "kc_lower": 98.5, "kc_middle": 99.5, "ma15": 100.0,
                "atr": 2.0, "is_closed": False,
            },
        ]
    else:
        rows = [
            {
                "timestamp": 120_000, "open": 100.8, "high": 101.0,
                "low": 99.7, "close": 99.8, "kc_upper": 102.0,
                "kc_lower": 100.0, "kc_middle": 101.0, "ma5": 100.3,
                "ma15": 100.6, "atr": 2.0, "is_closed": True,
            },
            {
                "timestamp": 180_000, "open": 100.0, "high": 100.1,
                "low": 98.7, "close": 99.0, "kc_upper": 101.0,
                "kc_lower": 99.5, "kc_middle": 100.0, "ma5": 99.8,
                "ma15": 100.2, "atr": 2.0, "is_closed": True,
            },
            {
                "timestamp": 240_000, "open": 99.2, "high": 99.2,
                "low": 98.7, "close": 99.0, "kc_upper": 101.5,
                "kc_lower": 99.5, "kc_middle": 100.5, "ma15": 100.0,
                "atr": 2.0, "is_closed": False,
            },
        ]
    frame = pd.DataFrame(rows)
    frame.attrs.update({
        "entry_finality_verified": True,
        "timeframe_ms": 60_000,
    })
    frame = _add_market_structure_history(frame, side)
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


class _TestnetEntryExchange:
    def __init__(self, outcome):
        self.outcome = outcome
        self.position = None
        self.order = None

    async def load_markets(self):
        return {}

    def market(self, _symbol):
        return {
            "linear": True,
            "contractSize": 1,
            "info": {
                "filters": [
                    {
                        "filterType": "LOT_SIZE",
                        "stepSize": "0.001",
                        "minQty": "0.001",
                        "maxQty": "1000000",
                    },
                    {
                        "filterType": "MARKET_LOT_SIZE",
                        "stepSize": "0.001",
                        "minQty": "0.001",
                        "maxQty": "1000000",
                    },
                    {
                        "filterType": "MIN_NOTIONAL",
                        "notional": "5",
                    },
                ],
            },
        }

    def amount_to_precision(self, _symbol, amount):
        return f"{float(amount):.3f}"

    def price_to_precision(self, _symbol, price):
        return f"{float(price):.4f}"

    async def set_leverage(self, _leverage, _symbol):
        return None

    async def set_margin_mode(self, _mode, _symbol):
        return None

    async def create_order(
        self, symbol, _order_type, side, amount, _price=None, params=None,
    ):
        requested = float(amount)
        filled = requested if self.outcome == "filled" else requested * 0.4
        status = "closed" if self.outcome == "filled" else "open"
        self.order = {
            "id": "mock-order",
            "symbol": symbol,
            "side": side,
            "amount": requested,
            "filled": filled,
            "average": 101.0,
            "status": status,
            "params": dict(params or {}),
        }
        if filled > 0:
            self.position = {
                "side": "LONG" if side == "buy" else "SHORT",
                "qty": filled,
                "entry_price": 101.0,
            }
        return dict(self.order)

    async def cancel_order(self, _order_id, _symbol, _params=None):
        if self.outcome == "ambiguous_partial":
            raise ccxt.NetworkError("mock cancel acknowledgement unknown")
        if self.order is not None:
            self.order["status"] = "canceled"
        return {}

    async def fetch_order(self, _order_id, _symbol, _params=None):
        if self.outcome == "ambiguous_partial":
            raise ccxt.NetworkError("mock order reconciliation unavailable")
        return dict(self.order)


@pytest.mark.parametrize(
    ("outcome", "opened", "claim_status"),
    [
        ("filled", True, "CONSUMED"),
        ("partial_terminal", True, "CONSUMED"),
        ("ambiguous_partial", False, "UNKNOWN"),
    ],
)
def test_wait_testnet_submission_reconciles_fills_before_consuming_claim(
    tmp_path, monkeypatch, outcome, opened, claim_status,
):
    from core import config

    monkeypatch.setattr(config, "ENTRY_DISABLED_SYMBOLS", ())
    exchange = _TestnetEntryExchange(outcome)
    account = BinanceTestnetAccount(
        exchange,
        state_file=str(tmp_path / f"testnet_{outcome}.json"),
    )
    account_logs = []
    account.log = lambda message, *_args: account_logs.append(str(message))
    account.balance = 200.0
    account.available_balance = 200.0
    account._is_live_mainnet = lambda: False
    account._prepare_leverage = _async_noop
    account._cancel_all_orders = _async_noop

    async def refresh(*, force=False):
        assert force is True
        account.last_sync_at = time.time()
        if exchange.position is not None:
            account.positions["CAP/USDT"] = {
                **account.position_meta.get("CAP/USDT", {}),
                **exchange.position,
            }
        return 0.0

    account.refresh = refresh
    authority = WaitEntryAuthority(tmp_path / f"authority_{outcome}.json")
    account.wait_entry_authority = authority
    first = {
        "timestamp": 60_000, "open": 100.0, "high": 100.5, "low": 99.5,
        "close": 100.0, "atr": 2.0, "is_closed": True,
    }
    setup = {
        "timestamp": 120_000, "open": 100.0, "high": 100.2, "low": 99.4,
        "close": 99.6, "atr": 2.0, "is_closed": True,
    }
    live = {
        "timestamp": 180_000, "open": 100.0, "high": 101.2, "low": 99.8,
        "close": 101.0, "atr": 2.0, "is_closed": False,
    }
    authority.observe(
        "CAP/USDT", [first],
        {**first, "timestamp": 120_000, "is_closed": False},
        100.0, quote_fresh=True, position_open=False,
        authoritative_position_state=True,
    )
    candidate = authority.observe(
        "CAP/USDT", [first, setup], live, 101.0,
        quote_fresh=True, position_open=False,
        authoritative_position_state=True,
    )
    frame = pd.DataFrame([
        {
            **row,
            "kc_upper": 110.0,
            "kc_lower": 90.0,
            "kc_middle": 99.9 if row["timestamp"] == 60_000 else 100.1,
            "ma15": 100.0,
            "ma5": (
                99.0 if row["timestamp"] == 60_000 else
                101.0 if row["timestamp"] == 180_000 else 100.5
            ),
        }
        for row in (first, setup, live)
    ])
    frame.attrs.update({
        "entry_finality_verified": True,
        "timeframe_ms": 60_000,
    })
    frame = _add_market_structure_history(frame, "LONG")
    account.entry_frame_provider = lambda _symbol: _resolved(frame)
    account.wait_quote_provider = lambda _symbol: {
        "price": 101.0,
        "observed_at": time.monotonic(),
    }
    engine = TradingEngine.__new__(TradingEngine)
    engine.account = account
    engine.wait_entry_authority = authority
    engine.symbol_rotation = SimpleNamespace(
        get_dynamic_leverage=lambda _symbol, _score: 5,
    )

    submitted = asyncio.run(
        _submit_wait_entry(engine, "CAP/USDT", frame, candidate),
    )

    assert submitted is opened, account_logs
    assert exchange.order["params"]["newClientOrderId"] == (
        "WAIT" + candidate["trigger_id"][:32]
    )
    assert authority._data["claims"][candidate["trigger_id"]]["status"] == (
        claim_status
    )
    if outcome == "ambiguous_partial":
        assert "CAP/USDT" in account.positions
        assert authority.has_unknown_claims()
    else:
        assert account.positions["CAP/USDT"]["position_uuid"] == (
            authority.position_uuid_for_trigger(candidate["trigger_id"])
        )


async def _async_noop(*_args, **_kwargs):
    return None


def test_wait_runner_to_paper_account_revalidates_and_consumes_claim(
    tmp_path, monkeypatch,
):
    from core import config

    monkeypatch.setattr(config, "ENTRY_DISABLED_SYMBOLS", ())
    monkeypatch.setattr(
        paper_account, "STATE_FILE", str(tmp_path / "paper_account.json"),
    )
    authority = WaitEntryAuthority(tmp_path / "wait.json")
    first = {
        "timestamp": 60_000, "open": 100.0, "high": 100.5, "low": 99.5,
        "close": 100.0, "atr": 2.0, "is_closed": True,
    }
    setup = {
        "timestamp": 120_000, "open": 100.0, "high": 100.2, "low": 99.4,
        "close": 99.6, "atr": 2.0, "is_closed": True,
    }
    live = {
        "timestamp": 180_000, "open": 100.0, "high": 101.2, "low": 99.8,
        "close": 101.0, "atr": 2.0, "is_closed": False,
    }
    assert authority.observe(
        "CAP/USDT",
        [first],
        {**first, "timestamp": 120_000, "is_closed": False},
        100.0,
        quote_fresh=True,
        position_open=False,
        authoritative_position_state=True,
    )["status"] == "WAIT_BASELINE_ESTABLISHED"
    candidate = authority.observe(
        "CAP/USDT",
        [first, setup],
        live,
        101.0,
        quote_fresh=True,
        position_open=False,
        authoritative_position_state=True,
    )
    assert candidate["status"] == "TRIGGER_AVAILABLE"

    frame = pd.DataFrame([
        {
            **row,
            "kc_upper": 110.0,
            "kc_lower": 90.0,
            "kc_middle": 99.9 if row["timestamp"] == 60_000 else 100.1,
            "ma15": 100.0,
            "ma5": (
                99.0 if row["timestamp"] == 60_000 else
                101.0 if row["timestamp"] == 180_000 else 100.5
            ),
        }
        for row in (first, setup, live)
    ])
    frame.attrs.update({
        "entry_finality_verified": True,
        "timeframe_ms": 60_000,
    })
    frame = _add_market_structure_history(frame, "LONG")
    account = paper_account.PaperAccount()
    account.balance = 200.0
    account.available_balance = 200.0
    account.entry_frame_provider = lambda _symbol: _resolved(frame)
    account.wait_quote_provider = lambda _symbol: {
        "price": 101.0,
        "observed_at": time.monotonic(),
    }
    account.wait_entry_authority = authority
    engine = TradingEngine.__new__(TradingEngine)
    engine.account = account
    engine.wait_entry_authority = authority
    engine.symbol_rotation = SimpleNamespace(
        get_dynamic_leverage=lambda _symbol, _score: 5,
    )

    opened = asyncio.run(_submit_wait_entry(
        engine, "CAP/USDT", frame, candidate,
    ))

    assert opened is True
    position = account.positions["CAP/USDT"]
    assert position["entry_authority_claim_id"] == candidate["trigger_id"]
    assert position["position_uuid"] == authority.position_uuid_for_trigger(
        candidate["trigger_id"],
    )
    assert authority._data["claims"][candidate["trigger_id"]]["status"] == "CONSUMED"


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_kc2bar_runner_to_paper_account_revalidates_and_consumes_claim(
    tmp_path, monkeypatch, side,
):
    from core import config

    monkeypatch.setattr(config, "ENTRY_DISABLED_SYMBOLS", ())
    monkeypatch.setattr(
        paper_account, "STATE_FILE", str(tmp_path / f"paper_{side}.json"),
    )
    signal = _signal(side)
    monkeypatch.setattr(
        entry_contract, "evaluate_entry_contract",
        lambda _frame, **_kwargs: signal,
    )
    monkeypatch.setattr(
        entry_firewall, "evaluate_entry_contract",
        lambda _frame, **_kwargs: signal,
    )
    if side == "LONG":
        previous_middle, current_middle = 99.0, 100.3
        previous_ma5, current_ma5 = 99.3, 100.2
        previous_ma15, current_ma15 = 99.5, 99.8
        confirmation_upper, confirmation_lower = 102.0, 100.2
        live_upper, live_lower, live_middle = 100.5, 98.5, 99.5
        live_high, live_low = 101.5, 99.5
    else:
        previous_middle, current_middle = 101.0, 99.7
        previous_ma5, current_ma5 = 100.7, 99.8
        previous_ma15, current_ma15 = 100.5, 100.2
        confirmation_upper, confirmation_lower = 99.5, 98.5
        live_upper, live_lower, live_middle = 101.5, 99.5, 100.5
        live_high, live_low = 100.5, 98.5
    frame = pd.DataFrame([
        {
                "timestamp": 120_000, "open": 100.0,
                "high": 102.2 if side == "LONG" else 100.5,
                "low": 99.5 if side == "LONG" else 97.8,
                "close": 102.0 if side == "LONG" else 98.0, "atr": 2.0,
                "kc_upper": 101.0, "kc_lower": 97.0 if side == "LONG" else 99.0,
            "kc_middle": previous_middle, "ma5": previous_ma5,
            "ma15": previous_ma15, "is_closed": True,
        },
        {
                "timestamp": 180_000, "open": 100.0, "high": 101.2,
                "low": 98.8, "close": 100.8 if side == "LONG" else 99.2,
                "atr": 2.0, "kc_upper": 100.5 if side == "LONG" else 101.0,
                "kc_lower": 99.0 if side == "LONG" else 99.5,
            "kc_middle": current_middle, "ma5": current_ma5,
            "ma15": current_ma15, "is_closed": True,
        },
        {
            "timestamp": 240_000, "open": 100.0, "high": live_high,
                "low": live_low, "close": signal["price"], "atr": 2.0,
            "kc_upper": live_upper, "kc_lower": live_lower,
                "kc_middle": live_middle,
                "ma5": current_ma5 + (0.3 if side == "LONG" else -0.3),
                "ma15": 100.0,
            "is_closed": False,
        },
    ])
    frame.attrs.update({
        "entry_finality_verified": True,
        "timeframe_ms": 60_000,
    })
    frame = _add_market_structure_history(frame, side)
    candidates = symbol_runner._kc2bar_candidates(
        frame, "CAP/USDT", signal["price"],
    )
    assert len(candidates) == 1
    candidate = candidates[0]
    authority = WaitEntryAuthority(tmp_path / f"wait_{side}.json")
    account = paper_account.PaperAccount()
    account.balance = 200.0
    account.available_balance = 200.0
    account.entry_frame_provider = lambda _symbol: _resolved(frame)
    account.wait_quote_provider = lambda _symbol: {
        "price": signal["price"],
        "observed_at": time.monotonic(),
    }
    account.wait_entry_authority = authority
    engine = TradingEngine.__new__(TradingEngine)
    engine.account = account
    engine.wait_entry_authority = authority
    engine.symbol_rotation = SimpleNamespace(
        get_dynamic_leverage=lambda _symbol, _score: 5,
    )

    asyncio.run(_submit_kc2bar_entry(
        engine, "CAP/USDT", frame, candidate,
    ))

    position = account.positions["CAP/USDT"]
    assert position["entry_authority_claim_id"] == candidate["trigger_id"]
    assert position["position_uuid"] == authority.position_uuid_for_trigger(
        candidate["trigger_id"],
    )
    assert authority._data["claims"][candidate["trigger_id"]]["status"] == "CONSUMED"
