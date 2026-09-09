import pytest
from core.engine import TradingEngine
from test_direct_break_execution import setup_engine, anyio_backend
from test_channel_swing_execution import SYMBOL


def reclaim_setup(setup_engine):
    engine, frame = setup_engine("SHORT", "SHORT")
    frame.loc[66, ["open", "close"]] = [97., 96.]
    frame.loc[67, ["open", "close"]] = [97., 99.]
    frame.loc[68:69, ["open", "close"]] = [99., 100.]
    frame.loc[66:68, "ma3"] = [96., 97., 98.]
    frame["high"] = frame[["open", "close"]].max(axis=1) + .1
    frame["low"] = frame[["open", "close"]].min(axis=1) - .1
    frame.loc[69, "open"] = 97.  # Crossing is happening now, not two bars ago.
    engine.tickers[SYMBOL] = 100.
    engine._channel_chop_state = lambda _: {"detected": False, "clear_direction": "LONG"}
    return engine, frame


@pytest.mark.parametrize("invalid", [None, "wick", "touch", "already_above", "returned_below", "opposite_closed_color"])
def test_short_reclaim_requires_live_cross_without_closed_confirmation(setup_engine, invalid):
    engine, frame = reclaim_setup(setup_engine)
    if invalid == "wick":
        frame.loc[69, "high"] = 101.
        engine.tickers[SYMBOL] = 97.5
    if invalid == "touch": engine.tickers[SYMBOL] = 98.
    if invalid == "already_above": frame.loc[69, "open"] = 98.5
    if invalid == "returned_below": engine.tickers[SYMBOL] = 97.
    if invalid == "opposite_closed_color": frame.loc[68, ["open", "close"]] = [97., 96.]
    result = TradingEngine._channel_swing_action(frame, engine.tickers[SYMBOL], "SHORT")
    assert result["action"] == ("REVERSE" if invalid in (None, "opposite_closed_color") else "HOLD")
    if result["action"] == "REVERSE":
        assert result["side"] == "LONG"
        assert result["reason"] == "KC_LOWER_RECLAIM_LONG"


@pytest.mark.parametrize("outside", [False, True])
def test_short_trough_only_exits_below_lower_rail(setup_engine, outside):
    engine, frame = reclaim_setup(setup_engine)
    frame.loc[66:68, "ma3"] = [100., 97. if outside else 99., 100.]
    frame.loc[67, ["open", "close"]] = [97., 97.]
    frame.loc[68, ["open", "close"]] = [97., 97.]
    result = TradingEngine._channel_swing_action(frame, 97., "SHORT")
    assert result["action"] == ("EXIT" if outside else "HOLD")


@pytest.mark.anyio
async def test_scan_closes_short_then_really_opens_long_inside_channel(setup_engine):
    engine, frame = reclaim_setup(setup_engine)
    events = []
    original_close, original_open = engine.account.close_position, engine.account.open_position
    async def close(*args, **kwargs):
        result = await original_close(*args, **kwargs)
        events.append("closed")
        return result
    async def open_long(*args, **kwargs):
        assert SYMBOL not in engine.account.positions
        events.append("open")
        return await original_open(*args, **kwargs)
    engine.account.close_position, engine.account.open_position = close, open_long
    _, candidates = await engine._process_single_symbol(SYMBOL, 1., None, False)
    assert events == ["closed", "open"]
    assert sorted(t["status"] for t in engine.account.trades) == ["CLOSED", "OPEN"]
    assert engine.account.positions[SYMBOL]["side"] == "LONG"
    assert not candidates
    assert SYMBOL not in engine._channel_outer_reentry_after_exit
    await engine._process_single_symbol(SYMBOL, 2., None, False)
    assert len(engine.account.trades) == 2


@pytest.mark.anyio
async def test_failed_close_never_opens_long(setup_engine):
    engine, frame = reclaim_setup(setup_engine)
    async def fail(*args, **kwargs): return False
    engine.account.close_position = fail
    await engine._process_single_symbol(SYMBOL, 1., None, False)
    assert engine.account.positions[SYMBOL]["side"] == "SHORT"
    assert not engine.account.trades


@pytest.mark.anyio
@pytest.mark.parametrize("block", ["chop", "daily", "price_changed", "account"])
async def test_close_success_but_new_long_must_pass_safety_checks(setup_engine, block):
    engine, frame = reclaim_setup(setup_engine)
    if block == "chop":
        engine._channel_chop_state = lambda _: {"detected": True, "clear_direction": None}
    if block == "account": engine._abnormal_market_entry_allowed = lambda *_: False
    if block == "price_changed":
        original_close = engine.account.close_position
        async def close_and_move(*args, **kwargs):
            result = await original_close(*args, **kwargs)
            engine.tickers[SYMBOL] = 97.
            return result
        engine.account.close_position = close_and_move
    await engine._process_single_symbol(SYMBOL, 1., None, block == "daily")
    assert SYMBOL not in engine.account.positions
    assert [t["status"] for t in engine.account.trades] == ["CLOSED"]
    assert SYMBOL not in engine._channel_outer_reentry_after_exit
