import asyncio
import copy
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from core.services.exits import trend_pivot_exit as policy
from core.services.exits import atr_step_profit
from test_live_ma5_owner_exit import market


def setup():
    p, f, stamp, quote = market("SHORT")
    previous = float(f.iloc[-2].ma5)
    p[policy.STATE_KEY] = dict(policy=policy.POLICY, identity=policy.position_identity(p),
                              reference_atr=1.,
                              ma5_peak=dict(baseline=previous+.1, extreme=previous-.1,
                                            favorable=True))
    f.loc[4:5, "kc_middle"] = [100.1, 100.]
    f.loc[4:5, "ma15"] = [100.1, 100.]
    return p, f, stamp, quote+1.


@pytest.mark.parametrize("symbol", ["龙虾/USDT", "CAP/USDT"])
@pytest.mark.parametrize("kc,ma15,exit_expected", [
    (-.1, -.1, False), (-.1, 0., True), (-.1, .1, True),
    (0., -.1, True), (.1, -.1, True), (.1, .1, True)])
def test_both_closed_trends_must_fall_to_block_short_ma5_exit(symbol, kc, ma15, exit_expected):
    p, f, stamp, quote = setup()
    f.loc[5, "kc_middle"] = 100.1+kc
    f.loc[5, "ma15"] = 100.1+ma15
    before = copy.deepcopy(p)
    evidence, problem = policy.evaluate(p, f, quote, stamp, symbol)
    assert bool(evidence) is exit_expected
    assert p == before
    if not exit_expected:
        assert problem == "HOLD_POSITION_STRONG_TREND"
    else:
        assert evidence["trend_hold"] is False
    f.loc[6, ["kc_middle", "ma15"]] = 200.
    assert bool(policy.evaluate(p, f, quote, stamp, symbol)[0]) is exit_expected


@pytest.mark.parametrize("fault", ["nan", "zero", "missing", "gap", "stale"])
def test_unknown_trend_never_grants_new_close(fault):
    p, f, stamp, quote = setup()
    if fault == "missing":
        f = f.drop(columns=["ma15"])
    elif fault == "gap":
        f.loc[4, "timestamp"] -= 1
    elif fault == "stale":
        stamp += 60000
    else:
        f.loc[5, "ma15"] = float("nan") if fault == "nan" else 0.
    assert policy.evaluate(p, f, quote, stamp, "CAP/USDT")[0] is None


@pytest.mark.parametrize("symbol", ["龙虾/USDT", "CAP/USDT"])
def test_no_doji_authority_for_short(symbol):
    p, f, stamp, _ = market("SHORT")
    f["ma5"] = 100.
    f["ma15"] = 100.
    assert policy.evaluate(p, f, 100., stamp, symbol)[0] is None
    p[policy.STATE_KEY] = dict(policy=policy.POLICY, identity=policy.position_identity(p),
                              pending=policy.DOJI_REASON,
                              evidence=dict(reason=policy.DOJI_REASON))
    assert not policy.close_allowed(p, {}, "Channel Swing "+policy.DOJI_REASON, True)
    assert policy.close_allowed(p, {}, "手動平倉", True)


@pytest.mark.parametrize("symbol", ["龙虾/USDT", "CAP/USDT"])
def test_blocked_closed_turn_not_replayed_when_trend_weakens(symbol, monkeypatch):
    from test_trend_pivot_owner_policy import market as closed_market
    p, f, stamp = closed_market("SHORT")
    f.loc[4:5, "kc_middle"] = [100.1, 100.]
    f.loc[4:5, "ma15"] = [100.1, 100.]
    monkeypatch.setattr(time, "time", lambda:stamp/1000)
    a = SimpleNamespace(positions={symbol:p}, position_meta={}, save_state=Mock(),
                        log=Mock(), close_position=AsyncMock(return_value=False))
    assert not asyncio.run(policy.enforce(a, symbol, 100., f, stamp))
    assert "pending" not in p[policy.STATE_KEY]
    assert p[policy.STATE_KEY]["ma5_observation"]["bar_ms"] == float(f.iloc[-1].timestamp)
    f.loc[5, "ma15"] = 100.2
    assert not asyncio.run(policy.enforce(a, symbol, 100., f, stamp))
    a.close_position.assert_not_awaited()


@pytest.mark.parametrize("symbol", ["龙虾/USDT", "CAP/USDT"])
def test_hold_persists_then_fresh_live_turn_can_close_once(symbol, tmp_path, monkeypatch):
    from core.paper_account import PaperAccount
    monkeypatch.setattr("core.paper_account.STATE_FILE", str(tmp_path/"hold.json"))
    p, f, stamp, quote = setup()
    monkeypatch.setattr(time, "time", lambda:stamp/1000)
    a = PaperAccount()
    a.positions = {symbol:p}
    a.position_meta = {}
    assert not asyncio.run(policy.enforce(a, symbol, quote, f, stamp))
    assert "pending" not in p[policy.STATE_KEY]
    restored = PaperAccount()
    assert restored.positions[symbol][policy.STATE_KEY]["ma5_peak"] == p[policy.STATE_KEY]["ma5_peak"]
    f.loc[5, "ma15"] = 100.2
    assert asyncio.run(policy.enforce(restored, symbol, quote, f, stamp))
    assert symbol not in restored.positions
    assert len(restored.trades) == 1
    assert not asyncio.run(policy.enforce(restored, symbol, quote, f, stamp))


@pytest.mark.parametrize("symbol", ["龙虾/USDT", "CAP/USDT"])
@pytest.mark.parametrize("kc,ma15,blocked", [(.1, .1, True), (.1, 0., False),
                                           (0., .1, False), (-.1, .1, False)])
def test_long_strong_trend_holds_peak_pullback(symbol, kc, ma15, blocked):
    p, f, stamp, quote = market("LONG")
    previous = float(f.iloc[-2].ma5)
    p[policy.STATE_KEY] = dict(policy=policy.POLICY, identity=policy.position_identity(p),
                              reference_atr=1.,
                              ma5_peak=dict(baseline=previous-.1, extreme=previous+.1,
                                            favorable=True))
    f.loc[4:5, "kc_middle"] = [100., 100.+kc]
    f.loc[4:5, "ma15"] = [100., 100.+ma15]
    evidence, problem = policy.evaluate(p, f, quote-1., stamp, symbol)
    assert bool(evidence) is not blocked
    if blocked:
        assert problem == "HOLD_POSITION_STRONG_TREND"


@pytest.mark.parametrize("symbol", ["龙虾/USDT", "CAP/USDT"])
@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_ladder_priority_closes_even_in_strong_trend(symbol, side, tmp_path, monkeypatch):
    from core.paper_account import PaperAccount
    monkeypatch.setattr("core.paper_account.STATE_FILE", str(tmp_path/"priority.json"))
    p, f, stamp, _ = market(side)
    sign = 1 if side == "LONG" else -1
    f.loc[4:5, "kc_middle"] = [100., 100.+sign*.1]
    f.loc[4:5, "ma15"] = [100., 100.+sign*.1]
    monkeypatch.setattr(time, "time", lambda:stamp/1000)
    a = PaperAccount()
    a.positions = {symbol:p}
    a.position_meta = {}
    step = atr_step_profit.STEPS[symbol]
    assert not asyncio.run(policy.enforce(a, symbol, 100.+sign*step*2, f, stamp))
    assert asyncio.run(policy.enforce(a, symbol, 100.+sign*step*1.5, f, stamp))
    assert a.trades[0]["reason"] == "Channel Swing "+atr_step_profit.REASON
