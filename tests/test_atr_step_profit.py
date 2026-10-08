import asyncio
import copy
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from core.services.exits import atr_step_profit as ladder, trend_pivot_exit as policy
from core.services.exits.peak_trailing_exit import estimated_net_pnl
from test_live_ma5_owner_exit import market


@pytest.mark.parametrize("symbol,step", [("龙虾/USDT", 1.), ("CAP/USDT", 2.)])
@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_exact_steps_and_half_step_floors(symbol, step, side):
    p, _, stamp, _ = market(side)
    sign = 1 if side == "LONG" else -1
    state = {"reference_atr":1.}
    for gain, level in [(step-.001, 0), (step, 1), (step*2-.001, 1),
                        (step*2, 2), (step*4.2, 4), (step*3.9, 4)]:
        before = copy.deepcopy(state)
        evidence, problem, patch = ladder.evaluate(p, state, symbol, 100.+sign*gain, stamp,
                                                   fee=.0005, slippage=.0001)
        assert state == before and evidence is None and problem is None
        state.update(patch)
        assert state["atr_step_profit"]["level"] == level
        if level:
            assert state["atr_step_profit"]["floor_price"] == pytest.approx(100.+sign*(level-.5)*step)
    floor = state["atr_step_profit"]["floor_price"]
    assert ladder.evaluate(p, state, symbol, floor, stamp, fee=.0005, slippage=.0001)[0]["reason"] == ladder.REASON
    assert ladder.evaluate(p, state, symbol, floor-sign*.5, stamp, fee=.0005, slippage=.0001)[0]


@pytest.mark.parametrize("symbol", ["龙虾/USDT", "CAP/USDT"])
@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_cost_floor_net_zero_and_never_relaxes(symbol, side):
    p, _, stamp, _ = market(side)
    p["entry_atr"] = .1
    state = {"reference_atr":.1}
    sign = 1 if side == "LONG" else -1
    quote = 100.+sign*ladder.STEPS[symbol]*.1
    _, _, patch = ladder.evaluate(p, state, symbol, quote, stamp, fee=.0005, slippage=.0001)
    state.update(patch)
    floor = state["atr_step_profit"]["floor_price"]
    assert estimated_net_pnl(100., floor, 1., sign, .0005, .0001) == pytest.approx(0., abs=1e-10)
    _, _, patch = ladder.evaluate(p, state, symbol, quote, stamp, fee=0., slippage=0.)
    assert patch["atr_step_profit"]["floor_price"] == floor


@pytest.mark.parametrize("symbol", ["龙虾/USDT", "CAP/USDT"])
@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_durable_frozen_atr_retry_real_close_and_restart(symbol, side, tmp_path, monkeypatch):
    from core.paper_account import PaperAccount
    monkeypatch.setattr("core.paper_account.STATE_FILE", str(tmp_path/"step.json"))
    p, _, stamp, _ = market(side)
    sign = 1 if side == "LONG" else -1
    monkeypatch.setattr(time, "time", lambda:stamp/1000)
    a = PaperAccount()
    a.positions = {symbol:p}
    a.position_meta = {}
    peak = 100.+sign*ladder.STEPS[symbol]*2
    assert not asyncio.run(policy.enforce(a, symbol, peak, None, stamp))
    assert p[policy.STATE_KEY]["atr_step_profit"]["level"] == 2
    restored = PaperAccount()
    restored.positions[symbol]["entry_atr"] = 99.
    floor = restored.positions[symbol][policy.STATE_KEY]["atr_step_profit"]["floor_price"]
    restored.close_position = AsyncMock(return_value=False)
    assert not asyncio.run(policy.enforce(restored, symbol, floor, None, stamp))
    assert restored.positions[symbol][policy.STATE_KEY]["pending"] == ladder.REASON
    restored.close_position.assert_awaited_once()
    retry = PaperAccount()
    assert asyncio.run(policy.enforce(retry, symbol, floor-sign*.01, None, stamp))
    assert symbol not in retry.positions
    assert retry.trades[0]["reason"] == "Channel Swing "+ladder.REASON
    assert not asyncio.run(policy.enforce(retry, symbol, floor, None, stamp))


def test_invalid_atr_no_floor_and_failed_save_no_close(monkeypatch):
    p, _, stamp, _ = market("LONG")
    monkeypatch.setattr(time, "time", lambda:stamp/1000)
    p["entry_atr"] = float("nan")
    assert ladder.evaluate(p, {}, "CAP/USDT", 110., stamp, fee=.0005, slippage=.0001)[1]
    p["entry_atr"] = .01
    a = SimpleNamespace(positions={"CAP/USDT":p}, position_meta={}, log=Mock(),
                        save_state=Mock(side_effect=OSError("disk full")),
                        close_position=AsyncMock(return_value=False))
    assert not asyncio.run(policy.enforce(a, "CAP/USDT", 100.02, None, stamp))
    a.close_position.assert_not_awaited()
    a.log.assert_called()


def test_no_hindsight_peak_and_no_cross_position_state():
    p, _, stamp, _ = market("LONG")
    p["max_pnl_usdt"] = 999.
    assert policy.evaluate(p, None, 100., stamp, "龙虾/USDT")[0] is None
    p[policy.STATE_KEY] = dict(policy=policy.POLICY, identity=["LONG", 1., 99., 99.],
                              atr_step_profit=dict(level=10), pending=ladder.REASON,
                              evidence=dict(reason=ladder.REASON))
    assert policy.evaluate(p, None, 100., stamp, "龙虾/USDT")[0] is None
