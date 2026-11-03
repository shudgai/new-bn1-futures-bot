"""Owner 2026-11-03: swing-only meme entries and pivot exits only with >= 1 ATR profit.

Entry: KC middle moves in the entry direction on 3 consecutive completed bars and ADX >= 20.
Exit: a confirmed pivot exit needs the current quote >= 1 ATR in profit (LONG/SHORT symmetric).
"""

import time

import pandas as pd
import pytest

from core.gates.master_gate_funnel import MarketSnapshot, MasterGateFunnel
from core.services.exits.strict_true_pivot import pivot_min_profit_reached


def snapshot(side, middles, adx):
    sign = 1 if side == "LONG" else -1
    mids = [100 + sign * v for v in middles]  # oldest -> newest
    return MarketSnapshot(
        "CAP/USDT", 101, 100, 101, 101.5, 99.5, 0, 0, 100.5, 100.4, 100.2, 100.1,
        102, mids[3], mids[2], 98, 1.0, "RANGE", 0,
        kc_middle_prev2=mids[1], kc_middle_prev3=mids[0], adx=adx,
    )


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_three_bar_kc_trend_with_adx_20_passes(side):
    assert MasterGateFunnel.swing_trend_block(snapshot(side, [0.0, 0.1, 0.2, 0.3], 20.0), side) is None


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
@pytest.mark.parametrize("middles", [
    [0.0, 0.1, 0.2, 0.2],    # flat last bar
    [0.0, 0.1, 0.05, 0.3],   # one counter bar
    [0.3, 0.2, 0.1, 0.0],    # against the entry
])
def test_kc_trend_must_hold_three_bars(side, middles):
    assert MasterGateFunnel.swing_trend_block(snapshot(side, middles, 30.0), side) == (
        f"BLOCKED_{side}_KC_TREND_NOT_3_BARS")


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
@pytest.mark.parametrize("adx", [19.99, float("nan"), 9.3])
def test_adx_below_20_or_invalid_blocks(side, adx):
    assert MasterGateFunnel.swing_trend_block(snapshot(side, [0.0, 0.1, 0.2, 0.3], adx), side) == (
        f"BLOCKED_{side}_ADX_BELOW_20")


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_trend_block_applies_inside_entry_filters_even_for_priority_channels(side, monkeypatch):
    monkeypatch.setattr(MasterGateFunnel, "priority_channel", classmethod(lambda cls, m, s: "TEST"))
    monkeypatch.setattr(MasterGateFunnel, "evaluate_market_blacklist",
                        classmethod(lambda cls, m: (False, "")))
    result = MasterGateFunnel.evaluate_entry_filters(snapshot(side, [0.0, 0.1, 0.2, 0.3], 10.0), side)
    assert not result.can_execute
    assert result.reason == f"BLOCKED_{side}_ADX_BELOW_20"


def pivot_frame(atr=1.0):
    now_minute = int(time.time() // 60)
    rows = [dict(timestamp=(now_minute - 1) * 60_000, open=100, high=101, low=99, close=100,
                 atr=atr, is_closed=True)]
    frame = pd.DataFrame(rows)
    frame.attrs.update(timeframe_ms=60_000, snapshot_ms=time.time() * 1000)
    return frame


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
@pytest.mark.parametrize("gain,expected", [(0.06, False), (0.999, False), (1.0, True), (1.5, True)])
def test_pivot_exit_requires_one_atr_current_profit(side, gain, expected):
    sign = 1 if side == "LONG" else -1
    position = {"side": side, "entry_price": 100.0, "symbol": "BTC/USDT"}
    quote = 100.0 + sign * gain
    assert pivot_min_profit_reached(position, pivot_frame(), quote) is expected


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_pivot_min_profit_fails_closed_on_bad_data(side):
    position = {"side": side, "entry_price": 100.0}
    assert pivot_min_profit_reached(position, None, 105.0) is False
    assert pivot_min_profit_reached(position, pivot_frame(atr=float("nan")), 105.0) is False
