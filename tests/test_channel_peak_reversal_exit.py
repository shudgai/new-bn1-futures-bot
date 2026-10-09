import pytest

from core.services.exits.peak_trailing_exit import (
    PEAK_REASON,
    evaluate_peak_trailing,
)


def position(side):
    return dict(
        side=side,
        entry_price=100.,
        qty=1.,
        open_timestamp=60.,
        entry_mode="CHANNEL_SWING",
        entry_atr=1.,
    )


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_channel_peak_reversal_closes_on_live_half_atr_pullback(side):
    sign = 1 if side == "LONG" else -1
    held = position(side)

    assert evaluate_peak_trailing(
        held, 100. + sign * 1.0, 61_000, fee=0., slippage=0.
    ) is None
    assert evaluate_peak_trailing(
        held, 100. + sign * 2.0, 62_000, fee=0., slippage=0.
    ) is None

    result = evaluate_peak_trailing(
        held, 100. + sign * 1.5, 63_000, fee=0., slippage=0.
    )

    assert result["reason"] == PEAK_REASON
    assert result["trigger"] == "CHANNEL_PEAK_PULLBACK_REVERSAL"
    assert held["peak_trailing_state"]["pending"] == PEAK_REASON


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_peak_reversal_close_retries_after_failed_order(side):
    sign = 1 if side == "LONG" else -1
    held = position(side)
    evaluate_peak_trailing(held, 100. + sign * 2.0, 61_000, fee=0., slippage=0.)
    first = evaluate_peak_trailing(
        held, 100. + sign * 1.5, 62_000, fee=0., slippage=0.
    )

    retry = evaluate_peak_trailing(
        held, 100. + sign * 1.6, 63_000, fee=0., slippage=0.
    )

    assert first["trigger"] == "CHANNEL_PEAK_PULLBACK_REVERSAL"
    assert retry["reason"] == PEAK_REASON
    assert retry["trigger"] == "CHANNEL_PEAK_PULLBACK_REVERSAL"


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_subthreshold_pullback_keeps_position_open(side):
    sign = 1 if side == "LONG" else -1
    held = position(side)
    evaluate_peak_trailing(held, 100. + sign * 2.0, 61_000, fee=0., slippage=0.)

    result = evaluate_peak_trailing(
        held, 100. + sign * 1.51, 62_000, fee=0., slippage=0.
    )

    assert result is None
