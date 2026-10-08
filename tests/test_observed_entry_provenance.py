import copy
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from core.services.continuation_qualification import observe, qualification
from core.services.entry_contract import evaluate_entry_contract, evaluate_small_bridge_breakout
from core.services.small_bridge_state import pattern
from test_small_multi_bridge_entry import candles as small_candles
from test_v2_execution_boundary import candles as general_candles


def account():
    return SimpleNamespace(positions={}, channel_continuation_qualifications={},
                           channel_small_bridge_states={}, save_state=Mock(), log=Mock())


@pytest.mark.parametrize("symbol", ["龙虾/USDT", "CAP/USDT"])
@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_general_origin_cancel_and_no_same_pair_resurrection(symbol, side):
    a = account()
    f = general_candles(side)
    q = float(f.iloc[-1].close)
    observe(a, symbol, f, q)
    origin = qualification(a, symbol, side, f, q)
    assert origin and origin["active"]
    restored = copy.deepcopy(a)
    assert qualification(restored, symbol, side, f, q)
    observe(a, symbol, f, float(f.iloc[-1].kc_middle))
    assert qualification(a, symbol, side, f, q) is None
    observe(a, symbol, f, q)
    assert qualification(a, symbol, side, f, q) is None


@pytest.mark.parametrize("symbol", ["龙虾/USDT", "CAP/USDT"])
@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_no_origin_no_continuation_or_turn_alias(symbol, side):
    a = account()
    f = general_candles(side)
    f.loc[f.index[-3], "open"] = float(f.iloc[-3].close)
    observe(a, symbol, f, float(f.iloc[-1].close))
    assert not a.channel_continuation_qualifications
    assert evaluate_entry_contract(f, account=a, symbol=symbol,
                                   code="KC_OUTSIDE_"+side) is None
    assert evaluate_entry_contract(f, account=a, symbol=symbol,
                                   code="KC_CHANNEL_TURN_"+side) is None


@pytest.mark.parametrize("symbol", ["龙虾/USDT", "CAP/USDT"])
@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_small_bridges_persist_beyond_window_and_freeze_atr(symbol, side):
    from core.services.small_bridge_state import observe as observe_retired
    a = account()
    f = small_candles(side, 2)
    f["kc_middle"] = 100.
    q = float(f.iloc[-1].close)
    observe_retired(a, symbol, f)
    assert pattern(a, symbol, f, q)["small_bridge_count"] == 2
    f.loc[f.index[-2], "atr"] = 10.
    observe_retired(a, symbol, f)
    assert pattern(a, symbol, f, q)["live_body_reference_atr"] == 1.
    sign = 1 if side == "LONG" else -1
    for _ in range(205):
        f = f.tail(3).copy()
        f.loc[f.index[-1], ["close", "high", "low", "is_closed"]] = [
            100.-sign*.2, 100.25, 99.75, True]
        next_row = f.iloc[-1].copy()
        next_row["timestamp"] += 60000
        next_row["is_closed"] = False
        next_row["atr"] = 1.
        import pandas as pd
        f = pd.concat([f, next_row.to_frame().T], ignore_index=True)
        f["is_closed"] = f["is_closed"].astype(bool)
        f["atr"] = 1.
        observe_retired(a, symbol, f)
    assert pattern(a, symbol, f, 100.+sign)["small_bridge_count"] == 207
    assert pattern(copy.deepcopy(a), symbol, f, 100.+sign)
    assert pattern(a, "unrelated", f, q) is None


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_live_giant_without_small_bridge_is_blocked(side):
    from test_lobster_cap_gates import frame
    f = frame(side)
    assert evaluate_small_bridge_breakout(f, float(f.iloc[-1].close)) is None
    assert evaluate_entry_contract(f, code="KC_LIVE_BODY_BREAKOUT_"+side) is None


def test_persistence_failure_never_authorizes_entry():
    a = account()
    a.save_state.side_effect = OSError("disk unavailable")
    f = general_candles()
    with pytest.raises(OSError):
        observe(a, "CAP/USDT", f, float(f.iloc[-1].close))
    assert not a.channel_continuation_qualifications["CAP/USDT"]["active"]
    a.log.assert_called()


def test_entry_provenance_failure_does_not_skip_held_exit(monkeypatch):
    import asyncio
    import time
    from unittest.mock import AsyncMock
    from core.engine import TradingEngine
    from core.services.continuation_qualification import observe_runtime
    a = account()
    f = general_candles()
    a.save_state.side_effect = OSError("disk unavailable")
    assert not observe_runtime(a, "CAP/USDT", f, float(f.iloc[-1].close))
    a.log.assert_called()
    engine = object.__new__(TradingEngine)
    engine.account = a
    engine._channel_exit_frames = {"CAP/USDT":f}
    engine._reevaluate_after_close = AsyncMock()
    exit_check = AsyncMock(return_value=True)
    monkeypatch.setattr("core.services.exits.realtime_profit_exit.enforce_realtime_profit_exit",
                        exit_check)
    with monkeypatch.context() as patch:
        patch.setattr("core.services.continuation_qualification.observe_runtime", lambda *args:False)
        assert asyncio.run(engine._instant_quote_exit("CAP/USDT", float(f.iloc[-1].close),
                                                     time.time()*1000))
    exit_check.assert_awaited_once()
    engine._reevaluate_after_close.assert_not_awaited()


@pytest.mark.parametrize("symbol", ["龙虾/USDT", "CAP/USDT"])
@pytest.mark.parametrize("side", ["LONG", "SHORT"])
@pytest.mark.parametrize("offset", [-60000, 60000])
def test_runtime_stale_or_future_frame_cannot_poison_provenance(symbol, side, offset):
    from core.services.continuation_qualification import observe_runtime
    a = account()
    f = general_candles(side)
    f["timestamp"] += offset
    assert not observe_runtime(a, symbol, f, float(f.iloc[-1].close))
    assert not a.channel_small_bridge_states
    assert not a.channel_continuation_qualifications
    a.save_state.assert_not_called()
    a.log.assert_called()


def test_paper_restart_preserves_both_provenance_stores(tmp_path, monkeypatch):
    from core.paper_account import PaperAccount
    monkeypatch.setattr("core.paper_account.STATE_FILE", str(tmp_path/"entry.json"))
    a = PaperAccount()
    f = general_candles()
    observe(a, "CAP/USDT", f, float(f.iloc[-1].close))
    s = small_candles("SHORT", 3)
    s["kc_middle"] = 100.
    observe(a, "龙虾/USDT", s, float(s.iloc[-1].close))
    restored = PaperAccount()
    assert restored.channel_continuation_qualifications == a.channel_continuation_qualifications
    assert restored.channel_small_bridge_states == a.channel_small_bridge_states


@pytest.mark.parametrize("symbol", ["龙虾/USDT", "CAP/USDT"])
@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_live_pattern_shared_contract_and_last_submit_revalidation(symbol, side):
    import asyncio
    import time
    from unittest.mock import AsyncMock
    from core.services.entry_firewall import validate_account_entry
    a = account()
    f = small_candles(side, 4)
    stamp = int(time.time()//60)*60000
    f["timestamp"] += stamp-f.iloc[-1].timestamp
    sign = 1 if side == "LONG" else -1
    f["kc_middle"] = 100.
    f["ma5"] = [100.+sign*i*.005 for i in range(len(f))]
    f["ma15"] = 100.
    f["ma3"] = 100.
    f.loc[f.index[-1], "close"] = 100.+sign*1.5
    f.loc[f.index[-1], "high"] = 102.
    f.loc[f.index[-1], "low"] = 98.
    f.attrs["entry_finality_verified"] = True
    a.trades = []
    a.last_closed_at = {}
    a.entry_frame_provider = AsyncMock(return_value=f)
    observe(a, symbol, f, float(f.iloc[-1].close))
    decision = evaluate_entry_contract(f, account=a, symbol=symbol)
    assert decision is None
    context = dict(entry_signal_code="KC_LIVE_BODY_BREAKOUT_"+side,
                   channel_confirmation_bar_id=float(f.iloc[-1].timestamp))
    with pytest.raises(ValueError):
        asyncio.run(validate_account_entry(a, symbol, side, context))


@pytest.mark.parametrize("symbol", ["龙虾/USDT", "CAP/USDT"])
@pytest.mark.parametrize("side", ["LONG", "SHORT"])
@pytest.mark.parametrize("offset", [0., -.01, .01])
def test_live_quote_must_be_strictly_beyond_live_ma5(symbol, side, offset):
    a = account()
    f = small_candles(side, 4)
    sign = 1 if side == "LONG" else -1
    quote = 100. + sign * 1.5
    f["kc_middle"] = 100.
    f["ma5"] = [100. + sign * i * .005 for i in range(len(f))]
    live_ma5 = quote - sign * offset
    f.loc[f.index[-5:-1], "close"] = (5 * live_ma5 - quote) / 4.
    f["high"] = f[["open", "close"]].max(axis=1) + .05
    f["low"] = f[["open", "close"]].min(axis=1) - .05
    f.loc[f.index[-1], ["close", "high", "low"]] = [quote, 102., 98.]
    import core.services.small_bridge_state as observed
    from unittest.mock import patch
    evidence = evaluate_small_bridge_breakout(small_candles(side, 4), 100. + sign)
    diagnostics = {}
    with patch.object(observed, "pattern", return_value=evidence):
        decision = evaluate_entry_contract(f, quote, code="KC_LIVE_BODY_BREAKOUT_"+side,
                                          account=a, symbol=symbol,
                                          diagnostics=diagnostics)
    assert decision is None
    assert diagnostics["reason"] == "BLOCKED_OBSOLETE_ENTRY_SIGNAL"


@pytest.mark.parametrize("symbol", ["龙虾/USDT", "CAP/USDT"])
@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_continuation_requires_retained_general_pair_and_outside_ma5(symbol, side):
    from core.services.entry_contract import evaluate_continuation_entry
    a = account()
    f = general_candles(side)
    observe(a, symbol, f, float(f.iloc[-1].close))
    origin = copy.deepcopy(a.channel_continuation_qualifications[symbol])
    f["timestamp"] += 60000
    sign = 1 if side == "LONG" else -1
    f.loc[f.index[-3], "open"] = float(f.iloc[-3].close)
    observe(a, symbol, f, float(f.iloc[-1].close))
    decision = evaluate_continuation_entry(f, float(f.iloc[-1].close), symbol=symbol, account=a)
    assert decision and decision["continuation_pair_id"] == origin["pair_id"]
    a.positions[symbol] = {"side":side}
    observe(a, symbol, f, float(f.iloc[-1].kc_middle))
    assert not a.channel_continuation_qualifications[symbol]["active"]
    assert not evaluate_continuation_entry(f, float(f.iloc[-1].close), symbol=symbol, account=a)


@pytest.mark.parametrize("symbol", ["龙虾/USDT", "CAP/USDT"])
@pytest.mark.parametrize("side", ["LONG", "SHORT"])
@pytest.mark.parametrize("entry", ["general", "cross"])
def test_shared_runner_real_paper_fills_each_symbol_and_direction(symbol, side, entry, monkeypatch):
    import asyncio
    import time
    import pandas as pd
    from unittest.mock import AsyncMock
    from core.engine import TradingEngine
    from core.paper_account import PaperAccount
    from core.services.symbol_runner import process_single_symbol_runner
    monkeypatch.setattr(PaperAccount, "load_state", lambda self: None)
    monkeypatch.setattr(PaperAccount, "save_state", lambda self, **kwargs: None)
    monkeypatch.setattr("core.services.entry_finality.READ_INTERVAL_SECONDS", 0.)
    f = general_candles(side)
    if entry == "cross":
        from test_live_ma_cross_entry import candles as cross_candles
        f = cross_candles(side)
    a = PaperAccount()
    a.balance = 100.
    engine = object.__new__(TradingEngine)
    engine.account = a
    engine.exchange = SimpleNamespace(fetch_time=AsyncMock(return_value=time.time()*1000))
    engine.tickers = {symbol:float(f.iloc[-1].close)}
    engine.fetch_klines = AsyncMock(return_value=f)
    engine.strategy = SimpleNamespace(compute_indicators=lambda frame:frame)
    engine.symbol_rotation = SimpleNamespace(get_dynamic_leverage=lambda *args:2)
    engine._execution_price_is_safe = AsyncMock(return_value=True)
    asyncio.run(process_single_symbol_runner(engine, symbol, time.time(), None, False, exit_frame=f))
    assert a.positions[symbol]["side"] == side
    snapshot = a.trades[0]["entry_snapshot"]
    assert snapshot["entry_phase"] == ("KC_2BAR_CLOSED_CONFIRM" if entry == "general" else "MA5_MA15_LIVE_CROSS")
    a.positions.clear()
    asyncio.run(process_single_symbol_runner(engine, symbol, time.time(), None, False, exit_frame=f))
    assert len(a.trades) == 1


def test_lobster_2033_no_green_bridge_blocks_live_short():
    import pandas as pd
    rows = [
        dict(timestamp=(i+1)*60000, open=o, close=c, high=max(o,c)+.00003,
             low=min(o,c)-.00003, atr=atr, kc_lower=.0387, kc_middle=.0388,
             kc_upper=.0390, is_closed=True)
        for i, (o,c,atr) in enumerate([
            (.0389,.03895,.000103), (.03895,.03897,.000089),
            (.03899,.03899,.000088), (.03899,.03899,.000086),
            (.03899,.03897,.000082), (.03897,.03891,.000084)])
    ]
    rows.append(dict(timestamp=420000, open=.03890, close=.03869, high=.03896,
                     low=.03869, atr=.0001, kc_lower=.03871, kc_middle=.03882,
                     kc_upper=.03893, is_closed=False))
    assert evaluate_small_bridge_breakout(pd.DataFrame(rows), .03869) is None
