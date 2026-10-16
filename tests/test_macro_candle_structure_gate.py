import pytest
from core.gates.macro_candle_structure_gate import MacroCandleStructureGate
from core.services.gates.entry_gate import MacroCandleStructureGate as ExportedGate

def test_gate_export():
    assert MacroCandleStructureGate is ExportedGate

def test_long_vetoed_when_kc_position_too_high():
    gate = MacroCandleStructureGate()
    bar = {'open': 105.0, 'close': 106.0, 'high': 106.2, 'low': 104.8}
    indicators = {'kc_upper': 110.0, 'kc_lower': 90.0, 'kc_middle': 100.0, 'ma15': 106.0, 'atr': 2.0}

    can_open, reason = gate.can_open(
        'LONG', bar, indicators, entry_pattern="CROSSOVER",
        structure_confirmed=True,
        structure_confirmation="MA5_MA15_1M_CROSS",
    )
    assert can_open is False
    assert "VETO_CROSSOVER_LONG" in reason

def test_long_vetoed_when_bias_too_large():
    gate = MacroCandleStructureGate(max_bias_atr=1.2)
    bar = {'open': 110.0, 'close': 112.0, 'high': 112.2, 'low': 109.8}
    indicators = {'kc_upper': 110.0, 'kc_lower': 90.0, 'kc_middle': 100.0, 'ma15': 96.0, 'atr': 2.0}

    can_open, reason = gate.can_open(
        'LONG', bar, indicators, entry_pattern="BREAKOUT",
        channel_expanding=True,
        structure_confirmation="KC_OUTER_BREAKOUT",
    )
    assert can_open is False
    assert "VETO_LONG: 乖離過大" in reason

def test_long_vetoed_when_upper_wick_rejection():
    gate = MacroCandleStructureGate(max_bias_atr=1.2)
    bar = {'open': 99.8, 'close': 100.0, 'high': 101.0, 'low': 99.5}
    indicators = {'kc_upper': 110.0, 'kc_lower': 90.0, 'kc_middle': 100.0, 'ma15': 100.0, 'atr': 2.0}

    can_open, reason = gate.can_open(
        'LONG', bar, indicators, entry_pattern="CROSSOVER",
        structure_confirmed=True,
        structure_confirmation="MA5_MA15_1M_CROSS",
    )
    assert can_open is False
    assert "VETO_LONG: K 棒受阻留長上影線" in reason

def test_long_passed_when_all_conditions_met():
    gate = MacroCandleStructureGate(max_bias_atr=1.2)
    bar = {'open': 99.0, 'close': 100.0, 'high': 100.3, 'low': 98.8}
    indicators = {'kc_upper': 110.0, 'kc_lower': 90.0, 'kc_middle': 100.0, 'ma15': 100.0, 'atr': 2.0}

    can_open, reason = gate.can_open(
        'LONG', bar, indicators, entry_pattern="CONTINUATION",
    )
    assert can_open is True
    assert reason == "PASSED_MACRO_LONG_GATE"

@pytest.mark.parametrize("kc_pos", [0.35, 0.50, 0.65])
def test_long_mid_channel_pullback_positions_pass(kc_pos):
    gate = MacroCandleStructureGate()
    close = 90.0 + kc_pos * 20.0
    bar = {
        'open': close - 0.2, 'close': close, 'high': close + 0.1,
        'low': close - 0.3, 'volume': 100,
    }
    indicators = {
        'kc_upper': 110.0, 'kc_lower': 90.0, 'kc_middle': 100.0,
        'ma15': close, 'atr': 2.0,
    }

    can_open, reason = gate.can_open(
        'LONG', bar, indicators, entry_pattern="CONTINUATION",
    )

    assert can_open is True
    assert reason == "PASSED_MACRO_LONG_GATE"

def test_short_vetoed_when_kc_position_too_low():
    gate = MacroCandleStructureGate()
    # kc_upper = 110, kc_lower = 90 (range = 20)
    # close = 96 -> kc_pos = (96 - 90) / 20 = 0.30 < 0.35 (floor short chasing)
    bar = {'open': 97.0, 'close': 96.0, 'high': 97.2, 'low': 95.8, 'volume': 100}
    indicators = {'kc_upper': 110.0, 'kc_lower': 90.0, 'kc_middle': 100.0, 'ma15': 96.5, 'atr': 2.0}

    can_open, reason = gate.can_open(
        'SHORT', bar, indicators, entry_pattern="CROSSOVER",
        structure_confirmed=True,
        structure_confirmation="MA5_MA15_1M_CROSS",
    )
    assert can_open is False
    assert "VETO_CROSSOVER_SHORT: KC_Pos" in reason

@pytest.mark.parametrize("kc_pos", [0.35, 0.50, 0.65])
def test_short_mid_channel_pullback_positions_pass(kc_pos):
    gate = MacroCandleStructureGate()
    close = 90.0 + kc_pos * 20.0
    bar = {
        'open': close + 0.2, 'close': close, 'high': close + 0.3,
        'low': close - 0.1, 'volume': 100,
    }
    indicators = {
        'kc_upper': 110.0, 'kc_lower': 90.0, 'kc_middle': 100.0,
        'ma15': close, 'atr': 2.0,
    }

    can_open, reason = gate.can_open(
        'SHORT', bar, indicators, entry_pattern="CONTINUATION",
    )

    assert can_open is True
    assert reason == "PASSED_MACRO_SHORT_GATE"

def test_short_vetoed_when_bias_too_large():
    gate = MacroCandleStructureGate(max_bias_atr=1.2)
    bar = {'open': 90.0, 'close': 89.0, 'high': 90.2, 'low': 88.8}
    indicators = {'kc_upper': 110.0, 'kc_lower': 90.0, 'kc_middle': 100.0, 'ma15': 104.0, 'atr': 2.0}

    can_open, reason = gate.can_open(
        'SHORT', bar, indicators, entry_pattern="BREAKOUT",
        channel_expanding=True,
        structure_confirmation="KC_OUTER_BREAKOUT",
    )
    assert can_open is False
    assert "VETO_SHORT: 乖離過大" in reason


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_breakout_bias_limit_is_two_atr(side):
    if side == "SHORT":
        bar = {"open": 91.2, "close": 89.8, "high": 91.3, "low": 89.7}
        ma15 = 93.4
    else:
        bar = {"open": 108.8, "close": 110.2, "high": 110.3, "low": 108.7}
        ma15 = 106.6
    indicators = {
        "kc_upper": 110.0, "kc_lower": 90.0, "kc_middle": 100.0,
        "ma15": ma15, "atr": 2.0,
    }
    if side == "SHORT":
        bar["close"] = 89.8
        bar["open"] = 91.2
        indicators["ma15"] = 93.8  # exactly 2 ATR away

    allowed, reason = MacroCandleStructureGate().can_open(
        side, bar, indicators, entry_pattern="BREAKOUT",
        channel_expanding=True,
        structure_confirmation="KC_OUTER_BREAKOUT",
    )

    assert allowed is True
    assert reason == f"PASSED_MACRO_{side}_GATE"


def test_breakout_short_bias_above_two_atr_is_still_vetoed():
    allowed, reason = MacroCandleStructureGate().can_open(
        "SHORT",
        {"open": 91.2, "close": 89.8, "high": 91.3, "low": 89.7},
        {
            "kc_upper": 110.0, "kc_lower": 90.0, "kc_middle": 100.0,
            "ma15": 94.0, "atr": 2.0,
        },
        entry_pattern="BREAKOUT",
        channel_expanding=True,
        structure_confirmation="KC_OUTER_BREAKOUT",
    )
    assert allowed is False
    assert "2.00x ATR" in reason


def test_short_vetoed_when_lower_wick_exhaustion():
    gate = MacroCandleStructureGate(max_bias_atr=1.2)
    bar = {'open': 100.2, 'close': 100.0, 'high': 100.3, 'low': 98.5}
    indicators = {'kc_upper': 110.0, 'kc_lower': 90.0, 'kc_middle': 100.0, 'ma15': 100.0, 'atr': 2.0}

    can_open, reason = gate.can_open(
        'SHORT', bar, indicators, entry_pattern="CROSSOVER",
        structure_confirmed=True,
        structure_confirmation="MA5_MA15_1M_CROSS",
    )
    assert can_open is False
    assert "VETO_SHORT: 陰線下影線結構力竭" in reason

def test_short_bullish_candle_is_not_rejected_by_bearish_wick_rule():
    gate = MacroCandleStructureGate()
    bar = {'open': 99.8, 'close': 100.0, 'high': 100.3, 'low': 98.5}
    indicators = {'kc_upper': 110.0, 'kc_lower': 90.0, 'kc_middle': 100.0, 'ma15': 100.0, 'atr': 2.0}

    can_open, reason = gate.can_open(
        'SHORT', bar, indicators, entry_pattern="CROSSOVER",
        structure_confirmed=True,
        structure_confirmation="MA5_MA15_1M_CROSS",
    )

    assert can_open is True
    assert reason == "PASSED_MACRO_SHORT_GATE"

def test_short_passed_when_all_conditions_met():
    gate = MacroCandleStructureGate(max_bias_atr=1.2)
    bar = {'open': 101.0, 'close': 100.0, 'high': 101.2, 'low': 99.8}
    indicators = {'kc_upper': 110.0, 'kc_lower': 90.0, 'kc_middle': 100.0, 'ma15': 100.0, 'atr': 2.0}

    can_open, reason = gate.can_open(
        'SHORT', bar, indicators, entry_pattern="CONTINUATION",
    )
    assert can_open is True
    assert reason == "PASSED_MACRO_SHORT_GATE"

def test_unknown_side():
    gate = MacroCandleStructureGate()
    bar = {'open': 100, 'close': 100, 'high': 101, 'low': 99}
    indicators = {'kc_upper': 110, 'kc_lower': 90, 'kc_middle': 100, 'ma15': 100, 'atr': 1}
    can_open, reason = gate.can_open(
        'INVALID', bar, indicators, entry_pattern="CONTINUATION",
    )
    assert can_open is False
    assert reason == "UNKNOWN_SIDE"


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
@pytest.mark.parametrize(
    "mode,bar,indicators,extra",
    [
        (
            "BREAKOUT",
            {"open": 109.0, "high": 111.2, "low": 108.8, "close": 111.0},
            {"kc_upper": 110.0, "kc_lower": 90.0, "kc_middle": 100.0, "ma15": 110.5, "atr": 2.0},
            {
                "channel_expanding": True,
                "structure_confirmation": "KC_OUTER_BREAKOUT",
            },
        ),
        (
            "CONTINUATION",
            {"open": 99.0, "high": 100.1, "low": 98.9, "close": 100.0},
            {"kc_upper": 110.0, "kc_lower": 90.0, "kc_middle": 100.0, "ma15": 100.0, "atr": 2.0},
            {},
        ),
        (
            "CROSSOVER",
            {"open": 99.0, "high": 100.1, "low": 98.9, "close": 100.0},
            {"kc_upper": 110.0, "kc_lower": 90.0, "kc_middle": 100.0, "ma15": 100.0, "atr": 2.0},
            {
                "structure_confirmed": True,
                "structure_confirmation": "MA5_MA15_1M_CROSS",
            },
        ),
        (
            "REVERSAL",
            {"open": 99.0, "high": 100.1, "low": 98.9, "close": 100.0},
            {"kc_upper": 110.0, "kc_lower": 90.0, "kc_middle": 100.0, "ma15": 100.0, "atr": 2.0},
            {
                "structure_confirmed": True,
                "structure_confirmation": "CONFIRMED_KC_OUTER_PEAK_VALLEY",
            },
        ),
    ],
)
def test_each_entry_mode_uses_its_structure_gate(side, mode, bar, indicators, extra):
    if side == "SHORT":
        if mode == "BREAKOUT":
            bar = {"open": 91.0, "high": 91.2, "low": 88.8, "close": 89.0}
            indicators = {
                "kc_upper": 110.0, "kc_lower": 90.0, "kc_middle": 100.0,
                "ma15": 89.5, "atr": 2.0,
            }
        else:
            bar = {"open": 101.0, "high": 101.1, "low": 99.9, "close": 100.0}

    allowed, reason = MacroCandleStructureGate().can_open(
        side, bar, indicators, entry_pattern=mode, **extra,
    )

    assert allowed is True
    assert reason == f"PASSED_MACRO_{side}_GATE"


@pytest.mark.parametrize(
    "mode,extra,reason",
    [
        ("BREAKOUT", {}, "VETO_BREAKOUT: KC 通道未擴張或缺少有效破軌確認"),
        ("CROSSOVER", {}, "VETO_CROSSOVER: 缺少已確認的 1M 交叉或 5M 均線收斂"),
        ("REVERSAL", {}, "VETO_REVERSAL: 缺少峰谷背離或長針力竭確認"),
        ("NOT_A_MODE", {}, "REJECT_UNAUTHORIZED_ENTRY_PATTERN"),
    ],
)
def test_mode_gate_fails_closed_without_required_metadata(mode, extra, reason):
    bar = {"open": 99.0, "high": 100.1, "low": 98.9, "close": 100.0}
    indicators = {
        "kc_upper": 110.0, "kc_lower": 90.0, "kc_middle": 100.0,
        "ma15": 100.0, "atr": 2.0,
    }

    allowed, actual_reason = MacroCandleStructureGate().can_open(
        "LONG", bar, indicators, entry_pattern=mode, **extra,
    )

    assert allowed is False
    assert actual_reason == reason


@pytest.mark.parametrize(
    "side,bar,ma15",
    [
        ("LONG", {"open": 111.0, "high": 112.1, "low": 110.9, "close": 112.0}, 111.5),
        ("SHORT", {"open": 89.0, "high": 89.1, "low": 87.9, "close": 88.0}, 88.5),
    ],
)
def test_continuation_mode_allows_strong_outer_band_zone(side, bar, ma15):
    indicators = {
        "kc_upper": 110.0, "kc_lower": 90.0, "kc_middle": 100.0,
        "ma15": ma15, "atr": 2.0,
    }

    allowed, reason = MacroCandleStructureGate().can_open(
        side, bar, indicators, entry_pattern="CONTINUATION",
    )

    assert allowed is True
    assert reason == f"PASSED_MACRO_{side}_GATE"


def test_crossover_gate_accepts_confirmed_five_minute_ma_convergence():
    bar = {"open": 99.0, "high": 100.1, "low": 98.9, "close": 100.0}
    indicators = {
        "kc_upper": 110.0, "kc_lower": 90.0, "kc_middle": 100.0,
        "ma15": 100.0, "atr": 2.0,
    }

    allowed, reason = MacroCandleStructureGate().can_open(
        "LONG",
        bar,
        indicators,
        entry_pattern="CROSSOVER",
        structure_confirmation="MA5_MA15_5M_CONVERGENCE",
        structure_confirmed=True,
    )

    assert allowed is True
    assert reason == "PASSED_MACRO_LONG_GATE"


@pytest.mark.parametrize(
    "side,bar,extra",
    [
        (
            "LONG",
            {"open": 100.0, "high": 100.4, "low": 99.9, "close": 100.3},
            {"ma5_current": 100.2, "ma5_previous": 100.1},
        ),
        (
            "SHORT",
            {"open": 100.0, "high": 100.1, "low": 99.6, "close": 99.7},
            {"ma5_current": 99.8, "ma5_previous": 99.9},
        ),
    ],
)
def test_outer_pivot_then_ma_cross_reversal_gate_accepts_aligned_ma5(
    side, bar, extra,
):
    indicators = {
        "kc_upper": 101.0, "kc_lower": 99.0, "kc_middle": 100.0,
        "ma15": 100.0, "atr": 2.0, **extra,
    }
    allowed, reason = MacroCandleStructureGate().can_open(
        side,
        bar,
        indicators,
        entry_pattern="REVERSAL",
        structure_confirmation="CONFIRMED_KC_OUTER_PIVOT_THEN_MA5_MA15_CROSS",
        structure_confirmed=True,
    )
    assert allowed is True
    assert reason == f"PASSED_MACRO_{side}_GATE"


@pytest.mark.parametrize(
    "side,bar,ma5_current,ma5_previous",
    [
        (
            "LONG",
            {"open": 99.9, "high": 100.4, "low": 99.8, "close": 100.3},
            100.2, 100.1,
        ),
        (
            "SHORT",
            {"open": 100.1, "high": 100.2, "low": 99.6, "close": 99.7},
            99.8, 99.9,
        ),
    ],
)
def test_ma5_ma15_cross_gate_accepts_without_outer_pivot_structure(
    side, bar, ma5_current, ma5_previous,
):
    allowed, reason = MacroCandleStructureGate().can_open(
        side,
        bar,
        {
            "kc_upper": 102.0, "kc_lower": 98.0, "kc_middle": 100.0,
            "ma15": 100.0, "atr": 2.0,
            "ma5_current": ma5_current, "ma5_previous": ma5_previous,
        },
        entry_pattern="CROSSOVER",
        structure_confirmation="MA5_MA15_1M_CROSS",
        structure_confirmed=True,
    )

    assert allowed is True
    assert reason == f"PASSED_MACRO_{side}_GATE"


def test_top_pivot_crossover_passes_outside_mid_channel_and_two_atr_bias():
    allowed, reason = MacroCandleStructureGate().can_open(
        "SHORT",
        {"open": 102.0, "high": 102.2, "low": 99.7, "close": 100.0},
        {
            "kc_upper": 101.0, "kc_lower": 99.0, "kc_middle": 100.0,
            "ma15": 104.0, "atr": 2.0,
            "ma5_current": 100.1, "ma5_previous": 100.2,
        },
        entry_pattern="CROSSOVER",
        structure_confirmation="CONFIRMED_KC_OUTER_PIVOT_THEN_MA5_MA15_CROSS",
        structure_confirmed=True,
    )
    assert allowed is True
    assert reason == "PASSED_MACRO_SHORT_GATE"


@pytest.mark.parametrize(
    "side,wick_ratio,expected",
    [
        ("LONG", 0.45, True),
        ("LONG", 0.51, False),
        ("SHORT", 0.45, True),
        ("SHORT", 0.51, False),
    ],
)
def test_outer_pivot_crossover_allows_moderate_wick_but_rejects_over_half(
    side, wick_ratio, expected,
):
    wick = wick_ratio / (1.0 - wick_ratio)
    if side == "LONG":
        bar = {
            "open": 100.0, "close": 101.0,
            "high": 101.0 + wick, "low": 100.0,
        }
        ma5_current, ma5_previous = 100.9, 100.8
    else:
        bar = {
            "open": 100.0, "close": 99.0,
            "high": 100.0, "low": 99.0 - wick,
        }
        ma5_current, ma5_previous = 99.1, 99.2
    allowed, reason = MacroCandleStructureGate().can_open(
        side,
        bar,
        {
            "kc_upper": 110.0, "kc_lower": 90.0, "kc_middle": 100.0,
            "ma15": 100.0, "atr": 1.0,
            "ma5_current": ma5_current, "ma5_previous": ma5_previous,
        },
        entry_pattern="CROSSOVER",
        structure_confirmation="CONFIRMED_KC_OUTER_PIVOT_THEN_MA5_MA15_CROSS",
        structure_confirmed=True,
    )

    assert allowed is expected
    if expected:
        assert reason == f"PASSED_MACRO_{side}_GATE"
    else:
        assert "峰谷交叉" in reason


def test_outer_pivot_then_ma_cross_rejects_wrong_ma5_slope():
    allowed, reason = MacroCandleStructureGate().can_open(
        "LONG",
        {"open": 100.0, "high": 100.4, "low": 99.9, "close": 100.3},
        {
            "kc_upper": 101.0, "kc_lower": 99.0, "kc_middle": 100.0,
            "ma15": 100.0, "atr": 2.0,
            "ma5_current": 100.1, "ma5_previous": 100.2,
        },
        entry_pattern="REVERSAL",
        structure_confirmation="CONFIRMED_KC_OUTER_PIVOT_THEN_MA5_MA15_CROSS",
        structure_confirmed=True,
    )
    assert allowed is False
    assert reason == "VETO_REVERSAL: MA5 斜率或價格位置逆向"


def test_breakout_short_requires_solid_body_and_rejects_extreme_lower_wick():
    indicators = {
        "kc_upper": 110.0, "kc_lower": 90.0, "kc_middle": 100.0,
        "ma15": 90.0, "atr": 2.0,
    }
    extreme_wick, reason = MacroCandleStructureGate().can_open(
        "SHORT",
        {"open": 92.0, "high": 92.1, "low": 87.8, "close": 90.0},
        indicators,
        entry_pattern="BREAKOUT",
        channel_expanding=True,
        structure_confirmation="KC_OUTER_BREAKOUT",
    )
    assert extreme_wick is False
    assert "下影線超過 K 棒區間 50%" in reason

    weak_body, reason = MacroCandleStructureGate().can_open(
        "SHORT",
        {"open": 91.0, "high": 91.1, "low": 88.9, "close": 90.0},
        indicators,
        entry_pattern="BREAKOUT",
        channel_expanding=True,
        structure_confirmation="KC_OUTER_BREAKOUT",
    )
    assert weak_body is False
    assert "實體比例未超過 K 棒區間 60%" in reason


@pytest.mark.parametrize(
    "side,bar,indicators",
    [
        (
            "LONG",
            {"open": 101.0, "high": 101.3, "low": 100.9, "close": 101.2},
            {
                "kc_upper": 101.0, "kc_lower": 99.0, "kc_middle": 100.0,
                "ma15": 101.0, "atr": 2.0, "ma5_current": 101.1,
                "ma5_previous": 101.0, "kc_middle_current": 100.1,
                "kc_middle_previous": 100.0,
            },
        ),
        (
            "SHORT",
            {"open": 99.0, "high": 99.1, "low": 98.7, "close": 98.8},
            {
                "kc_upper": 101.0, "kc_lower": 99.0, "kc_middle": 100.0,
                "ma15": 99.0, "atr": 2.0, "ma5_current": 98.9,
                "ma5_previous": 99.0, "kc_middle_current": 99.9,
                "kc_middle_previous": 100.0,
            },
        ),
    ],
)
def test_profit_lock_continuation_gate_requires_outer_location_and_aligned_trend(
    side, bar, indicators,
):
    allowed, reason = MacroCandleStructureGate().can_open(
        side,
        bar,
        indicators,
        entry_pattern="CONTINUATION",
        structure_confirmation="PROFIT_LOCK_CONTINUATION",
        structure_confirmed=True,
    )
    assert allowed is True
    assert reason == f"PASSED_MACRO_{side}_GATE"


@pytest.mark.parametrize(
    "side,bar",
    [
        ("LONG", {"open": 99.8, "high": 100.1, "low": 98.0, "close": 100.0}),
        ("SHORT", {"open": 100.2, "high": 102.0, "low": 99.9, "close": 100.0}),
    ],
)
def test_reversal_gate_requires_directional_exhaustion_pinbar(side, bar):
    indicators = {
        "kc_upper": 110.0, "kc_lower": 90.0, "kc_middle": 100.0,
        "ma15": 100.0, "atr": 2.0,
    }

    allowed, reason = MacroCandleStructureGate().can_open(
        side,
        bar,
        indicators,
        entry_pattern="REVERSAL",
        structure_confirmation="PINBAR_EXHAUSTION_TURN",
        structure_confirmed=True,
    )

    assert allowed is True
    assert reason == f"PASSED_MACRO_{side}_GATE"


def test_gate_fails_closed_for_missing_or_non_finite_structure_values():
    gate = MacroCandleStructureGate()
    bar = {"open": 99.0, "high": 100.1, "low": 98.9, "close": 100.0}
    indicators = {
        "kc_upper": 110.0, "kc_lower": 90.0, "kc_middle": 100.0,
        "ma15": 100.0, "atr": 2.0,
    }

    assert gate.can_open(
        "LONG", bar, {**indicators, "atr": float("nan")},
        entry_pattern="CONTINUATION",
    ) == (
        False, "VETO_INVALID_STRUCTURE_DATA",
    )
    assert gate.can_open(
        "LONG", bar, {key: value for key, value in indicators.items() if key != "atr"},
        entry_pattern="CONTINUATION",
    ) == (
        False, "VETO_INVALID_STRUCTURE_DATA",
    )


def test_gate_rejects_missing_or_conflicting_entry_pattern():
    bar = {"open": 99.0, "high": 100.1, "low": 98.9, "close": 100.0}
    indicators = {
        "kc_upper": 110.0, "kc_lower": 90.0, "kc_middle": 100.0,
        "ma15": 100.0, "atr": 2.0,
    }
    gate = MacroCandleStructureGate()

    assert gate.can_open("LONG", bar, indicators) == (
        False, "REJECT_UNAUTHORIZED_ENTRY_PATTERN",
    )
    assert gate.can_open(
        "LONG", bar, indicators, entry_pattern="BREAKOUT",
        entry_signal_mode="CONTINUATION",
    ) == (False, "REJECT_UNAUTHORIZED_ENTRY_PATTERN")
