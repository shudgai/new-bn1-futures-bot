import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from core.services.entry_contract import evaluate_entry_contract
from core.services.entry_firewall import validate_account_entry
from test_live_ma_cross_entry import candles
from test_live_reversal_body_entry import engine_account


def fixture(side, symbol):
    f = candles(side)
    sign = 1 if side == "LONG" else -1
    f.loc[13:14, "kc_middle"] = [100., 100.+sign*.1]
    f.loc[13:14, "ma15"] = [100., 100.+sign*.01]
    # The closed MA5 need not have already turned; the live slope is authoritative.
    f.loc[13, "ma5"] = float(f.loc[14].ma5)+sign*.01
    close = dict(id=time.time()*1000-50., symbol=symbol, action="CLOSE_"+side,
                 status="CLOSED", entry_mode="CHANNEL_SWING",
                 reason="Channel Swing EXIT_FIXED_ATR_HALF_STEP_PROFIT")
    return f, close


@pytest.mark.parametrize("symbol", ["龙虾/USDT", "CAP/USDT"])
@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_same_bar_inside_kc_reentry_and_final_quote_direction(symbol, side):
    f, close = fixture(side, symbol)
    a = SimpleNamespace(positions={}, pending_limit_orders={}, trades=[close], last_closed_at={},
                        save_state=Mock(), log=Mock(), entry_frame_provider=AsyncMock(return_value=f))
    decision = evaluate_entry_contract(f, account=a, symbol=symbol)
    assert decision["type"] == "FAST_CLOSE_REENTRY_"+side
    assert f.iloc[-1].kc_lower < decision["price"] < f.iloc[-1].kc_upper
    context = dict(entry_signal_code=decision["type"],
                   channel_confirmation_bar_id=decision["confirmation_bar_id"])
    assert asyncio.run(validate_account_entry(a, symbol, side, context))
    sign = 1 if side == "LONG" else -1
    f.loc[15, "close"] = 100.-sign
    with pytest.raises(ValueError, match="WAIT_FAST_REENTRY"):
        asyncio.run(validate_account_entry(a, symbol, side, context))


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
@pytest.mark.parametrize("fault", ["no_close", "failed", "manual", "other_symbol", "future_close",
                                  "held", "pending", "closing", "already_reopened",
                                  "ma5_flat", "ma5_nan", "kc_flat", "ma15_opposite",
                                  "stale", "unverified"])
def test_close_and_current_direction_are_mandatory(side, fault):
    symbol = "CAP/USDT"
    f, close = fixture(side, symbol)
    a = SimpleNamespace(positions={}, pending_limit_orders={}, closing_lock=set(),
                        trades=[close], last_closed_at={}, save_state=Mock(), log=Mock(),
                        entry_frame_provider=AsyncMock(return_value=f))
    sign = 1 if side == "LONG" else -1
    if fault == "no_close":
        a.trades = []
    elif fault == "failed":
        close["status"] = "FAILED"
    elif fault == "manual":
        close["reason"] = "手動平倉"
    elif fault == "other_symbol":
        close["symbol"] = "龙虾/USDT"
    elif fault == "future_close":
        close["id"] += 60000
    elif fault == "held":
        a.positions[symbol] = {}
    elif fault == "pending":
        a.pending_limit_orders[symbol] = {}
    elif fault == "closing":
        a.closing_lock.add(symbol)
    elif fault == "already_reopened":
        a.trades.append(dict(id=close["id"]+1, symbol=symbol, action="OPEN_"+side))
    elif fault == "ma5_flat":
        f.loc[15, "close"] = float(f.iloc[14].ma5)*5-f.iloc[:-1].close.tail(4).sum()
    elif fault == "ma5_nan":
        f.loc[14, "ma5"] = float("nan")
    elif fault == "kc_flat":
        f.loc[14, "kc_middle"] = float(f.iloc[13].kc_middle)
    elif fault == "ma15_opposite":
        f.loc[14, "ma15"] = 100.-sign*.01
    elif fault == "stale":
        f["timestamp"] -= 60000
    else:
        f.attrs["entry_finality_verified"] = False
    ctx = dict(entry_signal_code="FAST_CLOSE_REENTRY_"+side,
               channel_confirmation_bar_id=float(f.iloc[-1].timestamp))
    with pytest.raises(ValueError):
        asyncio.run(validate_account_entry(a, symbol, side, ctx))


@pytest.mark.parametrize("symbol", ["龙虾/USDT", "CAP/USDT"])
@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_immediate_engine_reentry_restart_and_one_consumption(symbol, side, tmp_path, monkeypatch):
    from core.paper_account import PaperAccount
    f, close = fixture(side, symbol)
    e, a = engine_account(f, symbol, tmp_path, monkeypatch)
    e.is_running = True
    a.trades = [close]
    asyncio.run(e._reevaluate_after_close(symbol))
    assert a.positions[symbol]["side"] == side
    opened = next(t for t in a.trades if t["action"] == "OPEN_"+side)
    assert opened["entry_snapshot"]["entry_phase"] == "FAST_CLOSE_REENTRY"
    assert opened["entry_snapshot"]["reentry_close_id"] == close["id"]
    a.positions.clear()
    a.save_state(strict=True)
    restored = PaperAccount()
    assert evaluate_entry_contract(f, code="FAST_CLOSE_REENTRY_"+side,
                                   account=restored, symbol=symbol) is None
    restored.trades.insert(0, dict(close, id=time.time()*1000))
    assert evaluate_entry_contract(f, code="FAST_CLOSE_REENTRY_"+side,
                                   account=restored, symbol=symbol) is None


@pytest.mark.parametrize("fault", ["slots", "capital", "daily", "pending", "execution_quality"])
def test_fast_reentry_uses_account_submit_safety(fault, tmp_path, monkeypatch):
    f, close = fixture("LONG", "CAP/USDT")
    e, a = engine_account(f, "CAP/USDT", tmp_path, monkeypatch)
    e.is_running = True
    a.trades = [close]
    if fault == "slots":
        monkeypatch.setattr("core.engine.MAX_SLOTS", 1)
        a.positions["OTHER"] = {}
    elif fault == "capital":
        a.balance = 0.
    elif fault == "daily":
        a.daily_loss_limit_hit = lambda:(True, 10.)
    elif fault == "pending":
        a.pending_limit_orders["CAP/USDT"] = {}
    else:
        e._execution_price_is_safe = AsyncMock(return_value=False)
    asyncio.run(e._reevaluate_after_close("CAP/USDT"))
    assert "CAP/USDT" not in a.positions
    assert len(a.trades) == 1


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_failed_entry_not_consumed_and_concurrent_rechecks_open_once(side, tmp_path, monkeypatch):
    f, close = fixture(side, "CAP/USDT")
    e, a = engine_account(f, "CAP/USDT", tmp_path, monkeypatch)
    e.is_running = True
    a.trades = [close]
    e._execution_price_is_safe = AsyncMock(return_value=False)
    asyncio.run(e._reevaluate_after_close("CAP/USDT"))
    assert len(a.trades) == 1
    e._execution_price_is_safe = AsyncMock(return_value=True)

    async def simultaneous():
        await asyncio.gather(e._reevaluate_after_close("CAP/USDT"),
                             e._reevaluate_after_close("CAP/USDT"))

    asyncio.run(simultaneous())
    assert a.positions["CAP/USDT"]["side"] == side
    assert len([t for t in a.trades if t["action"].startswith("OPEN")]) == 1


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_other_entry_cannot_bypass_post_close_ma5_direction(side):
    from test_live_reversal_body_entry import reversal_frame
    f, close = fixture(side, "CAP/USDT")
    f = reversal_frame(side)
    # The independent big-body rule remains valid, but the latest MA5 is adverse.
    sign = 1 if side == "LONG" else -1
    live_ma5 = (f.iloc[:-1].close.tail(4).sum()+float(f.iloc[-1].close))/5.
    f.loc[14, "ma5"] = live_ma5+sign*.1
    a = SimpleNamespace(positions={}, trades=[close], last_closed_at={})
    diagnostics = {}
    assert evaluate_entry_contract(f, code="KC_LIVE_REVERSAL_BODY_"+side,
                                   account=a, symbol="CAP/USDT", diagnostics=diagnostics) is None
    assert diagnostics["reason"] == "BLOCKED_REENTRY_MA5_FLAT_OPPOSITE_OR_INVALID"
