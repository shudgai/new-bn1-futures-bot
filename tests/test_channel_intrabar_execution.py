"""Entry succeeds on the first eligible quote without a pullback."""
import pytest
from core.engine import TradingEngine
from test_channel_aligned_entry import aligned_frame
from test_channel_sustained_trend import trend
from test_channel_swing_execution import _execution_engine, SYMBOL

@pytest.fixture
def anyio_backend():
    return "asyncio"

@pytest.mark.anyio
@pytest.mark.parametrize("side", ["LONG", "SHORT"])
@pytest.mark.parametrize("route", ["fresh", "cached", "reentry"])
@pytest.mark.parametrize("opposite_live", [False, True])
@pytest.mark.parametrize("continuation", [False, True])
async def test_first_eligible_quote_fills_without_pullback(side, route, opposite_live, continuation, monkeypatch):
    f = aligned_frame(side, "breakout")
    sign = 1 if side == "LONG" else -1
    if continuation:
        f, _ = trend(side)
    f.loc[19, "open"] -= sign * .2
    f.loc[19, "low" if side == "LONG" else "high"] = f.loc[19, "open"] - sign * .1
    if opposite_live:
        f.loc[19, "open"] = float(f.iloc[-1]["close"]) + sign * .1
        f["high"] = f[["open", "close"]].max(axis=1) + .2
        f["low"] = f[["open", "close"]].min(axis=1) - .2
    bar = 1200000
    f["timestamp"] = [(bar - (19-i)*60)*1000 for i in range(20)]
    f["atr"] = 1.
    anchor = float(f.iloc[-1]["close"])
    f.loc[5, "high" if side == "LONG" else "low"] = anchor + sign * 3.
    e = _execution_engine(f, side, True)
    del e._channel_intrabar_ready
    e.account.positions.clear()
    e.account.save_state = lambda: None
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
        assert bool(placed) == (i == 0), e.account.logs
        assert len(e.account.events) == 1

@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
@pytest.mark.parametrize('age', [0., 6., -1., float('nan')])
def test_immediate_entry_still_requires_fresh_quote(side, age, monkeypatch):
    f = aligned_frame(side, 'breakout')
    e = _execution_engine(f, side, True)
    del e._channel_intrabar_ready
    e.account.positions.clear()
    monkeypatch.setattr('core.engine.time.time', lambda: 1201.)
    e._channel_entry_quote_times = {SYMBOL: 1201. - age}
    assert bool(e._channel_intrabar_ready(SYMBOL, f, float(f.iloc[-1]['close']), side)) == (age == 0.)
