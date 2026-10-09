import asyncio
from unittest.mock import AsyncMock, Mock

import pandas as pd
import pytest

from core.engine import TradingEngine
from core.services.entry_contract import evaluate_entry_contract
from core.services.entry_firewall import validate_account_entry
from test_wait_authority import account
from test_two_slot_full_margin import make_engine

SYMBOLS = ("龙虾/USDT", "CAP/USDT")
SIDES = ("LONG", "SHORT")


def pair_frame(side):
    f = pd.DataFrame([
        dict(timestamp=480000., open=100., close=100.5, high=100.6, low=99.9,
             kc_lower=99.75, kc_middle=100., kc_upper=100.25, atr=1., is_closed=True),
        dict(timestamp=540000., open=100.5, close=100.75, high=100.8, low=100.45,
             kc_lower=99.85, kc_middle=100.1, kc_upper=100.35, atr=1., is_closed=True),
        dict(timestamp=600000., open=100.75, close=100.8, high=100.85, low=100.7,
             kc_lower=99.95, kc_middle=100.2, kc_upper=100.45, atr=1., is_closed=False)])
    if side == "SHORT":
        src = f.copy()
        for key, origin in (("open", "open"), ("close", "close"), ("high", "low"),
                            ("low", "high"), ("kc_lower", "kc_upper"),
                            ("kc_upper", "kc_lower"), ("kc_middle", "kc_middle")):
            f[key] = 200-src[origin]
    sign = 1 if side == "LONG" else -1
    prefix = []
    for i in range(4):
        c = 100.+sign*(-.4+i*.1)
        prefix.append(dict(f.iloc[0], timestamp=240000.+i*60000.,
                           open=c-sign*.05, close=c, high=c+.06, low=c-.06))
    f = pd.concat([pd.DataFrame(prefix, index=[-4, -3, -2, -1]), f])
    f["ma5"] = f["close"].rolling(5).mean().fillna(f["close"]-sign*.2)
    f["ma15"] = 100.+(f["close"]-100.).cumsum()/15.
    f.attrs.update(entry_finality_verified=True, entry_quote_ms=602000.,
                   entry_finality_server_ms=602000., timeframe_ms=60000)
    return f


@pytest.mark.parametrize("symbol", SYMBOLS)
@pytest.mark.parametrize("side", SIDES)
def test_general_two_closed_bodies_kc_ma_directions_and_nonchoppy_history(symbol, side):
    f = pair_frame(side)
    d = evaluate_entry_contract(f, account=account(), symbol=symbol)
    assert d["type"] == "KC_2BAR_CONFIRM_"+side
    assert d["breakout_bar_id"] == 480000.
    assert d["pair_confirmation_bar_id"] == 540000.


@pytest.mark.parametrize("symbol", SYMBOLS)
@pytest.mark.parametrize("side", SIDES)
@pytest.mark.parametrize("fault", [
    "first_outside", "wick_only", "wrong_color", "weak_first", "weak_second",
    "second_inside", "second_unclosed", "flat_kc", "opposite_kc", "quote_touch"])
def test_general_rejects_missing_two_real_closed_breakout_bodies(symbol, side, fault):
    f = pair_frame(side)
    sign = 1 if side == "LONG" else -1
    edge = "kc_upper" if sign == 1 else "kc_lower"
    if fault == "first_outside":
        f.loc[0, "open"] = f.loc[0, edge]+sign*.01
    elif fault == "wick_only":
        f.loc[0, "close"] = f.loc[0, edge]
    elif fault == "wrong_color":
        f.loc[1, ["open", "close"]] = [f.loc[1, "close"], f.loc[1, "open"]]
    elif fault.startswith("weak"):
        f.loc[0 if fault == "weak_first" else 1, "high"] += 10.
    elif fault == "second_inside":
        f.loc[1, "close"] = f.loc[1, edge]
    elif fault == "second_unclosed":
        f.loc[1, "is_closed"] = False
    elif fault in ("flat_kc", "opposite_kc"):
        f.loc[1, "kc_middle"] = f.loc[0, "kc_middle"]-sign*(.01 if fault == "opposite_kc" else 0)
    else:
        f.loc[2, "close"] = f.loc[2, edge]
        f.loc[2, "low"] = min(f.loc[2, "low"], f.loc[2, "close"])
        f.loc[2, "high"] = max(f.loc[2, "high"], f.loc[2, "close"])
    assert evaluate_entry_contract(f, code="KC_2BAR_CONFIRM_"+side,
                                   account=account(), symbol=symbol) is None


@pytest.mark.parametrize("side", SIDES)
@pytest.mark.parametrize("ratio,passes", [(.199999, False), (.20, True), (.200001, True)])
def test_general_body_twenty_percent_boundary(side, ratio, passes):
    f = pair_frame(side)
    body = abs(f.loc[0, "close"]-f.loc[0, "open"])
    span = body/ratio
    f.loc[0, "high"] = max(f.loc[0, "close"], f.loc[0, "open"])+(span-body)/2
    f.loc[0, "low"] = min(f.loc[0, "close"], f.loc[0, "open"])-(span-body)/2
    assert bool(evaluate_entry_contract(f, account=account(), symbol="CAP/USDT")) is passes


@pytest.mark.parametrize("symbol", SYMBOLS)
@pytest.mark.parametrize("side", SIDES)
def test_live_cross_first_tick_no_half_atr_and_no_wait_scheduling(symbol, side, monkeypatch):
    f = pair_frame(side)
    sign = 1 if side == "LONG" else -1
    f.loc[2, ["open", "close", "high", "low"]] = [100., 100.+sign*.01, 100.01, 99.99]
    f.loc[2, ["kc_lower", "kc_middle", "kc_upper"]] = [99.995, 100., 100.005]
    e = TradingEngine.__new__(TradingEngine)
    e.account = account()
    e._channel_exit_frames = {symbol: f}
    e._observe_independent_wait = Mock(return_value=None)
    e._execute_confirmed_channel_break = AsyncMock(return_value=True)
    callbacks = []
    e._schedule_exit_followup = lambda symbol, cb: callbacks.append(cb)
    monkeypatch.setattr("time.time", lambda: 602.)
    e._observe_channel_entry_quote(symbol, 100.+sign*.01, 602000.)
    assert len(callbacks) == 1
    asyncio.run(callbacks[0]())
    assert e._execute_confirmed_channel_break.await_args.kwargs["v8_reason"] == "KC_LIVE_CROSS_"+side


@pytest.mark.parametrize("symbol", SYMBOLS)
@pytest.mark.parametrize("side", SIDES)
@pytest.mark.parametrize("fault", ["outside_open", "stale", "next_bar", "held", "same_bar_close"])
def test_live_tick_never_schedules_gap_open_stale_or_unsafe_entries(symbol, side, fault, monkeypatch):
    f = pair_frame(side)
    sign = 1 if side == "LONG" else -1
    price = 100.+sign*.01
    f.loc[1, "close"] = f.loc[1, "open"]
    f.loc[2, ["open", "close", "high", "low"]] = [100., price, 100.01, 99.99]
    f.loc[2, ["kc_lower", "kc_middle", "kc_upper"]] = [99.995, 100., 100.005]
    a = account()
    quoted = 602000.
    if fault == "outside_open":
        f.loc[2, "open"] = price
    elif fault == "stale":
        quoted = 590000.
    elif fault == "next_bar":
        quoted = 660000.
    elif fault == "held":
        a.positions[symbol] = {"side": side}
    else:
        a.last_closed_at = {symbol: 601.}
    e = TradingEngine.__new__(TradingEngine)
    e.account, e._channel_exit_frames = a, {symbol: f}
    e._observe_independent_wait = Mock(return_value=None)
    e._schedule_exit_followup = Mock()
    monkeypatch.setattr("time.time", lambda: quoted/1000 if fault == "next_bar" else 602.)
    e._observe_channel_entry_quote(symbol, price, quoted)
    e._schedule_exit_followup.assert_not_called()


@pytest.mark.parametrize("side", SIDES)
def test_coexisting_same_side_authorities_choose_one_live_decision(side):
    f = pair_frame(side)
    opening = float(f.iloc[-1].kc_middle)
    f.loc[2, "open"] = opening
    f.loc[2, "low"] = min(f.loc[2, "low"], opening)
    f.loc[2, "high"] = max(f.loc[2, "high"], opening)
    assert evaluate_entry_contract(f, account=account(), symbol="CAP/USDT")["type"] == "KC_LIVE_CROSS_"+side
    assert evaluate_entry_contract(f, code="KC_2BAR_CONFIRM_"+side,
                                   account=account(), symbol="CAP/USDT")["type"] == "KC_2BAR_CONFIRM_"+side


@pytest.mark.parametrize("symbol", SYMBOLS)
@pytest.mark.parametrize("side", SIDES)
def test_real_general_firewall_concurrent_fill_and_restart(symbol, side, monkeypatch, tmp_path):
    import core.paper_account as paper
    monkeypatch.setattr(paper, "STATE_FILE", str(tmp_path/"paper.json"))
    a = paper.PaperAccount()
    a.balance = 200.
    e, _ = make_engine(a, monkeypatch, side, symbol)
    f = pair_frame(side)
    price = float(f.iloc[-1].close)
    e.tickers[symbol] = price
    e._entry_boundary_frame = AsyncMock(return_value=f)
    e._fresh_channel_entry_snapshot = AsyncMock(return_value=dict(
        frame=f, price=price, decision=evaluate_entry_contract(f, account=a, symbol=symbol)))
    signal = dict(side=side, signal_code="KC_2BAR_CONFIRM_"+side,
                  entry_mode="CHANNEL_SWING", candidate_bar_id=600000., score=100)

    async def run():
        return await asyncio.gather(*(e._place_structured_entry(symbol, signal, price) for _ in range(2)))
    assert sorted(asyncio.run(run())) == [False, True]
    assert len(a.trades) == 1
    restored = paper.PaperAccount()
    assert restored.positions[symbol]["position_uuid"] == a.positions[symbol]["position_uuid"]
    restored.positions.clear()
    assert not evaluate_entry_contract(f, account=restored, symbol=symbol)


@pytest.mark.parametrize("symbol", SYMBOLS)
@pytest.mark.parametrize("side", SIDES)
def test_requested_pair_cannot_be_replaced_by_live_cross_at_firewall(symbol, side, monkeypatch):
    a = account()
    f = pair_frame(side)
    f.loc[1, "close"] = f.loc[1, "open"]
    opening = 100.2 if side == "LONG" else 99.8
    f.loc[2, "open"] = opening
    f.loc[2, "low"] = min(f.loc[2, "low"], opening)
    f.loc[2, "high"] = max(f.loc[2, "high"], opening)
    a.entry_frame_provider = AsyncMock(return_value=f)
    monkeypatch.setattr("time.time", lambda: 602.)
    assert evaluate_entry_contract(f, account=a, symbol=symbol)["type"] == "KC_LIVE_CROSS_"+side
    with pytest.raises(ValueError):
        asyncio.run(validate_account_entry(a, symbol, side, dict(
            entry_signal_code="KC_2BAR_CONFIRM_"+side, channel_confirmation_bar_id=600000.)))
