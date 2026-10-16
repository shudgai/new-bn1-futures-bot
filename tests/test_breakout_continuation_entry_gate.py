import asyncio

import pytest

from core.gates.breakout_continuation_entry_gate import BreakoutContinuationEntryGate
from core.services.gates.entry_gate import BreakoutContinuationEntryGate as ExportedGate
from core.services.entry_firewall import validate_account_entry
from core.services.strategies.wild_swing_strategy import WildSwingStrategy
from test_wild_swing_strategy import _frame


def test_gate_is_exported_from_service_gate_module():
    assert BreakoutContinuationEntryGate is ExportedGate


def _candle(open_price, close, high=None, low=None):
    return {
        "open": open_price,
        "high": high if high is not None else max(open_price, close) + 0.05,
        "low": low if low is not None else min(open_price, close) - 0.05,
        "close": close,
    }


@pytest.mark.parametrize(
    "side,rows,upper,lower,basis,expected_type",
    [
        (
            "LONG",
            [
                _candle(99.8, 99.9),
                _candle(99.9, 99.8),
                _candle(99.8, 99.9),
                _candle(99.8, 101.0, 101.1, 99.7),
            ],
            100.0, 90.0, 95.0, "CHANNEL_CONSOLIDATION_BREAKOUT",
        ),
        (
            "SHORT",
            [
                _candle(100.2, 100.1),
                _candle(100.1, 100.2),
                _candle(100.2, 100.1),
                _candle(100.2, 99.0, 100.3, 98.9),
            ],
            110.0, 100.0, 105.0, "CHANNEL_CONSOLIDATION_BREAKOUT",
        ),
        (
            "LONG",
            [
                _candle(100.0, 100.1),
                _candle(100.9, 101.0, 101.1, 100.8),
                _candle(101.9, 102.0, 102.2, 101.8),
                _candle(103.6, 104.5, 104.6, 103.5),
            ],
            104.0, 90.0, 100.0, "OUTSIDE_CHANNEL_CONTINUATION",
        ),
        (
            "SHORT",
            [
                _candle(100.0, 99.9),
                _candle(99.1, 99.0, 99.2, 98.9),
                _candle(98.1, 98.0, 98.2, 97.8),
                _candle(95.8, 95.0, 95.9, 94.9),
            ],
            110.0, 96.0, 100.0, "OUTSIDE_CHANNEL_CONTINUATION",
        ),
    ],
)
def test_gate_detects_two_patterns_for_both_sides(
    side, rows, upper, lower, basis, expected_type,
):
    klines = [_candle(100.0, 100.0)] + rows

    signal = BreakoutContinuationEntryGate.evaluate(
        klines,
        [upper] * len(klines),
        [lower] * len(klines),
        [basis] * len(klines),
    )

    assert signal is not None
    assert signal["action"] == f"OPEN_{side}"
    assert signal["type"] == expected_type


def test_gate_fails_closed_for_positions_and_invalid_data():
    klines = [_candle(99.8, 99.9)] * 5
    rails = [110.0] * 5
    floors = [90.0] * 5
    basis = [100.0] * 5

    assert BreakoutContinuationEntryGate.evaluate(
        klines, rails, floors, basis, has_position=True,
    ) is None
    assert BreakoutContinuationEntryGate.evaluate(
        klines, rails[:-1], floors, basis,
    ) is None
    assert BreakoutContinuationEntryGate.evaluate(
        [*klines[:-1], {"open": 1, "high": 0, "low": 2, "close": 1}],
        rails, floors, basis,
    ) is None


def _outside_continuation_frame():
    frame = _frame("LONG")
    frame["atr"] = 5.0
    frame["open"] = 100.0
    frame["high"] = 100.2
    frame["low"] = 99.8
    frame["close"] = 100.0
    frame["kc_upper"] = 104.0
    frame["kc_middle"] = 100.0
    frame["kc_lower"] = 96.0
    frame["ma5"] = 100.2
    frame["ma15"] = 100.0

    frame.loc[8, ["open", "high", "low", "close"]] = [100.0, 100.1, 99.9, 100.1]
    frame.loc[9, ["open", "high", "low", "close"]] = [100.9, 101.1, 100.8, 101.0]
    frame.loc[10, ["open", "high", "low", "close"]] = [101.9, 102.2, 101.8, 102.0]
    frame.loc[11, ["open", "high", "low", "close"]] = [103.6, 104.6, 103.5, 104.5]
    frame.loc[10, "ma5"] = 100.3
    frame.loc[11, "ma5"] = 100.2
    frame.attrs["timeframe_ms"] = 60_000
    frame.attrs["entry_finality_verified"] = True
    return frame


def test_additional_closed_candle_candidate_reaches_existing_macro_gate():
    frame = _outside_continuation_frame()
    signal = WildSwingStrategy().evaluate_entry(
        "LOBSTER/USDT", frame, "LONG",
    )

    assert signal is not None
    assert signal["reason"] == "OUTSIDE_CHANNEL_CONTINUATION_ENTRY"
    assert signal["entry_pattern"] == "CONTINUATION"
    assert signal["structure_confirmation"] == "OUTSIDE_CHANNEL_CONTINUATION"
    assert signal["bar_id"] == float(frame.iloc[-1]["timestamp"])

    from core.gates.macro_candle_structure_gate import MacroCandleStructureGate

    current = frame.iloc[-1]
    allowed, reason = MacroCandleStructureGate().can_open(
        "LONG",
        {name: float(current[name]) for name in ("open", "high", "low", "close")},
        {
            name: float(current[name])
            for name in ("kc_upper", "kc_lower", "kc_middle", "ma15", "atr")
        },
        entry_pattern=signal["entry_pattern"],
        structure_confirmation=signal["structure_confirmation"],
        structure_confirmed=signal["structure_confirmed"],
        channel_expanding=signal["channel_expanding"],
    )

    assert allowed is True
    assert reason == "PASSED_MACRO_LONG_GATE"


def test_additional_candidate_is_revalidated_by_final_entry_firewall(monkeypatch):
    from core.vision import chart_guard

    async def approve(*args, **kwargs):
        return True, "approved for test"

    monkeypatch.setattr(chart_guard, "evaluate_qwen_entry_vision", approve)
    frame = _outside_continuation_frame()
    bar_id = float(frame.iloc[-1]["timestamp"])

    class Account:
        positions = {}
        closing_lock = set()
        pending_limit_orders = {}
        trades = []

        async def entry_frame_provider(self, symbol):
            assert symbol == "LOBSTER/USDT"
            return frame.copy()

    context = {
        "entry_mode": "WILD_SWING",
        "source": "WILD_SWING_1M",
        "entry_pattern": "CONTINUATION",
        "entry_signal_code": "WILD_SWING_OUTSIDE_CHANNEL_CONTINUATION_ENTRY",
        "entry_signal_mode": "CONTINUATION",
        "channel_confirmation_bar_id": bar_id,
        "dual_tf_meta": {"bar_id": bar_id},
    }

    result = asyncio.run(
        validate_account_entry(Account(), "LOBSTER/USDT", "LONG", context)
    )

    assert result["entry_mode"] == "WILD_SWING"
    assert result["entry_pattern"] == "CONTINUATION"
    assert result["side"] == "LONG"
