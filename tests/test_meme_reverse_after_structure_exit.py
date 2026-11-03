"""Owner 2026-11-03: after a structure exit, the opposite side may open within 3 bars.

Only into a clear trend: KC middle moving that way by >= 0.05 ATR, fewer than 2 MA5/MA15 crosses in
8 bars, MA5 beyond MA15 and turning that way, a directional candle closed and quoted beyond KC
middle and MA15, and the anti-chase limits. It is exempt from cooldown, the 3-bar KC/ADX swing
filter and the macro conflict. Disaster/manual closes never reverse.
"""

import pytest

import core.gates.master_gate_funnel as mgf
from core.gates.master_gate_funnel import MarketSnapshot, MasterGateFunnel

SYMBOLS = ["1000PEPE/USDT", "龙虾/USDT"]
STRUCTURE_REASON = {
    "SHORT": "EXIT_SHORT_KC_FLIP_CLOSED_ABOVE_MIDDLE",
    "LONG": "EXIT_LONG_KC_FLIP_CLOSED_BELOW_MIDDLE",
}
OPPOSITE = {"LONG": "SHORT", "SHORT": "LONG"}


def reverse_snapshot(symbol, side, *, close=100.6, ma5=100.4, middle_prev=100.35, crosses=0):
    """Right after a short exit: KC middle already rising, MA5 above MA15, green bar (LONG).
    Macro is still BEARISH and ADX low, so only the reverse channel can pass. SHORT mirrors."""
    f = (lambda p: p) if side == "LONG" else (lambda p: 200 - p)
    upper, lower = (101.5, 99.3) if side == "LONG" else (100.7, 98.5)
    return MarketSnapshot(
        symbol, f(close), f(100.0), f(close), f(max(close, 100.0) + 0.1), f(99.95),
        f(100.3), f(100.1), f(ma5), f(100.3), f(100.3), f(100.35),
        upper, f(100.45), f(middle_prev), lower, 1.0,
        "BEARISH" if side == "LONG" else "BULLISH", crosses,
        kc_middle_prev2=f(100.6), kc_middle_prev3=f(100.7), adx=10.0,
    )


@pytest.fixture(autouse=True)
def clean_registry(monkeypatch):
    monkeypatch.setattr(mgf, "_STRUCTURE_EXITS", {})
    monkeypatch.setattr(MasterGateFunnel, "evaluate_market_blacklist",
                        classmethod(lambda cls, m: (False, "")))


def open_gate(side, m, cooldown=True):
    fn = MasterGateFunnel.evaluate_open_long if side == "LONG" else MasterGateFunnel.evaluate_open_short
    return fn(m, is_in_cooldown=cooldown)


@pytest.mark.parametrize("symbol", SYMBOLS)
@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_structure_exit_opens_opposite_side_inside_window(symbol, side):
    m = reverse_snapshot(symbol, side)
    assert not open_gate(side, m).can_execute  # baseline: blocked without the record
    mgf.record_position_close(symbol, OPPOSITE[side], STRUCTURE_REASON[OPPOSITE[side]])
    decision = open_gate(side, m)
    assert decision.can_execute, decision.reason
    assert decision.details["priority_channel"] == mgf.REVERSE_TRIGGER


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
@pytest.mark.parametrize("reason", ["DISASTER_LOSS_3ATR", "MANUAL", "HARD_STOP_LOSS", "EXIT_BY_EXTREME_WATERFALL"])
def test_non_structure_exit_does_not_reverse(side, reason):
    mgf.record_position_close("龙虾/USDT", OPPOSITE[side], reason)
    assert mgf.reverse_window_side("龙虾/USDT") is None
    assert not open_gate(side, reverse_snapshot("龙虾/USDT", side)).can_execute


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_same_side_does_not_reopen(side):
    mgf.record_position_close("1000PEPE/USDT", side, STRUCTURE_REASON[side])
    assert not MasterGateFunnel.is_reverse_after_structure_exit(reverse_snapshot("1000PEPE/USDT", side), side)


def test_flat_threshold_boundary_is_inclusive():
    mgf.record_position_close("龙虾/USDT", "SHORT", STRUCTURE_REASON["SHORT"])
    m = reverse_snapshot("龙虾/USDT", "LONG", middle_prev=100.45 - mgf.REVERSE_KC_FLAT_ATR)
    assert MasterGateFunnel.is_reverse_after_structure_exit(m, "LONG")
    m = reverse_snapshot("龙虾/USDT", "LONG", middle_prev=100.45 - mgf.REVERSE_KC_FLAT_ATR + 1e-6)
    assert not MasterGateFunnel.is_reverse_after_structure_exit(m, "LONG")


def test_window_is_close_bar_plus_three_bars():
    closed_at = 960_020.0  # 20 s into the minute starting at 960_000
    mgf.record_position_close("1000PEPE/USDT", "SHORT", STRUCTURE_REASON["SHORT"], closed_at)
    assert mgf.reverse_window_side("1000PEPE/USDT", closed_at) == "LONG"
    assert mgf.reverse_window_side("1000PEPE/USDT", 960_239.9) == "LONG"
    assert mgf.reverse_window_side("1000PEPE/USDT", 960_240.0) is None
    assert mgf.reverse_window_side("1000PEPE/USDT", closed_at - 1) is None


def test_later_close_replaces_record():
    mgf.record_position_close("1000PEPE/USDT", "SHORT", STRUCTURE_REASON["SHORT"])
    mgf.record_position_close("1000PEPE/USDT", "LONG", "MANUAL")
    assert mgf.reverse_window_side("1000PEPE/USDT") is None


def test_alias_symbols_share_record():
    mgf.record_position_close("1000PEPEUSDT", "SHORT", STRUCTURE_REASON["SHORT"])
    assert mgf.reverse_window_side("1000PEPE/USDT") == "LONG"


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
@pytest.mark.parametrize("kwargs", [
    {"close": 100.4},           # below KC middle (LONG) / above (SHORT)
    {"close": 99.9},            # wrong candle colour
    {"ma5": 100.25},            # MA5 falling and below MA15
    {"ma5": 100.3},             # MA5 flat and level with MA15
    {"middle_prev": 100.42},    # KC middle moved only 0.03 ATR: flat
    {"middle_prev": 100.5},     # KC middle against the reverse side
    {"crosses": 2},             # MA5/MA15 chop in the last 8 bars
])
def test_reverse_needs_clear_trend_not_flat_or_chop(side, kwargs):
    mgf.record_position_close("龙虾/USDT", OPPOSITE[side], STRUCTURE_REASON[OPPOSITE[side]])
    assert not open_gate(side, reverse_snapshot("龙虾/USDT", side, **kwargs)).can_execute


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_anti_chase_still_applies(side):
    mgf.record_position_close("龙虾/USDT", OPPOSITE[side], STRUCTURE_REASON[OPPOSITE[side]])
    decision = open_gate(side, reverse_snapshot("龙虾/USDT", side, close=102.5))
    assert not decision.can_execute
