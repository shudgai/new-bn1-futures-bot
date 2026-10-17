import asyncio

import pandas as pd
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
def test_legacy_consolidation_patterns_are_not_public_entry_modes(
    side, rows, upper, lower, basis, expected_type,
):
    klines = [_candle(100.0, 100.0)] + rows
    signal = BreakoutContinuationEntryGate.evaluate(
        klines,
        [upper] * len(klines),
        [lower] * len(klines),
        [basis] * len(klines),
        ma5_series=[100.0] * len(klines),
        ma15_series=[100.0] * len(klines),
    )
    assert signal is None


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


@pytest.mark.parametrize(
    "side,previous_rail,current_rail,previous_bar,current_bar,ma5",
    [
        (
            "LONG", 100.0, 100.2,
            _candle(99.8, 100.1, 100.2, 99.7),
            _candle(100.5, 100.8, 100.9, 100.4),
            [100.0, 100.1],
        ),
        (
            "SHORT", 100.0, 99.8,
            _candle(100.2, 99.9, 100.3, 99.8),
            _candle(99.5, 99.2, 99.6, 99.1),
            [100.0, 99.9],
        ),
    ],
)
def test_gate_detects_initial_two_bar_breakout(
    side, previous_rail, current_rail, previous_bar, current_bar, ma5,
):
    klines = [_candle(100.0, 100.0)] * 3 + [previous_bar, current_bar]
    upper = [110.0] * 3 + [previous_rail, current_rail]
    lower = [90.0] * 5
    if side == "SHORT":
        upper = [110.0] * 5
        lower = [90.0] * 3 + [previous_rail, current_rail]

    signal = BreakoutContinuationEntryGate.evaluate(
        klines,
        upper,
        lower,
        [100.0] * 5,
        ma5_series=[100.0] * 3 + ma5,
        ma15_series=[100.0] * 5,
    )

    assert signal is not None
    assert signal["action"] == f"OPEN_{side}"
    assert signal["type"] == "CONFIRMED_2BAR_BREAKOUT"


@pytest.mark.parametrize(
    "side,current_close,current_upper,current_lower,ma5_current,ma5_previous",
    [
        ("LONG", 101.0, 100.0, 90.0, 100.2, 100.1),
        ("SHORT", 89.0, 110.0, 90.0, 99.8, 99.9),
    ],
)
def test_gate_detects_outside_continuation_with_directional_ma5(
    side, current_close, current_upper, current_lower,
    ma5_current, ma5_previous,
):
    klines = [_candle(100.0, 100.0)] * 5
    klines[-2] = (
        _candle(current_close - 0.5, current_close - 0.4)
        if side == "LONG"
        else _candle(current_close + 0.5, current_close + 0.4)
    )
    klines[-1] = _candle(current_close - 0.3, current_close)
    upper = [current_upper + 1.0] * (len(klines) - 1) + [current_upper]
    lower = [current_lower - 1.0] * (len(klines) - 1) + [current_lower]
    signal = BreakoutContinuationEntryGate.evaluate(
        klines,
        upper,
        lower,
        [100.0] * len(klines),
        ma5_series=[ma5_previous] * (len(klines) - 1) + [ma5_current],
        ma15_series=[100.0] * len(klines),
    )

    assert signal is not None
    assert signal["action"] == f"OPEN_{side}"
    assert signal["type"] == "CONTINUATION_OUTSIDE_KC"


@pytest.mark.parametrize(
    "side,current_close,current_upper,current_lower,ma5_current,ma5_previous",
    [
        ("LONG", 101.0, 100.0, 90.0, 100.1, 100.1),
        ("SHORT", 89.0, 110.0, 90.0, 99.9, 99.9),
    ],
)
def test_gate_rejects_outside_continuation_when_ma5_is_flat(
    side, current_close, current_upper, current_lower,
    ma5_current, ma5_previous,
):
    klines = [_candle(100.0, 100.0)] * 5
    klines[-2] = _candle(current_close + 0.2, current_close + 0.3)
    klines[-1] = _candle(current_close - 0.3, current_close)
    upper = [current_upper + 1.0] * (len(klines) - 1) + [current_upper]
    lower = [current_lower - 1.0] * (len(klines) - 1) + [current_lower]
    if side == "SHORT":
        klines[-2] = _candle(current_close - 0.3, current_close - 0.2)
    signal = BreakoutContinuationEntryGate.evaluate(
        klines,
        upper,
        lower,
        [100.0] * len(klines),
        ma5_series=[ma5_previous] * (len(klines) - 1) + [ma5_current],
        ma15_series=[100.0] * len(klines),
    )

    assert signal is None


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
    frame.loc[10, "kc_middle"] = 99.9
    frame.loc[11, "kc_middle"] = 100.0
    frame.attrs["timeframe_ms"] = 60_000
    frame.attrs["entry_finality_verified"] = True
    return frame


def _numeric_wild_swing_frame(side, pattern):
    frame = _frame(side)
    frame["atr"] = 2.0
    frame["volume"] = 10.0
    frame["open"] = 100.0
    frame["high"] = 100.2
    frame["low"] = 99.8
    frame["close"] = 100.0
    frame["kc_upper"] = 105.0
    frame["kc_lower"] = 95.0
    frame["kc_middle"] = 100.0
    frame["ma5"] = 100.0
    frame["ma15"] = 99.8 if side == "LONG" else 100.3

    if side == "LONG":
        frame.loc[10, ["ma5", "kc_middle", "kc_upper"]] = [100.1, 100.0, 105.0]
        frame.loc[11, ["ma5", "kc_middle", "kc_upper", "kc_lower"]] = [
            100.2, 100.1, 105.5, 95.5,
        ]
        if pattern == "INITIAL_2BAR_BREAKOUT":
            frame.loc[10, ["open", "high", "low", "close"]] = [
                104.8, 105.3, 104.7, 105.2,
            ]
        else:
            frame.loc[10, ["open", "high", "low", "close"]] = [
                104.8, 105.0, 104.7, 104.9,
            ]
        frame.loc[11, ["open", "high", "low", "close"]] = [
            106.0, 106.6, 105.8, 106.5,
        ]
    else:
        frame.loc[10, ["ma5", "kc_middle", "kc_lower"]] = [100.1, 100.0, 95.0]
        frame.loc[11, ["ma5", "kc_middle", "kc_upper", "kc_lower"]] = [
            100.0, 99.9, 104.5, 94.5,
        ]
        if pattern == "INITIAL_2BAR_BREAKOUT":
            frame.loc[10, ["open", "high", "low", "close"]] = [
                95.2, 95.3, 94.7, 94.8,
            ]
        else:
            frame.loc[10, ["open", "high", "low", "close"]] = [
                95.1, 95.2, 94.9, 95.0,
            ]
        frame.loc[11, ["open", "high", "low", "close"]] = [
            94.0, 94.1, 93.4, 93.5,
        ]

    frame.attrs["timeframe_ms"] = 60_000
    frame.attrs["entry_finality_verified"] = True
    return frame


@pytest.mark.parametrize(
    "side,pattern,expected_reason,expected_confirmation",
    [
        (
            "LONG", "INITIAL_2BAR_BREAKOUT",
            "CONFIRMED_2BAR_BREAKOUT_ENTRY", "CONFIRMED_2BAR_BREAKOUT",
        ),
        (
            "SHORT", "INITIAL_2BAR_BREAKOUT",
            "CONFIRMED_2BAR_BREAKOUT_ENTRY", "CONFIRMED_2BAR_BREAKOUT",
        ),
        (
            "LONG", "CONTINUATION_OUTSIDE_KC",
            "CONTINUATION_OUTSIDE_KC_ENTRY", "CONTINUATION_OUTSIDE_KC",
        ),
        (
            "SHORT", "CONTINUATION_OUTSIDE_KC",
            "CONTINUATION_OUTSIDE_KC_ENTRY", "CONTINUATION_OUTSIDE_KC",
        ),
    ],
)
def test_numeric_patterns_revalidate_without_qwen(
    monkeypatch, side, pattern, expected_reason, expected_confirmation,
):
    from core.vision import chart_guard

    qwen_calls = []

    async def reject_if_called(*args, **kwargs):
        qwen_calls.append((args, kwargs))
        return False, "unexpected visual review"

    monkeypatch.setattr(chart_guard, "evaluate_qwen_entry_vision", reject_if_called)
    frame = _numeric_wild_swing_frame(side, pattern)
    signal = WildSwingStrategy().evaluate_entry("LOBSTER/USDT", frame, side)

    assert signal is not None
    assert signal["reason"] == expected_reason
    assert signal["structure_confirmation"] == expected_confirmation

    class Account:
        positions = {}
        closing_lock = set()
        pending_limit_orders = {}
        trades = []

        async def entry_frame_provider(self, symbol):
            assert symbol == "LOBSTER/USDT"
            return frame.copy()

    bar_id = float(frame.iloc[-1]["timestamp"])
    result = asyncio.run(
        validate_account_entry(
            Account(),
            "LOBSTER/USDT",
            side,
            {
                "entry_mode": "WILD_SWING",
                "source": "WILD_SWING_1M",
                "entry_pattern": signal["entry_pattern"],
                "entry_signal_mode": signal["entry_pattern"],
                "entry_signal_code": f"WILD_SWING_{expected_reason}",
                "channel_confirmation_bar_id": bar_id,
                "dual_tf_meta": {"bar_id": bar_id},
            },
        )
    )

    assert result["side"] == side
    assert result["confirmation_bar_id"] == bar_id
    assert qwen_calls == []


@pytest.mark.parametrize(
    "side,pattern",
    [
        ("LONG", "INITIAL_2BAR_BREAKOUT"),
        ("LONG", "CONTINUATION_OUTSIDE_KC"),
        ("SHORT", "INITIAL_2BAR_BREAKOUT"),
        ("SHORT", "CONTINUATION_OUTSIDE_KC"),
    ],
)
def test_all_math_entry_patterns_require_kc_ma5_ma15_alignment(side, pattern):
    frame = _numeric_wild_swing_frame(side, pattern)
    assert WildSwingStrategy().evaluate_entry(
        "LOBSTER/USDT", frame, side,
    ) is not None

    wrong_ma_order = frame.copy()
    wrong_kc_direction = frame.copy()
    if side == "LONG":
        wrong_ma_order.loc[11, "ma15"] = wrong_ma_order.loc[11, "ma5"] + 0.1
        wrong_kc_direction.loc[10, "kc_middle"] = 100.2
    else:
        wrong_ma_order.loc[11, "ma15"] = wrong_ma_order.loc[11, "ma5"] - 0.1
        wrong_kc_direction.loc[10, "kc_middle"] = 99.8

    assert WildSwingStrategy().evaluate_entry(
        "LOBSTER/USDT", wrong_ma_order, side,
    ) is None
    assert WildSwingStrategy().evaluate_entry(
        "LOBSTER/USDT", wrong_kc_direction, side,
    ) is None


def test_additional_closed_candle_candidate_reaches_existing_macro_gate():
    frame = _outside_continuation_frame()
    frame.loc[10, "ma5"] = 100.1
    signal = WildSwingStrategy().evaluate_entry(
        "LOBSTER/USDT", frame, "LONG",
    )

    assert signal is not None
    assert signal["reason"] == "CONTINUATION_OUTSIDE_KC_ENTRY"
    assert signal["entry_pattern"] == "CONTINUATION"
    assert signal["structure_confirmation"] == "CONTINUATION_OUTSIDE_KC"
    assert signal["bar_id"] == float(frame.iloc[-1]["timestamp"])

    from core.gates.macro_candle_structure_gate import MacroCandleStructureGate

    current = frame.iloc[-1]
    allowed, reason = MacroCandleStructureGate().can_open(
        "LONG",
        {name: float(current[name]) for name in ("open", "high", "low", "close")},
        {
            name: float(current[name])
            for name in ("kc_upper", "kc_lower", "kc_middle", "ma15", "atr")
        } | {
            "ma5_current": signal["ma5_current"],
            "ma5_previous": signal["ma5_previous"],
        },
        entry_pattern=signal["entry_pattern"],
        structure_confirmation=signal["structure_confirmation"],
        structure_confirmed=signal["structure_confirmed"],
        channel_expanding=signal["channel_expanding"],
    )

    assert allowed is True
    assert reason == "PASSED_MACRO_LONG_PURE_MATH_GATE"


def test_additional_candidate_is_revalidated_by_final_entry_firewall(monkeypatch):
    frame = _outside_continuation_frame()
    frame.loc[10, "ma5"] = 100.1
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
        "entry_signal_code": "WILD_SWING_CONTINUATION_OUTSIDE_KC_ENTRY",
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


@pytest.mark.parametrize(
    "side,open_price,high,low,close",
    [
        ("LONG", 106.5, 106.6, 106.5, 106.55),
        ("SHORT", 93.5, 93.5, 93.4, 93.45),
    ],
)
def test_live_bar_cannot_authorize_new_signal_through_final_firewall(
    side, open_price, high, low, close,
):
    frame = _numeric_wild_swing_frame(side, "CONTINUATION_OUTSIDE_KC")
    live = frame.iloc[-1].copy()
    live["timestamp"] += 60_000
    live["is_closed"] = False
    live["open"], live["high"], live["low"], live["close"] = (
        open_price, high, low, close,
    )
    frame = pd.concat([frame, live.to_frame().T], ignore_index=True)
    frame.attrs["timeframe_ms"] = 60_000
    frame.attrs["entry_finality_verified"] = True
    signal = WildSwingStrategy().evaluate_entry(
        "LOBSTER/USDT", frame, side, tick_price=close,
    )
    assert signal is not None
    assert signal["is_intrabar"] is False
    assert signal["bar_id"] == float(frame.iloc[-2]["timestamp"])

    class Account:
        positions = {}
        closing_lock = set()
        pending_limit_orders = {}
        trades = []

        async def entry_frame_provider(self, symbol):
            assert symbol == "LOBSTER/USDT"
            return frame.copy()

    live_bar_id = float(live["timestamp"])
    closed_bar_id = float(frame.iloc[-2]["timestamp"])
    with pytest.raises(ValueError, match="WILD_SWING_SIGNAL_STALE_OR_INVALID"):
        asyncio.run(validate_account_entry(
            Account(),
            "LOBSTER/USDT",
            side,
            {
                "entry_mode": "WILD_SWING",
                "source": "WILD_SWING_1M",
                "entry_pattern": "CONTINUATION",
                "entry_signal_mode": "CONTINUATION",
                "entry_signal_code": (
                    "WILD_SWING_CONTINUATION_OUTSIDE_KC_ENTRY"
                ),
                "channel_confirmation_bar_id": live_bar_id,
                "dual_tf_meta": {"bar_id": live_bar_id},
            },
        ))
    assert closed_bar_id != live_bar_id


@pytest.mark.parametrize(
    "side,entry_type,live_open,tick,previous_extreme,expected_type",
    [
        ("LONG", "inside", 99.8, 100.1, 100.3, "INSTANT_BREAKOUT_INSIDE"),
        ("LONG", "outside", 100.1, 100.5, 100.3, "INSTANT_BREAKOUT_OUTSIDE"),
        ("SHORT", "inside", 100.0, 99.7, 99.5, "INSTANT_BREAKOUT_INSIDE"),
        ("SHORT", "outside", 99.7, 99.3, 99.5, "INSTANT_BREAKOUT_OUTSIDE"),
    ],
)
def test_pure_math_gate_detects_intrabar_breakouts(
    side, entry_type, live_open, tick, previous_extreme, expected_type,
):
    closed = [
        {**_candle(100.0, 100.0, 100.3, 99.5), "timestamp": index * 60_000}
        for index in range(4)
    ]
    if side == "LONG":
        closed[-1]["high"] = previous_extreme
        upper, lower, basis = 100.0, 90.0, 95.0
    else:
        closed[-1]["low"] = previous_extreme
        upper, lower, basis = 110.0, 99.8, 105.0
    live = {
        **_candle(live_open, tick, max(live_open, tick), min(live_open, tick)),
        "timestamp": 240_000,
        "is_closed": False,
        "kc_upper": upper,
        "kc_lower": lower,
        "kc_middle": basis,
    }
    signal = BreakoutContinuationEntryGate.evaluate_pure_math(
        closed,
        [upper] * 4,
        [lower] * 4,
        [basis] * 4,
        [100.0] * 4,
        [100.0] * 4,
        tick_price=tick,
        live_candle=live,
    )
    assert signal is not None
    assert signal["action"] == f"OPEN_{side}"
    assert signal["type"] == expected_type


def test_intrabar_gate_rejects_tick_outside_live_candle_ohlc():
    closed = [
        {**_candle(100.0, 100.0), "timestamp": index * 60_000}
        for index in range(4)
    ]
    live = {
        **_candle(99.8, 100.0),
        "timestamp": 240_000,
        "is_closed": False,
        "kc_upper": 100.0,
        "kc_lower": 90.0,
        "kc_middle": 95.0,
    }
    assert BreakoutContinuationEntryGate.evaluate_pure_math(
        closed,
        [100.0] * 4,
        [90.0] * 4,
        [95.0] * 4,
        [100.0] * 4,
        [100.0] * 4,
        tick_price=101.0,
        live_candle=live,
    ) is None


@pytest.mark.parametrize(
    "side,live_open,tick,previous_extreme",
    [
        ("LONG", 100.1, 100.5, 101.0),
        ("SHORT", 98.9, 98.5, 98.0),
    ],
)
def test_outside_ma5_continuation_can_trigger_on_live_quote(
    side, live_open, tick, previous_extreme,
):
    closed = [
        {**_candle(100.0, 100.0, 100.3, 99.5), "timestamp": index * 60_000}
        for index in range(4)
    ]
    upper, lower, basis = (
        (100.0, 90.0, 95.0)
        if side == "LONG"
        else (110.0, 99.0, 105.0)
    )
    if side == "LONG":
        closed[-1]["high"] = previous_extreme
    else:
        closed[-1]["low"] = previous_extreme
    live = {
        **_candle(live_open, tick, max(live_open, tick), min(live_open, tick)),
        "timestamp": 240_000,
        "is_closed": False,
        "kc_upper": upper,
        "kc_lower": lower,
        "kc_middle": basis,
    }

    signal = BreakoutContinuationEntryGate.evaluate_pure_math(
        closed,
        [upper] * 4,
        [lower] * 4,
        [basis] * 4,
        [100.0] * 4,
        [100.0] * 4,
        tick_price=tick,
        live_candle=live,
    )

    assert signal is not None
    assert signal["action"] == f"OPEN_{side}"
    assert signal["type"] == "CONTINUATION_OUTSIDE_KC"
    assert signal["is_intrabar"] is True


@pytest.mark.parametrize(
    "side,live_open,tick,previous_extreme,ma5_series",
    [
        ("LONG", 100.1, 100.5, 101.0, [100.0, 100.0, 100.0, 100.2]),
        ("SHORT", 98.9, 98.5, 98.0, [100.0, 100.0, 100.0, 99.6]),
    ],
)
def test_live_outside_continuation_rejects_opposite_ma5_slope(
    side, live_open, tick, previous_extreme, ma5_series,
):
    closed = [
        {**_candle(100.0, 100.0, 100.3, 99.5), "timestamp": index * 60_000}
        for index in range(4)
    ]
    upper, lower, basis = (
        (100.0, 90.0, 95.0)
        if side == "LONG"
        else (110.0, 99.0, 105.0)
    )
    if side == "LONG":
        closed[-1]["high"] = previous_extreme
    else:
        closed[-1]["low"] = previous_extreme
    live = {
        **_candle(live_open, tick, max(live_open, tick), min(live_open, tick)),
        "timestamp": 240_000,
        "is_closed": False,
        "kc_upper": upper,
        "kc_lower": lower,
        "kc_middle": basis,
    }

    assert BreakoutContinuationEntryGate.evaluate_pure_math(
        closed,
        [upper] * 4,
        [lower] * 4,
        [basis] * 4,
        ma5_series,
        [100.0] * 4,
        tick_price=tick,
        live_candle=live,
    ) is None
