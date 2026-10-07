import asyncio
import copy

import pandas as pd
import pytest

from core.services.entry_contract import evaluate_entry_contract
from core.services.swing_entry_gate import evaluate_swing_entry_gate
from core.services.wait_authority import STATE_KEY
from test_wait_authority import paper_submission_fixture


def swing_frame(side="LONG", event="pivot"):
    closes = [100.]*10 + [99., 99.2, 99.4, 99.6, 99.8, 102.]
    rows = [dict(timestamp=(i+1)*60000., open=c-.01, close=c, high=c+.02,
                 low=c-.02, kc_middle=98., atr=1., ma5=100., is_closed=True)
            for i, c in enumerate(closes)]
    rows.append(dict(rows[-1], timestamp=1020000., is_closed=False))
    f = pd.DataFrame(rows)
    f.loc[13:15, "ma5"] = [100., 99.9, 99.95] if event == "pivot" else [99.8, 99.9, 99.95]
    if side == "SHORT":
        old = f.copy()
        for key, source in (("open", "open"), ("close", "close"), ("high", "low"),
                            ("low", "high"), ("ma5", "ma5"), ("kc_middle", "kc_middle")):
            f[key] = 200.-old[source]
    return f


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
@pytest.mark.parametrize("event", ["pivot", "cross"])
def test_latest_completed_pivot_or_cross_qualifies(side, event):
    status, evidence = evaluate_swing_entry_gate(swing_frame(side, event), side)
    assert status == "PASS"
    assert evidence["swing_event"] == ("MA5_PIVOT" if event == "pivot" else "MA5_MA15_CROSS")
    assert evidence["swing_event_bar_ms"] == 960000.
    assert evidence["swing_live_bar_ms"] == 1020000.
    assert evidence["swing_ma5_slope_atr"] == pytest.approx(.05)


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
@pytest.mark.parametrize("move,allowed", [(.049999, False), (.05, True), (.050001, True),
                                        (0., False), (-.1, False)])
def test_ma5_slope_inclusive_boundary(side, move, allowed):
    f = swing_frame(side)
    sign = 1 if side == "LONG" else -1
    f.loc[15, "ma5"] = f.loc[14, "ma5"]+sign*move
    status, evidence = evaluate_swing_entry_gate(f, side)
    assert bool(evidence) is allowed
    if not allowed:
        assert status == "BLOCKED_SWING_FLAT_OR_OPPOSITE_MA5"


@pytest.mark.parametrize("fault", ["no_event", "expired", "flat_prices", "bad_ma5",
                                  "missing_ma5", "gap", "unclosed", "overlap"])
def test_invalid_or_expired_swing_never_passes(fault):
    f = swing_frame()
    if fault == "no_event":
        f.loc[13:15, "ma5"] = [99.8, 99.9, 100.]
        f.loc[15, "close"] = 100.
        f.loc[15, "high"] = 100.02
        f.loc[15, "low"] = 99.98
        f.loc[15, "open"] = 99.99
    elif fault == "expired":
        f.loc[16, "is_closed"] = True
        f = pd.concat([f, pd.DataFrame([dict(f.iloc[-1], timestamp=1080000., is_closed=False)])])
    elif fault == "flat_prices":
        f.loc[:15, ["open", "close", "high", "low"]] = [100., 100., 101., 99.]
    elif fault == "bad_ma5":
        f.loc[14, "ma5"] = float("nan")
    elif fault == "missing_ma5":
        f = f.drop(columns="ma5")
    elif fault == "gap":
        f.loc[14, "timestamp"] -= 30000.
    elif fault == "unclosed":
        f.loc[14, "is_closed"] = False
    else:
        f.loc[:15, "open"] = 98.
        f.loc[:15, "low"] = 97.9
    assert evaluate_swing_entry_gate(f, "LONG")[1] is None


@pytest.mark.parametrize("symbol", ["龙虾/USDT", "CAP/USDT"])
@pytest.mark.parametrize("side", ["LONG", "SHORT"])
@pytest.mark.parametrize("code", ["KC_2BAR_CONFIRM_", "KC_LIVE_BODY_BREAKOUT_", "WAIT_LIVE_BIG_"])
def test_every_authority_shares_gate_and_never_creates_authority(symbol, side, code, monkeypatch):
    import core.services.entry_contract as contract
    decision = dict(action="ENTER", side=side, type=code+side)
    monkeypatch.setattr(contract, "_evaluate_authority_contract", lambda *a, **k: copy.deepcopy(decision))
    f = swing_frame(side)
    result = evaluate_entry_contract(f, symbol=symbol, code=code+side)
    assert result["type"] == code+side and result["swing_event"] == "MA5_PIVOT"
    f.loc[15, "ma5"] = f.loc[14, "ma5"]
    diagnostics = {}
    assert evaluate_entry_contract(f, symbol=symbol, diagnostics=diagnostics) is None
    assert diagnostics["reason"] == "BLOCKED_SWING_FLAT_OR_OPPOSITE_MA5"
    monkeypatch.setattr(contract, "_evaluate_authority_contract", lambda *a, **k: None)
    assert evaluate_entry_contract(swing_frame(side), symbol=symbol) is None


@pytest.mark.parametrize("symbol", ["龙虾/USDT", "CAP/USDT"])
@pytest.mark.parametrize("side", ["LONG", "SHORT"])
@pytest.mark.parametrize("kind", ["live", "pair", "pivot"])
def test_real_kc_authorities_require_fresh_swing_event(symbol, side, kind):
    from test_entry_chop_gate import eligible_frame, authority_code
    f = eligible_frame(kind, side)
    sign = 1 if side == "LONG" else -1
    latest = float(f.iloc[-2].ma5)
    atr = float(f.iloc[-2].atr)
    f.loc[f.index[-4], "ma5"] = latest
    f.loc[f.index[-3], "ma5"] = latest-sign*.1*atr
    result = evaluate_entry_contract(f, symbol=symbol, code=authority_code(kind, side))
    assert result is not None and result["swing_event"] == "MA5_PIVOT"
    f.loc[f.index[-3], "ma5"] = latest
    diagnostics = {}
    assert evaluate_entry_contract(f, symbol=symbol, code=authority_code(kind, side),
                                   diagnostics=diagnostics) is None
    assert diagnostics["reason"] == {
        "live": "BLOCKED_SWING_FLAT_OR_OPPOSITE_MA5",
        "pair": "WAIT_NEW_KC_BREAKOUT",
        "pivot": "WAIT_SUSTAINED_OUTER_RUN_PIVOT_RETURN",
    }[kind]


def qualified_wait_fixture(symbol, side, monkeypatch, tmp_path):
    a, _, f, decision, e, signal = paper_submission_fixture(symbol, side, monkeypatch, tmp_path)
    return a, f, decision, e, signal


@pytest.mark.parametrize("symbol", ["龙虾/USDT", "CAP/USDT"])
@pytest.mark.parametrize("side", ["LONG", "SHORT"])
@pytest.mark.parametrize("changed", [False, True])
def test_four_wait_paths_final_firewall_revalidation_and_claim_consumption(
        symbol, side, changed, monkeypatch, tmp_path):
    a, f, d, e, signal = qualified_wait_fixture(symbol, side, monkeypatch, tmp_path)
    setup = copy.deepcopy(a.position_meta[STATE_KEY][symbol]["setup"])
    if changed:
        f.loc[5, "ma5"] = f.loc[4, "ma5"]
    opened = asyncio.run(e._place_structured_entry(symbol, signal, d["price"]))
    assert opened is (not changed)
    state = a.position_meta[STATE_KEY][symbol]
    if changed:
        assert not a.positions and not a.trades and not state.get("claims")
        assert state["setup"] == setup
    else:
        assert state["claims"]["600000"]["phase"] == "FILLED"
        assert a.positions[symbol]["entry_snapshot"]["swing_event"] == "MA5_PIVOT"


@pytest.mark.parametrize("symbol", ["龙虾/USDT", "CAP/USDT"])
@pytest.mark.parametrize("side", ["LONG", "SHORT"])
@pytest.mark.parametrize("prefix", ["KC_LIVE_BODY_BREAKOUT_", "KC_2BAR_CONFIRM_",
                                  "WAIT_LIVE_BIG_", "CAP_KC_CONTINUATION_"])
def test_chop_exemption_uses_actual_authority_not_requested_code(symbol, side, prefix, monkeypatch):
    import core.services.entry_contract as contract
    actual_code = prefix+side
    monkeypatch.setattr(contract, "_evaluate_authority_contract",
                        lambda *a, **k: dict(action="ENTER", side=side, type=actual_code))
    f = swing_frame(side)
    f.loc[:15, ["open", "close", "high", "low"]] = [100., 100., 101., 99.]
    diagnostics = {}
    result = evaluate_entry_contract(f, symbol=symbol, code="KC_LIVE_BODY_BREAKOUT_"+side,
                                    diagnostics=diagnostics)
    if prefix in ("KC_LIVE_BODY_BREAKOUT_", "KC_2BAR_CONFIRM_"):
        assert result and result["chop_limits_exempt"] is True
        assert result["chop_efficiency"] == 0.
        assert result["chop_mean_overlap"] == 1.
    else:
        assert result is None and diagnostics["reason"] == "BLOCKED_CHOP_LOW_EFFICIENCY"


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
@pytest.mark.parametrize("prefix", ["KC_LIVE_BODY_BREAKOUT_", "KC_2BAR_CONFIRM_"])
@pytest.mark.parametrize("fault", ["flat_ma5", "no_event", "gap", "invalid_ohlc", "history"])
def test_breakout_exemption_preserves_fresh_swing_and_market_validation(side, prefix, fault):
    f = swing_frame(side)
    if fault == "flat_ma5":
        f.loc[15, "ma5"] = f.loc[14, "ma5"]
    elif fault == "no_event":
        sign = 1 if side == "LONG" else -1
        f.loc[13, "ma5"] = f.loc[14, "ma5"]-sign*.1
        f.loc[15, ["open", "close", "high", "low"]] = [100., 100., 101., 99.]
    elif fault == "gap":
        f.loc[14, "timestamp"] -= 30000.
    elif fault == "invalid_ohlc":
        f.loc[14, "low"] = 200.
    else:
        f = f.iloc[-6:].copy()
    status, evidence = evaluate_swing_entry_gate(f, side, authority_code=prefix+side)
    assert evidence is None
    assert status == {
        "flat_ma5": "BLOCKED_SWING_FLAT_OR_OPPOSITE_MA5",
        "no_event": "WAIT_FRESH_MA5_PIVOT_OR_CROSS",
        "gap": "BLOCKED_CHOP_CANDLE_IDENTITY",
        "invalid_ohlc": "BLOCKED_CHOP_MARKET_DATA",
        "history": "BLOCKED_CHOP_HISTORY",
    }[fault]
