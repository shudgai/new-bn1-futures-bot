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
                         low=close-.1, close=close, ma5=close, ma15=100., atr=1.,
                         kc_middle=100.-sign*i*.1, is_closed=True))
    previous = sum(r["close"] for r in rows[-5:]) / 5.
    rows[-1]["ma5"] = previous
    rows.append(dict(timestamp=bar, open=100., high=101., low=99., close=100.,
                     ma5=100., atr=1., is_closed=False))
    frame = pd.DataFrame(rows)
    frame.attrs["timeframe_ms"] = 60000
    position = dict(side=side, entry_mode="CHANNEL_SWING", entry_price=100.,
                    qty=1., open_timestamp=(bar+100)/1000, entry_atr=1.)
    flat_quote = rows[-6]["close"]
    return position, frame, float(bar+1000), flat_quote


@pytest.mark.parametrize("symbol", ["龙虾/USDT", "CAP/USDT"])
@pytest.mark.parametrize("side", ["LONG", "SHORT"])
@pytest.mark.parametrize("movement,exit_expected", [(0., False), (-.1, True), (.1, False)])
def test_live_observed_peak_needs_adverse_direction_not_flat(symbol, side, movement, exit_expected):
    p, f, stamp, flat_quote = market(side)
    sign = 1 if side == "LONG" else -1
    f.attrs["symbol"] = symbol
    previous = float(f.iloc[-2].ma5)
    p[policy.STATE_KEY] = dict(policy=policy.POLICY, identity=policy.position_identity(p),
                              reference_atr=1.,
                              ma5_peak=dict(baseline=previous-sign*.1,
                                            extreme=previous+sign*.1, favorable=True))
    before = copy.deepcopy(p)
    evidence, _ = policy.evaluate(p, f, flat_quote+sign*movement, stamp)
    assert bool(evidence) is exit_expected
    if evidence:
        assert evidence["reason"] == policy.LIVE_REASON
        assert evidence["quote_ms"] == stamp
        assert evidence["ma5_retreat"] >= .1
    assert p == before


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
    sign = 1 if side == "LONG" else -1
    previous = float(f.iloc[-2].ma5)
    p[policy.STATE_KEY] = dict(policy=policy.POLICY, identity=policy.position_identity(p),
                              reference_atr=1.,
                              ma5_peak=dict(baseline=previous-sign*.1,
                                            extreme=previous+sign*.1, favorable=True))
    quote -= sign*.5
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
    sign = 1 if side == "LONG" else -1
    assert not asyncio.run(policy.enforce(account, "CAP/USDT", quote-sign, f, stamp))
    stamp += 1
    assert not asyncio.run(policy.enforce(account, "CAP/USDT", quote+sign, f, stamp))
    stamp += 1
    assert asyncio.run(policy.enforce(account, "CAP/USDT", quote-sign*.5, f, stamp))
    assert account.trades[0]["reason"] == "Channel Swing "+policy.LIVE_REASON
    assert not asyncio.run(policy.enforce(account, "CAP/USDT", quote, f, stamp))
    assert len(account.trades) == 1


@pytest.mark.parametrize("symbol", ["龙虾/USDT", "CAP/USDT"])
@pytest.mark.parametrize("side", ["LONG", "SHORT"])
@pytest.mark.parametrize("retreat", [.099, .10, .101])
def test_observed_live_peak_threshold_and_fixed_atr(symbol, side, retreat, monkeypatch):
    p, f, stamp, flat_quote = market(side)
    sign = 1 if side == "LONG" else -1
    monkeypatch.setattr(time, "time", lambda: stamp/1000)
    a = SimpleNamespace(positions={symbol:p}, position_meta={}, save_state=Mock(),
                        log=Mock(), close_position=AsyncMock(return_value=False))
    assert not asyncio.run(policy.enforce(a, symbol, flat_quote-sign, f, stamp))
    stamp += 1
    assert not asyncio.run(policy.enforce(a, symbol, flat_quote+sign*.25, f, stamp))
    f["atr"] = 100.
    p["entry_atr"] = 100.
    peak = p[policy.STATE_KEY]["ma5_peak"]["extreme"]
    last_closes = float(f.iloc[:-1].close.tail(4).sum())
    quote = 5*(peak-sign*retreat)-last_closes
    stamp += 1
    assert not asyncio.run(policy.enforce(a, symbol, quote, f, stamp))
    if retreat < .1:
        a.close_position.assert_not_awaited()
        assert "pending" not in p[policy.STATE_KEY]
    else:
        assert a.close_position.await_count == 1
        assert p[policy.STATE_KEY]["evidence"]["fixed_entry_atr"] == 1.


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_first_adverse_quote_has_no_observed_favorable_peak(side):
    p, f, stamp, quote = market(side)
    sign = 1 if side == "LONG" else -1
    assert policy.evaluate(p, f, quote-sign*5, stamp)[0] is None


@pytest.mark.parametrize("symbol", ["龙虾/USDT", "CAP/USDT"])
@pytest.mark.parametrize("side", ["LONG", "SHORT"])
@pytest.mark.parametrize("kc_movement", [.1, 0., -.1])
def test_kc_trend_holds_ma5_pullback_until_closed_reversal(symbol, side, kc_movement):
    p, f, stamp, quote = market(side)
    sign = 1 if side == "LONG" else -1
    previous = float(f.iloc[-2].ma5)
    p[policy.STATE_KEY] = dict(policy=policy.POLICY, identity=policy.position_identity(p),
                              reference_atr=1.,
                              ma5_peak=dict(baseline=previous-sign*.1,
                                            extreme=previous+sign*.1, favorable=True))
    f.loc[5, "kc_middle"] = float(f.loc[4, "kc_middle"])+sign*kc_movement
    evidence, _ = policy.evaluate(p, f, quote-sign, stamp)
    assert evidence
    f.loc[6, "kc_middle"] = 1000. if side == "SHORT" else 1.
    assert policy.evaluate(p, f, quote-sign, stamp)[0]


@pytest.mark.parametrize("symbol", ["龙虾/USDT", "CAP/USDT"])
@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_real_restart_preserves_peak_fixed_atr_and_consumes_close_once(symbol, side, tmp_path, monkeypatch):
    from core.paper_account import PaperAccount
    monkeypatch.setattr("core.paper_account.STATE_FILE", str(tmp_path/"peak.json"))
    p, f, stamp, quote = market(side)
    sign = 1 if side == "LONG" else -1
    monkeypatch.setattr(time, "time", lambda: stamp/1000)
    a = PaperAccount()
    a.positions = {symbol:p}
    assert not asyncio.run(policy.enforce(a, symbol, quote-sign, f, stamp))
    stamp += 1
    assert not asyncio.run(policy.enforce(a, symbol, quote+sign*.25, f, stamp))
    peak = copy.deepcopy(p[policy.STATE_KEY]["ma5_peak"])
    restored = PaperAccount()
    assert restored.positions[symbol][policy.STATE_KEY]["ma5_peak"] == peak
    assert restored.positions[symbol][policy.STATE_KEY]["reference_atr"] == 1.
    stamp += 1
    assert asyncio.run(policy.enforce(restored, symbol, quote-sign*.5, f, stamp))
    after = PaperAccount()
    assert symbol not in after.positions
    assert len(after.trades) == 1
    assert after.trades[0]["reason"] == "Channel Swing "+policy.LIVE_REASON
    assert not asyncio.run(policy.enforce(after, symbol, quote, f, stamp))


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
@pytest.mark.parametrize("atr", [0., float("nan"), None])
def test_missing_entry_atr_cannot_invent_peak_exit(side, atr):
    p, f, stamp, quote = market(side)
    p["entry_atr"] = atr
    assert policy.evaluate(p, f, quote, stamp)[0] is None


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_v8_observation_preserved_but_ungated_pending_revoked(side):
    p, _, _, _ = market(side)
    peak = dict(baseline=100., extreme=101. if side == "LONG" else 99., favorable=True)
    p[policy.STATE_KEY] = dict(policy="observed_ma5_peak_turn_010_atr_v8",
                              identity=policy.position_identity(p), reference_atr=1.,
                              ma5_peak=peak, pending="EXIT_LIVE_MA5_PEAK_TURN_010_ATR",
                              evidence={"reason":"EXIT_LIVE_MA5_PEAK_TURN_010_ATR"})
    state = policy.migrate(p, {})
    assert state["ma5_peak"] == peak
    assert state["reference_atr"] == 1.
    assert "pending" not in state


def test_disk_failure_blocks_close_and_reports_error():
    from test_trend_pivot_owner_policy import market as closed_market
    p, f, stamp = closed_market()
    a = SimpleNamespace(positions={"CAP/USDT":p}, position_meta={},
                        save_state=Mock(side_effect=OSError("disk unavailable")),
                        log=Mock(), close_position=AsyncMock())
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(time, "time", lambda: stamp/1000)
        assert not asyncio.run(policy.enforce(a, "CAP/USDT", 100., f, stamp))
    a.close_position.assert_not_awaited()
    a.log.assert_called()
