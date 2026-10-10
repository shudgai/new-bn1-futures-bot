import pytest

from core.services.exits.peak_trailing_exit import (
    DOJI_RULE_VERSION,
    STATE_KEY,
    confirmed_doji_reversal,
    evaluate_peak_trailing,
    migrate_peak_state,
)


def position(side="LONG"):
    return {
        "symbol": "CAP/USDT",
        "side": side,
        "entry_price": 100.0,
        "qty": 1.0,
        "open_timestamp": 60.0,
        "entry_mode": "CHANNEL_SWING",
    }


def snapshot(side="LONG", *, reversal_close=99.4, reversal_high=100.1,
             reversal_low=99.3, ma5=99.7, kc_upper=103.0,
             kc_lower=97.0):
    opening = 100.0
    return {
        "quote_ms": 240_000.0,
        "snapshot_bar_id": 180_000.0,
        "history_5": [
            {
                "ms": 120_000.0, "o": 100.0, "h": 101.0, "l": 99.0,
                "c": 100.0, "ma5": 100.0, "atr": 1.0,
                "kc_upper": 103.0, "kc_lower": 97.0,
            },
            {
                "ms": 180_000.0, "o": opening, "h": reversal_high,
                "l": reversal_low, "c": reversal_close, "ma5": ma5,
                "atr": 1.0, "kc_upper": kc_upper, "kc_lower": kc_lower,
            },
        ],
    }


def test_weak_second_candle_does_not_confirm_doji_reversal():
    current = snapshot(
        reversal_close=99.9, reversal_high=100.05, reversal_low=99.85,
        ma5=100.0,
    )

    assert confirmed_doji_reversal(position(), current) is None


@pytest.mark.parametrize(
    ("reversal_high", "reversal_low", "reversal_close"),
    [(100.1, 99.3, 99.4), (100.1, 99.5, 99.6)],
)
def test_strong_reversal_body_confirms_by_atr_or_body_ratio(
    reversal_high, reversal_low, reversal_close,
):
    current = snapshot(
        reversal_close=reversal_close,
        reversal_high=reversal_high,
        reversal_low=reversal_low,
        ma5=99.7,
    )

    assert confirmed_doji_reversal(position(), current) == (
        "EXIT_DOJI_BEARISH_CONFIRMATION"
    )


def test_close_above_kc_upper_blocks_doji_reversal_exit():
    current = snapshot(
        reversal_close=99.4, reversal_high=100.1, reversal_low=99.3,
        ma5=99.7, kc_upper=99.3,
    )

    assert confirmed_doji_reversal(position(), current) is None


def test_close_above_kc_lower_blocks_mirrored_short_doji_exit():
    current = snapshot(
        "SHORT", reversal_close=100.6, reversal_high=100.7,
        reversal_low=99.9, ma5=100.3, kc_lower=100.7,
    )

    assert confirmed_doji_reversal(position("SHORT"), current) is None


def test_old_pending_doji_exit_is_revoked_under_stricter_rule():
    current_position = position()
    current_position["peak_trailing_state"] = {
        "policy": "confirmed_pivot_abnormal_only_v3",
        "identity": ["LONG", 60.0, 100.0, 1.0],
        "pending": "EXIT_ADVERSE_ABNORMAL_BODY",
        "trigger": "EXIT_DOJI_BEARISH_CONFIRMATION",
        "doji_rule_version": DOJI_RULE_VERSION - 1,
        "peak_price": 100.0,
    }

    state = migrate_peak_state(current_position)

    assert "pending" not in state
    assert "trigger" not in state


def test_armed_net_roe_lock_suppresses_valid_doji_morphology_exit():
    current_position = position()
    current_position.update(
        qty=10.0,
        margin=1000.0,
        leverage=1.0,
        entry_atr=1.0,
    )
    assert evaluate_peak_trailing(
        current_position, 108.12, {"quote_ms": 180_000.0},
    ) is None
    current = snapshot(
        reversal_close=105.7, reversal_high=106.1, reversal_low=105.3,
        ma5=105.8, kc_upper=106.5, kc_lower=104.5,
    )
    current["quote_ms"] = 240_001.0
    current.update(
        live_bar_ms=240_000.0,
        closed_bar_ms=180_000.0,
        live_open=105.7,
        live_high=105.8,
        live_low=105.6,
        atr=1.0,
        kc_upper=106.5,
        kc_middle=105.5,
        kc_lower=104.5,
        live_kc_upper=106.5,
        live_kc_lower=104.5,
    )

    result = evaluate_peak_trailing(current_position, 105.7, current)

    assert current_position[STATE_KEY]["net_roe_lock_armed"] is True
    assert result is None
