import pytest

from core.services.exits import atr_step_profit as ladder, trend_pivot_exit as policy
from test_live_ma5_owner_exit import market


@pytest.mark.parametrize("symbol", ["龙虾/USDT", "CAP/USDT"])
@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_fixed_atr_profit_lock_is_retired(symbol, side):
    position, _, stamp, _ = market(side)

    result = ladder.evaluate(
        position, {"reference_atr": 1.}, symbol, 110., stamp,
        fee=.0005, slippage=.0001,
    )

    assert result == (None, None, {})


@pytest.mark.parametrize(
    "prior_policy",
    [
        policy.POLICY,
        "fixed_atr_half_step_or_doji_or_kc_ma5_v11",
    ],
)
@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_migration_revokes_pending_atr_profit_lock(prior_policy, side):
    position, _, _, _ = market(side)
    identity = policy.position_identity(position)
    saved = {
        "policy": prior_policy,
        "identity": identity,
        "pending": ladder.REASON,
        "evidence": {"reason": ladder.REASON},
        "atr_step_profit": {"level": 2, "floor_price": 101.5},
        "ma5_peak": {"baseline": 100., "extreme": 102., "favorable": True},
    }
    meta = {policy.STATE_KEY: saved.copy()}
    position[policy.STATE_KEY] = saved.copy()

    state = policy.migrate(position, meta)

    assert state.get("pending") not in policy.REASONS
    assert "evidence" not in state
    assert "atr_step_profit" not in state
    assert state["ma5_peak"] == saved["ma5_peak"]
    assert meta[policy.STATE_KEY] == state


def test_no_hindsight_peak_and_no_cross_position_state():
    position, _, stamp, _ = market("LONG")
    position["max_pnl_usdt"] = 999.

    assert policy.evaluate(position, None, 100., stamp, "龙虾/USDT")[0] is None
