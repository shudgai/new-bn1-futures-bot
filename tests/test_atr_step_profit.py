import asyncio
import copy
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from core.services.exits import atr_step_profit as ladder, trend_pivot_exit as policy
from core.services.exits.peak_trailing_exit import estimated_net_pnl
from test_live_ma5_owner_exit import market


@pytest.mark.parametrize("symbol,step", [("龙虾/USDT", 1.0), ("CAP/USDT", 2.0)])
@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_exact_steps_and_half_step_floors(symbol, step, side):
    position, _, stamp, _ = market(side)
    sign = 1 if side == "LONG" else -1
    state = {"reference_atr": 1.}
    for gain, level in [(step-.001, 0), (step, 1), (step*2-.001, 1),
                        (step*2, 2), (step*4.2, 4), (step*3.9, 4)]:
        before = copy.deepcopy(state)
        evidence, problem, patch = ladder.evaluate(
            position, state, symbol, 100.+sign*gain, stamp,
            fee=.0005, slippage=.0001,
        )
        assert state == before and evidence is None and problem is None
        state.update(patch)
        assert state["atr_step_profit"]["level"] == level
        if level:
            assert state["atr_step_profit"]["floor_price"] == pytest.approx(
                100.+sign*(level-.5)*step
            )
    floor = state["atr_step_profit"]["floor_price"]
    assert ladder.evaluate(
        position, state, symbol, floor, stamp, fee=.0005, slippage=.0001
    )[0]["reason"] == ladder.REASON
    assert ladder.evaluate(
        position, state, symbol, floor-sign*.5, stamp, fee=.0005, slippage=.0001
    )[0]


@pytest.mark.parametrize("symbol", ["龙虾/USDT", "CAP/USDT"])
@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_cost_floor_net_zero_and_never_relaxes(symbol, side):
    position, _, stamp, _ = market(side)
    position["entry_atr"] = .1
    state = {"reference_atr": .1}
    sign = 1 if side == "LONG" else -1
    quote = 100.+sign*ladder.STEPS[symbol]*.1
    _, _, patch = ladder.evaluate(
        position, state, symbol, quote, stamp, fee=.0005, slippage=.0001
    )
    state.update(patch)
    floor = state["atr_step_profit"]["floor_price"]
    assert estimated_net_pnl(100., floor, 1., sign, .0005, .0001) >= -1e-10
    _, _, patch = ladder.evaluate(
        position, state, symbol, quote, stamp, fee=0., slippage=0.
    )
    assert patch["atr_step_profit"]["floor_price"] == floor


@pytest.mark.parametrize(
    "symbol,old_step,new_step,peak,old_level,old_floor,new_level,new_floor",
    [
        ("龙虾/USDT", 1.5, 1.0, 3.0, 2, 102.25, 3, 102.5),
        ("CAP/USDT", 3.0, 2.0, 6.0, 2, 104.5, 3, 105.0),
    ],
)
def test_step_change_preserves_peak_and_never_relaxes_floor(
    symbol, old_step, new_step, peak, old_level, old_floor, new_level, new_floor
):
    position, _, stamp, _ = market("LONG")
    state = {
        "reference_atr": 1.,
        "atr_step_profit": {
            "symbol": symbol,
            "step_atr": old_step,
            "reference_atr": 1.,
            "peak_gain_atr": peak,
            "level": old_level,
            "floor_price": old_floor,
        },
    }

    evidence, problem, patch = ladder.evaluate(
        position, state, symbol, 100.+peak, stamp, fee=.0005, slippage=.0001
    )

    assert evidence is None and problem is None
    updated = patch["atr_step_profit"]
    assert updated["step_atr"] == new_step
    assert updated["level"] == new_level
    assert updated["floor_price"] == pytest.approx(new_floor)
    assert updated["floor_price"] >= old_floor


@pytest.mark.parametrize("symbol", ["龙虾/USDT", "CAP/USDT"])
@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_durable_frozen_atr_retry_real_close_and_restart(symbol, side, tmp_path, monkeypatch):
    from core.paper_account import PaperAccount

    monkeypatch.setattr("core.paper_account.STATE_FILE", str(tmp_path/"step.json"))
    position, _, stamp, _ = market(side)
    sign = 1 if side == "LONG" else -1
    monkeypatch.setattr(time, "time", lambda: stamp/1000)
    account = PaperAccount()
    account.positions = {symbol: position}
    account.position_meta = {}
    peak = 100.+sign*ladder.STEPS[symbol]*2
    assert not asyncio.run(policy.enforce(account, symbol, peak, None, stamp))
    assert position[policy.STATE_KEY]["atr_step_profit"]["level"] == 2
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
    position, _, stamp, _ = market("LONG")
    monkeypatch.setattr(time, "time", lambda: stamp/1000)
    position["entry_atr"] = float("nan")
    assert ladder.evaluate(
        position, {}, "CAP/USDT", 110., stamp, fee=.0005, slippage=.0001
    )[1]
    position["entry_atr"] = .01
    account = SimpleNamespace(
        positions={"CAP/USDT": position}, position_meta={}, log=Mock(),
        save_state=Mock(side_effect=OSError("disk full")),
        close_position=AsyncMock(return_value=False),
    )
    assert not asyncio.run(policy.enforce(account, "CAP/USDT", 100.02, None, stamp))
    account.close_position.assert_not_awaited()
    account.log.assert_called()


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_migration_keeps_matching_ladder_and_retry(side):
    position, _, _, _ = market(side)
    identity = policy.position_identity(position)
    saved = {
        "policy": policy.POLICY,
        "identity": identity,
        "pending": ladder.REASON,
        "evidence": {"reason": ladder.REASON},
        "atr_step_profit": {
            "symbol": "龙虾/USDT",
            "step_atr": ladder.STEPS["龙虾/USDT"],
            "reference_atr": 1.,
            "level": 2,
            "floor_price": 101.5,
        },
        "ma5_peak": {"baseline": 100., "extreme": 102., "favorable": True},
    }
    meta = {policy.STATE_KEY: saved.copy()}
    position[policy.STATE_KEY] = saved.copy()

    state = policy.migrate(position, meta)

    assert state["pending"] == ladder.REASON
    assert state["evidence"] == saved["evidence"]
    assert state["atr_step_profit"] == saved["atr_step_profit"]
    assert state["ma5_peak"] == saved["ma5_peak"]
    assert meta[policy.STATE_KEY] == state


def test_no_hindsight_peak_and_no_cross_position_state():
    position, _, stamp, _ = market("LONG")
    position["max_pnl_usdt"] = 999.

    assert policy.evaluate(position, None, 100., stamp, "龙虾/USDT")[0] is None
