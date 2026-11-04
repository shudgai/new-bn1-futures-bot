"""Owner 2026-11-03: quality-gated meme entries and pivot exits only with >= 1 ATR profit.

Entry: KC slope clears 0.05 ATR and MA5 is at least 0.10 ATR from KC middle; ADX is not a gate.
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
@pytest.mark.parametrize("adx", [None, 19.99, float("nan"), 9.3])
def test_latest_kc_bar_and_low_or_missing_adx_do_not_block(side, adx):
    assert MasterGateFunnel.swing_trend_block(
        snapshot(side, [0.0, 0.2, 0.1, 0.3], adx), side,
    ) is None


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
@pytest.mark.parametrize("middles", [
    [0.0, 0.1, 0.2, 0.2],   # flat last bar
    [0.0, 0.1, 0.2, 0.1],   # counter-direction last bar
])
def test_latest_kc_bar_must_move_in_entry_direction(side, middles):
    assert MasterGateFunnel.swing_trend_block(snapshot(side, middles, 30.0), side) == (
        f"BLOCKED_{side}_KC_DIRECTION")


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
@pytest.mark.parametrize("slope,blocked", [(0.049999, True), (0.05, False), (0.050001, False)])
def test_kc_middle_slope_must_clear_atr_floor(side, slope, blocked):
    sign = 1 if side == "LONG" else -1
    m = snapshot(side, [0.0, 0.1, 0.2, 0.2 + slope], 30.0)
    m = m._replace(ma5_curr=m.kc_middle + sign * 0.2)
    result = MasterGateFunnel.swing_trend_block(m, side)
    assert (result == f"BLOCKED_{side}_KC_MIDDLE_TOO_FLAT") is blocked


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
@pytest.mark.parametrize("distance,blocked", [(0.099999, True), (0.1, False), (0.100001, False)])
def test_ma5_must_be_separated_from_kc_middle_by_at_least_point_one_atr(
    side, distance, blocked,
):
    sign = 1 if side == "LONG" else -1
    m = snapshot(side, [0.0, 0.1, 0.2, 0.4], 30.0)
    m = m._replace(ma5_curr=m.kc_middle + sign * distance)
    result = MasterGateFunnel.swing_trend_block(m, side)
    assert (result == f"BLOCKED_{side}_MA5_TOO_CLOSE_TO_KC_MIDDLE") is blocked


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_trend_block_applies_inside_entry_filters_even_for_priority_channels(side, monkeypatch):
    monkeypatch.setattr(MasterGateFunnel, "priority_channel", classmethod(lambda cls, m, s: "TEST"))
    monkeypatch.setattr(MasterGateFunnel, "evaluate_market_blacklist",
                        classmethod(lambda cls, m: (False, "")))
    result = MasterGateFunnel.evaluate_entry_filters(snapshot(side, [0.0, 0.1, 0.2, 0.1], 10.0), side)
    assert not result.can_execute
    assert result.reason == f"BLOCKED_{side}_KC_DIRECTION"


@pytest.mark.parametrize(
    "fault,expected_reason",
    [
        ("flat", "KC_MIDDLE_TOO_FLAT"),
        ("near", "MA5_TOO_CLOSE_TO_KC_MIDDLE"),
    ],
)
@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_flat_or_ma5_cluster_vetoes_priority_entry(side, fault, expected_reason, monkeypatch):
    sign = 1 if side == "LONG" else -1
    slope = 0.04 if fault == "flat" else 0.2
    distance = 0.2 if fault == "flat" else 0.05
    m = snapshot(side, [0.0, 0.1, 0.2, 0.2 + slope], 30.0)
    m = m._replace(ma5_curr=m.kc_middle + sign * distance)
    monkeypatch.setattr(
        MasterGateFunnel, "priority_channel",
        classmethod(lambda cls, market, entry_side: "TEST_PRIORITY"),
    )
    monkeypatch.setattr(
        MasterGateFunnel, "evaluate_market_blacklist",
        classmethod(lambda cls, market: (False, "")),
    )
    result = MasterGateFunnel.evaluate_entry_filters(m, side)
    assert not result.can_execute
    assert result.reason == f"BLOCKED_{side}_{expected_reason}"


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
