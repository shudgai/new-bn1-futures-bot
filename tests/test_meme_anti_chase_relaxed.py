import math

import pandas as pd
import pytest

from core.gates.central_entry_gate import CentralEntryGate
from core.gates.master_gate_funnel import MasterGateFunnel
from test_meme_master_funnel import SYMBOLS, market


def evaluate(m, side):
    if side == "LONG":
        return MasterGateFunnel.evaluate_open_long(m)
    return MasterGateFunnel.evaluate_open_short(m)


def mirror(side, value):
    return value if side == "LONG" else 200 - value


@pytest.mark.parametrize("symbol", SYMBOLS)
@pytest.mark.parametrize("side", ["LONG", "SHORT"])
@pytest.mark.parametrize("bias_atr,allowed", [(1.4, True), (1.5, True), (1.5005, False)])
def test_ordinary_entry_ma15_limit_is_1_5_atr(symbol, side, bias_atr, allowed):
    m = market(side, symbol)
    sign = 1 if side == "LONG" else -1
    m = m._replace(quote=m.ma15_curr + sign * bias_atr * m.atr)
    decision = evaluate(m, side)
    if allowed:
        assert decision.reason != f"BLOCKED_{side}_MA15_EXTENSION_1_ATR", decision
    else:
        assert not decision.can_execute
        assert decision.reason == f"BLOCKED_{side}_MA15_EXTENSION_1_ATR"


def big_body(side, symbol, open_bias_atr, band_gap_atr=0.0):
    # Body 0.9 ATR from 101.2 to 103.0; rail at 101.5 unless the open is placed beyond it.
    upper = 101.5 if band_gap_atr == 0 else 101.2 - band_gap_atr * 2
    data = dict(
        open_price=101.2, close_price=103.0, quote=103.0, high_price=103.05, low_price=101.15,
        kc_upper=upper, kc_middle=100.0, kc_middle_prev=99.9, kc_lower=98.5,
        ma15_curr=101.2 - open_bias_atr * 2, ma15_prev=101.2 - open_bias_atr * 2 - .01,
        ma5_curr=101.5, ma5_prev=101.4,
    )
    m = market("LONG", symbol, **data)
    if side == "SHORT":
        mirrored = {k: 200 - getattr(m, k) for k in (
            "quote", "open_price", "close_price", "ma5_curr", "ma5_prev",
            "ma15_curr", "ma15_prev", "kc_middle", "kc_middle_prev")}
        mirrored.update(
            high_price=200 - m.low_price, low_price=200 - m.high_price,
            kc_upper=200 - m.kc_lower, kc_lower=200 - m.kc_upper, macro_trend="BEARISH",
        )
        m = m._replace(**mirrored)
    return m


@pytest.mark.parametrize("symbol", SYMBOLS)
@pytest.mark.parametrize("side", ["LONG", "SHORT"])
@pytest.mark.parametrize("open_bias_atr,allowed", [(1.3, True), (1.99, True), (2.0005, False)])
def test_big_body_breakout_measures_2_atr_from_open(symbol, side, open_bias_atr, allowed):
    m = big_body(side, symbol, open_bias_atr)
    assert MasterGateFunnel.is_big_body_breakout(m, side)
    decision = evaluate(m, side)
    assert decision.can_execute is allowed, decision
    if not allowed:
        assert decision.reason == f"BLOCKED_{side}_MA15_EXTENSION_1_ATR"


@pytest.mark.parametrize("symbol", SYMBOLS)
@pytest.mark.parametrize("side", ["LONG", "SHORT"])
@pytest.mark.parametrize("gap_atr,eligible", [(0.2, True), (0.25, True), (0.26, False)])
def test_big_body_breakout_open_may_sit_0_25_atr_beyond_band(symbol, side, gap_atr, eligible):
    m = big_body(side, symbol, 0.5, band_gap_atr=gap_atr)
    assert MasterGateFunnel.is_big_body_breakout(m, side) is eligible


def test_intrabar_live_bar_without_channel_uses_last_closed_channel():
    rows = [
        dict(close=100.0, open=99.9, high=100.1, low=99.8, atr=1.0, kc_upper=102.0,
             kc_lower=98.0, kc_middle=100.0, ma5=100.0, ma15=100.0, is_closed=True),
        dict(close=100.5, open=100.0, high=100.6, low=99.9, atr=1.1, kc_upper=102.6,
             kc_lower=98.2, kc_middle=100.4, ma5=100.2, ma15=100.1, is_closed=True),
        dict(close=100.8, open=100.5, high=100.9, low=100.4, atr=math.nan, kc_upper=math.nan,
             kc_lower=math.nan, kc_middle=math.nan, ma5=100.4, ma15=100.2, is_closed=False),
    ]
    metrics = CentralEntryGate._extract_market_metrics(pd.DataFrame(rows), 100.8, allow_intrabar=True)
    assert metrics["valid"], metrics
    assert metrics["is_closed"] is False
    assert math.isclose(metrics["kc_middle"], 100.4)
    assert math.isclose(metrics["atr"], 1.1)
