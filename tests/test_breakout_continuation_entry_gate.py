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
            _candle(100.22, 100.28, 100.30, 100.20),
            [100.0, 100.1],
        ),
        (
            "SHORT", 100.0, 99.8,
            _candle(100.2, 99.9, 100.3, 99.8),
            _candle(99.78, 99.72, 99.80, 99.70),
            [100.0, 99.9],
        ),
    ],
)
def test_gate_detects_two_bar_breakout_without_ma_trend_alignment(
    side, previous_rail, current_rail, previous_bar, current_bar, ma5,
):
    ma5 = [100.26, 100.25] if side == "LONG" else [99.74, 99.75]
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
        ("LONG", 100.08, 100.0, 90.0, 100.06, 100.03),
        ("SHORT", 89.92, 110.0, 90.0, 89.94, 89.97),
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
    klines[-1] = (
        _candle(current_close - 0.3, current_close)
        if side == "LONG"
        else _candle(current_close + 0.3, current_close)
    )
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
        ("LONG", 101.0, 100.0, 90.0, 100.2, 100.1),
        ("SHORT", 89.0, 110.0, 90.0, 99.8, 99.9),
    ],
)
def test_gate_rejects_wrong_color_outside_continuation(
    side, current_close, current_upper, current_lower,
    ma5_current, ma5_previous,
):
    klines = [_candle(100.0, 100.0)] * 5
    klines[-1] = (
        _candle(current_close + 0.3, current_close)
        if side == "LONG"
        else _candle(current_close - 0.3, current_close)
    )
    upper = [current_upper] * len(klines)
    lower = [current_lower] * len(klines)
    signal = BreakoutContinuationEntryGate.evaluate(
        klines,
        upper,
        lower,
        [100.0] * len(klines),
        ma5_series=[ma5_previous] * (len(klines) - 1) + [ma5_current],
        ma15_series=[100.0] * len(klines),
    )

    assert signal is None


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


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_wild_swing_breakout_ignores_ma_order_and_kc_middle_slope(side):
    frame = _numeric_wild_swing_frame(side, "INITIAL_2BAR_BREAKOUT")
    if side == "LONG":
        frame.loc[10, ["ma5", "kc_middle"]] = [100.2, 100.2]
        frame.loc[11, ["ma5", "ma15", "kc_middle"]] = [100.0, 100.4, 100.1]
    else:
        frame.loc[10, ["ma5", "kc_middle"]] = [99.8, 99.8]
        frame.loc[11, ["ma5", "ma15", "kc_middle"]] = [100.0, 99.7, 99.9]

    signal = WildSwingStrategy().evaluate_entry("LOBSTER/USDT", frame, side)

    assert signal is not None
    assert signal["structure_confirmation"] == "CONFIRMED_2BAR_BREAKOUT"


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_wild_swing_single_bar_continuation_can_reenter_after_exit(side):
    frame = _numeric_wild_swing_frame(side, "CONTINUATION_OUTSIDE_KC")
    strategy = WildSwingStrategy()
    state = strategy.state_for("LOBSTER/USDT")
    state.bars_since_last_exit = 1
    state.last_exit_side = side
    state.last_exit_bar_id = float(frame.iloc[-1]["timestamp"]) - 60_000

    signal = strategy.evaluate_entry("LOBSTER/USDT", frame, side)

    assert signal is not None
    assert signal["structure_confirmation"] == "CONTINUATION_OUTSIDE_KC"


@pytest.mark.parametrize(
    "elapsed_bars,expected_signal",
    [(2, False), (3, True)],
)
def test_wild_swing_cooldown_advances_from_closed_bar_ids(
    elapsed_bars, expected_signal,
):
    frame = _numeric_wild_swing_frame("SHORT", "INITIAL_2BAR_BREAKOUT")
    strategy = WildSwingStrategy()
    state = strategy.state_for("CAP/USDT")
    bar_id = float(frame.iloc[-1]["timestamp"])
    state.bars_since_last_exit = 0
    state.last_exit_side = "SHORT"
    state.last_exit_bar_id = bar_id - elapsed_bars * 60_000

    signal = strategy.evaluate_entry("CAP/USDT", frame, "SHORT")

    assert (signal is not None) is expected_signal
    assert state.bars_since_last_exit == elapsed_bars


def _frame_with_verified_structure_history(frame, side):
    frame = frame.copy()
    prefix = pd.concat([frame.iloc[[0]]] * 8, ignore_index=True)
    prefix["timestamp"] = [index * 60_000 for index in range(8)]
    frame["timestamp"] = frame["timestamp"].astype(float) + 8 * 60_000
    frame = pd.concat([prefix, frame], ignore_index=True)
    frame["ma5"] = [
        110.0 - index * 0.1 if side == "LONG"
        else 90.0 + index * 0.1
        for index in range(len(frame))
    ]
    frame["ma15"] = [
        115.0 + index * 0.1 if side == "LONG"
        else 85.0 - index * 0.1
        for index in range(len(frame))
    ]
    frame["kc_middle"] = [
        95.0 + index * 0.1 if side == "LONG"
        else 105.0 - index * 0.1
        for index in range(len(frame))
    ]
    frame["kc_upper"] = frame["kc_middle"] + 5.0
    frame["kc_lower"] = frame["kc_middle"] - 5.0
    last = frame.index[-3:]
    if side == "LONG":
        frame.loc[last, "ma5"] = [100.6, 100.4, 100.2]
        frame.loc[last, "ma15"] = [101.2, 101.4, 101.6]
        frame.loc[last, "kc_middle"] = [100.6, 100.3, 100.0]
        frame.loc[last, "kc_upper"] = [104.8, 105.0, 105.5]
        frame.loc[last, "kc_lower"] = [94.5, 94.8, 95.0]
    else:
        frame.loc[last, "ma5"] = [99.4, 99.6, 99.8]
        frame.loc[last, "ma15"] = [98.8, 98.6, 98.4]
        frame.loc[last, "kc_middle"] = [99.4, 99.7, 100.0]
        frame.loc[last, "kc_upper"] = [105.5, 105.2, 105.0]
        frame.loc[last, "kc_lower"] = [95.3, 95.0, 94.5]
    frame.attrs["timeframe_ms"] = 60_000
    frame.attrs["entry_finality_verified"] = True
    return frame


def _align_outer_entry_mas(frame, side):
    frame = frame.copy()
    recent = frame.index[-3:]
    if side == "LONG":
        frame.loc[recent, "ma15"] = [99.0, 99.2, 99.4]
        frame.loc[recent, "ma5"] = [99.4, 99.6, 99.8]
    else:
        frame.loc[recent, "ma15"] = [101.0, 100.8, 100.6]
        frame.loc[recent, "ma5"] = [100.6, 100.4, 100.2]
    return frame


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_two_bar_breakout_requires_ma_alignment_at_final_firewall(side):
    frame = _frame_with_verified_structure_history(
        _numeric_wild_swing_frame(side, "INITIAL_2BAR_BREAKOUT"),
        side,
    )
    bar_id = float(frame.iloc[-1]["timestamp"])
    pattern = "BREAKOUT"
    confirmation = "CONFIRMED_2BAR_BREAKOUT"

    class Account:
        positions = {}
        closing_lock = set()
        pending_limit_orders = {}
        trades = []

        async def entry_frame_provider(self, symbol):
            assert symbol == "LOBSTER/USDT"
            return frame.copy()

    with pytest.raises(ValueError, match="VETO_"):
        asyncio.run(
            validate_account_entry(
                Account(),
                "LOBSTER/USDT",
                side,
                {
                    "entry_mode": "WILD_SWING",
                    "source": "WILD_SWING_1M",
                    "entry_pattern": pattern,
                    "entry_signal_mode": pattern,
                    "entry_signal_code": f"WILD_SWING_{confirmation}_ENTRY",
                    "channel_confirmation_bar_id": bar_id,
                    "dual_tf_meta": {"bar_id": bar_id},
                },
            )
        )


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
    frame = _align_outer_entry_mas(
        _numeric_wild_swing_frame(side, pattern),
        side,
    )
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
def test_outer_entries_do_not_require_ma5_ma15_order_or_kc_middle_direction(
    side, pattern,
):
    frame = _numeric_wild_swing_frame(side, pattern)
    wrong_ma_order = frame.copy()
    wrong_kc_direction = frame.copy()
    if side == "LONG":
        wrong_ma_order.loc[11, "ma15"] = wrong_ma_order.loc[11, "ma5"] + 0.1
        wrong_kc_direction.loc[10, "kc_middle"] = 100.2
        if pattern == "INITIAL_2BAR_BREAKOUT":
            wrong_ma_order.loc[10, "ma5"] = 100.2
            wrong_ma_order.loc[11, "ma5"] = 100.0
            wrong_ma_order.loc[11, "ma15"] = 100.4
    else:
        wrong_ma_order.loc[11, "ma15"] = wrong_ma_order.loc[11, "ma5"] - 0.1
        wrong_kc_direction.loc[10, "kc_middle"] = 99.8
        if pattern == "INITIAL_2BAR_BREAKOUT":
            wrong_ma_order.loc[10, "ma5"] = 99.8
            wrong_ma_order.loc[11, "ma5"] = 100.0
            wrong_ma_order.loc[11, "ma15"] = 99.7

    assert WildSwingStrategy().evaluate_entry(
        "LOBSTER/USDT", wrong_ma_order, side,
    ) is not None
    assert WildSwingStrategy().evaluate_entry(
        "LOBSTER/USDT", wrong_kc_direction, side,
    ) is not None


@pytest.mark.parametrize(
    "side,pattern,confirmation",
    [
        ("LONG", "INITIAL_2BAR_BREAKOUT", "CONFIRMED_2BAR_BREAKOUT"),
        ("SHORT", "INITIAL_2BAR_BREAKOUT", "CONFIRMED_2BAR_BREAKOUT"),
        ("LONG", "CONTINUATION_OUTSIDE_KC", "CONTINUATION_OUTSIDE_KC"),
        ("SHORT", "CONTINUATION_OUTSIDE_KC", "CONTINUATION_OUTSIDE_KC"),
    ],
)
@pytest.mark.parametrize("quote_outside", [True, False])
def test_engine_blocks_outer_entry_with_flat_ma_structure(
    side, pattern, confirmation, quote_outside,
):
    from types import SimpleNamespace

    from core.engine import TradingEngine

    frame = _frame_with_verified_structure_history(
        _numeric_wild_swing_frame(side, pattern),
        side,
    )
    frame["kc_middle"] = 100.0
    frame["kc_upper"] = 105.0
    frame["kc_lower"] = 95.0
    frame["ma5"] = 100.0
    frame["ma15"] = 100.0
    frame["atr"] = 2.0
    if confirmation == "CONTINUATION_OUTSIDE_KC":
        ma5_column = frame.columns.get_loc("ma5")
        frame.iat[-2, ma5_column] = 100.0
        frame.iat[-1, ma5_column] = (
            100.01 if side == "LONG" else 99.99
        )
    outside_quote = 106.5 if side == "LONG" else 93.5
    quote = outside_quote if quote_outside else (105.0 if side == "LONG" else 95.0)
    account = SimpleNamespace(
        positions={},
        closing_lock=set(),
        pending_limit_orders={},
        last_closed_side={},
        last_closed_at={},
        trades=[],
        logs=[],
        get_wallet_balance=lambda: 100.0,
        get_available_balance=lambda: 100.0,
    )
    account.log = lambda *args: account.logs.append(args)
    async def open_position(**kwargs):
        account.entry_context = kwargs["entry_context"]
        return True

    account.open_position = open_position
    engine = SimpleNamespace(
        account=account,
        symbol_rotation=SimpleNamespace(
            get_dynamic_leverage=lambda *args: 10,
        ),
        _full_wallet_entry_margin=lambda *args: 10.0,
    )

    opened = asyncio.run(
        TradingEngine.execute_dual_tf_entry(
            engine,
            symbol="CAP/USDT",
            frame=frame,
            price=quote,
            side=side,
            mode_name=f"WILD_SWING_{confirmation}_ENTRY",
            meta_dict={
                "bar_id": float(frame.iloc[-1]["timestamp"]),
                "entry_pattern": (
                    "BREAKOUT"
                    if confirmation == "CONFIRMED_2BAR_BREAKOUT"
                    else "CONTINUATION"
                ),
                "structure_confirmation": confirmation,
            },
            entry_mode="WILD_SWING",
        )
    )

    assert opened is False
    assert not hasattr(account, "entry_context")


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_outer_continuation_still_requires_directional_ma5(side):
    frame = _numeric_wild_swing_frame(side, "CONTINUATION_OUTSIDE_KC")
    if side == "LONG":
        frame.loc[10, "ma5"] = 100.2
        frame.loc[11, "ma5"] = 100.0
    else:
        frame.loc[10, "ma5"] = 99.8
        frame.loc[11, "ma5"] = 100.0

    assert WildSwingStrategy().evaluate_entry(
        "LOBSTER/USDT", frame, side,
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
    assert reason in {"PASSED_MACRO_LONG_PURE_MATH_GATE", "PASSED_MACRO_LONG_GATE"}


def test_additional_candidate_is_revalidated_by_final_entry_firewall(monkeypatch):
    frame = _outside_continuation_frame()
    frame.loc[9:11, "ma15"] = [99.6, 99.8, 100.0]
    frame.loc[9:11, "ma5"] = [100.2, 101.0, 101.8]
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
    "live_open,tick,upper,lower,basis,ma5",
    [
        (99.6, 99.7, 110.0, 99.8, 105.0, [100.0, 100.0, 100.0, 100.5]),
        (100.2, 100.1, 100.0, 90.0, 95.0, [100.0, 100.0, 100.0, 99.5]),
    ],
)
def test_intrabar_breakout_rejects_direction_opposed_to_live_candle(
    live_open, tick, upper, lower, basis, ma5,
):
    closed = [
        {
            **_candle(100.0, close, max(close, 100.05), min(close, 99.0)),
            "timestamp": index * 60_000,
        }
        for index, close in enumerate([100.0, 100.0, 100.0, ma5[-1]])
    ]
    live = {
        **_candle(
            live_open, tick, max(live_open, tick) + 0.1,
            min(live_open, tick) - 0.1,
        ),
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
        ma5,
        [100.0] * 4,
        tick_price=tick,
        live_candle=live,
    )
    assert signal is None


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
