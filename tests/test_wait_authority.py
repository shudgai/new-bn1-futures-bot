import copy
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pandas as pd
import pytest

from core.services.wait_authority import (
    STATE_KEY, WaitAuthority, completed_classification,
)


def account():
    return SimpleNamespace(position_meta={}, positions={}, pending_limit_orders={},
                           closing_lock=set(), save_state=Mock(), log=Mock())


def frame(bar, completed_open=100., completed_close=99.8, atr=1.):
    rows = [
        dict(timestamp=bar-120000., open=100., close=100., high=101., low=99.,
             atr=atr, is_closed=True),
        dict(timestamp=bar-60000., open=completed_open, close=completed_close,
             high=max(completed_open, completed_close)+.1,
             low=min(completed_open, completed_close)-.1, atr=atr, is_closed=True),
        dict(timestamp=bar, open=100., close=100., high=100., low=100.,
             atr=atr, is_closed=False),
    ]
    return pd.DataFrame(rows)


def observe(authority, symbol, f, monkeypatch, price=100., offset=1000, flat=True):
    stamp = float(f.iloc[-1].timestamp)+offset
    monkeypatch.setattr("time.time", lambda: stamp/1000)
    return authority.observe(symbol, f, price, stamp, flat_confirmed=flat)


def armed(symbol, side, monkeypatch):
    a = account()
    w = WaitAuthority(a)
    observe(w, symbol, frame(180000.), monkeypatch)
    close = 99.8 if side == "LONG" else 100.2
    observe(w, symbol, frame(240000., completed_close=close), monkeypatch)
    assert a.position_meta[STATE_KEY][symbol]["side"] == side
    return a, w


@pytest.mark.parametrize("symbol", ["龙虾/USDT", "CAP/USDT"])
@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_four_independent_live_trigger_paths(symbol, side, monkeypatch):
    a, w = armed(symbol, side, monkeypatch)
    sign = 1 if side == "LONG" else -1
    candidate = observe(w, symbol, frame(240000.), monkeypatch,
                        price=100+sign*.5, offset=2000)
    assert candidate["type"] == "WAIT_LIVE_BIG_"+side
    assert candidate["entry_atr"] == 1.
    assert candidate["wait_setup"]["bar"] == 180000.
    assert candidate["confirmation_bar_id"] == 240000.
    assert candidate["symbol"] == symbol
    assert set(a.position_meta[STATE_KEY]) == {symbol}


@pytest.mark.parametrize("symbol", ["龙虾/USDT", "CAP/USDT"])
@pytest.mark.parametrize("side", ["LONG", "SHORT"])
@pytest.mark.parametrize("body,expected", [(.499999, False), (.5, True), (.500001, True)])
def test_live_threshold_without_completed_doji_or_kc_or_ma(symbol, side, body, expected, monkeypatch):
    _, w = armed(symbol, side, monkeypatch)
    f = frame(240000.)
    assert bool(observe(w, symbol, f, monkeypatch,
                        price=100+(1 if side == "LONG" else -1)*body, offset=2000)) == expected


@pytest.mark.parametrize("symbol", ["龙虾/USDT", "CAP/USDT"])
@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_bridges_refresh_medium_doji_opposite_big_and_no_expiry(symbol, side, monkeypatch):
    a, w = armed(symbol, side, monkeypatch)
    sign = 1 if side == "LONG" else -1
    setup = copy.deepcopy(a.position_meta[STATE_KEY][symbol]["setup"])
    bar = 240000.
    for _ in range(25):
        bar += 60000
        observe(w, symbol, frame(bar, completed_close=100+sign*.2), monkeypatch)
        state = a.position_meta[STATE_KEY][symbol]
        assert state["side"] == side and state["setup"] == setup
        assert state["status"] == "WAIT_BRIDGE_PRESERVED"
    for close, status in ((100+sign*.35, "WAIT_MEDIUM_KEEP"),
                          (100., "WAIT_DOJI_KEEP"),
                          (100-sign*.7, "WAIT_COMPLETED_BIG_KEEP")):
        bar += 60000
        observe(w, symbol, frame(bar, completed_close=close), monkeypatch)
        state = a.position_meta[STATE_KEY][symbol]
        assert state["side"] == side and state["setup"] == setup
        assert state["status"] == status
    bar += 60000
    observe(w, symbol, frame(bar, completed_close=100-sign*.2), monkeypatch)
    assert a.position_meta[STATE_KEY][symbol]["setup"]["bar"] == bar-60000


@pytest.mark.parametrize("body,expected", [
    (.099, "DOJI"), (.1, "SMALL_GREEN"), (.101, "SMALL_GREEN"),
    (.1-1e-14, "SMALL_GREEN"), (.25, "SMALL_GREEN"), (.250001, "MEDIUM"),
])
def test_shared_doji_tolerance_and_small_boundary(body, expected):
    row = SimpleNamespace(open=1., close=1.+body, high=2., low=1.)
    assert completed_classification(row, 1.) == expected


@pytest.mark.parametrize("high,low,close", [(1., 1., 1.), (.9, 1., 1.),
                                         (1.1, 1., 1.2), (1.1, 1., float("nan"))])
def test_invalid_ohlc_not_doji_or_setup(high, low, close):
    assert completed_classification(SimpleNamespace(open=1., close=close, high=high, low=low), 1.) == "INVALID_CANDLE"


@pytest.mark.parametrize("symbol", ["龙虾/USDT", "CAP/USDT"])
@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_restart_does_not_replay_completed_setup_or_trigger(symbol, side, monkeypatch):
    import core.services.wait_authority as module
    a, w = armed(symbol, side, monkeypatch)
    setup = copy.deepcopy(a.position_meta[STATE_KEY][symbol]["setup"])
    monkeypatch.setattr(module, "SESSION", "new-process")
    observe(w, symbol, frame(600000., completed_close=100.8), monkeypatch)
    state = a.position_meta[STATE_KEY][symbol]
    assert state["setup"] == setup
    assert "candidate" not in state
    assert state["side"] == side


@pytest.mark.parametrize("symbol", ["龙虾/USDT", "CAP/USDT"])
@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_position_suspension_and_flat_rebuild_not_old_setup(symbol, side, monkeypatch):
    a, w = armed(symbol, side, monkeypatch)
    a.positions[symbol] = {"qty": 1., "side": side}
    observe(w, symbol, frame(300000.), monkeypatch)
    a.positions.clear()
    assert observe(w, symbol, frame(360000., completed_close=99.8),
                   monkeypatch, price=100.5) is None
    assert "setup" not in a.position_meta[STATE_KEY][symbol]
    close = 99.8 if side == "LONG" else 100.2
    observe(w, symbol, frame(420000., completed_close=close), monkeypatch)
    assert a.position_meta[STATE_KEY][symbol]["side"] == side


@pytest.mark.parametrize("symbol", ["龙虾/USDT", "CAP/USDT"])
@pytest.mark.parametrize("side", ["LONG", "SHORT"])
@pytest.mark.parametrize("outcome,filled,position,phase", [
    ("FAILED", 0., False, "FAILED"), ("TIMEOUT", 0., False, "UNKNOWN"),
    ("PARTIAL", .1, True, "FILLED"), ("UNKNOWN", .1, False, "UNKNOWN"),
    ("FILLED", 1., True, "FILLED"),
])
def test_claims_consume_only_authoritative_positive_position(symbol, side, outcome, filled, position, phase, monkeypatch):
    a, w = armed(symbol, side, monkeypatch)
    price = 100+(1 if side == "LONG" else -1)*.5
    d = observe(w, symbol, frame(240000.), monkeypatch, price=price, offset=2000)
    w.claim(symbol, d["wait_trigger_id"])
    with pytest.raises(ValueError, match="DUPLICATE_OR_UNKNOWN"):
        w.claim(symbol, d["wait_trigger_id"])
    if position:
        a.positions[symbol] = {"qty": filled, "side": side}
    w.settle(symbol, d["wait_trigger_id"], outcome=outcome, filled_qty=filled,
             position_confirmed=position)
    state = a.position_meta[STATE_KEY][symbol]
    assert state["claims"]["240000"]["phase"] == phase
    assert ("setup" not in state) == (phase == "FILLED")
    assert observe(w, symbol, frame(240000.), monkeypatch, price=price, offset=3000) is None


def test_missing_atr_keeps_wait_and_suspends_authority(monkeypatch):
    a, w = armed("CAP/USDT", "LONG", monkeypatch)
    f = frame(240000.)
    f.loc[f.index[-2], "atr"] = float("nan")
    assert observe(w, "CAP/USDT", f, monkeypatch, price=100.5, offset=2000) is None
    assert a.position_meta[STATE_KEY]["CAP/USDT"]["side"] == "LONG"
    assert a.position_meta[STATE_KEY]["CAP/USDT"]["status"] == "WAIT_DATA_SUSPENDED"


def test_frozen_atr_same_candle_and_read_only_diagnostics(monkeypatch):
    a, w = armed("CAP/USDT", "LONG", monkeypatch)
    f = frame(240000., atr=2.)
    d = observe(w, "CAP/USDT", f, monkeypatch, price=100.5, offset=2000)
    assert d["wait_fixed_atr"] == 1.
    before = copy.deepcopy(a.position_meta)
    assert w.candidate("CAP/USDT", 100.5, 242000.)["wait_fixed_atr"] == 1.
    assert w.candidate("CAP/USDT", 100.49, 242000.) is None
    assert a.position_meta == before


def test_restart_freezes_atr_but_requires_new_live_observation(monkeypatch):
    import core.services.wait_authority as module
    a, w = armed("CAP/USDT", "LONG", monkeypatch)
    observe(w, "CAP/USDT", frame(240000.), monkeypatch, price=100.5, offset=2000)
    monkeypatch.setattr(module, "SESSION", "restarted")
    assert w.candidate("CAP/USDT", 100.5, 242000.) is None
    d = observe(w, "CAP/USDT", frame(240000., atr=2.), monkeypatch, price=100.5, offset=3000)
    assert d["wait_fixed_atr"] == 1.


def paper_submission_fixture(symbol, side, monkeypatch, tmp_path):
    import core.paper_account as paper
    import core.engine as engine_module
    from core.engine import TradingEngine
    from core.services.entry_contract import evaluate_entry_contract
    monkeypatch.setattr(paper, "STATE_FILE", str(tmp_path/"paper.json"))
    monkeypatch.chdir(tmp_path)
    (tmp_path/"logs").mkdir()
    monkeypatch.setattr(engine_module, "DEFAULT_SYMBOLS", ["龙虾/USDT", "CAP/USDT"])
    monkeypatch.setattr(engine_module, "MAX_SLOTS", 2)
    a = paper.PaperAccount()
    a.balance = 200.
    w = WaitAuthority(a)
    observe(w, symbol, frame(180000.), monkeypatch)
    close = 99.8 if side == "LONG" else 100.2
    f = frame(240000., completed_close=close)
    observe(w, symbol, f, monkeypatch)
    price = 100+(1 if side == "LONG" else -1)*.5
    observe(w, symbol, f, monkeypatch, price=price, offset=2000)
    f.loc[f.index[-1], "close"] = price
    f.loc[f.index[-1], "high"] = max(100., price)
    f.loc[f.index[-1], "low"] = min(100., price)
    f.attrs.update(entry_quote_ms=242000., entry_finality_server_ms=242000.,
                   entry_finality_verified=True)
    d = evaluate_entry_contract(f, account=a, symbol=symbol)
    assert d["type"] == "WAIT_LIVE_BIG_"+side
    e = TradingEngine.__new__(TradingEngine)
    e.account = a
    e.symbol_rotation = SimpleNamespace(get_dynamic_leverage=lambda *args: 5.)
    e.tickers = {symbol: price}
    e._execution_price_is_safe = AsyncMock(return_value=True)
    e._fresh_channel_entry_snapshot = AsyncMock(return_value=dict(frame=f, decision=d, price=price))
    e._entry_boundary_frame = AsyncMock(return_value=f)
    signal = dict(side=side, entry_mode="CHANNEL_SWING", signal_code=d["type"],
                  candidate_bar_id=240000., score=100)
    return a, w, f, d, e, signal


@pytest.mark.parametrize("symbol", ["龙虾/USDT", "CAP/USDT"])
@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_paper_engine_firewall_shared_lock_claim_fill_and_persistence(symbol, side, monkeypatch, tmp_path):
    import core.paper_account as paper
    a, w, f, d, e, signal = paper_submission_fixture(symbol, side, monkeypatch, tmp_path)
    price = float(d["price"])

    async def run():
        results = await asyncio.gather(*(
            e._place_structured_entry(symbol, signal, price) for _ in range(2)))
        assert sorted(results) == [False, True]
    asyncio.run(run())
    assert len([t for t in a.trades if t["action"] == "OPEN_"+side]) == 1
    assert a.positions[symbol]["margin"] == pytest.approx(e._half_wallet_entry_margin(200., 200., 5.))
    assert a.position_meta[STATE_KEY][symbol]["claims"]["240000"]["phase"] == "FILLED"
    restored = paper.PaperAccount()
    assert restored.positions[symbol]["entry_snapshot"]["wait_trigger_id"] == d["wait_trigger_id"]
    assert "setup" not in restored.position_meta[STATE_KEY][symbol]
    assert restored.positions[symbol]["position_uuid"] == a.positions[symbol]["position_uuid"]
    assert all(event["delivered"] for event in restored.position_meta[STATE_KEY][symbol]["event_outbox"])


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_arbitration_opposite_blocks_even_explicit_wait_code(side, monkeypatch):
    import core.services.entry_contract as contract
    a, w = armed("CAP/USDT", side, monkeypatch)
    f = frame(240000.)
    price = 100+(1 if side == "LONG" else -1)*.5
    observe(w, "CAP/USDT", f, monkeypatch, price=price, offset=2000)
    f.attrs["entry_quote_ms"] = 242000.
    monkeypatch.setattr(contract, "_evaluate_strategy_contract",
                        lambda *args, **kwargs: {"side": "SHORT" if side == "LONG" else "LONG"})
    diagnostics = {}
    assert contract.evaluate_entry_contract(f, price, code="WAIT_LIVE_BIG_"+side,
                                            account=a, symbol="CAP/USDT", diagnostics=diagnostics) is None
    assert diagnostics["reason"] == "BLOCKED_OPPOSITE_ENTRY_AUTHORITIES"


def test_data_suspension_does_not_change_frozen_live_atr(monkeypatch):
    _, w = armed("CAP/USDT", "LONG", monkeypatch)
    bad = frame(240000.)
    bad.loc[bad.index[-2], "atr"] = float("nan")
    observe(w, "CAP/USDT", bad, monkeypatch, offset=2000)
    d = observe(w, "CAP/USDT", frame(240000., atr=2.), monkeypatch,
                price=100.5, offset=3000)
    assert d["wait_fixed_atr"] == 1.


def test_unknown_wait_claim_blocks_all_other_authorities(monkeypatch):
    import core.services.entry_contract as contract
    a, w = armed("CAP/USDT", "LONG", monkeypatch)
    d = observe(w, "CAP/USDT", frame(240000.), monkeypatch, price=100.5, offset=2000)
    w.claim("CAP/USDT", d["wait_trigger_id"])
    w.settle("CAP/USDT", d["wait_trigger_id"], outcome="TIMEOUT",
             filled_qty=0., position_confirmed=False)
    other = Mock(return_value={"action": "ENTER", "side": "SHORT"})
    monkeypatch.setattr(contract, "_evaluate_strategy_contract", other)
    diagnostics = {}
    assert contract.evaluate_entry_contract(frame(300000.), account=a,
                                            symbol="CAP/USDT", diagnostics=diagnostics) is None
    assert diagnostics["reason"] == "BLOCKED_WAIT_ORDER_RECONCILIATION"
    other.assert_not_called()


def test_restart_reconciles_unknown_claim_only_with_matching_position(monkeypatch):
    import core.services.wait_authority as module
    a, w = armed("CAP/USDT", "LONG", monkeypatch)
    d = observe(w, "CAP/USDT", frame(240000.), monkeypatch, price=100.5, offset=2000)
    w.claim("CAP/USDT", d["wait_trigger_id"])
    w.settle("CAP/USDT", d["wait_trigger_id"], outcome="UNKNOWN",
             filled_qty=0., position_confirmed=False)
    monkeypatch.setattr(module, "SESSION", "after-interrupted-submit")
    a.positions["CAP/USDT"] = {"qty": .1, "side": "LONG",
                               "entry_snapshot": {"wait_trigger_id": d["wait_trigger_id"]}}
    observe(w, "CAP/USDT", frame(300000.), monkeypatch)
    state = a.position_meta[STATE_KEY]["CAP/USDT"]
    assert state["claims"]["240000"]["phase"] == "FILLED"
    assert "setup" not in state


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_same_side_authorities_produce_one_wait_decision(side, monkeypatch):
    import core.services.entry_contract as contract
    a, w = armed("CAP/USDT", side, monkeypatch)
    f = frame(240000.)
    price = 100+(1 if side == "LONG" else -1)*.5
    observe(w, "CAP/USDT", f, monkeypatch, price=price, offset=2000)
    f.attrs["entry_quote_ms"] = 242000.
    monkeypatch.setattr(contract, "_evaluate_strategy_contract",
                        lambda *args, **kwargs: {"side": side})
    d = contract.evaluate_entry_contract(f, price, account=a, symbol="CAP/USDT")
    assert d["type"] == "WAIT_LIVE_BIG_"+side


def test_direct_wait_account_submit_without_engine_lock_is_rejected(monkeypatch):
    from core.services.entry_firewall import validate_account_entry
    a, _ = armed("CAP/USDT", "LONG", monkeypatch)
    with pytest.raises(ValueError, match="WAIT_REQUIRES_SHARED_SUBMIT_LOCK"):
        asyncio.run(validate_account_entry(a, "CAP/USDT", "LONG",
                                          {"entry_signal_code": "WAIT_LIVE_BIG_LONG"}))


def test_same_candidate_ticks_do_not_force_disk_write_every_quote(monkeypatch):
    a, w = armed("CAP/USDT", "LONG", monkeypatch)
    observe(w, "CAP/USDT", frame(240000.), monkeypatch, price=100.5, offset=2000)
    before = a.save_state.call_count
    observe(w, "CAP/USDT", frame(240000.), monkeypatch, price=100.6, offset=3000)
    assert a.save_state.call_count == before
    assert w.candidate("CAP/USDT", 100.6, 243000.)["price"] == 100.6


def test_unknown_live_candle_finality_is_not_accepted(monkeypatch):
    a, w = armed("CAP/USDT", "LONG", monkeypatch)
    f = frame(240000.)
    f["is_closed"] = f["is_closed"].astype(object)
    f.loc[f.index[-1], "is_closed"] = "false"
    assert observe(w, "CAP/USDT", f, monkeypatch, price=100.5, offset=2000) is None
    assert a.position_meta[STATE_KEY]["CAP/USDT"]["status"] == "WAIT_DATA_SUSPENDED"


@pytest.mark.parametrize("symbol", ["龙虾/USDT", "CAP/USDT"])
@pytest.mark.parametrize("side", ["LONG", "SHORT"])
@pytest.mark.parametrize("fault", ["pending", "slots", "capital", "stale", "persistence"])
def test_four_paper_paths_safety_faults_never_open(symbol, side, fault, monkeypatch, tmp_path):
    import core.paper_account as paper
    a, w, f, d, e, signal = paper_submission_fixture(symbol, side, monkeypatch, tmp_path)
    if fault == "pending":
        a.pending_limit_orders[symbol] = {}
    elif fault == "slots":
        a.pending_limit_orders.update({str(i): {} for i in range(2)})
    elif fault == "capital":
        a.balance = 10.
        monkeypatch.setattr(a, "get_available_balance", lambda: 1.)
    elif fault == "stale":
        monkeypatch.setattr("time.time", lambda: 249.)
    elif fault == "persistence":
        monkeypatch.setattr(paper.os, "fsync", Mock(side_effect=OSError("disk sync failed")))
    assert not asyncio.run(e._place_structured_entry(symbol, signal, d["price"]))
    assert not a.positions
    assert not a.trades


@pytest.mark.parametrize("symbol", ["龙虾/USDT", "CAP/USDT"])
@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_engine_interrupted_submit_quarantines_all_new_entries(symbol, side, monkeypatch, tmp_path):
    from core.services.entry_firewall import validate_account_entry
    from core.services.entry_gate_integrity import assert_commit_proof
    from core.services.entry_contract import evaluate_entry_contract
    a, w, f, d, e, signal = paper_submission_fixture(symbol, side, monkeypatch, tmp_path)

    async def interrupted(**kwargs):
        context = kwargs["entry_context"]
        await validate_account_entry(a, symbol, side, context)
        assert_commit_proof(a, symbol, side, context)
        raise TimeoutError("unknown submit result")

    monkeypatch.setattr(a, "open_position", interrupted)
    assert not asyncio.run(e._place_structured_entry(symbol, signal, d["price"]))
    assert a.position_meta[STATE_KEY][symbol]["claims"]["240000"]["phase"] == "UNKNOWN"
    assert evaluate_entry_contract(f, account=a, symbol=symbol) is None
    assert not a.positions and not a.trades


def test_outbox_replays_same_event_identity_without_order_claim(monkeypatch):
    a, w = armed("CAP/USDT", "LONG", monkeypatch)
    state = a.position_meta[STATE_KEY]["CAP/USDT"]
    event = state["event_outbox"][0]
    event["delivered"] = False
    event_id = event["event_id"]
    before = len(state["event_outbox"])
    observe(w, "CAP/USDT", frame(240000.), monkeypatch, offset=2000)
    state = a.position_meta[STATE_KEY]["CAP/USDT"]
    assert len(state["event_outbox"]) == before
    assert state["event_outbox"][0]["event_id"] == event_id
    assert state["event_outbox"][0]["delivered"]
    assert not state.get("claims")


@pytest.mark.parametrize("symbol", ["龙虾/USDT", "CAP/USDT"])
@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_live_quote_crossing_schedules_entry_without_candle_close(symbol, side, monkeypatch, tmp_path):
    a, w, f, d, e, signal = paper_submission_fixture(symbol, side, monkeypatch, tmp_path)
    e.is_running = True
    e._channel_exit_frames = {symbol: f}
    sign = 1 if side == "LONG" else -1
    assert observe(w, symbol, f, monkeypatch, price=100+sign*.49, offset=2500) is None
    monkeypatch.setattr("time.time", lambda: 243.)

    async def run():
        e._observe_channel_entry_quote(symbol, d["price"], 243000.)
        tasks = list(e._exit_followup_tasks.values())
        assert len(tasks) == 1
        await asyncio.gather(*tasks)
    asyncio.run(run())
    assert symbol in a.positions
    assert a.positions[symbol]["entry_snapshot"]["entry_phase"] == "INDEPENDENT_WAIT"
    assert not bool(f.iloc[-1].is_closed)


@pytest.mark.parametrize("symbol", ["龙虾/USDT", "CAP/USDT"])
@pytest.mark.parametrize("side", ["LONG", "SHORT"])
@pytest.mark.parametrize("opposite", [False, True])
def test_real_two_bar_authority_arbitrates_with_wait(symbol, side, opposite, monkeypatch):
    import core.services.entry_contract as contract
    from test_entry_chop_gate import eligible_frame
    a, w = armed(symbol, side, monkeypatch)
    other_side = ("SHORT" if side == "LONG" else "LONG") if opposite else side
    f = eligible_frame("pair", other_side)
    quote = float(f.iloc[-1].close)
    opened = quote-(1 if side == "LONG" else -1)*.5
    f.loc[f.index[-1], "open"] = opened
    f.loc[f.index[-1], "high"] = max(float(f.iloc[-1].high), opened)
    f.loc[f.index[-1], "low"] = min(float(f.iloc[-1].low), opened)
    observe(w, symbol, f, monkeypatch, price=opened)
    # The WAIT scale is frozen; the independent KC authority uses fresh data.
    f.loc[f.index[-2], "atr"] = 2.
    observe(w, symbol, f, monkeypatch, price=quote, offset=2000)
    stamp = float(f.iloc[-1].timestamp)+2000
    f.attrs.update(entry_quote_ms=stamp, entry_finality_server_ms=stamp,
                   entry_finality_verified=True)
    legacy = contract._evaluate_strategy_contract(
        f, quote, code="KC_2BAR_CONFIRM_"+other_side, account=a, symbol=symbol)
    assert legacy and legacy["side"] == other_side
    diagnostics = {}
    d = contract.evaluate_entry_contract(f, quote, account=a, symbol=symbol, diagnostics=diagnostics)
    if opposite:
        assert d is None
        assert diagnostics["reason"] == "BLOCKED_OPPOSITE_ENTRY_AUTHORITIES"
    else:
        assert d["type"] == "WAIT_LIVE_BIG_"+side


@pytest.mark.parametrize("symbol", ["龙虾/USDT", "CAP/USDT"])
@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_bar_boundary_cache_delay_does_not_erase_observed_setup(symbol, side, monkeypatch):
    a = account()
    w = WaitAuthority(a)
    old = frame(180000.)
    observe(w, symbol, old, monkeypatch)
    assert observe(w, symbol, old, monkeypatch, offset=61000) is None
    assert a.position_meta[STATE_KEY][symbol]["status"] == "WAIT_DATA_SUSPENDED"
    close = 99.8 if side == "LONG" else 100.2
    new = frame(240000., completed_close=close)
    price = 100+(1 if side == "LONG" else -1)*.5
    d = observe(w, symbol, new, monkeypatch, price=price, offset=2000)
    assert d["side"] == side
    assert d["wait_setup"]["bar"] == 180000.


@pytest.mark.parametrize("symbol", ["龙虾/USDT", "CAP/USDT"])
@pytest.mark.parametrize("side", ["LONG", "SHORT"])
@pytest.mark.parametrize("body,expected", [(.249999, True), (.25, True), (.250001, False)])
def test_four_setup_threshold_paths(symbol, side, body, expected, monkeypatch):
    a = account()
    w = WaitAuthority(a)
    observe(w, symbol, frame(180000.), monkeypatch)
    sign = 1 if side == "LONG" else -1
    observe(w, symbol, frame(240000., completed_close=100-sign*body), monkeypatch)
    assert (a.position_meta[STATE_KEY][symbol].get("side") == side) is expected


@pytest.mark.parametrize("symbol", ["龙虾/USDT", "CAP/USDT"])
@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_four_doji_before_small_cannot_create_setup(symbol, side, monkeypatch):
    a = account()
    w = WaitAuthority(a)
    observe(w, symbol, frame(180000.), monkeypatch)
    sign = 1 if side == "LONG" else -1
    d = observe(w, symbol, frame(240000., completed_close=100-sign*.001),
                monkeypatch, price=100+sign*.5)
    assert d is None
    assert "side" not in a.position_meta[STATE_KEY][symbol]
    assert a.position_meta[STATE_KEY][symbol]["status"] == "WAIT_DOJI_KEEP"


@pytest.mark.parametrize("symbol", ["龙虾/USDT", "CAP/USDT"])
@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_four_invalid_completed_candles_suspend_without_clearing_wait(symbol, side, monkeypatch):
    a, w = armed(symbol, side, monkeypatch)
    f = frame(300000.)
    f.loc[f.index[-2], ["high", "low"]] = [100., 100.]
    sign = 1 if side == "LONG" else -1
    assert observe(w, symbol, f, monkeypatch, price=100+sign*.5) is None
    assert a.position_meta[STATE_KEY][symbol]["side"] == side
    assert a.position_meta[STATE_KEY][symbol]["status"] == "WAIT_DATA_SUSPENDED"
