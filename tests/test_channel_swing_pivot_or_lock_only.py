import pytest

import core.gates.holding_protection_gate as holding_gate
from core.gates.holding_protection_gate import HoldingProtectionExitGate
from core.gates.unified_pipeline_gates import ExitGate


@pytest.mark.parametrize("reason", [
    "EXIT_BY_MEME_ATR_TRAILING",
    "EXIT_BY_MEME_TREND_DEAD",
    "LONG_EXIT_MA15_BREAK_AND_MA5_DECAY",
    "EXIT_BY_PULLBACK",
])
def test_channel_swing_rejects_non_pivot_non_lock_exit_labels(reason):
    position = {
        "symbol": "龍蝦/USDT",
        "side": "LONG",
        "entry_mode": "CHANNEL_SWING",
        "entry_price": 100.0,
    }

    allowed, veto, _ = HoldingProtectionExitGate.validate_exit(
        position, frame=None, quote=101.0, candidate_reason=reason,
    )

    assert not allowed
    assert veto == "VETO_HOLDING_UNTIL_TRUE_PIVOT"


def test_channel_swing_allows_exit_when_true_pivot_is_confirmed(monkeypatch):
    monkeypatch.setattr(
        holding_gate, "has_confirmed_true_pivot",
        lambda *_args, **_kwargs: (True, "PEAK", {"pivot_bar": 123}),
    )
    position = {
        "symbol": "龍蝦/USDT",
        "side": "LONG",
        "entry_mode": "CHANNEL_SWING",
        "entry_price": 100.0,
    }

    allowed, reason, _ = HoldingProtectionExitGate.validate_exit(
        position, frame=None, quote=101.0,
        candidate_reason="EXIT_BY_MEME_CLIMAX_TOP",
    )

    assert allowed
    assert reason == "EXIT_BY_MEME_CLIMAX_TOP"


def test_channel_swing_allows_only_verified_profit_lock(monkeypatch):
    monkeypatch.setattr(
        holding_gate, "_verified_profit_lock_details",
        lambda *_args, **_kwargs: {
            "lock_type": "NET_ROE",
            "floor_net_roe_pct": 5.0,
            "current_net_roe_pct": 4.9,
        },
    )
    position = {
        "symbol": "龍蝦/USDT",
        "side": "LONG",
        "entry_mode": "CHANNEL_SWING",
        "entry_price": 100.0,
    }

    allowed, reason, _ = HoldingProtectionExitGate.validate_exit(
        position, frame=None, quote=101.0,
        candidate_reason="EXIT_BY_RATCHET_PROFIT_LOCK",
    )

    assert allowed
    assert reason == "EXIT_BY_RATCHET_PROFIT_LOCK"


@pytest.mark.parametrize("reason", [
    "EXIT_BY_TREND_BROKEN",
    "EXIT_BY_KC_MID_STRUCTURE_BROKEN",
    "LONG_EXIT_MA15_BREAK_AND_MA5_DECAY",
    "EXIT_BY_MEME_ATR_TRAILING",
])
def test_exit_gate_suppresses_ordinary_exit_until_pivot_or_lock(reason):
    result = ExitGate._gate_unconfirmed_exit((True, reason, {"source": "test"}))

    assert result[0] is False
    assert result[1] == ExitGate.GENERAL_EXIT_VETO_REASON
    assert result[2]["suppressed_exit_reason"] == reason


def test_exit_gate_evaluate_suppresses_ma_break_exit(monkeypatch):
    monkeypatch.setattr(
        ExitGate, "_evaluate_core",
        staticmethod(lambda *_args, **_kwargs: (
            True, "LONG_EXIT_MA15_BREAK_AND_MA5_DECAY", {"source": "core"},
        )),
    )
    position = {
        "side": "LONG", "entry_price": 95.0, "highest_price": 100.0,
        "open_timestamp": 1_000.0,
    }
    market = {
        "raw_df": None,
        "current_price": 99.0,
        "atr": 1.0,
        "kc_middle": 100.0,
        "kc_middle_slope": 0.0,
        "macro_trend": "BULLISH",
        "ma3": 100.0,
        "prev_ma3": 101.0,
        "ma15": 100.0,
    }

    should_exit, reason, meta = ExitGate.evaluate(position, market)

    assert not should_exit
    assert reason == ExitGate.GENERAL_EXIT_VETO_REASON
    assert meta["suppressed_exit_reason"] == "LONG_EXIT_MA15_BREAK_AND_MA5_DECAY"


@pytest.mark.parametrize("reason", [
    "CIRCUIT_BREAKER_HARD_SL",
    "EXIT_BY_RATCHET_PROFIT_LOCK",
    "NET_ROE_THREE_TIER_GIVEBACK",
    "HYBRID_DYNAMIC_RATCHET_GIVEBACK",
    "EARLY_PIVOT_PEAK_EXIT",
    "CLOSE_SHORT_AT_BOTTOM",
])
def test_exit_gate_preserves_safety_lock_and_confirmed_pivot_exits(reason):
    result = (True, reason, {"source": "test"})

    assert ExitGate._gate_unconfirmed_exit(result) == result
