import pytest
from test_direct_break_execution import setup_engine, anyio_backend
from test_channel_swing_execution import SYMBOL


def confirmed_break(engine, frame, side):
    frame["ma15"] = [
        100. + (i * .01 if side == "LONG" else -i * .01)
        for i in range(len(frame))
    ]
    frame.loc[69, "open"] = 100.
    frame.loc[68, "close"] = 104. if side == "LONG" else 96.
    engine.tickers[SYMBOL] = float(frame.loc[68, "close"])


@pytest.mark.anyio
@pytest.mark.parametrize("side", ["LONG", "SHORT"])
async def test_chop_without_confirmed_break_still_has_no_entry(setup_engine, side):
    engine, frame = setup_engine(side)
    engine._channel_chop_state = lambda _: {"detected": True, "clear_direction": None}

    await engine._process_single_symbol(SYMBOL, 1., None, False)

    assert not engine.account.positions
    assert not engine.account.trades


@pytest.mark.anyio
@pytest.mark.parametrize("side", ["LONG", "SHORT"])
async def test_confirmed_break_is_not_blocked_by_chop_state(setup_engine, side):
    engine, frame = setup_engine(side)
    confirmed_break(engine, frame, side)
    # A previous close must not force the next fresh breakout to be opposite.
    engine._channel_outer_reentry_after_exit[SYMBOL] = (
        "SHORT" if side == "LONG" else "LONG"
    )
    _, candidates = await engine._process_single_symbol(SYMBOL, 1., side, False)
    assert engine.account.positions[SYMBOL]["side"] == side
    assert engine.account.trades[-1]["action"] == f"OPEN_{side}"
    assert not candidates
    assert SYMBOL not in engine._channel_outer_reentry_after_exit


@pytest.mark.anyio
@pytest.mark.parametrize("side", ["LONG", "SHORT"])
@pytest.mark.parametrize("latched", [False, True])
async def test_direct_confirmed_break_does_not_use_chop_guard(setup_engine, side, latched):
    engine, frame = setup_engine(side)
    confirmed_break(engine, frame, side)
    engine._channel_chop_locked = {SYMBOL: True} if latched else {}
    engine._channel_chop_state = lambda _: {"detected": not latched, "clear_direction": None}
    result = await engine._execute_confirmed_channel_break(SYMBOL, frame, engine.tickers[SYMBOL], side)
    assert result is True
    assert engine.account.positions[SYMBOL]["side"] == side
    assert engine.account.trades[-1]["action"] == f"OPEN_{side}"


@pytest.mark.anyio
@pytest.mark.parametrize("side", ["LONG", "SHORT"])
async def test_unlock_still_requires_live_outer_break(setup_engine, side):
    engine, frame = setup_engine(side)
    engine._channel_chop_state = lambda _: {"detected": False, "clear_direction": side}
    # Direction is clear, but the live body has not crossed a rail.
    await engine._process_single_symbol(SYMBOL, 1., None, False)
    assert not engine.account.trades
    confirmed_break(engine, frame, side)
    result = await engine._execute_confirmed_channel_break(
        SYMBOL, frame, engine.tickers[SYMBOL], side,
    )
    assert result is True
    assert engine.account.positions[SYMBOL]["side"] == side
