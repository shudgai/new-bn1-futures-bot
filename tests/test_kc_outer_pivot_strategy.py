import pytest

from core.services.exits.peak_trailing_exit import (
    ABNORMAL_REASON,
    evaluate_peak_trailing,
    three_point_pivot_exit,
)
from core.services.exits.profit_exit_telemetry import ProfitExitTelemetry


@pytest.fixture(autouse=True)
def disable_exit_telemetry(monkeypatch):
    monkeypatch.setattr(ProfitExitTelemetry, "ENABLED", False)


def pivot_history(side):
    rows = [
        dict(ms=120_000., o=100., h=104., l=98., c=102., ma5=98.,
             atr=1., kc_lower=90., kc_middle=100., kc_upper=110.),
        dict(ms=180_000., o=102., h=110., l=99., c=108., ma5=99.,
             atr=1., kc_lower=91., kc_middle=101., kc_upper=111.),
        dict(ms=240_000., o=108., h=109., l=100., c=107., ma5=98.5,
             atr=1., kc_lower=92., kc_middle=102., kc_upper=112.),
    ]
    if side == "SHORT":
        return [
            dict(ms=row["ms"], o=200. - row["o"], h=200. - row["l"],
                 l=200. - row["h"], c=200. - row["c"],
                 ma5=201. - row["ma5"], atr=row["atr"],
                 kc_lower=200. - row["kc_upper"],
                 kc_middle=200. - row["kc_middle"],
                 kc_upper=200. - row["kc_lower"])
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


def snapshot(side, bars=None, **overrides):
    rows = pivot_history(side) if bars is None else bars
    result = dict(
        quote_ms=300_000.,
        snapshot_bar_id=rows[-1]["ms"],
        reason=None,
        history_outer_pivots=rows,
        atr=1.,
        ma5_history=[97., 98., 99.] if side == "LONG" else [103., 102., 101.],
        ma15_history=[95., 96., 97.] if side == "LONG" else [105., 104., 103.],
    )
    result.update(overrides)
    return result


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_channel_position_holds_on_confirmed_pivot_and_later_quote(side):
    position_data = position(side)
    result = evaluate_peak_trailing(
        position_data, 101., snapshot(side), fee=0., slippage=0.
    )

    assert result is None
    assert not position_data['peak_trailing_state'].get('pending')
    retry = evaluate_peak_trailing(
        position_data, 101., {'quote_ms': 360_000.}, fee=0., slippage=0.
    )
    assert retry is None


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
@pytest.mark.parametrize("symbol", ["SUI/USDT", "龙虾/USDT"])
def test_sui_and_lobster_hold_instead_of_pivot_pullback_close(side, symbol):
    position_data = position(side, symbol=symbol)
    result = evaluate_peak_trailing(
        position_data, 101., snapshot(side), fee=0., slippage=0.
    )

    assert result is None


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
@pytest.mark.parametrize("symbol", ["SUI/USDT", "龙虾/USDT"])
def test_sui_and_lobster_reject_entry_bar_pivot_even_after_confirmation(side, symbol):
    position_data = position(side, symbol=symbol)
    position_data["open_timestamp"] = 195.
    result = three_point_pivot_exit(position_data, snapshot(side))

    assert result is None


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
@pytest.mark.parametrize("symbol", ["SUI/USDT", "龙虾/USDT"])
def test_sui_and_lobster_do_not_replay_entry_bar_pivot_after_next_candle(side, symbol):
    position_data = position(side, symbol=symbol)
    position_data["open_timestamp"] = 195.
    bars = pivot_history(side) + [
        dict(ms=300_000., o=107., h=108., l=101., c=106., ma5=99.)
        if side == "LONG" else
        dict(ms=300_000., o=93., h=99., l=92., c=94., ma5=101.)
    ]

    result = three_point_pivot_exit(
        position_data, snapshot(side, bars, quote_ms=360_000.)
    )

    assert result is None


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
@pytest.mark.parametrize("symbol", ["SUI/USDT", "龙虾/USDT"])
def test_sui_and_lobster_do_not_recover_pre_entry_pivot_after_restart(side, symbol):
    position_data = position(side, symbol=symbol)
    position_data["open_timestamp"] = 195.
    bars = pivot_history(side)
    for index, ms in enumerate(range(300_000, 900_000, 60_000)):
        if side == "LONG":
            high = 108. - index
            bars.append(dict(ms=float(ms), o=high - 2., h=high, l=high - 8.,
                             c=high - 1., atr=1., kc_lower=92. + index,
                             kc_middle=102. + index, kc_upper=112. + index))
        else:
            low = 92. + index
            bars.append(dict(ms=float(ms), o=low + 2., h=low + 7., l=low,
                             c=low + 3., atr=1., kc_lower=88. - index,
                             kc_middle=98. - index, kc_upper=108. - index))

    result = three_point_pivot_exit(
        position_data, snapshot(side, bars, quote_ms=900_000.)
    )

    assert result is None


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_other_symbols_do_not_reuse_a_pivot_formed_before_entry(side):
    position_data = position(side)
    position_data["open_timestamp"] = 195.
    result = three_point_pivot_exit(position_data, snapshot(side))

    assert result is None


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_confirmed_pivot_is_not_a_channel_exit_even_with_confirmed_trend(side):
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

    assert result is None


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_channel_holds_on_pivot_regardless_of_outer_rail(side):
    bars = pivot_history(side)
    bars[1].update(kc_upper=bars[1]["h"] + 1., kc_lower=bars[1]["l"] - 1.)

    result = evaluate_peak_trailing(
        position(side), 101., snapshot(side, bars), fee=0., slippage=0.
    )

    assert result is None


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_confirmed_pivot_waits_when_ma_or_ck_direction_is_unclear(side):
    data = snapshot(side, ma5_history=[100., 100., 100.],
                    ma15_history=[100., 100., 100.])

    result = evaluate_peak_trailing(
        position(side), 101., data, fee=0., slippage=0.
    )

    assert result is None


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_pivot_does_not_exit_before_confirmation_candle_is_closed(side):
    data = snapshot(side, snapshot_bar_id=180_000.)

    result = evaluate_peak_trailing(
        position(side), 101., data, fee=0., slippage=0.
    )

    assert result is None


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_two_consecutive_closed_adverse_abnormal_bodies_exit(side):
    bars = [
        dict(ms=120_000., o=100., h=101., l=99., c=100., atr=1.,
             kc_lower=90., kc_middle=100., kc_upper=110.),
        dict(ms=180_000., o=100., h=101., l=99., c=100., atr=1.,
             kc_lower=90., kc_middle=100., kc_upper=110.),
        dict(ms=240_000., o=100., h=101., l=99., c=100., atr=1.,
             kc_lower=90., kc_middle=100., kc_upper=110.),
    ]
    if side == "LONG":
        bars[1].update(o=100., c=99.4, l=99.4)
        bars[2].update(o=99.4, c=98.8, l=98.8)
    else:
        bars[1].update(o=100., c=100.6, h=100.6)
        bars[2].update(o=100.6, c=101.2, h=101.2)
    data = snapshot(side, bars, quote_ms=300_000.)

    result = evaluate_peak_trailing(
        position(side), 99. if side == "LONG" else 101.,
        data, fee=0., slippage=0.,
    )

    assert result is not None
    assert result["trigger"] == "TWO_CLOSED_ADVERSE_ABNORMAL"


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_one_closed_abnormal_body_alone_does_not_exit(side):
    bars = [
        dict(ms=120_000., o=100., h=101., l=99., c=100., atr=1.,
             kc_lower=90., kc_middle=100., kc_upper=110.),
        dict(ms=180_000., o=100., h=101., l=99., c=100., atr=1.,
             kc_lower=90., kc_middle=100., kc_upper=110.),
        dict(ms=240_000., o=100., h=101., l=99., c=100., atr=1.,
             kc_lower=90., kc_middle=100., kc_upper=110.),
    ]
    if side == "LONG":
        bars[1].update(o=100., c=99.4, l=99.4)
    else:
        bars[1].update(o=100., c=100.6, h=100.6)

    result = evaluate_peak_trailing(
        position(side), 100., snapshot(side, bars, quote_ms=300_000.),
        fee=0., slippage=0.,
    )

    assert result is None


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


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
@pytest.mark.parametrize("symbol", ["SUI/USDT", "龙虾/USDT"])
def test_entry_bar_pivot_cannot_exit_after_minimum_hold_expires(side, symbol):
    position_data = position(side, symbol=symbol)
    position_data["open_timestamp"] = 195.
    result = evaluate_peak_trailing(
        position_data, 101., snapshot(side, quote_ms=330_000.),
        fee=0., slippage=0.,
    )
    assert result is None
    assert not position_data["peak_trailing_state"].get("pending")
