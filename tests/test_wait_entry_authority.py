import json

import pytest

from core.services.wait_entry_authority import WaitEntryAuthority


def _candle(
    timestamp, opening=100.0, closing=100.0, *, atr=2.0, span=1.0,
    is_closed=True,
):
    return {
        "timestamp": timestamp,
        "open": opening,
        "high": max(opening, closing) + (span - abs(closing - opening)) / 2,
        "low": min(opening, closing) - (span - abs(closing - opening)) / 2,
        "close": closing,
        "atr": atr,
        "is_closed": is_closed,
    }


def _observe(
    authority, symbol, closed, live, quote,
    *, position_open=False, authoritative_position_state=True, quote_fresh=True,
):
    return authority.observe(
        symbol,
        closed,
        live,
        quote,
        quote_fresh=quote_fresh,
        position_open=position_open,
        authoritative_position_state=authoritative_position_state,
    )


@pytest.mark.parametrize(
    "symbol,side,setup_color",
    [
        ("LOBSTER/USDT", "LONG", "RED"),
        ("LOBSTER/USDT", "SHORT", "GREEN"),
        ("CAP/USDT", "LONG", "RED"),
        ("CAP/USDT", "SHORT", "GREEN"),
    ],
)
def test_four_symbol_direction_paths_trigger_on_live_big_body(
    tmp_path, symbol, side, setup_color,
):
    authority = WaitEntryAuthority(tmp_path / "wait.json")
    baseline = _candle(60_000)
    baseline_live = _candle(120_000, is_closed=False)
    assert _observe(
        authority, symbol, [baseline], baseline_live, 100.0,
    )["status"] == "WAIT_BASELINE_ESTABLISHED"

    setup = _candle(
        120_000,
        100.0,
        99.6 if setup_color == "RED" else 100.4,
    )
    live = _candle(180_000, 100.0, 100.2, span=2.4, is_closed=False)
    candidate = _observe(
        authority, symbol, [baseline, setup], live,
        101.0 if side == "LONG" else 99.0,
    )

    assert candidate["status"] == "TRIGGER_AVAILABLE"
    assert candidate["side"] == side
    assert candidate["setup_bar_id"] == 120_000
    assert candidate["live_bar_id"] == 180_000
    assert candidate["atr"] == 2.0


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_setup_and_live_thresholds_are_inclusive(tmp_path, side):
    authority = WaitEntryAuthority(tmp_path / "wait.json")
    baseline = _candle(60_000, atr=2.0)
    _observe(
        authority, "CAP/USDT", [baseline],
        _candle(120_000, is_closed=False), 100.0,
    )
    setup = _candle(
        120_000,
        100.0,
        99.5 if side == "LONG" else 100.5,
        span=0.6,
    )
    trigger_live = _candle(180_000, 100.0, 100.2, span=2.4, is_closed=False)
    exact_quote = 101.0 if side == "LONG" else 99.0
    result = _observe(
        authority, "CAP/USDT", [baseline, setup], trigger_live, exact_quote,
    )
    assert result["status"] == "TRIGGER_AVAILABLE"


def test_doji_precedes_small_setup_and_invalid_ohlc_is_rejected(tmp_path):
    authority = WaitEntryAuthority(tmp_path / "wait.json")
    baseline = _candle(60_000)
    _observe(
        authority, "LOBSTER/USDT", [baseline],
        _candle(120_000, is_closed=False), 100.0,
    )
    doji = _candle(120_000, 100.0, 100.05, span=1.0)
    result = _observe(
        authority, "LOBSTER/USDT", [baseline, doji],
        _candle(180_000, is_closed=False), 100.0,
    )
    assert result["status"] == "WAIT_NO_ACTIVE_SETUP"
    state = json.loads((tmp_path / "wait.json").read_text())
    assert state["symbols"]["LOBSTER/USDT"]["active_side"] is None

    invalid = dict(doji, high=99.0)
    result = _observe(
        authority, "CAP/USDT", [baseline, invalid],
        _candle(180_000, is_closed=False), 100.0,
    )
    assert result["status"] == "WAIT_INVALID_MARKET_DATA"


@pytest.mark.parametrize(
    "symbol",
    ["LOBSTER/USDT", "CAP/USDT"],
)
def test_zero_range_candle_is_invalid_not_doji(tmp_path, symbol):
    authority = WaitEntryAuthority(tmp_path / f"{symbol}.json")
    zero_range = {
        "timestamp": 60_000,
        "open": 100.0,
        "high": 100.0,
        "low": 100.0,
        "close": 100.0,
        "atr": 2.0,
        "is_closed": True,
    }
    result = _observe(
        authority, symbol, [zero_range],
        _candle(120_000, is_closed=False), 100.0,
    )
    assert result["status"] == "WAIT_INVALID_MARKET_DATA"


@pytest.mark.parametrize(
    "body,expected",
    [
        (0.099999999, None),
        (0.1 - 5e-14, "GREEN"),
        (0.1, "GREEN"),
        (0.1 + 5e-14, "GREEN"),
    ],
)
def test_entry_doji_threshold_uses_shared_tolerance(body, expected):
    candle = {
        "timestamp": 60_000.0,
        "open": 1.0,
        "high": 1.5,
        "low": 0.5,
        "close": 1.0 + body,
        "atr": 1.0,
    }
    assert WaitEntryAuthority._small_direction(candle, 1.0) == expected


def test_small_refresh_and_bridge_do_not_flip_active_wait(tmp_path):
    authority = WaitEntryAuthority(tmp_path / "wait.json")
    c0 = _candle(60_000)
    _observe(authority, "CAP/USDT", [c0], _candle(120_000, is_closed=False), 100)
    setup = _candle(120_000, 100, 99.6)
    _observe(authority, "CAP/USDT", [c0, setup], _candle(180_000, is_closed=False), 100)
    bridge = _candle(180_000, 99.6, 99.8)
    _observe(
        authority, "CAP/USDT", [c0, setup, bridge],
        _candle(240_000, is_closed=False), 99.8,
    )
    state = json.loads((tmp_path / "wait.json").read_text())["symbols"]["CAP/USDT"]
    assert state["active_side"] == "LONG"
    assert state["setup_bar_id"] == 120_000

    refresh = _candle(240_000, 99.8, 99.5)
    _observe(
        authority, "CAP/USDT", [c0, setup, bridge, refresh],
        _candle(300_000, is_closed=False), 99.5,
    )
    state = json.loads((tmp_path / "wait.json").read_text())["symbols"]["CAP/USDT"]
    assert state["active_side"] == "LONG"
    assert state["setup_bar_id"] == 240_000


def test_doji_and_opposite_big_keep_existing_wait_without_refresh(tmp_path):
    authority = WaitEntryAuthority(tmp_path / "wait.json")
    c0 = _candle(60_000)
    _observe(authority, "CAP/USDT", [c0], _candle(120_000, is_closed=False), 100)
    setup = _candle(120_000, 100, 99.6)
    _observe(
        authority, "CAP/USDT", [c0, setup],
        _candle(180_000, is_closed=False), 100,
    )
    doji = _candle(180_000, 99.8, 99.82, span=1.0)
    _observe(
        authority, "CAP/USDT", [c0, setup, doji],
        _candle(240_000, is_closed=False), 99.82,
    )
    state_after_doji = json.loads(
        (tmp_path / "wait.json").read_text(),
    )["symbols"]["CAP/USDT"]
    assert state_after_doji["active_side"] == "LONG"
    assert state_after_doji["setup_bar_id"] == 120_000

    opposite_big = _candle(240_000, 100, 99.0, span=2.4)
    result = _observe(
        authority, "CAP/USDT", [setup, doji, opposite_big],
        _candle(300_000, is_closed=False), 100.0,
    )
    state_after_big = json.loads(
        (tmp_path / "wait.json").read_text(),
    )["symbols"]["CAP/USDT"]
    assert result["status"] == "WAIT_LIVE_TRIGGER"
    assert state_after_big["active_side"] == "LONG"
    assert state_after_big["setup_bar_id"] == 120_000


def test_completed_big_trigger_without_live_observation_is_not_backfilled(tmp_path):
    authority = WaitEntryAuthority(tmp_path / "wait.json")
    c0 = _candle(60_000)
    _observe(authority, "CAP/USDT", [c0], _candle(120_000, is_closed=False), 100)
    setup = _candle(120_000, 100, 99.6)
    _observe(
        authority, "CAP/USDT", [c0, setup],
        _candle(180_000, is_closed=False), 100,
    )
    completed_big = _candle(180_000, 100, 101.0, span=2.4)
    result = _observe(
        authority, "CAP/USDT", [setup, completed_big],
        _candle(240_000, is_closed=False), 101.0,
    )

    assert result["status"] == "MISSED_LIVE_TRIGGER"
    state = json.loads(
        (tmp_path / "wait.json").read_text(),
    )["symbols"]["CAP/USDT"]
    assert state["active_side"] == "LONG"
    assert state["setup_bar_id"] == 120_000


def test_restart_preserves_wait_but_does_not_replay_live_trigger(tmp_path):
    path = tmp_path / "wait.json"
    first = WaitEntryAuthority(path)
    c0 = _candle(60_000)
    _observe(first, "LOBSTER/USDT", [c0], _candle(120_000, is_closed=False), 100)
    setup = _candle(120_000, 100, 99.6)
    live = _candle(180_000, 100, 101.0, span=2.4, is_closed=False)
    first.observe(
        "LOBSTER/USDT", [c0, setup], live, 101.0,
        quote_fresh=True, position_open=False,
        authoritative_position_state=True,
    )

    restarted = WaitEntryAuthority(path)
    assert _observe(
        restarted, "LOBSTER/USDT", [c0, setup], live, 101.0,
    )["status"] == "WAIT_RESTART_BASELINE"
    assert _observe(
        restarted, "LOBSTER/USDT", [c0, setup], live, 101.0,
    )["status"] == "WAIT_RESTART_BASELINE"


def test_restart_quarantines_bound_order_claim_for_reconciliation(tmp_path):
    path = tmp_path / "wait.json"
    authority = WaitEntryAuthority(path)
    baseline = _candle(60_000)
    _observe(
        authority, "CAP/USDT", [baseline],
        _candle(120_000, is_closed=False), 100.0,
    )
    setup = _candle(120_000, 100.0, 99.6)
    live = _candle(180_000, 100.0, 100.2, span=2.4, is_closed=False)
    candidate = _observe(authority, "CAP/USDT", [baseline, setup], live, 101.0)
    trigger_id = candidate["trigger_id"]
    assert authority.claim(trigger_id)
    assert authority.bind_order_identity(
        trigger_id, "WAIT" + trigger_id[:32],
    )

    restarted = WaitEntryAuthority(path)
    unknown = restarted.unknown_claims("CAP/USDT")
    assert len(unknown) == 1
    assert unknown[0]["trigger_id"] == trigger_id
    assert unknown[0]["client_order_id"] == "WAIT" + trigger_id[:32]


def test_claim_is_single_use_unknown_is_quarantined_and_reconciled(tmp_path):
    authority = WaitEntryAuthority(tmp_path / "wait.json")
    c0 = _candle(60_000)
    _observe(authority, "CAP/USDT", [c0], _candle(120_000, is_closed=False), 100)
    setup = _candle(120_000, 100, 99.6)
    live = _candle(180_000, 100, 100.2, span=2.4, is_closed=False)
    candidate = _observe(authority, "CAP/USDT", [c0, setup], live, 101.0)
    trigger = candidate["trigger_id"]

    assert authority.claim(trigger)
    assert not authority.claim(trigger)
    assert authority.is_claim_valid(trigger, "CAP/USDT", "LONG", 180_000)
    assert authority.resolve(
        trigger, filled_qty=0, position_present=False, outcome="UNKNOWN",
    ) == "UNKNOWN"
    next_closed = _candle(180_000, 99.9, 99.7)
    quarantined = _observe(
        authority, "CAP/USDT", [setup, next_closed],
        _candle(240_000, span=2.4, is_closed=False), 101.0,
    )
    assert quarantined["status"] == "WAIT_SYMBOL_QUARANTINED"
    assert authority.reconcile(
        trigger, filled_qty=0, position_present=False, order_reconciled=True,
    ) == "FAILED"


def test_inflight_claim_blocks_a_second_live_bar_for_same_symbol(tmp_path):
    authority = WaitEntryAuthority(tmp_path / "wait.json")
    baseline = _candle(60_000)
    _observe(
        authority, "CAP/USDT", [baseline],
        _candle(120_000, is_closed=False), 100.0,
    )
    setup = _candle(120_000, 100.0, 99.6)
    live = _candle(180_000, 100.0, 100.2, span=2.4, is_closed=False)
    first = _observe(authority, "CAP/USDT", [baseline, setup], live, 101.0)
    assert authority.claim(first["trigger_id"])

    bridge = _candle(180_000, 99.6, 99.8)
    next_live = _candle(240_000, 100.0, 100.2, span=2.4, is_closed=False)
    second = _observe(
        authority, "CAP/USDT", [setup, bridge], next_live, 101.0,
    )

    assert second["status"] == "WAIT_SYMBOL_SUBMISSION_IN_FLIGHT"


def test_claim_revalidation_requires_same_live_trigger_and_fresh_threshold(tmp_path):
    authority = WaitEntryAuthority(tmp_path / "wait.json")
    baseline = _candle(60_000)
    _observe(
        authority, "CAP/USDT", [baseline],
        _candle(120_000, is_closed=False), 100.0,
    )
    setup = _candle(120_000, 100.0, 99.6)
    live = _candle(180_000, 100.0, 100.2, span=2.4, is_closed=False)
    candidate = _observe(authority, "CAP/USDT", [baseline, setup], live, 101.0)
    assert authority.claim(candidate["trigger_id"])

    assert authority.revalidate_claim(
        candidate,
        [baseline, setup],
        live,
        101.0,
        quote_fresh=True,
        position_open=False,
        authoritative_position_state=True,
    )
    assert not authority.revalidate_claim(
        candidate,
        [baseline, setup],
        live,
        100.9,
        quote_fresh=True,
        position_open=False,
        authoritative_position_state=True,
    )
    assert not authority.revalidate_claim(
        candidate,
        [baseline, setup],
        _candle(240_000, is_closed=False),
        101.0,
        quote_fresh=True,
        position_open=False,
        authoritative_position_state=True,
    )


def test_lobster_aliases_share_wait_state_and_claim_identity(tmp_path):
    authority = WaitEntryAuthority(tmp_path / "wait.json")
    baseline = _candle(60_000)
    _observe(
        authority, "LOBSTER/USDT", [baseline],
        _candle(120_000, is_closed=False), 100.0,
    )
    setup = _candle(120_000, 100.0, 99.6)
    _observe(
        authority, "龙虾/USDT", [baseline, setup],
        _candle(180_000, is_closed=False), 100.0,
    )
    live = _candle(180_000, 100.0, 100.2, span=2.4, is_closed=False)
    candidate = _observe(
        authority, "LOBSTER/USDT", [baseline, setup], live, 101.0,
    )

    assert candidate["status"] == "TRIGGER_AVAILABLE"
    assert candidate["symbol"] == "LOBSTER/USDT"
    assert authority.claim(candidate["trigger_id"])
    assert authority.is_claim_valid(
        candidate["trigger_id"], "龙虾/USDT", "LONG", 180_000,
    )


def test_partial_fill_with_position_consumes_wait_and_position_close_rebuilds(tmp_path):
    authority = WaitEntryAuthority(tmp_path / "wait.json")
    c0 = _candle(60_000)
    _observe(authority, "LOBSTER/USDT", [c0], _candle(120_000, is_closed=False), 100)
    setup = _candle(120_000, 100, 99.6)
    live = _candle(180_000, 100, 100.2, span=2.4, is_closed=False)
    candidate = _observe(authority, "LOBSTER/USDT", [c0, setup], live, 101.0)
    assert authority.claim(candidate["trigger_id"])
    assert authority.resolve(
        candidate["trigger_id"],
        filled_qty=0.1,
        position_present=True,
        outcome="PARTIAL",
    ) == "UNKNOWN"
    assert authority.reconcile(
        candidate["trigger_id"],
        filled_qty=0.1,
        position_present=True,
        order_reconciled=True,
    ) == "CONSUMED"

    held = _observe(
        authority, "LOBSTER/USDT", [c0, setup], live, 101,
        position_open=True,
    )
    assert held["status"] == "WAIT_POSITION_OPEN"
    flat = _observe(
        authority, "LOBSTER/USDT", [c0, setup], live, 101,
        position_open=False,
    )
    assert flat["status"] == "WAIT_REBUILD_AFTER_FLAT"
    state = json.loads((tmp_path / "wait.json").read_text())["symbols"]["LOBSTER/USDT"]
    assert state["active_side"] is None


def test_gap_and_stale_data_suspend_wait(tmp_path):
    authority = WaitEntryAuthority(tmp_path / "wait.json")
    c0 = _candle(60_000)
    _observe(authority, "CAP/USDT", [c0], _candle(120_000, is_closed=False), 100)
    setup = _candle(120_000, 100, 99.6)
    _observe(authority, "CAP/USDT", [c0, setup], _candle(180_000, is_closed=False), 100)
    gap = _candle(240_000, 100, 99.8)
    assert _observe(
        authority, "CAP/USDT", [c0, setup, gap],
        _candle(300_000, is_closed=False), 100,
    )["status"] == "WAIT_DATA_SUSPENDED"
    newer = _candle(300_000, 100, 99.7)
    assert _observe(
        authority, "CAP/USDT", [gap, newer],
        _candle(360_000, is_closed=False), 100,
    )["status"] == "WAIT_RESUMED_ON_NEW_DATA"
