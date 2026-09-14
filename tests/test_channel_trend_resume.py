"""Continuation after a fill, missed-peak shorts, and confirmed reversal longs."""
import asyncio
from unittest.mock import AsyncMock

import pandas as pd
import pytest

from core.services.entry_diagnostics_service import entry_diagnostics
from core.services.strategies.outer_strategy import aligned_entry, peak_lower_breakout_ready
from test_channel_breakout_valley_fix import breakout_market
from test_channel_swing_execution import SYMBOL, _execution_engine


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def peak_market() -> pd.DataFrame:
    frame = pd.DataFrame({
        "open": [100., 101., 103., 103., 101., 99., 97.],
        "close": [100.5, 102., 104., 102., 100., 97., 96.8],
        "ma3": [100., 101., 103., 102., 100., 98., 97.],
        "kc_middle": [102., 102.2, 102.4, 102.3, 102.2, 102.1, 102.],
    })
    frame["high"] = frame[["open", "close"]].max(axis=1) + .2
    frame["low"] = frame[["open", "close"]].min(axis=1) - .2
    frame["ma15"], frame["kc_upper"], frame["kc_lower"], frame["atr"] = 102., 106., 98., 3.
    frame["timestamp"] = [60000 * (i + 1) for i in range(len(frame))]
    return frame


def engine_for(frame: pd.DataFrame, side: str, monkeypatch):
    engine = _execution_engine(frame, side, True)
    engine.account.positions.clear()
    engine.account.trades = []
    engine.account.save_state = lambda: None
    engine.is_running = True
    price = float(frame.iloc[-1]["close"])
    now = float(frame.iloc[-1]["timestamp"]) / 1000 + 1
    engine.tickers[SYMBOL] = price
    engine._channel_exit_frames = {SYMBOL: frame.copy()}
    engine._channel_entry_quote_times = {SYMBOL: now}
    engine._channel_chop_state = lambda *_: {"detected": False}
    engine._abnormal_market_entry_allowed = lambda *a, **k: True
    monkeypatch.setattr("core.engine.time.time", lambda: now)
    monkeypatch.setattr("core.engine.DEFAULT_SYMBOLS", [SYMBOL])
    return engine, price, now


@pytest.mark.parametrize("case", ["closed", "live", "doji", "weak", "inside", "no_peak", "turned"])
def test_peak_lower_break_uses_one_closed_body(case: str) -> None:
    frame = peak_market()
    if case == "live":
        frame = frame.iloc[:-1].copy()
    elif case == "doji":
        frame.loc[5, "open"] = frame.loc[5, "close"]
    elif case == "weak":
        frame.loc[5, ["high", "low"]] = [110., 90.]
    elif case == "no_peak":
        frame["ma3"] = [105., 104., 103., 102., 100., 98., 97.]
    elif case == "turned":
        frame.loc[5, "ma3"] = 101.
    price = 99. if case == "inside" else float(frame.iloc[-1]["close"])
    assert peak_lower_breakout_ready(frame, price) is (case == "closed")
    assert (aligned_entry(frame, price)["action"] == "ENTER") is (case == "closed"), aligned_entry(frame, price)


@pytest.mark.anyio
async def test_peak_lower_break_reaches_account_and_rechecks_quote(monkeypatch) -> None:
    frame = peak_market()
    engine, price, now = engine_for(frame, "SHORT", monkeypatch)
    assert await engine._fresh_channel_entry_snapshot(SYMBOL, "SHORT")
    await engine._process_single_symbol(SYMBOL, now, None, False)
    assert len(engine.account.events) == 1, engine.account.logs
    engine.account.positions.clear()
    engine.tickers[SYMBOL] = 99.
    assert await engine._fresh_channel_entry_snapshot(SYMBOL, "SHORT") is None


def reversal_market(confirmed: bool) -> pd.DataFrame:
    frame = peak_market()
    frame.loc[4:6, "ma3"] = [100., 101., 102.]
    frame.loc[4:6, "kc_middle"] = [102., 102.1, 102.2]
    frame.loc[5, ["open", "high", "low", "close"]] = [105.5, 106.7, 105.3, 106.5]
    frame.loc[6, ["open", "high", "low", "close"]] = [106.5, 107.2, 106.3, 107.]
    if confirmed:
        live = frame.iloc[-1].copy()
        live["timestamp"] += 60000
        live[["open", "high", "low", "close"]] = [107., 107.12, 106.9, 107.1]
        frame = pd.concat([frame, live.to_frame().T], ignore_index=True)
    return frame


@pytest.mark.parametrize("confirmed", [False, True])
def test_long_reversal_after_peak_needs_two_closed_breakout_bodies(confirmed: bool) -> None:
    frame = reversal_market(confirmed)
    decision = aligned_entry(frame, float(frame.iloc[-1]["close"]))
    assert (decision["action"] == "ENTER") is confirmed, decision
    if confirmed:
        assert decision["side"] == "LONG"


@pytest.mark.anyio
@pytest.mark.parametrize("confirmed", [False, True])
async def test_reversal_scan_and_snapshot_cannot_bypass_two_bodies(confirmed, monkeypatch) -> None:
    frame = reversal_market(confirmed)
    engine, price, now = engine_for(frame, "LONG", monkeypatch)
    for options in ({}, {"pivot_entry_signal": True}):
        snapshot = await engine._fresh_channel_entry_snapshot(SYMBOL, "LONG", **options)
        assert (snapshot is not None) is confirmed
    await engine._process_single_symbol(SYMBOL, now, None, False)
    assert len(engine.account.events) == int(confirmed), engine.account.logs


@pytest.mark.anyio
@pytest.mark.parametrize("side", ["LONG", "SHORT"])
@pytest.mark.parametrize("ticket_mode", [None, "outer_cycle", "trend_same_side", "next_breakout"])
async def test_same_bar_continuation_reopens_once_without_profit_cooldown(side, ticket_mode, monkeypatch) -> None:
    frame = breakout_market(side)
    # No fresh breakout or pivot: only the completed close can authorize this entry.
    frame["kc_upper"], frame["kc_lower"] = 110., 90.
    engine, price, now = engine_for(frame, side, monkeypatch)
    assert aligned_entry(frame, price)["action"] == "WAIT"
    close = dict(symbol=SYMBOL, action="CLOSE_" + side, id=now * 1000 - 100,
                 reason="Channel Swing PROFIT_PROTECTION completed")
    engine.account.trades = [close]
    engine.account.last_closed_at = {SYMBOL: now - .1}
    engine._channel_entry_minute = {SYMBOL: int(now // 60)}
    engine._channel_used_confirmation = {SYMBOL: (side, engine._channel_candidate_bar_id(frame))}
    if ticket_mode:
        engine.account.channel_profit_reentries = {SYMBOL: dict(
            phase="closed", side=side, mode=ticket_mode, token="resume", requires_pullback=False,
            exit_bar_id=frame.iloc[-1]["timestamp"], close_reason=close["reason"],
            close_requested_at_ms=now * 1000 - 200,
        )}
    monkeypatch.setattr("core.engine.CHANNEL_PROFIT_REENTRY_COOLDOWN_SEC", 120)
    diagnostic = entry_diagnostics(engine, SYMBOL, frame, price, now)
    assert diagnostic["reason"] == "KC_ENTRY_READY", diagnostic
    await asyncio.gather(*(engine._process_single_symbol(SYMBOL, now, None, False) for _ in range(3)))
    assert len(engine.account.events) == 1, engine.account.logs
    assert engine.account.events[0][2] == side


@pytest.mark.anyio
async def test_same_bar_reentry_rejects_quote_reversal_during_order_check(monkeypatch) -> None:
    frame = breakout_market("LONG")
    frame["kc_upper"], frame["kc_lower"] = 110., 90.
    engine, price, now = engine_for(frame, "LONG", monkeypatch)
    engine.account.trades = [dict(symbol=SYMBOL, action="CLOSE_LONG", id=now * 1000 - 100,
                                  reason="Channel Swing PROFIT_PROTECTION completed")]

    async def change_quote(*_args) -> bool:
        engine.tickers[SYMBOL] = 100.
        return True

    engine._execution_price_is_safe = change_quote
    await engine._process_single_symbol(SYMBOL, now, None, False)
    assert not engine.account.events, engine.account.logs


@pytest.mark.anyio
async def test_successful_reentry_consumes_close_even_before_position_refresh(monkeypatch) -> None:
    frame = breakout_market("LONG")
    frame["kc_upper"], frame["kc_lower"] = 110., 90.
    engine, price, now = engine_for(frame, "LONG", monkeypatch)
    engine.account.trades = [dict(symbol=SYMBOL, action="CLOSE_LONG", id=now * 1000 - 100,
                                  reason="Channel Swing PROFIT_PROTECTION completed")]
    engine.account.open_position = AsyncMock(return_value=True)
    await asyncio.gather(*(engine._execute_confirmed_channel_break(SYMBOL, frame, price, "LONG") for _ in range(3)))
    assert engine.account.open_position.await_count == 1
    engine.account.trades.append(dict(symbol=SYMBOL, action="CLOSE_LONG", id=now * 1000,
                                     reason="Channel Swing PROFIT_PROTECTION completed again"))
    assert await engine._execute_confirmed_channel_break(SYMBOL, frame, price, "LONG")
    assert engine.account.open_position.await_count == 2


@pytest.mark.anyio
@pytest.mark.parametrize("case", ["stop", "pending", "opposite", "turned", "no_fill", "new_open", "daily", "balance", "abnormal"])
async def test_continuation_keeps_close_confirmation_direction_and_risk(case, monkeypatch) -> None:
    frame = breakout_market("LONG")
    frame["kc_upper"], frame["kc_lower"] = 110., 90.
    engine, price, now = engine_for(frame, "LONG", monkeypatch)
    close = dict(symbol=SYMBOL, action="CLOSE_LONG", id=now * 1000 - 100,
                 reason="Channel Swing PROFIT_PROTECTION completed")
    engine.account.trades = [close]
    if case == "stop":
        close["reason"] = "Channel Swing ATR_STOP"
    elif case == "pending":
        engine.account.channel_profit_reentries = {SYMBOL: dict(phase="closing", side="LONG", mode="outer_cycle")}
    elif case == "opposite":
        close["action"] = "CLOSE_SHORT"
    elif case == "turned":
        frame.loc[2, "ma3"] = 99.
    elif case == "no_fill":
        engine.account.trades.clear()
    elif case == "new_open":
        engine.account.trades.append(dict(symbol=SYMBOL, action="OPEN_LONG", id=now * 1000))
    elif case == "balance":
        engine.account.get_available_balance = lambda: 0.
    elif case == "abnormal":
        engine._abnormal_market_entry_allowed = lambda *a, **k: False
    await engine._process_single_symbol(SYMBOL, now, None, case == "daily")
    assert not engine.account.events, engine.account.logs
