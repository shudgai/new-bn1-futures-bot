"""CK-aligned closed pivots and position-specific middle exit regressions."""
import asyncio
import json
from unittest.mock import AsyncMock

import pytest
from core.channel_pivot_entry import pivot_entry, pivot_middle_exit
from core.engine import TradingEngine
from test_channel_swing_execution import _execution_engine, _narrow_channel_frame, SYMBOL


@pytest.fixture
def anyio_backend():
    return "asyncio"


def market(side="LONG"):
    f = _narrow_channel_frame()
    f["kc_upper"], f["kc_lower"], f["atr"] = 110., 90., 4.
    f.loc[15, ["open", "high", "low", "close", "ma3", "ma15"]] = [98., 99., 96., 97., 98., 96.]
    f.loc[16, ["open", "high", "low", "close", "ma3", "ma15"]] = [97., 98., 94., 95., 97., 96.1]
    f.loc[17, ["open", "high", "low", "close", "ma3", "ma15"]] = [95., 99., 95., 98., 97.5, 96.2]
    f.loc[18, ["open", "high", "low", "close", "ma3", "ma15"]] = [97.8, 99., 97.5, 98., 97.8, 96.3]
    f.loc[16:18] = f.loc[15:17].to_numpy()
    f.loc[19, ["open", "high", "low", "close"]] = [98., 98.2, 97.9, 98.1]
    f["kc_middle"] = 100.
    f.loc[16:18, "kc_middle"] = [99.8, 99.9, 100.]
    if side == "SHORT":
        original = f.copy()
        for key in ("open", "close", "ma3", "ma15", "kc_middle"):
            f[key] = 200. - original[key]
        f["high"], f["low"] = 200. - original["low"], 200. - original["high"]
    return f


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
@pytest.mark.parametrize("case", ["valid", "flat_ma15", "mixed_ma15", "opposite_ma15", "equal_extreme", "opposite_body", "doji", "flat_ma3", "no_ma3_turn", "invalid", "bad_ohlc", "live_only", "broken_pivot"])
def test_closed_pivot_confirmation(side, case):
    f = market(side)
    price = float(f.iloc[-1]["close"])
    extreme = "low" if side == "LONG" else "high"
    if case == "flat_ma15": f.loc[16:18, "ma15"] = 100.
    elif case == "mixed_ma15": f.loc[16:18, "ma15"] = [100., 101., 100.5]
    elif case == "opposite_ma15": f.loc[16:18, "ma15"] = f.loc[16:18, "ma15"].to_numpy()[::-1]
    elif case == "equal_extreme": f.loc[16, extreme] = f.loc[17, extreme]
    elif case == "opposite_body": f.loc[18, ["open", "close"]] = f.loc[18, ["close", "open"]].to_numpy()
    elif case == "doji": f.loc[18, "open"] = f.loc[18, "close"]
    elif case == "flat_ma3": f.loc[18, "ma3"] = f.loc[17, "ma3"]
    elif case == "no_ma3_turn": f.loc[16, "ma3"] = f.loc[17, "ma3"]
    elif case == "invalid": f.loc[17, "low"] = float("nan")
    elif case == "bad_ohlc": f.loc[18, "high"] = f.loc[18, "low"] - 1
    elif case == "live_only": f = f.iloc[:-1].copy()
    elif case == "broken_pivot": price = float(f.loc[17, extreme])
    result = pivot_entry(f, price)
    allowed = case in {"valid", "flat_ma15", "mixed_ma15", "opposite_ma15"}
    assert (result["action"] == "ENTER") is allowed, result
    if allowed: assert result["side"] == side


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_live_ma15_and_live_pivot_cannot_change_closed_signal(side):
    f = market(side); price = float(f.iloc[-1]["close"])
    original = pivot_entry(f, price)
    f.loc[19, ["ma15", "ma3", "high", "low"]] = [1., 1000., 1000., .01]
    assert pivot_entry(f, price) == original
    assert original["action"] == "ENTER"


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_middle_cross_is_strict_persistent_and_failed_close_latches(side):
    sign = 1 if side == "LONG" else -1
    p = {"side": side, "channel_pivot_entry": True}
    assert not pivot_middle_exit(p, 100. - sign, 100.)
    assert not pivot_middle_exit(p, 100., 100.)
    assert not pivot_middle_exit(p, 100. + sign, 100.)
    assert p["channel_pivot_middle_reached"]
    p = json.loads(json.dumps(p))
    assert pivot_middle_exit(p, 100., 100.)
    p = json.loads(json.dumps(p))
    assert pivot_middle_exit(p, 100. + sign, 100.)


@pytest.mark.anyio
@pytest.mark.parametrize("side", ["LONG", "SHORT"])
async def test_snapshot_accepts_inside_channel_and_rejects_changed_signal(side):
    f = market(side); price = float(f.iloc[-1]["close"])
    e = _execution_engine(f, side, True); e.account.positions.clear(); e.tickers[SYMBOL] = price
    assert await e._fresh_channel_entry_snapshot(SYMBOL, side, 18) is not None
    assert await e._fresh_channel_entry_snapshot(SYMBOL, side, 17) is None
    f.loc[16:18, "ma15"] = 100.
    assert await e._fresh_channel_entry_snapshot(SYMBOL, side, 18) is not None
    f.loc[16:18, "kc_middle"] = 100.
    assert await e._fresh_channel_entry_snapshot(SYMBOL, side, 18) is None


@pytest.mark.anyio
@pytest.mark.parametrize("side", ["LONG", "SHORT"])
async def test_process_opens_once_and_persists_pivot_context(side, monkeypatch):
    f = market(side); price = float(f.iloc[-1]["close"])
    e = _execution_engine(f, side, True); e.account.positions.clear(); e.tickers[SYMBOL] = price
    e.account.save_state = lambda: None
    monkeypatch.setattr("core.engine.DEFAULT_SYMBOLS", [SYMBOL])
    e._abnormal_market_entry_allowed = lambda *a, **k: True
    original_open = e.account.open_position
    async def record(**kwargs):
        result = await original_open(**kwargs)
        e.account.positions[SYMBOL].update(kwargs["entry_context"], entry_price=kwargs["price"], qty=.01, open_timestamp=1.)
        return result
    e.account.open_position = record
    await asyncio.gather(*(e._process_single_symbol(SYMBOL, 1., None, False) for _ in range(3)))
    assert len(e.account.events) == 1, e.account.logs
    assert e.account.events[0][0] == "open"
    assert e.account.positions[SYMBOL]["channel_pivot_entry"]
    assert not e.account.positions[SYMBOL]["channel_pivot_middle_reached"]
    assert e.account.positions[SYMBOL]["channel_confirmation_bar_id"] == 18
    # Persisted trade confirmation must reject the same signal after a restart.
    e.account.positions.clear(); e._channel_used_confirmation.clear()
    e.account.trades = [{"symbol": SYMBOL, "action": f"OPEN_{side}", "channel_confirmation_bar_id": 18}]
    assert not await e._execute_confirmed_channel_break(SYMBOL, f, price, side)
    assert len(e.account.events) == 1


@pytest.mark.anyio
@pytest.mark.parametrize("side", ["LONG", "SHORT"])
@pytest.mark.parametrize("success", [False, True])
async def test_real_middle_exit_waits_for_cross_and_retries(side, success):
    f = market(side); sign = 1 if side == "LONG" else -1
    e = _execution_engine(f, side, success); e.account.save_state = lambda: None
    p = e.account.positions[SYMBOL]
    p.update(channel_pivot_entry=True, entry_price=100. - 2*sign, qty=.001, open_timestamp=1.)
    for price in (100. - sign, 100., 100. + .1*sign):
        e.tickers[SYMBOL] = price; f.loc[19, "open"] = price
        await e._process_single_symbol(SYMBOL, 1., None, False)
        assert not e.account.events, e.account.logs
    assert p["channel_pivot_middle_reached"]
    assert e.account.position_meta[SYMBOL]["channel_pivot_middle_reached"]
    e.account.positions[SYMBOL] = json.loads(json.dumps(p))
    e.tickers[SYMBOL] = 100.; f.loc[19, "open"] = 100.
    await e._process_single_symbol(SYMBOL, 2., None, False)
    assert len(e.account.events) == 1, e.account.logs
    assert "UNARMED_MIDDLE_EXIT" in e.account.events[0][3]
    if not success:
        e.tickers[SYMBOL] = 100. + .1*sign
        await e._process_single_symbol(SYMBOL, 3., None, False)
        assert len(e.account.events) == 2, e.account.logs
    else:
        assert SYMBOL not in e.account.positions


@pytest.mark.anyio
@pytest.mark.parametrize("side", ["LONG", "SHORT"])
async def test_daily_halt_and_invalid_candidate_still_block(side, monkeypatch):
    f = market(side); price = float(f.iloc[-1]["close"])
    e = _execution_engine(f, side, True); e.account.positions.clear(); e.tickers[SYMBOL] = price
    e._place_structured_entry = AsyncMock(return_value=True)
    await e._execute_confirmed_channel_break(SYMBOL, f, price, side, daily_halt=True)
    e._place_structured_entry.assert_not_awaited()
    del e._place_structured_entry
    e._channel_invalid_entry_candidates = {(SYMBOL, side, 18)}
    monkeypatch.setattr("core.engine.DEFAULT_SYMBOLS", [SYMBOL])
    assert not await e._execute_confirmed_channel_break(SYMBOL, f, price, side)
    assert not e.account.events


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_confirmed_outer_break_can_enter_without_pivot(side):
    from test_channel_symmetric_rules import market as outer_market
    f = outer_market(side); price = float(f.iloc[-1]["close"])
    assert pivot_entry(f, price)["action"] == "WAIT"
    assert TradingEngine._channel_swing_action(f, price)["side"] == side
    # Latest authorization permits outside entry without a body crossing.
    f.loc[17, "open"] = 102.5 if side == "LONG" else 97.5
    assert TradingEngine._channel_swing_action(f, price)["side"] == side


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_primary_entry_preserves_pivot_signal_code(side):
    f = market(side)
    assert TradingEngine._channel_swing_action(f, float(f.iloc[-1]["close"])) == pivot_entry(f, float(f.iloc[-1]["close"]))


@pytest.mark.anyio
@pytest.mark.parametrize("side", ["LONG", "SHORT"])
async def test_pending_middle_close_retries_even_if_profit_later_arms(side):
    f = market(side); price = float(f.iloc[-1]["close"])
    e = _execution_engine(f, side, False); e.account.save_state = lambda: None
    p = e.account.positions[SYMBOL]
    p.update(channel_pivot_entry=True, channel_pivot_middle_exit_pending=True,
             entry_price=price - (5 if side == "LONG" else -5), qty=2., open_timestamp=1.)
    e.tickers[SYMBOL] = price
    await e._process_single_symbol(SYMBOL, 1., None, False)
    assert p["channel_profit_protection"]["armed"]
    assert len(e.account.events) == 1, e.account.logs
    assert "UNARMED_MIDDLE_EXIT" in e.account.events[0][3]


@pytest.mark.anyio
@pytest.mark.parametrize("side", ["LONG", "SHORT"])
async def test_real_paper_fill_and_reload_preserve_pivot_state(side, tmp_path, monkeypatch):
    import core.paper_account as pm
    from core.paper_account import PaperAccount
    monkeypatch.setattr(pm, "STATE_FILE", str(tmp_path / "pivot_account.json"))
    monkeypatch.setattr("core.engine.DEFAULT_SYMBOLS", [SYMBOL])
    f = market(side); f["ema_20"] = float("nan")
    e = _execution_engine(f, side, True); e.account = PaperAccount()
    e.account.positions.clear(); e.account.balance = 1000.; e.account.daily_start_balance = 1000.
    e.tickers[SYMBOL] = float(f.iloc[-1]["close"])
    await e._process_single_symbol(SYMBOL, 1., None, False)
    assert SYMBOL in e.account.positions, e.account.logs
    p = e.account.positions[SYMBOL]
    assert p["channel_pivot_entry"] and p["entry_kc_middle"] == 100.
    assert not pivot_middle_exit(p, 100. + (.1 if side == "LONG" else -.1), 100.)
    e.account.save_state()
    restored = PaperAccount()
    assert restored.positions[SYMBOL]["channel_pivot_entry"]
    assert restored.positions[SYMBOL]["channel_pivot_middle_reached"]
    assert restored.positions[SYMBOL]["channel_confirmation_bar_id"] == 18


@pytest.mark.anyio
@pytest.mark.parametrize("side", ["LONG", "SHORT"])
@pytest.mark.parametrize("blocked_by", ["abnormal", "room", "balance"])
async def test_pivot_keeps_order_safety_checks(side, blocked_by, monkeypatch):
    f = market(side); price = float(f.iloc[-1]["close"])
    e = _execution_engine(f, side, True); e.account.positions.clear(); e.tickers[SYMBOL] = price
    monkeypatch.setattr("core.engine.DEFAULT_SYMBOLS", [SYMBOL])
    if blocked_by == "abnormal": e._abnormal_market_entry_allowed = lambda *a, **k: False
    elif blocked_by == "room": f["atr"] = .001
    else: e.account.get_available_balance = lambda: 0.
    assert not await e._execute_confirmed_channel_break(SYMBOL, f, price, side)
    assert not e.account.events, e.account.logs


@pytest.mark.anyio
@pytest.mark.parametrize("side", ["LONG", "SHORT"])
async def test_testnet_fill_and_refresh_preserve_pivot_metadata(side, tmp_path, monkeypatch):
    import core.testnet_account as tm
    from test_testnet_account import FakeTestnetExchange
    monkeypatch.setattr(tm, "STATE_FILE", str(tmp_path / "testnet_pivot.json"))
    monkeypatch.setattr(tm, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(tm.BinanceTestnetAccount, "credentials_configured", staticmethod(lambda: True))
    exchange = FakeTestnetExchange()
    account = tm.BinanceTestnetAccount(exchange)
    await account.initialize()
    context = {"entry_mode": "CHANNEL_SWING", "channel_pivot_entry": True,
               "entry_kc_middle": 100., "channel_pivot_middle_reached": False,
               "channel_confirmation_bar_id": 18}
    assert await account.open_position("DOGE/USDT", side, 100., 25., 0., 0.,
                                       "Channel Swing MA15 pivot", atr=1., leverage=1,
                                       signal_score=100, entry_context=context)
    assert account.positions["DOGE/USDT"]["channel_pivot_entry"]
    assert account.trades[0]["channel_confirmation_bar_id"] == 18
    account.position_meta["DOGE/USDT"].update(channel_pivot_middle_reached=True,
                                           channel_pivot_middle_exit_pending=True)
    account.save_state()
    restored = tm.BinanceTestnetAccount(exchange)
    await restored.initialize()
    p = restored.positions["DOGE/USDT"]
    assert p["channel_pivot_entry"] and p["channel_pivot_middle_reached"]
    assert p["channel_pivot_middle_exit_pending"]
    assert p["channel_confirmation_bar_id"] == 18


@pytest.mark.anyio
@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
async def test_profit_reentry_waits_for_pullback_and_reclaim_not_inside_pivot(side, monkeypatch):
    from test_channel_outer_cycle import setup as outer_market
    f = market(side); price = float(f.iloc[-1]['close'])
    e = _execution_engine(f, side, True)
    e.account.positions.clear(); e.account.save_state = lambda: None
    e.tickers[SYMBOL] = price
    monkeypatch.setattr('core.engine.DEFAULT_SYMBOLS', [SYMBOL])
    e._abnormal_market_entry_allowed = lambda *a, **k: True
    e.account.channel_profit_reentries = {SYMBOL: dict(side=side, token='old', phase='closed', exit_bar_id=19)}
    await e._try_profit_reentry(SYMBOL, f, price, False)
    assert not e.account.events
    f.index += 1
    await e._try_profit_reentry(SYMBOL, f, price, False)
    assert not e.account.events  # A newer inside pivot does not reclaim the rail.
    fresh, recovered = outer_market(side)
    fresh.index += 2
    fresh['atr'] = 6.
    e.fetch_klines = AsyncMock(return_value=fresh)
    e.tickers[SYMBOL] = recovered
    await e._try_profit_reentry(SYMBOL, fresh, 100., False)
    await e._try_profit_reentry(SYMBOL, fresh, recovered, True)
    assert not e.account.events  # Daily halt still applies after a pullback.
    await e._try_profit_reentry(SYMBOL, fresh, recovered, False)
    assert len(e.account.events) == 1, e.account.logs
    assert e.account.events[0][2] == side
    assert SYMBOL not in e.account.channel_profit_reentries


@pytest.mark.anyio
@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
async def test_profit_reentry_migrates_ticket_and_preserves_failed_order(side):
    f = market(side); price = float(f.iloc[-1]['close'])
    e = _execution_engine(f, side, True)
    e.account.positions.clear(); e.account.save_state = lambda: None
    ticket = dict(side=side, token='old', phase='closed', pulled_back_inside=True)
    e.account.channel_profit_reentries = {SYMBOL: ticket}
    e._place_structured_entry = AsyncMock(return_value=False)
    await e._try_profit_reentry(SYMBOL, f, price, False)
    assert ticket['exit_bar_id'] == 19
    e._place_structured_entry.assert_not_awaited()
    from test_channel_outer_cycle import setup as outer_market
    f, price = outer_market(side)
    f.index += 1
    await e._try_profit_reentry(SYMBOL, f, 100., False)
    await e._try_profit_reentry(SYMBOL, f, price, False)
    e._place_structured_entry.assert_awaited_once()
    assert SYMBOL in e.account.channel_profit_reentries
    f.loc[f.index[-2], 'kc_middle'] = f.iloc[-3]['kc_middle']
    await e._try_profit_reentry(SYMBOL, f, price, False)
    e._place_structured_entry.assert_awaited_once()


@pytest.mark.anyio
@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
@pytest.mark.parametrize('bad', ['first_opposite', 'first_doji', 'first_live'])
async def test_pivot_requires_first_closed_turn_at_order_time(side, bad):
    f = market(side); price = float(f.iloc[-1]['close'])
    # Keep the live candle neutral to isolate the closed-pivot route.
    f.loc[19, "open"] = price
    e = _execution_engine(f, side, True); e.account.positions.clear(); e.tickers[SYMBOL] = price
    assert await e._fresh_channel_entry_snapshot(SYMBOL, side, 18) is not None
    row = 18
    if bad.endswith('opposite'):
        f.loc[row, ['open', 'close']] = f.loc[row, ['close', 'open']].to_numpy()
    elif bad.endswith('doji'):
        f.loc[row, 'open'] = f.loc[row, 'close']
    else:
        # The first turn candle is still live, so cannot confirm.
        e.fetch_klines = AsyncMock(return_value=f.iloc[:-1].copy())
    assert await e._fresh_channel_entry_snapshot(SYMBOL, side, 18) is None


def test_reported_pepe_and_lobster_entry_replay():
    from pathlib import Path
    import pandas as pd
    data = json.loads((Path(__file__).parent / 'fixtures' / 'pepe_lobster_entry_20260909.json').read_text())
    for name, price, expected in [('pepe', .0034988, 'ENTER'), ('lobster', .052423, 'ENTER')]:
        f = pd.DataFrame(data[name]); f = f[f.time <= 1788990300].copy()
        f['timestamp'] = f.time * 1000
        # Historical closed candles plus entry-time price; reconstruct the live
        # body without feeding its later closing price into the decision.
        live = f.index[-1]; opened = float(f.loc[live, 'open'])
        f.loc[live, ['close', 'high', 'low']] = [price, max(price, opened), min(price, opened)]
        decision = TradingEngine._channel_swing_action(f, price)
        assert decision['action'] == expected, (name, decision)
        if name == 'pepe':
            assert decision['side'] == 'SHORT'
            assert decision['reason'] == 'KC_OUTSIDE_SHORT'
        else:
            assert decision["side"] == "LONG"
            assert decision["reason"] == "KC_MA15_TROUGH_LONG"
            # A later bar cannot reuse the previous first-turn confirmation.
            later = pd.DataFrame(data[name]); later['timestamp'] = later.time * 1000
            assert TradingEngine._channel_swing_action(later, float(later.iloc[-1]['open']))['action'] == 'WAIT'
