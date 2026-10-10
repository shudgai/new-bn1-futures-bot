import pytest

from core.services.exits.peak_trailing_exit import (
    CONTINUATION_FAILED_TRIGGER,
    continuation_failed_exit,
    evaluate_peak_trailing,
)


def make_position(side):
    entry_bar_id = 180_000.0
    entry_low, entry_high = (100.0, 101.0) if side == "LONG" else (99.0, 100.0)
    return {
        "symbol": "CAP/USDT",
        "side": side,
        "entry_price": 100.5 if side == "LONG" else 99.5,
        "qty": 1.0,
        "margin": 10.0,
        "leverage": 10.0,
        "open_timestamp": 180.01,
        "entry_mode": "CHANNEL_SWING",
        "entry_snapshot": {
            "signal_code": "TRIGGER_C_CONTINUATION",
            "continuation_entry_bar_id": entry_bar_id,
            "continuation_entry_bar_low": entry_low,
            "continuation_entry_bar_high": entry_high,
        },
    }


def make_snapshot(side, *, close, ma5, candle_ms=240_000.0):
    opening = 100.5 if side == "LONG" else 99.5
    high = max(opening, close) + 0.1
    low = min(opening, close) - 0.1
    return {
        "quote_ms": candle_ms + 60_000.0,
        "snapshot_bar_id": candle_ms,
        "live_bar_id": candle_ms + 60_000.0,
        "closed_bar_ms": candle_ms,
        "live_bar_ms": candle_ms + 60_000.0,
        "live_open": close,
        "atr": 1.0,
        "kc_upper": 110.0,
        "kc_middle": 100.0,
        "kc_lower": 90.0,
        "ma5": ma5,
        "history_5": [
            {
                "ms": 180_000.0, "o": opening, "h": max(opening, high),
                "l": min(opening, low), "c": opening, "ma5": opening,
                "kc_upper": 110.0, "kc_lower": 90.0, "kc_middle": 100.0,
            },
            {
                "ms": candle_ms, "o": opening, "h": high, "l": low,
                "c": close, "ma5": ma5, "kc_upper": 110.0,
                "kc_lower": 90.0, "kc_middle": 100.0,
            },
        ],
    }


@pytest.mark.parametrize(
    ("side", "close", "ma5"),
    [("LONG", 100.2, 100.3), ("LONG", 99.8, 99.7),
     ("SHORT", 99.8, 99.7), ("SHORT", 100.2, 100.3)],
)
def test_next_closed_candle_failure_is_symmetric(side, close, ma5):
    evidence = continuation_failed_exit(
        make_position(side), make_snapshot(side, close=close, ma5=ma5),
    )

    assert evidence is not None
    assert evidence["trigger_bar_ms"] == 240_000.0
    assert evidence["continuation_failure_close"] == close


@pytest.mark.parametrize(
    ("side", "close", "ma5"),
    [("LONG", 100.5, 100.0), ("SHORT", 99.5, 100.0)],
)
def test_next_closed_candle_without_failure_keeps_position(side, close, ma5):
    assert continuation_failed_exit(
        make_position(side), make_snapshot(side, close=close, ma5=ma5),
    ) is None


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_fast_failure_exit_survives_channel_outer_hold(side):
    position = make_position(side)
    candle = make_snapshot(
        side, close=99.8 if side == "LONG" else 100.2, ma5=100.0,
    )
    candle["history_5"][-1].update(
        kc_upper=99.7 if side == "LONG" else 110.0,
        kc_lower=90.0 if side == "LONG" else 100.3,
    )

    result = evaluate_peak_trailing(
        position, candle["history_5"][-1]["c"], candle, fee=0.0, slippage=0.0,
    )

    assert result is not None
    assert result["trigger"] == CONTINUATION_FAILED_TRIGGER
    assert position["peak_trailing_state"]["pending"] == result["type"]


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_fast_failure_requires_exactly_the_next_closed_candle(side):
    snapshot = make_snapshot(
        side, close=99.8 if side == "LONG" else 100.2, ma5=100.0,
        candle_ms=300_000.0,
    )

    assert continuation_failed_exit(make_position(side), snapshot) is None


def test_fast_failure_requires_matching_continuation_entry_provenance():
    position = make_position("LONG")
    position["entry_snapshot"]["signal_code"] = "TRIGGER_A_KC_BREAKOUT"

    assert continuation_failed_exit(
        position, make_snapshot("LONG", close=99.8, ma5=100.0),
    ) is None
