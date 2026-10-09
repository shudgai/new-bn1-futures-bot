import asyncio
import copy
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
import pandas as pd

from core.engine import TradingEngine
from core.services.entry_contract import ENTRY_CODES, evaluate_entry_contract
from core.services.entry_firewall import validate_account_entry
from core.services.exits.peak_trailing_exit import STATE_KEY as EXIT_STATE, evaluate_peak_trailing
from core.services.exits.realtime_profit_exit import enforce_realtime_profit_exit
from core.services.exits.structural_holding_exit import FIRST_NET_PROFIT, HARD, WATERFALL
from core.services.dual_breakout_entry import CODES
from core.services.wait_authority import STATE_KEY
from test_wait_authority import account, frame
from test_live_ma5_v_exit import position, snapshot
from test_two_slot_full_margin import make_engine

SYMBOLS = ("龙虾/USDT", "CAP/USDT")
SIDES = ("LONG", "SHORT")


def outside_frame(side, bar=600000.):
    f = frame(bar)
    price = 100.+(.3 if side == "LONG" else -.3)
    f.loc[2, ["open", "close", "high", "low"]] = [
        100., price, max(100., price), min(100., price)]
    sign = 1 if side == "LONG" else -1
    for i in range(2):
        c = 100.+sign*i*.1
        f.loc[i, ["open", "close", "high", "low"]] = [c-sign*.05, c, c+.06, c-.06]
    prefix = []
    for i in range(4):
        c = 100.+sign*(-.4+i*.1)
        prefix.append(dict(f.iloc[0], timestamp=bar-360000.+i*60000.,
                           open=c-sign*.05, close=c, high=c+.06, low=c-.06))
    f = pd.concat([pd.DataFrame(prefix, index=[-4, -3, -2, -1]), f])
    f["ma5"] = f["close"].rolling(5).mean().fillna(f["close"]-sign*.2)
    f["ma15"] = 100.+(f["close"]-100.).cumsum()/15.
    f.attrs.update(entry_finality_verified=True, entry_quote_ms=bar+2000,
                   entry_finality_server_ms=bar+2000)
    return f


@pytest.mark.parametrize("symbol", SYMBOLS)
@pytest.mark.parametrize("side", SIDES)
def test_live_inside_open_crosses_without_pattern_direction_body_or_wait(symbol, side):
    a = account()
    f = outside_frame(side)
    f.loc[1, "kc_middle"] = 100.1 if side == "SHORT" else 99.9
    d = evaluate_entry_contract(f, account=a, symbol=symbol)
    assert d["type"] == "KC_LIVE_CROSS_"+side
    assert d["pending_signal_id"] == f"{symbol}:{side}:600000"
    assert "wait_trigger_id" not in d
    assert ENTRY_CODES == CODES
    assert a.position_meta == {}


@pytest.mark.parametrize("symbol", SYMBOLS)
@pytest.mark.parametrize("side", SIDES)
@pytest.mark.parametrize("fault", ["touch", "inside", "nan", "invalid_ohlc",
                                  "pending", "held", "closing", "unknown",
                                  "closed_same_bar", "opened_same_bar"])
def test_outer_authority_preserves_safety_and_candle_dedupe(symbol, side, fault):
    a = account()
    f = outside_frame(side)
    if fault in ("touch", "inside"):
        f.loc[2, "close"] = f.loc[2, "kc_upper" if side == "LONG" else "kc_lower"]
        if fault == "inside":
            f.loc[2, "close"] = 100.
    elif fault == "nan":
        f.loc[2, "close"] = float("nan")
    elif fault == "invalid_ohlc":
        f.loc[1, "high"] = 90.
    elif fault == "pending":
        a.pending_limit_orders[symbol] = {}
    elif fault == "held":
        a.positions[symbol] = {"side": side}
    elif fault == "closing":
        a.closing_lock.add(symbol)
    elif fault == "unknown":
        a.position_meta[STATE_KEY] = {symbol: {"claims": {"x": {"phase": "UNKNOWN"}}}}
    else:
        a.trades = [dict(symbol=symbol, id=602000,
                        action=("CLOSE_" if fault == "closed_same_bar" else "OPEN_")+side)]
    assert evaluate_entry_contract(f, account=a, symbol=symbol) is None


@pytest.mark.parametrize("symbol", SYMBOLS)
@pytest.mark.parametrize("side", SIDES)
def test_next_candle_continues_without_new_cross_or_two_colors(symbol, side):
    a = account()
    a.last_closed_at = {symbol: 599.9}
    a.trades = [dict(symbol=symbol, action="CLOSE_"+side, id=599900)]
    f = outside_frame(side)
    f.loc[1, "open"] = f.loc[1, "close"]
    assert evaluate_entry_contract(f, account=a, symbol=symbol)["side"] == side
    a.last_closed_at[symbol] = 600.
    assert evaluate_entry_contract(f, account=a, symbol=symbol) is None


@pytest.mark.parametrize("side", SIDES)
def test_obsolete_wait_and_body_requests_do_not_authorize(side):
    for prefix in ("WAIT_LIVE_BIG_", "KC_LIVE_BODY_BREAKOUT_"):
        assert evaluate_entry_contract(outside_frame(side), code=prefix+side,
                                       account=account(), symbol="CAP/USDT") is None


@pytest.mark.parametrize("symbol", SYMBOLS)
@pytest.mark.parametrize("side", SIDES)
@pytest.mark.parametrize("fault", ["stale", "inside", "future"])
def test_firewall_rechecks_latest_quote_and_freshness(symbol, side, fault, monkeypatch):
    a = account()
    f = outside_frame(side)
    monkeypatch.setattr("time.time", lambda: 602.)
    if fault == "inside":
        f.loc[2, "close"] = 100.
    else:
        f.attrs["entry_quote_ms"] = 590000. if fault == "stale" else 603000.
    a.entry_frame_provider = AsyncMock(return_value=f)
    with pytest.raises(ValueError):
        asyncio.run(validate_account_entry(a, symbol, side, {
            "entry_signal_code": "KC_LIVE_CROSS_"+side,
            "channel_confirmation_bar_id": 600000.}))


@pytest.mark.parametrize("symbol", SYMBOLS)
@pytest.mark.parametrize("side", SIDES)
def test_real_paper_concurrency_uuid_restart_and_close_then_continue(symbol, side, monkeypatch, tmp_path):
    import core.paper_account as paper
    monkeypatch.setattr(paper, "STATE_FILE", str(tmp_path/"paper.json"))
    a = paper.PaperAccount()
    a.balance = 200.
    e, signal = make_engine(a, monkeypatch, side, symbol)
    price = e.tickers[symbol]

    async def run():
        return await asyncio.gather(*(e._place_structured_entry(symbol, signal, price) for _ in range(2)))
    assert sorted(asyncio.run(run())) == [False, True]
    assert len(a.trades) == 1
    p = a.positions[symbol]
    assert p["entry_fee_paid"] > 0
    restored = paper.PaperAccount()
    assert restored.positions[symbol]["position_uuid"] == p["position_uuid"]
    restored.positions.clear()
    f = outside_frame(side)
    assert evaluate_entry_contract(f, account=restored, symbol=symbol) is None
    monkeypatch.setattr("time.time", lambda: 604.)
    assert asyncio.run(a.close_position(symbol, price, "MANUAL", is_manual=True))
    assert not evaluate_entry_contract(f, account=a, symbol=symbol)
    assert evaluate_entry_contract(outside_frame(side, 660000.), account=a, symbol=symbol)


def profit_position(side, symbol):
    p = position(side, symbol)
    p.update(open_timestamp=302., entry_snapshot={"signal_code": "KC_LIVE_CROSS_"+side})
    return p


@pytest.mark.parametrize("symbol", SYMBOLS)
@pytest.mark.parametrize("side", SIDES)
@pytest.mark.parametrize("gain,allowed", [(.01, False), (.2, True)])
def test_first_net_profit_without_reversal_or_close(symbol, side, gain, allowed):
    p = profit_position(side, symbol)
    sign = 1 if side == "LONG" else -1
    result = evaluate_peak_trailing(p, 100+sign*gain, snapshot(stamp=303000.))
    assert bool(result) is allowed
    if allowed:
        assert result["reason"] == FIRST_NET_PROFIT
        assert p[EXIT_STATE]["first_net_profit"]["net"] > 0


@pytest.mark.parametrize("side", SIDES)
@pytest.mark.parametrize("fault", ["next_bar", "old_position", "general", "manual", "invalid_data", "fees"])
def test_first_profit_scope_and_costs(side, fault):
    p = profit_position(side, "CAP/USDT")
    s = snapshot(stamp=303000.)
    if fault == "next_bar":
        s = snapshot(stamp=361000.)
    elif fault == "old_position":
        p["entry_snapshot"]["signal_code"] = "WAIT_LIVE_BIG_"+side
    elif fault == "general":
        p["entry_snapshot"]["signal_code"] = "KC_2BAR_CONFIRM_"+side
    elif fault == "manual":
        p.pop("entry_snapshot")
    elif fault == "invalid_data":
        s["reason"] = "NO_DATA"
    else:
        p["entry_fee_paid"] = 10.
    result = evaluate_peak_trailing(p, 100+(.2 if side == "LONG" else -.2), s)
    assert result is None


@pytest.mark.parametrize("symbol", SYMBOLS)
@pytest.mark.parametrize("side", SIDES)
def test_failed_profit_close_retries_after_restart_and_ignores_trend_hold(symbol, side, monkeypatch):
    p = profit_position(side, symbol)
    a = SimpleNamespace(positions={symbol: p}, position_meta={}, save_state=Mock(),
                        log=Mock(), close_position=AsyncMock(return_value=False))
    e = SimpleNamespace(account=a, is_running=True, _channel_exit_frames={})
    monkeypatch.setattr("time.time", lambda: 303.)
    monkeypatch.setattr("core.services.exits.realtime_profit_exit.cached_tick_indicators",
                        lambda *args: (snapshot(stamp=303000.), 1.))
    monkeypatch.setattr("core.services.exits.trend_hold_evaluator.evaluate_trend_hold",
                        lambda *args, **kwargs: ("HOLD", "TEST"))
    price = 100+(.2 if side == "LONG" else -.2)
    assert not asyncio.run(enforce_realtime_profit_exit(e, symbol, price, 303000.))
    a.close_position.assert_awaited_once()
    assert p[EXIT_STATE]["pending"] == FIRST_NET_PROFIT
    assert a.position_meta[symbol]["exit_protection_snapshot"]["first_net_profit"]["net"] > 0
    a.positions[symbol] = copy.deepcopy(p)
    a.close_position.return_value = True
    monkeypatch.setattr("time.time", lambda: 361.)
    monkeypatch.setattr("core.services.exits.realtime_profit_exit.cached_tick_indicators",
                        lambda *args: (snapshot(stamp=361000.), 1.))
    assert asyncio.run(enforce_realtime_profit_exit(e, symbol, price, 361000.))
    assert a.close_position.await_count == 2


@pytest.mark.parametrize("side", SIDES)
@pytest.mark.parametrize("risk", ["hard", "waterfall"])
def test_emergency_risk_remains_prioritized(side, risk):
    p = profit_position(side, "CAP/USDT")
    sign = 1 if side == "LONG" else -1
    s = snapshot(stamp=303000.)
    if risk == "hard":
        p["margin"] = 1.
        price = 100-sign*.1
    else:
        s["live_open"] = 100+sign*2.
        price = 100+sign*.2
    result = evaluate_peak_trailing(p, price, s)
    assert result["reason"] == (HARD if risk == "hard" else WATERFALL)


@pytest.mark.parametrize("side", SIDES)
@pytest.mark.parametrize("gain,allowed", [(-.000001, False), (0., False), (.000001, True)])
def test_strict_positive_net_boundary(side, gain, allowed):
    p = profit_position(side, "CAP/USDT")
    price = 100+(1 if side == "LONG" else -1)*gain
    assert bool(evaluate_peak_trailing(p, price, snapshot(stamp=303000.),
                                      fee=0., slippage=0.)) is allowed


@pytest.mark.parametrize("symbol", SYMBOLS)
@pytest.mark.parametrize("side", SIDES)
def test_normal_or_abnormal_closed_ticket_no_longer_requires_pullback(symbol, side, monkeypatch):
    e = TradingEngine.__new__(TradingEngine)
    e.account = account()
    ticket = dict(phase="closed", side=side, requires_pullback=True,
                  close_reason="Channel Swing EXIT_STRUCTURAL_WATERFALL")
    monkeypatch.setattr("core.services.closed_breakout_entry.matched_reentry_close",
                        lambda *args: 599900.)
    assert e._profit_reentry_ready(symbol, ticket, outside_frame(side), 100+(.3 if side == "LONG" else -.3))
    e.account.last_closed_at = {symbol: 600.}
    assert not e._profit_reentry_ready(symbol, ticket, outside_frame(side), 100+(.3 if side == "LONG" else -.3))
