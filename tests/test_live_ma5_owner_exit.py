import asyncio
import copy
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pandas as pd
import pytest

from core.services.exits import trend_pivot_exit as policy
from core.services.entry_contract import ma5_entry_ready


def market(side):
    bar = int(time.time() // 60) * 60000
    sign = 1 if side == "LONG" else -1
    rows = []
    for i in range(6):
        close = 100. + sign * i * .1
        rows.append(dict(timestamp=bar-(6-i)*60000, open=close, high=close+.1,
                         low=close-.1, close=close, ma5=close, atr=1.,
                         is_closed=True))
    previous = sum(r["close"] for r in rows[-5:]) / 5.
    rows[-1]["ma5"] = previous
    rows.append(dict(timestamp=bar, open=100., high=101., low=99., close=100.,
                     ma5=100., atr=1., is_closed=False))
    frame = pd.DataFrame(rows)
    frame.attrs["timeframe_ms"] = 60000
    position = dict(side=side, entry_mode="CHANNEL_SWING", entry_price=100.,
                    qty=1., open_timestamp=(bar+100)/1000)
    flat_quote = rows[-6]["close"]
    return position, frame, float(bar+1000), flat_quote


@pytest.mark.parametrize("symbol", ["龙虾/USDT", "CAP/USDT"])
@pytest.mark.parametrize("side", ["LONG", "SHORT"])
@pytest.mark.parametrize("movement,exit_expected", [(0., True), (-.1, True), (.1, False)])
def test_live_quote_flat_or_adverse_without_waiting_for_close(symbol, side, movement, exit_expected):
    p, f, stamp, flat_quote = market(side)
    sign = 1 if side == "LONG" else -1
    f.attrs["symbol"] = symbol
    evidence, _ = policy.evaluate(p, f, flat_quote+sign*movement, stamp)
    assert bool(evidence) is exit_expected
    if evidence:
        assert evidence["reason"] == policy.LIVE_REASON
        assert evidence["quote_ms"] == stamp
        assert evidence["flat"] is (movement == 0.)
    assert policy.STATE_KEY not in p


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
@pytest.mark.parametrize("fault", ["future", "nan", "gap", "short", "preentry", "finality"])
def test_live_invalid_data_no_authority(side, fault):
    p, f, stamp, quote = market(side)
    if fault == "future":
        f.loc[6, "timestamp"] += 60000
    elif fault == "nan":
        f.loc[4, "close"] = float("nan")
    elif fault == "gap":
        f.loc[4, "timestamp"] += 1
    elif fault == "short":
        f = f.tail(4)
    elif fault == "preentry":
        p["open_timestamp"] = (stamp+1)/1000
    else:
        f["is_closed"] = f["is_closed"].astype(object)
        f.loc[4, "is_closed"] = "true"
    assert policy.evaluate(p, f, quote, stamp)[0] is None


@pytest.mark.parametrize("symbol", ["龙虾/USDT", "CAP/USDT"])
@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_live_pending_retry_restart_and_position_isolation(symbol, side, monkeypatch):
    p, f, stamp, quote = market(side)
    monkeypatch.setattr(time, "time", lambda: stamp/1000)
    account = SimpleNamespace(positions={symbol:p}, position_meta={},
                              save_state=Mock(), log=Mock(),
                              close_position=AsyncMock(return_value=False))
    assert not asyncio.run(policy.enforce(account, symbol, quote, f, stamp))
    assert p[policy.STATE_KEY]["pending"] == policy.LIVE_REASON
    assert policy.close_allowed(p, {}, "Channel Swing "+policy.LIVE_REASON, True)
    assert not policy.close_allowed(p, {}, "Channel Swing EXIT_POST_ENTRY_DOJI_ADVERSE_06_ATR", True)
    account.positions[symbol] = copy.deepcopy(p)
    assert not asyncio.run(policy.enforce(account, symbol, quote, None, stamp))
    assert account.close_position.await_count == 2
    account.positions[symbol]["qty"] += 1.
    assert not asyncio.run(policy.enforce(account, symbol, quote, None, stamp))
    assert account.close_position.await_count == 2


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_live_flat_cannot_open(side):
    _, f, _, quote = market(side)
    sign = 1 if side == "LONG" else -1
    f.loc[4, "ma5"] = float(f.loc[5, "ma5"]) - sign*.1
    assert not ma5_entry_ready(f, quote, side)


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_real_paper_live_close_once(side, tmp_path, monkeypatch):
    from core.paper_account import PaperAccount
    monkeypatch.setattr("core.paper_account.STATE_FILE", str(tmp_path/"live.json"))
    p, f, stamp, quote = market(side)
    monkeypatch.setattr(time, "time", lambda: stamp/1000)
    account = PaperAccount()
    account.positions = {"CAP/USDT":p}
    account.position_meta = {}
    assert asyncio.run(policy.enforce(account, "CAP/USDT", quote, f, stamp))
    assert account.trades[0]["reason"] == "Channel Swing "+policy.LIVE_REASON
    assert not asyncio.run(policy.enforce(account, "CAP/USDT", quote, f, stamp))
    assert len(account.trades) == 1
