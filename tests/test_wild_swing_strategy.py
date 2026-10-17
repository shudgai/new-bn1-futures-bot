import pandas as pd
import pytest

from core.gates.macro_candle_structure_gate import MacroCandleStructureGate
from core.gates.entry_pattern_permitted_gate import EntryPatternPermittedGate
from core.services.strategies.wild_swing_strategy import WildSwingStrategy


def _frame(side):
    rows = []
    direction = -1 if side == "SHORT" else 1
    for index in range(12):
        middle = 100 + direction * index * 0.1
        ma15 = middle + (0.2 if side == "SHORT" else -0.2)
        close = middle + (0.4 if side == "SHORT" else -0.4)
        rows.append({
            "timestamp": index * 60_000,
            "open": close - direction * 0.1,
            "high": close + 0.2,
            "low": close - 0.2,
            "close": close,
            "volume": 10.0,
            "kc_upper": middle + 1.0,
            "kc_middle": middle,
            "kc_lower": middle - 1.0,
            "ma5": ma15 + (0.1 if side == "SHORT" else -0.1),
            "ma15": ma15,
            "is_closed": True,
        })

    if side == "SHORT":
        rows[-2].update(open=100.4, high=100.6, low=100.2, close=100.4,
                        kc_middle=100.0, ma15=100.2, ma5=100.3)
        rows[-1].update(open=100.1, high=100.2, low=99.4, close=99.5,
                        kc_middle=99.9, ma15=100.1, ma5=100.0)
    else:
        rows[-2].update(open=99.6, high=99.8, low=99.4, close=99.6,
                        kc_upper=101.0, kc_lower=99.0,
                        kc_middle=100.0, ma15=99.8, ma5=99.7)
        rows[-1].update(open=99.9, high=100.6, low=99.8, close=100.5,
                        kc_upper=102.0, kc_lower=99.0,
                        kc_middle=100.1, ma15=99.9, ma5=100.0)
    if side == "SHORT":
        rows[-2]["kc_upper"] = 101.5
        rows[-2]["kc_lower"] = 97.5
        rows[-1]["kc_upper"] = 101.5
        rows[-1]["kc_lower"] = 97.5
    return pd.DataFrame(rows)


def _pivot_cross_frame(side):
    frame = _frame(side)
    for index in range(len(frame)):
        frame.loc[index, [
            "open", "high", "low", "close", "volume", "kc_upper",
            "kc_middle", "kc_lower", "ma5", "ma15",
        ]] = [100.0, 100.3, 99.7, 100.05, 10.0, 101.0, 100.0, 99.0, 99.8, 100.0]
    if side == "LONG":
        frame.loc[8, ["high", "low"]] = [100.2, 99.2]
        frame.loc[9, ["open", "high", "low", "close", "kc_lower"]] = [
            99.0, 99.6, 98.0, 99.2, 98.1,
        ]
        frame.loc[10, ["high", "low", "ma5", "ma15"]] = [100.1, 99.0, 99.9, 100.0]
        frame.loc[11, [
            "open", "high", "low", "close", "ma5", "ma15",
        ]] = [99.9, 100.5, 99.8, 100.4, 100.1, 100.0]
    else:
        frame.loc[8, ["high", "low"]] = [100.8, 99.8]
        frame.loc[9, ["open", "high", "low", "close", "kc_upper"]] = [
            101.0, 102.0, 100.4, 100.8, 101.9,
        ]
        frame.loc[10, ["high", "low", "ma5", "ma15"]] = [101.2, 99.9, 100.1, 100.0]
        frame.loc[11, [
            "open", "high", "low", "close", "ma5", "ma15",
        ]] = [100.0, 100.1, 99.5, 99.6, 99.9, 100.0]
    return frame


def _profit_lock_continuation_frame(side):
    frame = _pivot_cross_frame(side)
    if side == "LONG":
        frame.loc[10, ["kc_upper", "kc_middle", "ma5", "ma15", "close", "high"]] = [
            100.2, 100.0, 100.3, 100.2, 100.4, 100.5,
        ]
        frame.loc[11, [
            "kc_upper", "kc_middle", "ma5", "ma15", "kc_lower",
            "open", "high", "low", "close",
        ]] = [100.2, 100.1, 100.4, 100.3, 98.0, 100.4, 100.7, 100.3, 100.6]
    else:
        frame.loc[10, ["kc_lower", "kc_middle", "ma5", "ma15", "close", "low"]] = [
            99.8, 100.0, 99.7, 99.8, 99.6, 99.5,
        ]
        frame.loc[11, [
            "kc_lower", "kc_middle", "ma5", "ma15", "kc_upper",
            "open", "high", "low", "close",
        ]] = [99.8, 99.9, 99.6, 99.7, 102.0, 99.6, 99.7, 99.3, 99.4]
    return frame


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_ma_cross_entry_needs_no_kc_outer_pivot(side):
    frame = _pivot_cross_frame(side)
    frame["atr"] = 2.0
    frame["kc_upper"] = 102.0
    frame["kc_lower"] = 98.0
    for index in frame.index[:-1]:
        open_price = float(frame.loc[index, "open"])
        close = float(frame.loc[index, "close"])
        frame.loc[index, "high"] = max(open_price, close) + 0.1
        frame.loc[index, "low"] = min(open_price, close) - 0.1
    if side == "LONG":
        frame.loc[10, "kc_middle"] = 100.0
        frame.loc[11, "kc_middle"] = 100.1
        frame.loc[10, "ma15"] = 100.2
        frame.loc[11, "ma15"] = 100.0
    else:
        frame.loc[10, "kc_middle"] = 100.0
        frame.loc[11, "kc_middle"] = 99.9
        frame.loc[10, "ma15"] = 99.8
        frame.loc[11, "ma15"] = 100.0

    signal = WildSwingStrategy().evaluate_entry("LOBSTER/USDT", frame, side)
    assert signal is not None
    assert signal["side"] == side
    assert signal["reason"] == "MA_CROSSOVER_ENTRY"
    assert signal["entry_pattern"] == "CROSSOVER"
    assert signal["structure_confirmation"] == "MA_CROSSOVER"
    assert "pivot_bar_id" not in signal
    assert "pivot_price" not in signal
    assert signal["bar_id"] == frame.iloc[-1]["timestamp"]
    assert (
        frame.iloc[-1]["ma15"] - frame.iloc[-2]["ma15"] < 0
        if side == "LONG"
        else frame.iloc[-1]["ma15"] - frame.iloc[-2]["ma15"] > 0
    )

    current = frame.iloc[-1]
    allowed, reason = MacroCandleStructureGate().can_open(
        side,
        {
            name: float(current[name])
            for name in ("open", "high", "low", "close")
        },
        {
            name: float(current[name])
            for name in ("kc_upper", "kc_lower", "kc_middle", "ma15", "atr")
        } | {
            "ma5_current": signal["ma5_current"],
            "ma5_previous": signal["ma5_previous"],
            "kc_middle_current": signal["kc_middle_current"],
            "kc_middle_previous": signal["kc_middle_previous"],
        },
        entry_pattern=signal["entry_pattern"],
        structure_confirmation=signal["structure_confirmation"],
        structure_confirmed=signal["structure_confirmed"],
        channel_expanding=signal["channel_expanding"],
    )
    assert allowed is True
    assert reason == f"PASSED_MACRO_{side}_PURE_MATH_GATE"
    pattern_allowed, _ = EntryPatternPermittedGate.check_signal(signal)
    assert pattern_allowed is True

    live_frame = frame.copy()
    live_frame.loc[live_frame.index[-1], "is_closed"] = False
    assert WildSwingStrategy().evaluate_entry("LOBSTER/USDT", live_frame, side) is None


def test_short_crossover_is_blocked_when_kc_middle_rises():
    frame = _pivot_cross_frame("SHORT")
    frame.loc[10, "kc_middle"] = 100.0
    frame.loc[11, "kc_middle"] = 100.1

    signal = WildSwingStrategy().evaluate_entry(
        "LOBSTER/USDT", frame, "SHORT",
    )
    assert signal is None


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_invalid_latest_closed_bar_cannot_fall_back_to_stale_entry(side):
    frame = _pivot_cross_frame(side)
    stale_signal_bar = frame.iloc[-1].copy()
    invalid_latest = stale_signal_bar.copy()
    invalid_latest["timestamp"] += 60_000
    invalid_latest["ma5"] = float("nan")
    frame = pd.concat(
        [frame, invalid_latest.to_frame().T],
        ignore_index=True,
    )

    assert WildSwingStrategy().evaluate_entry(
        "LOBSTER/USDT", frame, side,
    ) is None


@pytest.mark.parametrize(
    "side,current_middle",
    [("LONG", 100.6), ("SHORT", 99.5)],
)
def test_ma_crossover_requires_close_on_correct_side_of_kc_basis(
    side, current_middle,
):
    frame = _pivot_cross_frame(side)
    frame.loc[10, "kc_middle"] = 100.0
    frame.loc[11, "kc_middle"] = current_middle

    assert WildSwingStrategy().evaluate_entry(
        "LOBSTER/USDT", frame, side,
    ) is None


def test_short_crossover_is_not_generated_at_kc_upper_rail():
    frame = _pivot_cross_frame("SHORT")
    frame.loc[10, ["kc_middle", "ma5", "ma15"]] = [100.0, 100.3, 100.0]
    frame.loc[11, [
        "open", "high", "low", "close", "kc_upper", "kc_lower",
        "kc_middle", "ma5", "ma15",
    ]] = [100.2, 100.3, 99.9, 100.0, 100.0, 98.0, 99.9, 100.1, 100.2]

    assert WildSwingStrategy().evaluate_entry(
        "LOBSTER/USDT", frame, "SHORT",
    ) is None


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_live_outside_continuation_waits_for_candle_close(side):
    frame = _frame(side)
    frame["atr"] = 2.0
    frame["open"] = 100.0
    frame["high"] = 100.2
    frame["low"] = 99.8
    frame["close"] = 100.0
    frame["kc_upper"] = 105.0 if side == "LONG" else 104.5
    frame["kc_lower"] = 95.0 if side == "LONG" else 94.5
    frame["kc_middle"] = 100.0
    frame["ma5"] = 100.0
    frame["ma15"] = 100.0
    if side == "LONG":
        frame.loc[10, "kc_middle"] = 99.9
        frame.loc[11, "kc_middle"] = 100.0
        frame.loc[10, ["open", "high", "low", "close", "ma5"]] = [
            100.0, 105.0, 99.9, 104.9, 100.1,
        ]
        frame.loc[11, ["open", "high", "low", "close", "ma5", "kc_upper"]] = [
            106.0, 106.6, 105.8, 106.5, 100.2, 105.5,
        ]
    else:
        frame.loc[10, "kc_middle"] = 100.1
        frame.loc[11, "kc_middle"] = 100.0
        frame.loc[10, ["open", "high", "low", "close", "ma5"]] = [
            100.0, 100.1, 95.0, 95.1, 99.9,
        ]
        frame.loc[11, ["open", "high", "low", "close", "ma5", "kc_lower"]] = [
            94.0, 94.1, 93.4, 93.5, 99.8, 94.5,
        ]
    frame.attrs["timeframe_ms"] = 60_000
    live = frame.iloc[-1].copy()
    live["timestamp"] += 60_000
    live["is_closed"] = False
    if side == "LONG":
        live["open"], live["high"], live["low"], live["close"] = (
            106.5, 106.6, 106.5, 106.55,
        )
    else:
        live["open"], live["high"], live["low"], live["close"] = (
            93.5, 93.5, 93.4, 93.45,
        )
    frame = pd.concat([frame, live.to_frame().T], ignore_index=True)
    frame.attrs["timeframe_ms"] = 60_000

    closed_signal = WildSwingStrategy().evaluate_entry(
        "LOBSTER/USDT", frame, side,
    )
    signal = WildSwingStrategy().evaluate_entry(
        "LOBSTER/USDT", frame, side, tick_price=float(live["close"]),
    )

    assert signal == closed_signal
    assert signal is not None
    assert signal["is_intrabar"] is False
    assert signal["bar_id"] == float(frame.iloc[-2]["timestamp"])


@pytest.mark.parametrize(
    "side,updates",
    [
        (
            "SHORT",
            {
                -2: {
                    "open": 100.2, "high": 100.3, "low": 99.9, "close": 100.0,
                    "kc_upper": 101.5, "kc_lower": 99.8,
                    "ma5": 99.8, "ma15": 100.0,
                },
                -1: {
                    "open": 99.9, "high": 100.0, "low": 98.8, "close": 99.0,
                    "kc_upper": 101.2, "kc_lower": 99.2,
                    "ma5": 99.7, "ma15": 99.9,
                },
            },
        ),
        (
            "LONG",
            {
                -2: {
                    "open": 100.0, "high": 100.2, "low": 99.8, "close": 100.0,
                    "kc_upper": 100.2, "kc_lower": 98.5,
                    "ma5": 100.1, "ma15": 99.9,
                },
                -1: {
                    "open": 100.1, "high": 101.2, "low": 100.0, "close": 101.0,
                    "kc_upper": 100.8, "kc_lower": 98.3,
                    "ma5": 100.2, "ma15": 100.0,
                },
            },
        ),
    ],
)
def test_expanding_outer_band_breakout_with_solid_body_creates_breakout_signal(
    side, updates,
):
    frame = _frame(side)
    frame["atr"] = 2.0
    for offset, values in updates.items():
        row_index = frame.index[offset]
        for name, value in values.items():
            frame.loc[row_index, name] = value

    signal = WildSwingStrategy().evaluate_entry("LOBSTER/USDT", frame, side)

    assert signal is not None
    assert signal["side"] == side
    assert signal["reason"] == "CONTINUATION_OUTSIDE_KC_ENTRY"
    assert signal["entry_pattern"] == "CONTINUATION"
    assert signal["structure_confirmation"] == "CONTINUATION_OUTSIDE_KC"


def test_breakout_entry_rejects_body_ratio_at_or_below_sixty_percent():
    frame = _frame("SHORT")
    previous_index, current_index = frame.index[-2], frame.index[-1]
    frame.loc[previous_index, ["close", "kc_lower"]] = [100.0, 99.8]
    frame.loc[current_index, [
        "open", "high", "low", "close", "kc_upper", "kc_lower",
    ]] = [99.9, 100.5, 98.5, 99.0, 101.2, 99.2]

    assert WildSwingStrategy().evaluate_entry(
        "LOBSTER/USDT", frame, "SHORT",
    ) is None


def test_consecutive_green_candles_without_new_outer_pivot_and_cross_do_not_enter():
    frame = _frame("LONG")
    frame.loc[8:, "open"] = 99.8
    frame.loc[8:, "close"] = [99.9, 100.0, 100.1, 100.2]
    frame.loc[8:, "high"] = [100.0, 100.1, 100.2, 100.3]
    frame.loc[8:, "low"] = [99.7, 99.8, 99.9, 100.0]
    assert WildSwingStrategy().evaluate_entry("LOBSTER/USDT", frame, "LONG") is None


def test_outer_band_breakout_is_not_an_initial_entry_by_itself():
    frame = _frame("LONG")
    frame.loc[10, ["open", "high", "low", "close"]] = [100.0, 101.2, 99.9, 101.1]
    frame.loc[11, ["open", "high", "low", "close"]] = [101.1, 101.8, 101.0, 101.6]
    frame.loc[10, ["ma5", "ma15"]] = [100.0, 99.9]
    frame.loc[11, ["ma5", "ma15"]] = [99.8, 100.0]
    assert WildSwingStrategy().evaluate_entry("LOBSTER/USDT", frame, "LONG") is None


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_entry_mode_is_independent_of_profit_lock_metadata(side):
    frame = _profit_lock_continuation_frame(side)
    signal = WildSwingStrategy().evaluate_entry(
        "LOBSTER/USDT",
        frame,
        side,
    )
    assert signal is not None
    assert signal["reason"] == "CONFIRMED_2BAR_BREAKOUT_ENTRY"
    assert signal["structure_confirmation"] == "CONFIRMED_2BAR_BREAKOUT"
    assert signal["entry_pattern"] == "BREAKOUT"


def test_no_math_entry_is_created_for_flat_stall_after_profit_lock():
    frame = _pivot_cross_frame("LONG")
    frame.loc[9:, ["high", "close"]] = [[100.2, 100.2]] * 3
    frame.loc[9:, "volume"] = [10.0, 10.0, 5.0]
    frame.loc[10, ["kc_upper", "kc_middle", "ma5", "ma15"]] = [
        100.2, 100.0, 100.3, 100.3,
    ]
    frame.loc[11, ["kc_upper", "kc_middle", "ma5", "ma15",
                   "kc_lower", "open", "low"]] = [
        100.2, 100.1, 100.4, 100.4, 98.0, 100.4, 100.3,
    ]
    assert WildSwingStrategy().evaluate_entry(
        "LOBSTER/USDT", frame, "LONG",
    ) is None


def test_latest_profit_lock_exit_requires_most_recent_matching_close():
    trades = [
        {
            "symbol": "LOBSTER/USDT", "action": "CLOSE_LONG",
            "entry_mode": "WILD_SWING", "reason": "Wild Swing EXIT_BY_PROFIT_LOCK",
            "id": 1_740_000_000_000,
        },
        {
            "symbol": "LOBSTER/USDT", "action": "OPEN_LONG",
            "entry_mode": "WILD_SWING",
        },
    ]
    assert WildSwingStrategy.latest_profit_lock_exit(
        trades, "LOBSTER/USDT",
    ) is not None
    assert WildSwingStrategy.latest_profit_lock_exit(
        [trades[1], trades[0]], "LOBSTER/USDT",
    ) is None


@pytest.mark.parametrize(
    "side,previous_ma5,previous_ma15,current_ma5,current_ma15,close,middle,expected",
    [
        ("SHORT", 100.3, 100.2, 100.4, 100.1, 99.5, 99.9, "EXIT_BY_MA5_TREND_REVERSAL"),
        ("SHORT", 99.9, 100.0, 100.2, 100.1, 99.5, 99.9, "EXIT_BY_MA_GOLDEN_CROSS"),
        ("SHORT", 100.3, 100.4, 100.1, 100.4, 100.1, 100.0, "EXIT_BY_KC_BAND_BREACH"),
        ("LONG", 99.7, 99.8, 99.6, 99.7, 100.5, 100.1, "EXIT_BY_MA5_TREND_REVERSAL"),
        ("LONG", 100.1, 100.0, 99.8, 99.9, 100.5, 100.1, "EXIT_BY_MA_DEATH_CROSS"),
        ("LONG", 99.7, 99.8, 99.8, 99.8, 100.0, 100.1, "EXIT_BY_KC_BAND_BREACH"),
    ],
)
def test_exit_triggers_on_ma_reversal_or_kc_close(
    side, previous_ma5, previous_ma15, current_ma5, current_ma15,
    close, middle, expected,
):
    frame = _frame(side)
    frame.loc[10, ["ma5", "ma15"]] = [previous_ma5, previous_ma15]
    frame.loc[11, ["ma5", "ma15", "close", "kc_middle"]] = [
        current_ma5, current_ma15, close, middle,
    ]
    result = WildSwingStrategy().evaluate_exit(
        "LOBSTER/USDT", {"side": side}, frame,
    )
    assert result is not None
    assert result["action"] == f"CLOSE_{side}"
    assert result["reason"] == expected


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_exit_holds_when_trend_and_kc_position_remain_favorable(side):
    result = WildSwingStrategy().evaluate_exit(
        "LOBSTER/USDT", {"side": side}, _frame(side),
    )
    assert result is None


def test_exit_checks_ma5_reversal_from_live_indicator_on_every_tick():
    frame = _frame("SHORT")
    frame.loc[11, "is_closed"] = False
    frame.loc[11, "ma5"] = 100.4

    result = WildSwingStrategy().evaluate_exit(
        "LOBSTER/USDT", {"side": "SHORT"}, frame,
    )
    assert result is not None
    assert result["reason"] == "EXIT_BY_MA5_TREND_REVERSAL"


def test_exit_kc_trend_reversal_fail_safe():
    """第一道防線：持有多單時，只要 KC 通道翻空或中軌下行，立即市價全平多單，嚴禁逆勢死扛"""
    frame = _frame("LONG")
    # 將 KC 中軌改成向下翻空 (100.2 -> 99.8)
    frame.loc[10, "kc_middle"] = 100.2
    frame.loc[11, "kc_middle"] = 99.8
    frame.loc[11, "kc_trend"] = "DOWN"
    # MA 保持微幅多頭，以凸顯 KC 翻空的第一道防線清倉效力
    frame.loc[10, ["ma5", "ma15"]] = [100.0, 99.8]
    frame.loc[11, ["ma5", "ma15", "close"]] = [100.2, 99.9, 100.3]

    result = WildSwingStrategy().evaluate_exit(
        "LOBSTER/USDT", {"side": "LONG"}, frame,
    )
    assert result is not None
    assert result["action"] == "CLOSE_LONG"
    assert result["reason"] == "EXIT_BY_KC_BAND_BREACH"


def test_exit_trailing_profit_lock_and_breakeven():
    """第三道防線：持有多單浮盈達標 (+1.2%) 後，利潤回撤超過 0.8% 立即動態移動止盈；跌破成本+手續費立刻保本平倉"""
    frame = _frame("LONG")
    # 保持均線向上與通道向上
    frame.loc[10, ["ma5", "ma15", "kc_middle"]] = [100.0, 99.8, 100.0]
    frame.loc[11, ["ma5", "ma15", "kc_middle", "close"]] = [100.2, 99.9, 100.1, 100.3]

    # 開倉價 100.0，最高衝到 101.5 (+1.5% 浮盈)，當前跌到 100.5 (從高點回撤 1.0% >= 0.8%)
    pos = {
        "side": "LONG",
        "entry_price": 100.0,
        "highest_price": 101.5,
    }
    result = WildSwingStrategy().evaluate_exit(
        "LOBSTER/USDT", pos, frame, quote=100.5,
    )
    assert result is not None
    assert result["reason"] == "EXIT_BY_TRAILING_PROFIT_LOCK"

    # 若衝到 101.3 後跌回保本線 100.1 (< entry + 0.15U)
    pos2 = {
        "side": "LONG",
        "entry_price": 100.0,
        "highest_price": 101.3,
    }
    result2 = WildSwingStrategy().evaluate_exit(
        "LOBSTER/USDT", pos2, frame, quote=100.1,
    )
    assert result2 is not None
    assert result2["reason"] in ("EXIT_BY_TRAILING_PROFIT_LOCK", "EXIT_BY_BREAKEVEN")


def test_exit_hard_stop_loss():
    """第四道防線：虧損達到 -2.5% 或 2.2 ATR 時，觸發硬性止損清倉"""
    frame = _frame("LONG")
    # 均線保持向上但盤中長黑插針暴跌
    frame.loc[10, ["ma5", "ma15", "kc_middle"]] = [100.0, 99.8, 100.0]
    frame.loc[11, ["ma5", "ma15", "kc_middle", "close", "low"]] = [100.2, 99.9, 100.1, 97.0, 96.5]

    pos = {
        "side": "LONG",
        "entry_price": 100.0,
    }
    result = WildSwingStrategy().evaluate_exit(
        "LOBSTER/USDT", pos, frame, quote=97.0,
    )
    assert result is not None
    assert result["reason"] in ("EXIT_BY_HARD_STOP_LOSS", "EXIT_BY_KC_BAND_BREACH")


def test_exit_entry_bar_immunity_and_extreme_pinbar_exception():
    """開倉當根 (<= 60s) 保護：免疫普通回踩與硬止損，除非成型長上影極端力竭避雷針"""
    import time
    now_ts = time.time()
    frame = _frame("LONG")
    # 普通微幅回踩跌破 MA5
    frame.loc[10, ["ma5", "ma15", "kc_middle"]] = [100.0, 99.8, 100.0]
    frame.loc[11, ["ma5", "ma15", "kc_middle", "close"]] = [99.7, 99.9, 100.1, 99.6]

    pos = {
        "side": "LONG",
        "entry_price": 100.0,
        "open_timestamp": now_ts - 20.0,  # 20秒前剛開倉 (處於當根)
    }
    # 普通回踩在當根內應受到保護，繼續跑 (返回 None)
    result = WildSwingStrategy().evaluate_exit(
        "LOBSTER/USDT", pos, frame, quote=99.6,
    )
    assert result is None

    # 若開倉當根衝高形成極端避雷針 (High衝破上軌，超長上影線 >= 2倍實體)
    frame.loc[11, "high"] = 104.0
    frame.loc[11, "open"] = 100.0
    frame.loc[11, "close"] = 100.5  # 實體 0.5，上影線 104.0 - 100.5 = 3.5 (7倍實體)
    frame.loc[11, "kc_upper"] = 102.0
    result_pinbar = WildSwingStrategy().evaluate_exit(
        "LOBSTER/USDT", pos, frame, quote=100.1,
    )
    assert result_pinbar is not None
    assert result_pinbar["reason"] == "EXIT_LONG_BLOW_OFF_TOP"

