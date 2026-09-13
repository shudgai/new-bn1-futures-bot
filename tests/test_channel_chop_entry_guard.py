import pytest
from test_direct_break_execution import setup_engine, anyio_backend
from test_channel_swing_execution import SYMBOL
from channel_test_frames import closed_outer_entry_frame
from core.services.swing_service import channel_chop_state


def confirmed_break(engine, frame, side):
    frame["ma15"] = [
        100. + (i * .01 if side == "LONG" else -i * .01)
        for i in range(len(frame))
    ]
    frame.loc[69, "open"] = 100.
    frame.loc[68, "close"] = 104. if side == "LONG" else 96.
    engine.tickers[SYMBOL] = float(frame.loc[68, "close"])


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_chop_state_blocks_bandwidth_compression_symmetrically(side):
    frame = closed_outer_entry_frame(side)
    frame["kc_middle"] = 100.0
    frame["kc_upper"] = 101.0
    frame["kc_lower"] = 99.0
    target_index = frame.index[-2] if len(frame) > 20 else frame.index[-1]
    frame.loc[target_index, "kc_upper"] = 100.2
    frame.loc[target_index, "kc_lower"] = 99.8
    state = channel_chop_state(frame)
    assert state["detected"] is True
    assert state["compression"] is True


def test_chop_state_blocks_flat_small_body_market():
    frame = closed_outer_entry_frame("LONG")
    frame["kc_middle"] = 100.0
    frame["kc_upper"] = 101.0
    frame["kc_lower"] = 99.0
    frame.loc[frame.index[-5:-1], "open"] = 100.0
    frame.loc[frame.index[-5:-1], "close"] = 100.1
    frame.loc[frame.index[-5:-1], "atr"] = 1.0
    state = channel_chop_state(frame)
    assert state["detected"] is True
    assert state["low_momentum"] is True


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
async def test_confirmed_break_is_blocked_by_chop_state(setup_engine, side):
    engine, frame = setup_engine(side)
    confirmed_break(engine, frame, side)
    engine._channel_chop_state = lambda _: {"detected": True, "clear_direction": None}
    _, candidates = await engine._process_single_symbol(SYMBOL, 1., side, False)
    assert not engine.account.positions
    assert not engine.account.trades
    assert not candidates


@pytest.mark.anyio
@pytest.mark.parametrize("side", ["LONG", "SHORT"])
@pytest.mark.parametrize("latched", [False, True])
async def test_direct_confirmed_break_uses_chop_guard(setup_engine, side, latched):
    engine, frame = setup_engine(side)
    confirmed_break(engine, frame, side)
    engine._channel_chop_locked = {SYMBOL: True} if latched else {}
    engine._channel_chop_state = lambda _: {"detected": True, "clear_direction": None}
    result = await engine._execute_confirmed_channel_break(SYMBOL, frame, engine.tickers[SYMBOL], side)
    assert result is False
    assert not engine.account.positions
    assert not engine.account.trades


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
    assert result is False
    assert not engine.account.positions
    assert not engine.account.trades
