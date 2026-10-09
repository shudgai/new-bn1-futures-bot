import asyncio
from unittest.mock import AsyncMock

import pytest

from core.services.entry_chop_gate import evaluate_entry_chop
from core.services.entry_contract import evaluate_entry_contract, live_ma_values
from core.services.entry_firewall import validate_account_entry
from test_dual_breakout_pair import pair_frame, SYMBOLS, SIDES
from test_wait_authority import account


def qualified(side, authority):
    f = pair_frame(side)
    if authority == "KC_LIVE_CROSS":
        opened = float(f.iloc[-1].kc_middle)
        f.loc[2, "open"] = opened
        f.loc[2, "low"] = min(opened, f.loc[2, "low"])
        f.loc[2, "high"] = max(opened, f.loc[2, "high"])
    return f


@pytest.mark.parametrize("symbol", SYMBOLS)
@pytest.mark.parametrize("side", SIDES)
@pytest.mark.parametrize("authority", ["KC_LIVE_CROSS", "KC_2BAR_CONFIRM"])
@pytest.mark.parametrize("period", [5, 15])
@pytest.mark.parametrize("slope", ["opposite", "flat", "invalid", "missing"])
def test_both_routes_reject_each_bad_ma_direction(symbol, side, authority, period, slope):
    f = qualified(side, authority)
    evidence = live_ma_values(f, float(f.iloc[-1].close))
    sign = 1 if side == "LONG" else -1
    if slope == "missing":
        f = f.drop(columns=[f"ma{period}"])
    else:
        f.loc[1, f"ma{period}"] = (float("nan") if slope == "invalid" else
            evidence[f"entry_live_ma{period}"]+(sign*.1 if slope == "opposite" else 0))
    diagnostics = {}
    assert evaluate_entry_contract(f, code=authority+"_"+side, account=account(),
                                   symbol=symbol, diagnostics=diagnostics) is None
    assert diagnostics["reason"] == (
        "BLOCKED_LIVE_MA_DIRECTION_DATA" if slope in ("invalid", "missing")
        else f"WAIT_LIVE_MA{period}_DIRECTION")


@pytest.mark.parametrize("symbol", SYMBOLS)
@pytest.mark.parametrize("side", SIDES)
@pytest.mark.parametrize("authority", ["KC_LIVE_CROSS", "KC_2BAR_CONFIRM"])
def test_both_routes_cannot_exempt_ma5_whipsaw(symbol, side, authority):
    f = qualified(side, authority)
    previous = float(f.loc[1, "ma5"])
    sign = 1 if side == "LONG" else -1
    f.loc[f.index[:-1], "ma5"] = [
        previous-sign*.5, previous-sign*.3, previous-sign*.4,
        previous-sign*.2, previous-sign*.3, previous]
    assert evaluate_entry_chop(f)[0] == "PASS"
    diagnostics = {}
    assert evaluate_entry_contract(f, code=authority+"_"+side, account=account(),
                                   symbol=symbol, diagnostics=diagnostics) is None
    assert diagnostics["reason"] == "BLOCKED_CHOP_MA5_DIRECTION_CHANGES"


@pytest.mark.parametrize("changes,passes", [(0, True), (1, True), (2, False)])
def test_ma5_flip_boundary_and_flat_ignored(changes, passes):
    f = pair_frame("LONG")
    patterns = {0: [99., 99., 99.1, 99.2, 99.2, 99.3],
                1: [99., 99.1, 99.1, 99.2, 99.1, 99.1],
                2: [99., 99.1, 99.1, 99., 99., 99.2]}
    f.loc[f.index[:-1], "ma5"] = patterns[changes]
    status, result = evaluate_entry_chop(f, check_ma5_turns=True)
    assert bool(result) is passes
    if passes:
        assert result["chop_ma5_direction_changes"] == changes
    else:
        assert status == "BLOCKED_CHOP_MA5_DIRECTION_CHANGES"


@pytest.mark.parametrize("symbol", SYMBOLS)
@pytest.mark.parametrize("side", SIDES)
@pytest.mark.parametrize("authority", ["KC_LIVE_CROSS", "KC_2BAR_CONFIRM"])
@pytest.mark.parametrize("fault", ["ma5", "ma15", "whipsaw", "price_chop"])
def test_firewall_rejects_new_opposition_after_valid_candidate(symbol, side, authority, fault, monkeypatch):
    a, f = account(), qualified(side, authority)
    code = authority+"_"+side
    assert evaluate_entry_contract(f, code=code, account=a, symbol=symbol)
    if fault in ("ma5", "ma15"):
        averages = live_ma_values(f, float(f.iloc[-1].close))
        f.loc[1, fault] = averages["entry_live_"+fault]+(1 if side == "LONG" else -1)*.1
    elif fault == "whipsaw":
        f.loc[f.index[:4], "ma5"] = [99., 99.1, 99., 99.1]
    else:
        f.loc[f.index[:4], "close"] = [100., 99.8, 100., 99.8]
        for i in f.index[:4]:
            f.loc[i, "low"] = min(f.loc[i, "low"], f.loc[i, "close"])
            f.loc[i, "high"] = max(f.loc[i, "high"], f.loc[i, "close"])
    a.entry_frame_provider = AsyncMock(return_value=f)
    monkeypatch.setattr("time.time", lambda: 602.)
    with pytest.raises(ValueError, match="WAIT_LIVE_MA|BLOCKED_CHOP"):
        asyncio.run(validate_account_entry(a, symbol, side, dict(
            entry_signal_code=code, channel_confirmation_bar_id=600000.)))


@pytest.mark.parametrize("side", SIDES)
@pytest.mark.parametrize("authority", ["KC_LIVE_CROSS", "KC_2BAR_CONFIRM"])
def test_valid_direction_recovers_without_invalidating_entire_bar(side, authority):
    f, a = qualified(side, authority), account()
    old = float(f.loc[1, "ma5"])
    values = live_ma_values(f, float(f.iloc[-1].close))
    f.loc[1, "ma5"] = values["entry_live_ma5"]+(1 if side == "LONG" else -1)*.1
    assert not evaluate_entry_contract(f, account=a, symbol="CAP/USDT")
    f.loc[1, "ma5"] = old
    d = evaluate_entry_contract(f, code=authority+"_"+side, account=a, symbol="CAP/USDT")
    assert d and d["chop_limits_exempt"] is False
    assert d["entry_previous_ma5"] == old
