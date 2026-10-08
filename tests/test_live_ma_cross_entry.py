import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pandas as pd
import pytest

from core.services.entry_contract import evaluate_entry_contract, evaluate_live_ma_cross
from core.services.entry_firewall import validate_account_entry
from core.services.ma5_chop_gate import ma5_chop_problem


def candles(side="LONG"):
    sign = 1 if side == "LONG" else -1
    bar = int(time.time()//60)*60000
    closes = [100.+sign*.05]*10 + [100.]*4 + [100.+sign*.1]
    rows = [dict(timestamp=bar-(15-i)*60000, open=100., high=102., low=98.,
                 close=c, atr=1., kc_lower=98., kc_middle=100., kc_upper=102.,
                 is_closed=True) for i, c in enumerate(closes)]
    rows.append(dict(timestamp=bar, open=100., high=102., low=98.,
                     close=100.+sign, atr=1., kc_lower=98., kc_middle=100.,
                     kc_upper=102., is_closed=False))
    f = pd.DataFrame(rows)
    f["ma5"] = f.close.rolling(5).mean()
    f["ma15"] = f.close.rolling(15).mean()
    f["ma3"] = f.close.rolling(3).mean()
    # Older valid rolling values come from the constant preceding price history.
    for index in range(14):
        if pd.isna(f.loc[index, "ma5"]):
            f.loc[index, "ma5"] = 100.+sign*.05
        f.loc[index, "ma15"] = 100.+sign*.05
    f.attrs.update(timeframe_ms=60000, entry_finality_verified=True)
    return f


@pytest.mark.parametrize("symbol", ["龙虾/USDT", "CAP/USDT"])
@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_live_cross_inside_kc_and_fresh_account_revalidation(symbol, side):
    f = candles(side)
    a = SimpleNamespace(positions={}, trades=[], last_closed_at={}, save_state=Mock(),
                        log=Mock(), entry_frame_provider=AsyncMock(return_value=f))
    decision = evaluate_entry_contract(f, account=a, symbol=symbol)
    assert decision and decision["type"] == "MA5_MA15_LIVE_CROSS_"+side
    assert float(f.iloc[-1].kc_lower) < decision["price"] < float(f.iloc[-1].kc_upper)
    context = dict(entry_signal_code=decision["type"],
                   channel_confirmation_bar_id=decision["confirmation_bar_id"])
    assert asyncio.run(validate_account_entry(a, symbol, side, context))
    f.loc[f.index[-1], "close"] = 100.
    with pytest.raises(ValueError):
        asyncio.run(validate_account_entry(a, symbol, side, context))


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
@pytest.mark.parametrize("fault", ["already_crossed", "touch", "nan", "short", "opposite_ma15"])
def test_cross_must_be_new_strict_and_both_averages_directional(side, fault):
    f = candles(side)
    sign = 1 if side == "LONG" else -1
    if fault == "already_crossed":
        f.loc[14, "ma5"] = 100.+sign*.06
    elif fault == "touch":
        last4, last14 = f.iloc[:-1].close.tail(4).sum(), f.iloc[:-1].close.tail(14).sum()
        f.loc[15, "close"] = (last14-3*last4)/2.
    elif fault == "nan":
        f.loc[14, "ma15"] = float("nan")
    elif fault == "short":
        f = f.tail(10)
    else:
        f.loc[14, "ma15"] = 100.+sign*.2
    assert evaluate_live_ma_cross(f, float(f.iloc[-1].close)) is None


@pytest.mark.parametrize("symbol", ["龙虾/USDT", "CAP/USDT"])
@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_valid_cross_waits_for_entangled_lines_to_separate(symbol, side):
    f = candles(side)
    sign = 1 if side == "LONG" else -1
    quote = 100.+sign*.8
    assert evaluate_live_ma_cross(f, quote)
    diagnostics = {}
    assert evaluate_entry_contract(f, quote, symbol=symbol, diagnostics=diagnostics) is None
    assert diagnostics["reason"] == "BLOCKED_MA5_MA15_ENTANGLED"
    assert evaluate_entry_contract(f, 100.+sign, symbol=symbol)


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_retired_live_breakout_rejected_even_with_cross(side):
    f = candles(side)
    diagnostics = {}
    assert evaluate_entry_contract(f, code="KC_LIVE_BODY_BREAKOUT_"+side,
                                   diagnostics=diagnostics) is None
    assert diagnostics["reason"] == "BLOCKED_OBSOLETE_ENTRY_SIGNAL"


@pytest.mark.parametrize("symbol", ["龙虾/USDT", "CAP/USDT"])
@pytest.mark.parametrize("side", ["LONG", "SHORT"])
@pytest.mark.parametrize("entry", ["cross", "general"])
def test_historical_ma5_turns_do_not_veto_current_qualified_entry(symbol, side, entry):
    sign = 1 if side == "LONG" else -1
    if entry == "cross":
        f = candles(side)
        f.loc[9:14, "ma5"] = [100.+sign*v for v in (.012, .014, .016, .013, .018, .020)]
    else:
        from test_v2_execution_boundary import candles as general_candles
        f = general_candles(side)
        f.loc[:5, "ma5"] = [100.+sign*v for v in (1.3, 1.4, 1.2, 1.3, 1.4, 1.5)]
    assert ma5_chop_problem(f) == "BLOCKED_MA5_CHOP_TURNS"
    a = SimpleNamespace(positions={}, trades=[], last_closed_at={}, save_state=Mock(),
                        log=Mock(), entry_frame_provider=AsyncMock(return_value=f))
    decision = evaluate_entry_contract(f, account=a, symbol=symbol)
    assert decision and decision["type"] == (
        "MA5_MA15_LIVE_CROSS_"+side if entry == "cross" else "KC_2BAR_CONFIRM_"+side)
    context = dict(entry_signal_code=decision["type"],
                   channel_confirmation_bar_id=decision["confirmation_bar_id"])
    assert asyncio.run(validate_account_entry(a, symbol, side, context))


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_old_turns_removed_does_not_bypass_entanglement(side):
    f = candles(side)
    sign = 1 if side == "LONG" else -1
    f.loc[9:14, "ma5"] = [100.+sign*v for v in (.012, .014, .016, .013, .018, .020)]
    assert ma5_chop_problem(f) == "BLOCKED_MA5_CHOP_TURNS"
    diagnostics = {}
    assert evaluate_entry_contract(f, 100.+sign*.8, diagnostics=diagnostics) is None
    assert diagnostics["reason"] == "BLOCKED_MA5_MA15_ENTANGLED"


@pytest.mark.parametrize("symbol", ["龙虾/USDT", "CAP/USDT"])
@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_filled_cross_restart_and_same_bar_close_cannot_reopen(symbol, side, tmp_path, monkeypatch):
    from core.paper_account import PaperAccount
    monkeypatch.setattr("core.paper_account.STATE_FILE", str(tmp_path/"cross.json"))
    f = candles(side)
    a = PaperAccount()
    decision = evaluate_entry_contract(f, account=a, symbol=symbol)
    assert decision
    bar = float(f.iloc[-1].timestamp)
    a.trades = [
        dict(id=bar+1000, symbol=symbol, action="OPEN_"+side,
             entry_snapshot=dict(entry_phase=decision["entry_phase"], candidate_bar_id=bar,
                                 pending_signal_id=decision["pending_signal_id"])),
        dict(id=bar+2000, symbol=symbol, action="CLOSE_"+side, status="CLOSED"),
    ]
    a.save_state(strict=True)
    restored = PaperAccount()
    diagnostics = {}
    assert evaluate_entry_contract(f, account=restored, symbol=symbol,
                                   diagnostics=diagnostics) is None
    assert diagnostics["reason"] == "BLOCKED_MA_CROSS_ALREADY_FILLED"


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_next_bar_already_crossed_does_not_backfill_cross(side):
    f = candles(side)
    sign = 1 if side == "LONG" else -1
    f["timestamp"] += 60000
    f.loc[14, "ma5"] = 100.+sign*.22
    f.loc[14, "ma15"] = 100.+sign*.103
    assert evaluate_entry_contract(f, code="MA5_MA15_LIVE_CROSS_"+side) is None
