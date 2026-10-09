import pytest

from core.services.exits.peak_trailing_exit import (
    ABNORMAL_REASON,
    evaluate_peak_trailing,
)
from core.services.exits.profit_exit_telemetry import ProfitExitTelemetry


@pytest.fixture(autouse=True)
def disable_exit_telemetry(monkeypatch):
    monkeypatch.setattr(ProfitExitTelemetry, "ENABLED", False)


def pivot_history(side):
    rows = [
        dict(ms=120_000., o=100., h=104., l=98., c=102., ma5=98.),
        dict(ms=180_000., o=102., h=110., l=99., c=108., ma5=99.),
        dict(ms=240_000., o=108., h=109., l=100., c=107., ma5=98.5),
    ]
    if side == "SHORT":
        return [
            dict(ms=row["ms"], o=200. - row["o"], h=200. - row["l"],
                 l=200. - row["h"], c=200. - row["c"],
                 ma5=201. - row["ma5"])
            for row in rows
        ]
    return rows


def position(side, symbol=None):
    result = dict(
        side=side,
        entry_price=100.,
        qty=1.,
        open_timestamp=60.,
        entry_mode="CHANNEL_SWING",
        leverage=1.,
    )
    if symbol is not None:
        result["symbol"] = symbol
    return result


def sui_pivot_history(side):
    bars = [
        dict(ms=float(index * 60_000), o=100., h=101., l=99., c=100.,
             ma5=100., volume=500., kc_upper=101., kc_middle=100., kc_lower=99.)
        for index in range(1, 19)
    ]
    tail = pivot_history(side)
    for index, row in enumerate(tail, start=19):
        row["ms"] = float(index * 60_000)
        row["volume"] = float((22 - index) * 100)
        row.update(kc_upper=100.7, kc_middle=100., kc_lower=99.3)
    return bars + tail


def snapshot(side, bars=None, **overrides):
    rows = pivot_history(side) if bars is None else bars
    result = dict(
        quote_ms=300_000.,
        snapshot_bar_id=rows[-1]["ms"],
        reason=None,
        history_outer_pivots=rows,
    )
    result.update(overrides)
    return result


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_position_exits_on_latest_confirmed_three_point_pivot(side):
    position_data = position(side)
    result = evaluate_peak_trailing(
        position_data, 101., snapshot(side), fee=0., slippage=0.
    )

    assert result is not None
    assert result["type"] == ABNORMAL_REASON
    assert result["trigger"] == "THREE_POINT_PIVOT"
    assert position_data["peak_trailing_state"]["pending"] == ABNORMAL_REASON

    retry = evaluate_peak_trailing(
        position_data, 101., {"quote_ms": 360_000.}, fee=0., slippage=0.
    )
    assert retry["trigger"] == "THREE_POINT_PIVOT"


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_sui_three_point_pivot_requires_ma5_to_reverse_at_pivot(side):
    bars = sui_pivot_history(side)
    result = evaluate_peak_trailing(
        position(side, symbol="SUI/USDT"),
        101.,
        snapshot(side, bars, quote_ms=1_320_000.),
        fee=0.,
        slippage=0.,
    )

    assert result is not None
    assert result["trigger"] == "THREE_POINT_PIVOT"


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_sui_three_point_pivot_waits_when_ma5_does_not_reverse(side):
    bars = sui_pivot_history(side)
    bars[-1]["ma5"] = bars[-2]["ma5"] + (-1. if side == "SHORT" else 1.)
    position_data = position(side, symbol="SUI/USDT")
    result = evaluate_peak_trailing(
        position_data,
        101.,
        snapshot(side, bars, quote_ms=1_320_000.),
        fee=0.,
        slippage=0.,
    )

    assert result is None
    assert not position_data.get("peak_trailing_state", {}).get("pending")


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_sui_three_point_pivot_requires_21_valid_bars(side):
    bars = sui_pivot_history(side)[1:]
    result = evaluate_peak_trailing(
        position(side, symbol="SUI/USDT"),
        101.,
        snapshot(side, bars, quote_ms=1_320_000.),
        fee=0.,
        slippage=0.,
    )

    assert result is None


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
@pytest.mark.parametrize("blocker", ["wide_channel", "nondeclining_volume"])
def test_sui_three_point_pivot_waits_for_narrow_channel_and_falling_volume(
    side, blocker
):
    bars = sui_pivot_history(side)
    if blocker == "wide_channel":
        bars[-1]["kc_upper"] = 101.
        bars[-1]["kc_lower"] = 99.
    else:
        bars[-1]["volume"] = bars[-2]["volume"]
    result = evaluate_peak_trailing(
        position(side, symbol="SUI/USDT"),
        101.,
        snapshot(side, bars, quote_ms=1_320_000.),
        fee=0.,
        slippage=0.,
    )

    assert result is None


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_confirmed_pivot_is_not_vetoed_by_continuing_ma_trend(side):
    result = evaluate_peak_trailing(
        position(side),
        101.,
        snapshot(
            side,
            ma5_history=[97., 98., 99.] if side == "LONG" else [103., 102., 101.],
            ma15_history=[95., 96., 97.] if side == "LONG" else [105., 104., 103.],
        ),
        fee=0.,
        slippage=0.,
    )

    assert result is not None
    assert result["trigger"] == "THREE_POINT_PIVOT"


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_three_point_pivot_does_not_require_kc_outer_rail(side):
    bars = pivot_history(side)
    bars[1].update(kc_upper=bars[1]["h"] + 1., kc_lower=bars[1]["l"] - 1.)

    result = evaluate_peak_trailing(
        position(side), 101., snapshot(side, bars), fee=0., slippage=0.
    )

    assert result is not None
    assert result["trigger"] == "THREE_POINT_PIVOT"


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_old_pivot_is_not_replayed_after_a_newer_candle(side):
    bars = pivot_history(side)
    latest = dict(ms=300_000., o=102., h=109., l=101., c=106.)
    if side == "SHORT":
        latest = dict(ms=300_000., o=98., h=99., l=91., c=94.)
    bars.append(latest)

    result = evaluate_peak_trailing(
        position(side), 101., snapshot(side, bars, quote_ms=360_000.),
        fee=0., slippage=0.,
    )

    assert result is None


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
@pytest.mark.parametrize("reason", ["STALE_SNAPSHOT", "NO_DATA"])
def test_invalid_or_stale_snapshot_cannot_create_a_new_pivot_exit(side, reason):
    result = evaluate_peak_trailing(
        position(side), 101., snapshot(side, reason=reason), fee=0., slippage=0.
    )

    assert result is None


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_invalid_ohlc_cannot_create_a_pivot_exit(side):
    bars = pivot_history(side)
    bars[1]["h"] = bars[1]["o"] - 1.

    result = evaluate_peak_trailing(
        position(side), 101., snapshot(side, bars), fee=0., slippage=0.
    )

    assert result is None
