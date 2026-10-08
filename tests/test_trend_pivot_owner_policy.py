import asyncio
import copy
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pandas as pd
import pytest

from core.services.exits import trend_pivot_exit as policy
from core.services.ma5_chop_gate import ma5_chop_problem


def market(side="LONG"):
    bar = int(time.time() // 60) * 60000
    rows = [
        dict(timestamp=bar-(6-i)*60000, open=100., high=101., low=99.,
             close=100.5, kc_middle=100.+i, ma5=100.+i, atr=1.,
             is_closed=True)
        for i in range(6)
    ]
    rows[-2]["high"] = 103.
    rows[-1]["kc_middle"] = rows[-2]["kc_middle"] - .1
    rows.append(dict(timestamp=bar, open=100., high=101., low=99.,
                     close=100.5, kc_middle=104., ma5=106., atr=1., is_closed=False))
    if side == "SHORT":
        rows = [{**r, **{k: 200.-r[v] for k, v in
                (("open", "open"), ("close", "close"), ("high", "low"),
                 ("low", "high"), ("kc_middle", "kc_middle"), ("ma5", "ma5"))}}
                for r in rows]
    frame = pd.DataFrame(rows)
    frame.attrs["timeframe_ms"] = 60000
    p = dict(side=side, entry_mode="CHANNEL_SWING", entry_price=100., qty=2.,
             open_timestamp=(bar-7*60000)/1000, entry_atr=1., sl=98., tp=110.)
    return p, frame, float(bar+1000)


def arm_maturity(position):
    state = policy.migrate(position, {})
    sign = 1 if position["side"] == "LONG" else -1
    policy.observe_maturity(state, policy.position_identity(position),
                            position["entry_price"] + sign*3.,
                            position["open_timestamp"]*1000 + 1)


@pytest.mark.parametrize("symbol", ["龙虾/USDT", "CAP/USDT"])
@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_pivot_requires_closed_ck_reverse(symbol, side):
    p, f, stamp = market(side)
    assert policy.evaluate(p, f, 100., stamp)[0]["reason"] == policy.PIVOT_REASON
    sign = 1 if side == "LONG" else -1
    f.loc[5, "kc_middle"] = f.loc[4, "kc_middle"] + sign * .1
    assert policy.evaluate(p, f, 100., stamp)[0] is None
    f.loc[5, "kc_middle"] = f.loc[4, "kc_middle"]
    assert policy.evaluate(p, f, 100., stamp)[0] is None
    f.loc[5, "kc_middle"] = float("nan")
    assert policy.evaluate(p, f, 100., stamp)[0] is None


@pytest.mark.parametrize("symbol", ["龙虾/USDT", "CAP/USDT"])
@pytest.mark.parametrize("side", ["LONG", "SHORT"])
@pytest.mark.parametrize("body,allowed", [(.599999, False), (.6, True), (.600001, True)])
def test_doji_live_body_exact_threshold(symbol, side, body, allowed):
    p, f, stamp = market(side)
    arm_maturity(p)
    sign = 1 if side == "LONG" else -1
    f.loc[5, ["open", "high", "low", "close"]] = [100., 101., 99., 100.4]
    f.loc[5, "kc_middle"] = f.loc[4, "kc_middle"] + sign
    f.loc[6, ["open", "high", "low"]] = [100., 100.1, 99.9]
    result, _ = policy.evaluate(p, f, 100.-sign*body, stamp)
    assert bool(result) is allowed
    if result:
        assert result["reason"] == policy.DOJI_REASON
        assert result["reversal_body_atr"] == pytest.approx(body)


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_preentry_invalid_stale_and_weak_body_no_exit(side):
    p, f, stamp = market(side)
    p["open_timestamp"] = (f.iloc[-2].timestamp+1000)/1000
    assert policy.evaluate(p, f, 100., stamp)[0] is None
    p, f, stamp = market(side)
    assert policy.evaluate(p, f, 100., stamp+60000)[0] is None
    f.loc[5, "is_closed"] = False
    assert policy.evaluate(p, f, 100., stamp)[0] is None


@pytest.mark.parametrize("values,blocked", [
    ([100,101,102,103,104,105], False),
    ([100,100,101,101,100,100], False),
    ([100,101,100,101,102,103], True),
    ([100,99,100,99,98,97], True),
    ([100,100,100,100,100,100], False),
])
def test_single_ma5_gate(values, blocked):
    _, f, _ = market()
    f.loc[:5, "ma5"] = values
    assert (ma5_chop_problem(f) == "BLOCKED_MA5_CHOP_TURNS") is blocked
    f.loc[6, "ma5"] = -999.
    assert (ma5_chop_problem(f) == "BLOCKED_MA5_CHOP_TURNS") is blocked


def test_invalid_ma5_fail_closed():
    _, f, _ = market()
    f.loc[2, "ma5"] = float("nan")
    assert ma5_chop_problem(f) == "WAIT_MA5_CHOP_DATA"
    assert ma5_chop_problem(f.tail(5)) == "WAIT_MA5_CHOP_DATA"


@pytest.mark.parametrize("symbol", ["龙虾/USDT", "CAP/USDT"])
@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_retry_restart_and_retired_authorities(symbol, side, monkeypatch):
    p, f, stamp = market(side)
    monkeypatch.setattr(time, "time", lambda: stamp/1000)
    p.update(channel_hard_stop_pending="MARGIN_LOSS",
             peak_trailing_state={"pending": "EXIT_REALTIME_PEAK_TRAILING"})
    account = SimpleNamespace(positions={symbol:p}, position_meta={},
                              save_state=Mock(), log=Mock(),
                              close_position=AsyncMock(return_value=False))
    assert not asyncio.run(policy.enforce(account, symbol, 100., f, stamp))
    assert p[policy.STATE_KEY]["pending"] == policy.PIVOT_REASON
    assert "channel_hard_stop_pending" not in p
    assert p["sl"] == 0.
    assert not policy.close_allowed(p, {}, "Channel Swing HARD_STOP MARGIN_LOSS", True)
    assert not policy.close_allowed(p, {}, "Channel Swing EXIT_REALTIME_PEAK_TRAILING", True)
    assert policy.close_allowed(p, {}, "手動平倉", True)
    account.positions[symbol] = copy.deepcopy(p)
    assert not asyncio.run(policy.enforce(account, symbol, 100., None, stamp))
    assert account.close_position.await_count == 2
    account.positions[symbol]["open_timestamp"] += 1
    assert not asyncio.run(policy.enforce(account, symbol, 100., None, stamp))
    assert account.close_position.await_count == 2


def test_lobster_182042_keeps_short():
    p, f, stamp = market("SHORT")
    f.loc[4, "kc_middle"] = .03974796446852661
    f.loc[5, "kc_middle"] = .03966720594771455
    f.loc[5, ["open", "high", "low", "close", "atr"]] = [
        .03894, .03899, .03885, .0389, .000583]
    f.loc[6, ["open", "high", "low"]] = [.03892, .03893, .03861]
    assert policy.evaluate(p, f, .03893, stamp)[0] is None


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
@pytest.mark.parametrize("fault", ["doji_ratio", "weak_live", "same_color", "zero_range", "invalid_ohlc", "preentry", "live_doji"])
def test_doji_rejects_unqualified_pressure(side, fault):
    p, f, stamp = market(side)
    arm_maturity(p)
    sign = 1 if side == "LONG" else -1
    f.loc[5, ["open", "high", "low", "close"]] = [100., 101., 99., 100.5]
    f.loc[5, "kc_middle"] = f.loc[4, "kc_middle"] + sign
    f.loc[6, ["open", "high", "low"]] = [100., 100.1, 99.9]
    price = 100.-sign*.6
    if fault == "doji_ratio":
        f.loc[5, "close"] = 100.50001
    elif fault == "weak_live":
        f.loc[6, ["high", "low"]] = [102., 98.]
    elif fault == "same_color":
        price = 100.+sign*.6
    elif fault == "zero_range":
        f.loc[5, ["open", "high", "low", "close"]] = [100.]*4
    elif fault == "invalid_ohlc":
        f.loc[5, "high"] = 99.
    elif fault == "preentry":
        p["open_timestamp"] = (f.loc[5, "timestamp"]+1)/1000
    else:
        f.loc[5, "is_closed"] = False
    assert policy.evaluate(p, f, price, stamp)[0] is None


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_real_paper_authority_persists_and_closes_once(side, tmp_path, monkeypatch):
    from core.paper_account import PaperAccount
    monkeypatch.setattr("core.paper_account.STATE_FILE", str(tmp_path/"account.json"))
    p, f, stamp = market(side)
    monkeypatch.setattr(time, "time", lambda: stamp/1000)
    account = PaperAccount()
    account.positions = {"CAP/USDT": p}
    account.position_meta = {}
    account.save_state()
    assert not asyncio.run(account.close_position(
        "CAP/USDT", 100., "Channel Swing EXIT_REALTIME_PEAK_TRAILING", is_manual=True))
    assert asyncio.run(policy.enforce(account, "CAP/USDT", 100., f, stamp))
    assert "CAP/USDT" not in account.positions
    assert len([t for t in account.trades if t["action"].startswith("CLOSE")]) == 1
    assert account.trades[0][policy.STATE_KEY]["evidence"]["reason"] == policy.PIVOT_REASON
    assert not asyncio.run(policy.enforce(account, "CAP/USDT", 100., f, stamp))


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_real_paper_update_cannot_hard_stop_or_lock(side, tmp_path, monkeypatch):
    from core.paper_account import PaperAccount
    monkeypatch.setattr("core.paper_account.STATE_FILE", str(tmp_path/"account.json"))
    account = PaperAccount()
    p, f, stamp = market(side)
    account.positions = {"CAP/USDT": p}
    account.position_meta = {}
    account.close_position = AsyncMock(return_value=False)
    adverse = 50. if side == "LONG" else 150.
    asyncio.run(account.update_positions({"CAP/USDT": adverse}))
    account.close_position.assert_not_awaited()
    assert p["sl"] == 0.


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_common_contract_and_final_firewall_gate(side):
    from core.services.entry_contract import evaluate_entry_contract
    from core.services.entry_firewall import validate_entry_frame
    from test_strict_entry_contract import candles
    f = candles(side)
    first = f.iloc[[0]].copy()
    first["timestamp"] -= 60000
    f = pd.concat([first, f], ignore_index=True)
    f.attrs["timeframe_ms"] = 60000
    sign = 1 if side == "LONG" else -1
    f.loc[:5, "ma5"] = [100.+sign*i*.1 for i in range(6)]
    diagnostics = {}
    decision = evaluate_entry_contract(f, diagnostics=diagnostics)
    assert decision, diagnostics
    f.loc[:5, "ma5"] = [101.3,101.4,101.2,101.3,101.4,101.5] if side == "LONG" else [
        98.7,98.6,98.8,98.7,98.6,98.5]
    assert evaluate_entry_contract(f, diagnostics=diagnostics) is None
    assert diagnostics["reason"] == "BLOCKED_MA5_CHOP_TURNS"
    with pytest.raises(ValueError):
        validate_entry_frame(f, side, decision["type"])


@pytest.mark.parametrize("symbol", ["龙虾/USDT", "CAP/USDT"])
@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_scan_and_quote_adapter_share_new_authority(symbol, side, monkeypatch):
    from core.services.exits.realtime_profit_exit import enforce_realtime_profit_exit
    p, f, stamp = market(side)
    monkeypatch.setattr(time, "time", lambda: stamp/1000)
    account = SimpleNamespace(positions={symbol:p}, position_meta={},
                              save_state=Mock(), log=Mock(),
                              close_position=AsyncMock(return_value=False))
    engine = SimpleNamespace(is_running=True, account=account,
                             _channel_exit_frames={symbol:f})
    assert not asyncio.run(enforce_realtime_profit_exit(engine, symbol, 100., stamp))
    assert account.close_position.await_args.args[2] == "Channel Swing " + policy.PIVOT_REASON
    assert p[policy.STATE_KEY]["pending"] == policy.PIVOT_REASON


@pytest.mark.parametrize("symbol", ["龙虾/USDT", "CAP/USDT"])
@pytest.mark.parametrize("side", ["LONG", "SHORT"])
@pytest.mark.parametrize("gain,allowed", [(2.999999, False), (3., True), (3.000001, True)])
def test_doji_requires_observed_three_entry_atr(symbol, side, gain, allowed):
    p, f, stamp = market(side)
    sign = 1 if side == "LONG" else -1
    f.loc[5, ["open", "high", "low", "close"]] = [100., 101., 99., 100.4]
    f.loc[5, "kc_middle"] = f.loc[4, "kc_middle"] + sign
    f.loc[6, ["open", "high", "low"]] = [100., 100.1, 99.9]
    state = policy.migrate(p, {})
    policy.observe_maturity(state, policy.position_identity(p), 100.+sign*gain, stamp-1)
    result, _ = policy.evaluate(p, f, 100.-sign*.6, stamp)
    assert bool(result) is allowed
    if result:
        assert result["doji_arm_gain_atr"] == 3.
        assert result["max_favorable_move"] == pytest.approx(gain)


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_maturity_no_hindsight_invalid_atr_or_stale_peak(side):
    p, f, stamp = market(side)
    sign = 1 if side == "LONG" else -1
    p["peak_price"] = 100.+sign*50.
    f["high"] = 150.
    f["low"] = 50.
    state = policy.migrate(p, {})
    assert not policy.doji_mature(state, policy.position_identity(p))
    state["last_ms"] = stamp
    policy.observe_maturity(state, policy.position_identity(p), 100.+sign*50., stamp-1)
    assert not policy.doji_mature(state, policy.position_identity(p))
    policy.observe_maturity(state, policy.position_identity(p), 100.+sign*50.,
                            p["open_timestamp"]*1000-1)
    assert not policy.doji_mature(state, policy.position_identity(p))
    p["entry_atr"] = float("nan")
    p.pop(policy.STATE_KEY)
    state = policy.migrate(p, {})
    policy.observe_maturity(state, policy.position_identity(p), 100.+sign*50., stamp)
    assert not policy.doji_mature(state, policy.position_identity(p))


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_maturity_persists_fixed_atr_and_isolates_position(side, monkeypatch):
    p, f, stamp = market(side)
    sign = 1 if side == "LONG" else -1
    monkeypatch.setattr(time, "time", lambda: stamp/1000)
    account = SimpleNamespace(positions={"CAP/USDT":p}, position_meta={},
                              save_state=Mock(), log=Mock(),
                              close_position=AsyncMock(return_value=False))
    asyncio.run(policy.enforce(account, "CAP/USDT", 100.+sign*3., None, stamp))
    saved = copy.deepcopy(account.position_meta)
    p.pop(policy.STATE_KEY)
    p["entry_atr"] = 999.
    policy.migrate(p, saved["CAP/USDT"])
    assert p[policy.STATE_KEY]["fixed_entry_atr"] == 1.
    assert policy.doji_mature(p[policy.STATE_KEY], policy.position_identity(p))
    assert account.save_state.call_count > 0
    p["open_timestamp"] += 1.
    policy.migrate(p, saved["CAP/USDT"])
    assert not policy.doji_mature(p[policy.STATE_KEY], policy.position_identity(p))
    other, _, _ = market(side)
    policy.migrate(other, {})
    assert not policy.doji_mature(other[policy.STATE_KEY], policy.position_identity(other))


def test_old_unqualified_doji_pending_revoked_but_pivot_retry_preserved():
    p, _, _ = market()
    old = dict(policy=policy.PREVIOUS_POLICY, identity=policy.position_identity(p),
               pending=policy.DOJI_REASON, evidence={"reason": policy.DOJI_REASON})
    p[policy.STATE_KEY] = old
    policy.migrate(p, {})
    assert "pending" not in p[policy.STATE_KEY]
    assert not policy.close_allowed(p, {}, "Channel Swing "+policy.DOJI_REASON, True)
    p[policy.STATE_KEY] = {**old, "pending": policy.PIVOT_REASON,
                           "evidence": {"reason": policy.PIVOT_REASON}}
    policy.migrate(p, {})
    assert p[policy.STATE_KEY]["pending"] == policy.PIVOT_REASON
