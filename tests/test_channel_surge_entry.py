"""Later effective green candles release old surges while order guards remain."""
from unittest.mock import AsyncMock
import pytest
from core.channel_surge_entry import surge_recovery_entry
from core.engine import TradingEngine
from test_channel_pivot_entry import market
from test_channel_swing_execution import _execution_engine, SYMBOL


@pytest.fixture
def anyio_backend():
    return "asyncio"


def recovery_frame():
    f = market("LONG")
    f.loc[14, ["open", "high", "low", "close"]] = [90., 109., 89., 108.]
    return f


@pytest.mark.parametrize("case", ["valid", "live_only", "red", "doji", "small", "flat_ma3", "equal_low", "broken", "wick_break", "chase", "wrong_ck", "nan", "new_surge"])
def test_recovery_releases_old_surge_after_effective_green(case):
    f = recovery_frame()
    price = 98.1
    if case == "live_only": f = f.iloc[:-1]
    if case == "red": f.loc[18, ["open", "close"]] = [98., 95.]
    if case == "doji": f.loc[18, "open"] = f.loc[18, "close"]
    if case == "small": f.loc[18, "open"] = 97.9
    if case == "flat_ma3": f.loc[18, "ma3"] = f.loc[17, "ma3"]
    if case == "equal_low": f.loc[16, "low"] = f.loc[17, "low"]
    if case == "broken": price = f.loc[17, "low"]
    if case == "wick_break": f.loc[19, "low"] = f.loc[17, "low"]
    if case == "chase": price = 100.
    if case == "wrong_ck": f["kc_middle"] = 100.
    if case == "nan": f.loc[17, "low"] = float("nan")
    if case == "new_surge":
        f.loc[19, ["open", "low"]] = [80., 80.]
    result = surge_recovery_entry(f, price)
    released = case in {"valid", "flat_ma3", "equal_low", "broken", "wick_break", "chase", "wrong_ck"}
    if released:
        assert result is None
    else:
        assert result is not None and result["action"] == "WAIT"
    allowed = case in {"valid", "equal_low", "broken", "wick_break", "chase"}
    assert (TradingEngine._channel_swing_action(f, price).get("side") == "LONG") is allowed


@pytest.mark.anyio
@pytest.mark.parametrize("block", ["none", "fresh_red", "risk", "room", "balance", "minute", "halt"])
async def test_recovery_scan_and_real_order_revalidate(block, monkeypatch):
    f = recovery_frame()
    e = _execution_engine(f, "LONG", True)
    e.account.positions.clear()
    e.account.save_state = lambda: None
    e.tickers[SYMBOL] = 98.1
    e._channel_chop_state = lambda *a: {"detected": False}
    e._abnormal_market_entry_allowed = lambda *a, **k: block != "risk"
    e._channel_profit_room = lambda *a: dict(allowed=block != "room", reason="room", checked=False)
    if block == "balance": e.account.get_available_balance = lambda: 0.
    if block == "minute": e._channel_candle_entry_blocked = lambda *a, **k: True
    if block == "fresh_red":
        fresh = f.copy()
        fresh.loc[18, ["open", "close"]] = [98., 95.]
        e.fetch_klines = AsyncMock(side_effect=[f.copy(), fresh])
    monkeypatch.setattr("core.engine.DEFAULT_SYMBOLS", [SYMBOL])
    await e._process_single_symbol(SYMBOL, 1., None, block == "halt")
    assert len(e.account.events) == int(block == "none"), e.account.logs
    assert not any("處理失敗" in text for text, _ in e.account.logs)


@pytest.mark.anyio
@pytest.mark.parametrize("cached", [False, True])
async def test_released_surge_no_longer_caps_price_at_old_confirmation(cached, monkeypatch):
    f = recovery_frame()
    e = _execution_engine(f, "LONG", True)
    e.account.positions.clear()
    snapshot = dict(price=100., frame=f, kc_upper=110., kc_lower=90.)
    e._fresh_channel_entry_snapshot = AsyncMock(return_value=snapshot)
    monkeypatch.setattr("core.engine.DEFAULT_SYMBOLS", [SYMBOL])
    e.tickers[SYMBOL] = 100.
    signal = dict(side="LONG", entry_mode="CHANNEL_SWING", action="ENTER_MARKET", reason="released surge")
    assert await e._place_structured_entry(SYMBOL, signal, 100., channel_snapshot=snapshot if cached else None)
    assert len(e.account.events) == 1


@pytest.mark.anyio
@pytest.mark.parametrize("case", ["normal", "same_bar", "failed_close", "abnormal_wait", "fresh_ma"])
async def test_reentry_keeps_ticket_and_freshness_guards(case, monkeypatch):
    f = recovery_frame()
    e = _execution_engine(f, "LONG", True)
    e.account.positions.clear()
    e.account.save_state = lambda: None
    e.tickers[SYMBOL] = 98.1
    e._abnormal_market_entry_allowed = lambda *a, **k: True
    e._channel_profit_room = lambda *a: dict(allowed=True, checked=False)
    ticket = dict(side="LONG", phase="closed", token="recovery", mode="outer_cycle",
                  requires_pullback=False, exit_bar_id=16)
    if case == "same_bar": ticket["exit_bar_id"] = 19
    if case == "failed_close": ticket["phase"] = "closing"
    if case == "abnormal_wait": ticket["requires_pullback"] = True
    e.account.channel_profit_reentries = {SYMBOL: ticket}
    if case == "fresh_ma":
        fresh = f.copy()
        fresh.loc[18, "ma3"] = fresh.loc[17, "ma3"]
        e.fetch_klines = AsyncMock(return_value=fresh)
    monkeypatch.setattr("core.engine.DEFAULT_SYMBOLS", [SYMBOL])
    await e._try_profit_reentry(SYMBOL, f, 98.1, False)
    assert len(e.account.events) == int(case == "normal"), e.account.logs


def test_history_fetch_sizes_give_the_same_surge_decision():
    import pandas as pd
    f = recovery_frame()
    padding = pd.concat([f.iloc[:1]] * 100, ignore_index=True)
    full = pd.concat([padding, f], ignore_index=True)
    assert surge_recovery_entry(full, 98.1) == surge_recovery_entry(full.tail(80), 98.1)
