"""Exercise actual intrabar state across repeated order attempts."""
import pytest
from core.engine import TradingEngine
from test_channel_aligned_entry import aligned_frame
from test_channel_swing_execution import _execution_engine, SYMBOL

@pytest.fixture
def anyio_backend():
    return "asyncio"

@pytest.mark.anyio
@pytest.mark.parametrize("side", ["LONG", "SHORT"])
@pytest.mark.parametrize("route", ["fresh", "cached", "reentry"])
@pytest.mark.parametrize("opposite_live", [False, True])
@pytest.mark.parametrize("continuation", [False, True])
async def test_order_attempts_preserve_observed_pullback(side, route, opposite_live, continuation, monkeypatch):
    f = aligned_frame(side, "breakout")
    sign = 1 if side == "LONG" else -1
    if continuation:
        rail = "kc_upper" if side == "LONG" else "kc_lower"
        f.loc[17, "open"] = float(f.loc[17, rail]) + sign * .1
        f["high"] = f[["open", "close"]].max(axis=1) + .1
        f["low"] = f[["open", "close"]].min(axis=1) - .1
    f.loc[19, "open"] -= sign * .2
    f.loc[19, "low" if side == "LONG" else "high"] = f.loc[19, "open"] - sign * .1
    if opposite_live:
        f.loc[19, "open"] = float(f.iloc[-1]["close"]) + sign * .1
        f["high"] = f[["open", "close"]].max(axis=1) + .2
        f["low"] = f[["open", "close"]].min(axis=1) - .2
    bar = 1200000
    f["timestamp"] = [(bar - (19-i)*60)*1000 for i in range(20)]
    f["atr"] = 1.
    e = _execution_engine(f, side, True)
    del e._channel_intrabar_ready
    e.account.positions.clear()
    e.account.save_state = lambda: None
    e._channel_profit_room = lambda *a: dict(allowed=True, checked=False)
    e._abnormal_market_entry_allowed = lambda *a, **k: True
    monkeypatch.setattr("core.engine.DEFAULT_SYMBOLS", [SYMBOL])
    clock = [bar+1.]
    monkeypatch.setattr("core.engine.time.time", lambda: clock[0])
    signal = dict(side=side, entry_mode="CHANNEL_SWING", action="ENTER_MARKET", reason="ordered quotes")
    if route == "reentry":
        signal["profit_reentry_token"] = "new"
        e.account.channel_profit_reentries = {SYMBOL: dict(
            side=side, token="new", phase="closed", mode="outer_cycle",
            requires_pullback=False, exit_bar_id=(bar-120)*1000)}
    sign = 1 if side == "LONG" else -1
    anchor = float(f.iloc[-1]["close"])
    for i, price in enumerate([anchor, anchor-sign*.12, anchor-sign*.06]):
        clock[0] = bar+1.+i
        e.tickers[SYMBOL] = price
        e._observe_channel_entry_quote(SYMBOL, price, clock[0]*1000)
        snapshot = dict(price=price, frame=f.copy(),
                        kc_upper=float(f.iloc[-1]["kc_upper"]),
                        kc_lower=float(f.iloc[-1]["kc_lower"]))
        placed = await e._place_structured_entry(
            SYMBOL, signal.copy(), price,
            channel_snapshot=snapshot if route == "cached" else None)
        assert bool(placed) == (i == 2), e.account.logs
        assert len(e.account.events) == int(i == 2)
