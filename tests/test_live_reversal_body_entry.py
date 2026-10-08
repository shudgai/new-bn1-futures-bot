import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from core.services.entry_contract import evaluate_entry_contract
from core.services.entry_firewall import validate_account_entry
from test_live_ma_cross_entry import candles


def reversal_frame(side):
    f = candles(side)
    sign = 1 if side == "LONG" else -1
    f.loc[13, "kc_middle"] = 100.+sign*.1
    f.loc[14, "kc_middle"] = 100.
    f.loc[15, "close"] = 100.+sign
    f["ma5"] = float("nan")
    f["ma15"] = float("nan")
    f.loc[15, "atr"] = 50.
    return f


@pytest.mark.parametrize("symbol", ["龙虾/USDT", "CAP/USDT"])
@pytest.mark.parametrize("side", ["LONG", "SHORT"])
@pytest.mark.parametrize("body,eligible", [(.999, False), (1., True), (1.001, True)])
def test_live_body_threshold_inside_kc_without_ma(symbol, side, body, eligible):
    f = reversal_frame(side)
    sign = 1 if side == "LONG" else -1
    f.loc[15, "close"] = 100.+sign*body
    a = SimpleNamespace(positions={}, trades=[], last_closed_at={}, save_state=Mock(),
                        log=Mock(), entry_frame_provider=AsyncMock(return_value=f))
    code = "KC_LIVE_REVERSAL_BODY_"+side
    decision = evaluate_entry_contract(f, code=code, account=a, symbol=symbol)
    assert bool(decision) is eligible
    if not eligible:
        return
    assert decision["entry_atr"] == 1.
    assert decision["reversal_body"] >= 1.
    assert float(f.iloc[-1].kc_lower) < decision["price"] < float(f.iloc[-1].kc_upper)
    context = dict(entry_signal_code=code, channel_confirmation_bar_id=decision["confirmation_bar_id"])
    assert asyncio.run(validate_account_entry(a, symbol, side, context))
    f.loc[15, "close"] = 100.+sign*.9
    with pytest.raises(ValueError):
        asyncio.run(validate_account_entry(a, symbol, side, context))


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
@pytest.mark.parametrize("fault", ["flat_kc", "aligned_kc", "atr", "gap", "closed_live", "wick_only",
                                  "expired", "future", "unverified", "position"])
def test_reversal_cannot_bypass_direction_market_or_position_gates(side, fault):
    f = reversal_frame(side)
    sign = 1 if side == "LONG" else -1
    if fault == "flat_kc":
        f.loc[13, "kc_middle"] = 100.
    elif fault == "aligned_kc":
        f.loc[13, "kc_middle"] = 100.-sign*.1
    elif fault == "atr":
        f.loc[14, "atr"] = float("nan")
    elif fault == "gap":
        f.loc[13, "timestamp"] -= 1
    elif fault == "closed_live":
        f.loc[15, "is_closed"] = True
    elif fault == "wick_only":
        f.loc[15, "close"] = 100.+sign*.1
    elif fault == "expired":
        f["timestamp"] -= 60000
    elif fault == "future":
        f["timestamp"] += 60000
    elif fault == "unverified":
        f.attrs["entry_finality_verified"] = False
    a = SimpleNamespace(positions={"CAP/USDT":{}} if fault == "position" else {},
                        trades=[], last_closed_at={}, save_state=Mock(), log=Mock(),
                        entry_frame_provider=AsyncMock(return_value=f))
    context = dict(entry_signal_code="KC_LIVE_REVERSAL_BODY_"+side,
                   channel_confirmation_bar_id=float(f.iloc[-1].timestamp))
    with pytest.raises(ValueError):
        asyncio.run(validate_account_entry(a, "CAP/USDT", side, context))


def engine_account(f, symbol, tmp_path, monkeypatch):
    from core.engine import TradingEngine
    from core.paper_account import PaperAccount
    monkeypatch.setattr("core.paper_account.STATE_FILE", str(tmp_path/"reversal.json"))
    monkeypatch.setattr("core.services.entry_finality.READ_INTERVAL_SECONDS", 0.)
    a = PaperAccount()
    a.balance = 100.
    e = object.__new__(TradingEngine)
    e.account = a
    e.exchange = SimpleNamespace(fetch_time=AsyncMock(return_value=time.time()*1000))
    e.tickers = {symbol:float(f.iloc[-1].close)}
    e.fetch_klines = AsyncMock(return_value=f)
    e.strategy = SimpleNamespace(compute_indicators=lambda frame:frame)
    e.symbol_rotation = SimpleNamespace(get_dynamic_leverage=lambda *args:2)
    e._execution_price_is_safe = AsyncMock(return_value=True)
    return e, a


@pytest.mark.parametrize("symbol", ["龙虾/USDT", "CAP/USDT"])
@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_runner_real_paper_fill_restart_same_bar_dedupe(symbol, side, tmp_path, monkeypatch):
    from core.paper_account import PaperAccount
    from core.services.symbol_runner import process_single_symbol_runner
    f = reversal_frame(side)
    e, a = engine_account(f, symbol, tmp_path, monkeypatch)
    asyncio.run(process_single_symbol_runner(e, symbol, time.time(), None, False, exit_frame=f))
    assert a.positions[symbol]["side"] == side
    snapshot = a.trades[0]["entry_snapshot"]
    assert snapshot["signal_code"] == "KC_LIVE_REVERSAL_BODY_"+side
    assert snapshot["reversal_body_atr"] == 1.
    a.positions.clear()
    a.save_state(strict=True)
    restored = PaperAccount()
    diag = {}
    assert evaluate_entry_contract(f, account=restored, symbol=symbol, diagnostics=diag) is None
    assert diag["reason"] == "BLOCKED_LIVE_REVERSAL_BAR_ALREADY_FILLED"
    # A matched same-bar close cannot turn the consumed long K into another order.
    restored.trades.insert(0, dict(id=float(f.iloc[-1].timestamp)+2000, symbol=symbol,
                                  action="CLOSE_"+side, status="CLOSED"))
    assert evaluate_entry_contract(f, account=restored, symbol=symbol) is None


@pytest.mark.parametrize("fault", ["slots", "daily", "capital", "pending", "execution_quality"])
def test_reversal_still_uses_shared_engine_risk_gates(fault, tmp_path, monkeypatch):
    from core.services.symbol_runner import process_single_symbol_runner
    f = reversal_frame("LONG")
    e, a = engine_account(f, "CAP/USDT", tmp_path, monkeypatch)
    if fault == "slots":
        monkeypatch.setattr("core.engine.MAX_SLOTS", 1)
        a.positions["OTHER"] = {}
    elif fault == "daily":
        a.daily_loss_limit_hit = lambda:(True, 10.)
    elif fault == "capital":
        a.balance = 0.
    elif fault == "pending":
        a.pending_limit_orders["CAP/USDT"] = {}
    else:
        e._execution_price_is_safe = AsyncMock(return_value=False)
    asyncio.run(process_single_symbol_runner(e, "CAP/USDT", time.time(), None, False, exit_frame=f))
    assert not a.trades and "CAP/USDT" not in a.positions


def test_next_candle_cannot_reuse_previous_big_body():
    f = reversal_frame("LONG")
    f["timestamp"] += 60000
    f.loc[15, ["open", "close"]] = 101.
    assert evaluate_entry_contract(f, code="KC_LIVE_REVERSAL_BODY_LONG") is None
