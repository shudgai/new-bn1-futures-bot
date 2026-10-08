import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from core.services.entry_contract import evaluate_entry_contract
from core.services.entry_firewall import validate_account_entry
from test_live_ma_cross_entry import candles
from test_live_reversal_body_entry import reversal_frame
from test_v2_execution_boundary import candles as general_candles
from test_fast_close_reentry import fixture


@pytest.mark.parametrize("symbol", ["龙虾/USDT", "CAP/USDT"])
@pytest.mark.parametrize("side", ["LONG", "SHORT"])
@pytest.mark.parametrize("route", ["general", "cross", "reversal", "fast"])
def test_all_routes_revalidate_live_ma_position_at_account_firewall(symbol, side, route):
    sign = 1 if side == "LONG" else -1
    trades = []
    if route == "general":
        f = general_candles(side)
        code = "KC_2BAR_CONFIRM_"+side
    elif route == "cross":
        f = candles(side)
        code = "MA5_MA15_LIVE_CROSS_"+side
    elif route == "reversal":
        f = reversal_frame(side)
        code = "KC_LIVE_REVERSAL_BODY_"+side
    else:
        f, close = fixture(side, symbol)
        trades = [close]
        code = "FAST_CLOSE_REENTRY_"+side
    a = SimpleNamespace(positions={}, trades=trades, last_closed_at={},
                        save_state=Mock(), log=Mock(), entry_frame_provider=AsyncMock(return_value=f))
    decision = evaluate_entry_contract(f, code=code, account=a, symbol=symbol)
    assert decision
    assert sign*(decision["direction_live_ma5"]-decision["direction_live_ma15"]) > 0
    ctx = dict(entry_signal_code=code, channel_confirmation_bar_id=decision["confirmation_bar_id"])
    assert asyncio.run(validate_account_entry(a, symbol, side, ctx))
    # Change only older closes, keeping formation, live quote and current MA5 intact.
    old_indices = f.index[-15:-5]
    f.loc[old_indices, "close"] = 110. if side == "LONG" else 90.
    f.loc[old_indices, "high"] = f.loc[old_indices, ["open", "close", "high"]].max(axis=1)
    f.loc[old_indices, "low"] = f.loc[old_indices, ["open", "close", "low"]].min(axis=1)
    with pytest.raises(ValueError):
        asyncio.run(validate_account_entry(a, symbol, side, ctx))
    if route != "cross":
        diag = {}
        assert evaluate_entry_contract(f, code=code, account=a, symbol=symbol,
                                       diagnostics=diag) is None
        assert diag["reason"] == "BLOCKED_LIVE_MA5_MA15_DIRECTION"


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
@pytest.mark.parametrize("fault", ["short", "nan", "touch"])
def test_missing_invalid_touching_live_ma_cannot_authorize_reversal(side, fault):
    f = reversal_frame(side)
    if fault == "short":
        f = f.tail(10)
    elif fault == "nan":
        f.loc[2, "close"] = float("nan")
    else:
        prices = f.iloc[:-1].close.tail(14)
        quote = float(f.iloc[-1].close)
        live5 = (prices.tail(4).sum()+quote)/5.
        difference = live5*15-(prices.sum()+quote)
        f.loc[1, "close"] += difference
        f.loc[1, "high"] = max(float(f.loc[1, "high"]), float(f.loc[1, "close"]))
        f.loc[1, "low"] = min(float(f.loc[1, "low"]), float(f.loc[1, "close"]))
    assert evaluate_entry_contract(f, code="KC_LIVE_REVERSAL_BODY_"+side) is None
