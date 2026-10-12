import pytest

from core.services.exits.peak_trailing_exit import (
    LIVE_FLASH_DUMP_TRIGGER,
    live_intraday_sell_pressure_trigger,
    live_ma5_reversal_exit,
)


def _position(side="LONG", opened_at=60.0, peak_price=100.0):
    return {
        "side": side,
        "entry_price": 100.0,
        "open_timestamp": opened_at,
        "entry_atr": 1.0,
    }, {"peak_price": peak_price}


def _snapshot(
    *,
    quote_ms=240_000.0,
    closed_bar_ms=180_000.0,
    closed_price=99.0,
    closed_ma5=100.0,
    quote=99.0,
    live_ma5=100.0,
    closed_bar_is_closed=True,
):
    return {
        "quote_ms": quote_ms,
        "live_bar_ms": quote_ms // 60_000 * 60_000,
        "closed_bar_ms": closed_bar_ms,
        "snapshot_bar_id": closed_bar_ms,
        "closed_ma5": closed_ma5,
        "live_ma5": live_ma5,
        "last_close": closed_price,
        "quote_price": quote,
        "closed_bar_is_closed": closed_bar_is_closed,
        "reason": None,
    }


@pytest.mark.parametrize(
    ("side", "closed_price", "closed_ma5"),
    [("LONG", 99.9, 100.0), ("SHORT", 100.1, 100.0)],
)
def test_initial_breathing_room_suppresses_closed_ma5_break(side, closed_price, closed_ma5):
    position, state = _position(side, opened_at=180.0)
    snapshot = _snapshot(
        quote_ms=240_000.0,
        closed_bar_ms=180_000.0,
        closed_price=closed_price,
        closed_ma5=closed_ma5,
        quote=closed_price,
    )

    assert live_ma5_reversal_exit(position, snapshot, 1 if side == "LONG" else -1, state, 1.0) is None


@pytest.mark.parametrize(
    ("side", "closed_price"),
    [("LONG", 99.9), ("SHORT", 100.1)],
)
def test_closed_bar_ma5_break_exits_after_both_grace_conditions(side, closed_price):
    position, state = _position(side, opened_at=60.0)
    snapshot = _snapshot(closed_price=closed_price, quote=closed_price)

    result = live_ma5_reversal_exit(
        position, snapshot, 1 if side == "LONG" else -1, state, 1.0,
    )

    assert result["trigger_type"] == "CLOSED_MA5_BREACH"


def test_live_ma5_recross_without_close_confirmation_does_not_exit():
    position, state = _position(opened_at=60.0)
    snapshot = _snapshot(
        closed_price=100.1,
        closed_ma5=100.0,
        quote=99.9,
        live_ma5=100.1,
    )

    assert live_ma5_reversal_exit(position, snapshot, 1, state, 1.0) is None


def test_half_atr_profit_arms_ma5_lock_without_live_close_authority():
    position, state = _position(opened_at=60.0, peak_price=100.6)
    snapshot = _snapshot(
        closed_price=100.1,
        closed_ma5=100.0,
        quote=100.2,
        live_ma5=100.3,
    )

    result = live_ma5_reversal_exit(position, snapshot, 1, state, 1.0)

    assert state["ma5_profit_lock_armed"] is True
    assert result is None


def test_exact_half_atr_profit_does_not_arm_tight_live_ma5_exit():
    position, state = _position(opened_at=60.0, peak_price=100.5)
    snapshot = _snapshot(
        closed_price=100.1,
        closed_ma5=100.0,
        quote=100.2,
        live_ma5=100.3,
    )

    assert live_ma5_reversal_exit(position, snapshot, 1, state, 1.0) is None
    assert state["ma5_profit_lock_armed"] is False


def test_open_less_than_one_complete_post_entry_bar_has_no_ma5_close():
    position, state = _position(opened_at=301.0)
    snapshot = _snapshot(
        quote_ms=391_000.0,
        closed_bar_ms=300_000.0,
        closed_price=99.0,
        quote=99.0,
    )

    assert live_ma5_reversal_exit(position, snapshot, 1, state, 1.0) is None


def test_ma5_exit_requires_authoritatively_closed_bar_flag():
    position, state = _position(opened_at=60.0)
    snapshot = _snapshot(closed_bar_is_closed=False)

    assert live_ma5_reversal_exit(position, snapshot, 1, state, 1.0) is None


def test_strong_positive_ma5_slope_shields_long_from_soft_close():
    position, state = _position(opened_at=60.0)
    snapshot = _snapshot(closed_price=99.9, closed_ma5=100.0, live_ma5=99.85)
    snapshot["ma5_history"] = [99.7, 99.8, 99.9]

    assert live_ma5_reversal_exit(position, snapshot, 1, state, 1.0) is None


def test_armed_profit_lock_can_close_on_confirmed_ma5_break_despite_slope():
    position, state = _position(opened_at=60.0, peak_price=100.6)
    snapshot = _snapshot(closed_price=99.9, closed_ma5=100.0)
    snapshot["ma5_history"] = [99.7, 99.8, 99.9]

    result = live_ma5_reversal_exit(position, snapshot, 1, state, 1.0)

    assert result["trigger_type"] == "CLOSED_MA5_PROFIT_LOCK"


@pytest.mark.parametrize(
    ("price", "high", "expected"),
    [
        (99.1, 100.0, LIVE_FLASH_DUMP_TRIGGER),  # 0.9 ATR adverse body
        (99.8, 100.5, LIVE_FLASH_DUMP_TRIGGER),  # high retracement > 1.5 x body
        (99.7, 100.1, None),  # ordinary small pullback
        (100.0, 100.5, None),  # zero body is not an extreme wick
    ],
)
def test_intrabar_long_exit_is_limited_to_large_sell_pressure(price, high, expected):
    result = live_intraday_sell_pressure_trigger(
        {"side": "LONG"},
        price,
        {"atr": 1.0, "live_open": 100.0, "live_high": high},
    )

    assert result == expected
