import asyncio
from types import SimpleNamespace

import pandas as pd
import pytest

from core.gates.breakout_continuation_entry_gate import (
    CONFIRMED_2BAR_BREAKOUT, CONTINUATION_OUTSIDE_KC,
    BreakoutContinuationEntryGate as Gate,
)
from core.gates.central_entry_gate import CentralEntryGate
from core.gates.macro_candle_structure_gate import MacroCandleStructureGate
from core.gates.meme_entry_policy import is_exhausted_wick, uses_relaxed_entry_limits
from core.services.entry_methods import (
    KC_TWO_BAR_BREAKOUT, POST_CLOSE_KC_REENTRY,
    evaluate_entry_method, revalidate_entry_method,
)
from core.services.math_execution_gate import validate_entry_at_execution, validate_entry_math
from core.services.entry_firewall import has_valid_wild_outer_confirmation
from core.services.strategies.wild_swing_strategy import WildSwingStrategy
from test_entry_methods import method_context, post_close_market
from test_central_entry_gate import _build_valid_long_frame


SYMBOLS = ["1000PEPE/USDT", "龙虾/USDT"]


@pytest.mark.parametrize("symbol", [
    "1000PEPE/USDT", "1000PEPE/USDT:USDT", "1000PEPEUSDT",
    "PEPE/USDT", "PEPEUSDT", "龙虾/USDT", "龍蝦/USDT:USDT", "LOBSTER/USDT",
])
def test_policy_includes_only_approved_aliases(symbol):
    assert uses_relaxed_entry_limits(symbol)


@pytest.mark.parametrize("symbol", ["CAP/USDT", "CAP/USDT:USDT", "BTC/USDT", "", None])
def test_policy_does_not_relax_other_symbols(symbol):
    assert not uses_relaxed_entry_limits(symbol)


@pytest.mark.parametrize(
    "wick,body,span,expected",
    [(65, 20, 100, False), (65.001, 20, 100, True),
     (30, 20, 40, False), (30.001, 20, 40, True), (20, 20, 100, False)],
)
def test_exhaustion_requires_both_strict_thresholds(wick, body, span, expected):
    assert is_exhausted_wick(wick, body, span) is expected


@pytest.mark.parametrize("symbol", SYMBOLS + ["CAP/USDT"])
@pytest.mark.parametrize("side", ["LONG", "SHORT"])
@pytest.mark.parametrize("method", [KC_TWO_BAR_BREAKOUT, POST_CLOSE_KC_REENTRY])
def test_large_ma15_bias_shared_revalidation(symbol, side, method):
    market = post_close_market(side)
    sign = 1 if side == "LONG" else -1
    quote = float(market.iloc[-1]["close"])
    market.loc[market.index[-1], "ma15"] = quote - sign * 10
    exit_stamp = 1_265_000 if method == POST_CLOSE_KC_REENTRY else None
    signal = evaluate_entry_method(
        market, quote=quote, symbol=symbol, expected_method=method,
        last_exit_timestamp=exit_stamp,
    )
    assert signal
    context = method_context(signal, exit_stamp)
    market.attrs["entry_method_context"] = {"symbol": symbol, **context}
    market.attrs["entry_method_quote"] = quote
    results = [
        revalidate_entry_method(market, symbol=symbol, side=side, quote=quote, context=context),
        validate_entry_math(market, side),
        asyncio.run(validate_entry_at_execution(
            SimpleNamespace(), symbol, side, {**context, "entry_gate_frame": market},
            execution_price=quote,
        )),
    ]
    central = CentralEntryGate.verify_and_authorize(
        symbol=symbol, side=side, price=quote, klines_or_snapshot=market,
    )
    results.append((central.passed, central.reason))
    for allowed, reason in results:
        assert allowed is (symbol != "CAP/USDT"), reason
        if not allowed:
            assert "MA15_BIAS" in reason


@pytest.mark.parametrize("symbol", SYMBOLS + ["CAP/USDT"])
@pytest.mark.parametrize("side", ["LONG", "SHORT"])
@pytest.mark.parametrize("factor,allowed", [(1.6, True), (3.0, False)])
def test_outer_wick_policy_at_method_and_strategy(symbol, side, factor, allowed):
    market = post_close_market(side)
    row = market.iloc[-1]
    sign = 1 if side == "LONG" else -1
    body = abs(row["close"] - row["open"])
    market.loc[market.index[-1], "high" if side == "LONG" else "low"] = row["close"] + sign * body * factor
    quote = float(row["close"])
    signal = evaluate_entry_method(
        market, quote=quote, symbol=symbol, expected_method=KC_TWO_BAR_BREAKOUT,
    )
    assert signal
    result, reason = revalidate_entry_method(
        market, symbol=symbol, side=side, quote=quote, context=method_context(signal),
    )
    expected = allowed and symbol != "CAP/USDT"
    assert result is expected, reason
    exhausted = WildSwingStrategy._has_directional_exhaustion_wick(
        market.iloc[-1], side, symbol=symbol, entry_type=CONFIRMED_2BAR_BREAKOUT,
    )
    assert exhausted is not expected


@pytest.mark.parametrize("symbol", SYMBOLS)
@pytest.mark.parametrize("side", ["LONG", "SHORT"])
@pytest.mark.parametrize("method", [KC_TWO_BAR_BREAKOUT, POST_CLOSE_KC_REENTRY])
@pytest.mark.parametrize("distance,allowed", [(3.5, True), (3.50001, False)])
def test_final_quote_ma5_distance_limit(symbol, side, method, distance, allowed):
    market = post_close_market(side)
    sign = 1 if side == "LONG" else -1
    quote = float(market.iloc[-1]["ma5"]) + sign * distance
    exit_stamp = 1_265_000 if method == POST_CLOSE_KC_REENTRY else None
    signal = evaluate_entry_method(
        market, quote=quote, symbol=symbol, expected_method=method,
        last_exit_timestamp=exit_stamp,
    )
    assert signal
    result, reason = revalidate_entry_method(
        market, symbol=symbol, side=side, quote=quote,
        context=method_context(signal, exit_stamp),
    )
    assert result is allowed, reason
    if not result:
        assert "MA5_DISTANCE" in reason


def chase_inputs(side, *, count=2, distance=0.2):
    sign = 1 if side == "LONG" else -1
    edge = 101 if side == "LONG" else 99
    price = edge + sign * distance
    rows = [{"close": 100, "atr": 1}] + [{"close": price, "atr": 1}] * count
    candidate = {
        "type": CONTINUATION_OUTSIDE_KC, "action": f"OPEN_{side}",
        "price": price, "ma5_current": edge, "kc_upper": 101, "kc_lower": 99,
    }
    return candidate, rows, [101] * len(rows), [99] * len(rows)


@pytest.mark.parametrize("symbol", SYMBOLS + ["CAP/USDT", None])
@pytest.mark.parametrize("side", ["LONG", "SHORT"])
@pytest.mark.parametrize("count", [8, 9, 12, 13])
def test_continuation_bar_limit_is_per_symbol(symbol, side, count):
    args = chase_inputs(side, count=count)
    reason = Gate.late_chase_block_reason(*args, symbol=symbol)
    limit = 12 if symbol in SYMBOLS else 8
    assert (reason is None) is (count <= limit)
    if reason:
        assert "STALE_CONTINUATION" in reason


@pytest.mark.parametrize("symbol", SYMBOLS + ["CAP/USDT"])
@pytest.mark.parametrize("side", ["LONG", "SHORT"])
@pytest.mark.parametrize("distance", [2.5, 3.5, 3.50001])
def test_late_chase_band_limit_and_final_tick(symbol, side, distance):
    args = chase_inputs(side, distance=distance)
    reason = Gate.late_chase_block_reason(*args, symbol=symbol)
    assert (reason is None) is (distance <= (3.5 if symbol in SYMBOLS else 2.5))
    candidate, rows, up, low = chase_inputs(side)
    sign = 1 if side == "LONG" else -1
    edge = 101 if side == "LONG" else 99
    reason = Gate.late_chase_block_reason(
        candidate, rows, up, low, symbol=symbol, tick_price=edge + sign * 3.6,
    )
    assert "BAND_EXTENSION" in reason
    reason = Gate.late_chase_block_reason(
        *args, symbol=symbol, tick_price=edge + sign * 0.1,
    )
    assert (reason is None) is (distance <= (3.5 if symbol in SYMBOLS else 2.5))


@pytest.mark.parametrize("symbol", SYMBOLS + ["CAP/USDT"])
@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_scan_and_final_confirmation_use_same_symbol_limits(monkeypatch, symbol, side):
    candidate, rows, up, low = chase_inputs(side, count=10, distance=3.0)
    monkeypatch.setattr(Gate, "_evaluate_pure_math_raw", lambda *args, **kwargs: candidate)
    for row in rows:
        row.update(kc_upper=101, kc_lower=99, kc_middle=100, ma5=100, ma15=100)
    scan = Gate.evaluate_pure_math(rows, up, low, [100] * len(rows),
                                  [100] * len(rows), [100] * len(rows), symbol=symbol)
    final = Gate.validate_closed_outer_entry(
        rows, side, CONTINUATION_OUTSIDE_KC, symbol=symbol,
    )
    assert (scan is not None) is (symbol in SYMBOLS)
    assert (final is not None) is (symbol in SYMBOLS)


@pytest.mark.parametrize("symbol", SYMBOLS)
@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_pure_math_macro_route_still_rejects_extreme_wick(symbol, side):
    sign = 1 if side == "LONG" else -1
    bar = {"open": 100, "close": 100 + sign, "high": 105, "low": 95}
    bar["high" if side == "LONG" else "low"] = 100 + sign * 5
    bar["low" if side == "LONG" else "high"] = 100 - sign * 0.1
    indicators = {
        "kc_upper": 100.5, "kc_middle": 100, "kc_lower": 99.5, "ma15": 100,
        "atr": 1, "ma5_current": 100 + sign * 0.2, "ma5_previous": 100,
    }
    allowed, reason = MacroCandleStructureGate().can_open(
        side, bar, indicators, symbol=symbol, entry_pattern="BREAKOUT",
        structure_confirmation=CONFIRMED_2BAR_BREAKOUT, structure_confirmed=True,
    )
    assert not allowed
    assert "影線" in reason


def central_frame(side):
    market = _build_valid_long_frame()
    if side == "SHORT":
        for key in ("open", "close", "ma5", "ma15", "kc_middle"):
            market[key] = 200 - market[key]
        high, low = market["high"].copy(), market["low"].copy()
        market["high"], market["low"] = 200 - low, 200 - high
        upper, lower = market["kc_upper"].copy(), market["kc_lower"].copy()
        market["kc_upper"], market["kc_lower"] = 200 - lower, 200 - upper
        market["kc_middle_slope"] *= -1
        market["macro_trend"] = "BEARISH"
    return market


@pytest.mark.parametrize("symbol", SYMBOLS + ["CAP/USDT"])
@pytest.mark.parametrize("side", ["LONG", "SHORT"])
@pytest.mark.parametrize("wick", [2.5, 6.0])
def test_central_gate_outer_wicks_keep_cap_unchanged(symbol, side, wick):
    market = central_frame(side)
    sign = 1 if side == "LONG" else -1
    quote = float(market.iloc[-1]["close"])
    market.loc[market.index[-1], "high" if side == "LONG" else "low"] = quote + sign * wick
    result = CentralEntryGate.verify_and_authorize(
        symbol=symbol, side=side, price=quote, klines_or_snapshot=market,
        source="ENTRY_LONG_BREAKOUT" if side == "LONG" else "ENTRY_SHORT_BREAKDOWN",
    )
    expected = symbol in SYMBOLS and wick == 2.5
    assert result.passed is expected, result.reason
    if not result.passed:
        assert "WICK" in result.reason


@pytest.mark.parametrize("symbol", SYMBOLS)
@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_wild_scan_forwards_symbol_to_guard(monkeypatch, symbol, side):
    candidate, rows, _, _ = chase_inputs(side, count=10)
    for row in rows:
        row.update(kc_upper=101, kc_lower=99, kc_middle=100, _wild_ma5=100, ma15=100)
    monkeypatch.setattr(Gate, "_evaluate_pure_math_raw", lambda *args, **kwargs: candidate)
    assert WildSwingStrategy._pure_math_candidate(pd.DataFrame(rows), symbol=symbol) is not None
    assert WildSwingStrategy._pure_math_candidate(pd.DataFrame(rows), symbol="CAP/USDT") is None


@pytest.mark.parametrize("symbol", SYMBOLS)
@pytest.mark.parametrize("side", ["LONG", "SHORT"])
@pytest.mark.parametrize("change", ["invalid_atr", "unclosed", "retreat", "ma5_opposite"])
def test_relaxed_entry_still_fails_closed(symbol, side, change):
    market = post_close_market(side)
    quote = float(market.iloc[-1]["close"])
    signal = evaluate_entry_method(
        market, quote=quote, symbol=symbol, expected_method=KC_TWO_BAR_BREAKOUT,
    )
    assert signal
    if change == "invalid_atr":
        market.loc[market.index[-1], "atr"] = float("nan")
    elif change == "unclosed":
        market.loc[market.index[-1], "is_closed"] = False
    elif change == "retreat":
        quote = float(market.iloc[-1]["kc_upper" if side == "LONG" else "kc_lower"])
    else:
        market.loc[market.index[-1], "ma5"] = float(market.iloc[-2]["ma5"])
    allowed, reason = revalidate_entry_method(
        market, symbol=symbol, side=side, quote=quote, context=method_context(signal),
    )
    assert not allowed, reason


@pytest.mark.parametrize("symbol", SYMBOLS + ["CAP/USDT"])
@pytest.mark.parametrize("side", ["LONG", "SHORT"])
@pytest.mark.parametrize("count", [9, 12, 13])
def test_real_continuation_scanner_and_firewall_agree(symbol, side, count):
    sign = 1 if side == "LONG" else -1
    edge = 101 if side == "LONG" else 99
    rows = []
    for index in range(count + 1):
        close = 100 if index == 0 else edge + sign * (0.1 + 0.01 * index)
        opened = close - sign * 0.05
        if index == count - 1:
            opened = close + sign * 0.05
        ma5 = 100 + sign * (0.2 + 0.01 * index)
        rows.append({
            "timestamp": (index + 1) * 60_000, "open": opened, "close": close,
            "high": max(opened, close) + 0.01, "low": min(opened, close) - 0.01,
            "kc_upper": 101, "kc_middle": 100, "kc_lower": 99,
            "ma5": ma5, "_wild_ma5": ma5, "ma15": 100, "atr": 1,
            "is_closed": True,
        })
    market = pd.DataFrame(rows)
    market.attrs["timeframe_ms"] = 60_000
    candidate = WildSwingStrategy._pure_math_candidate(market, symbol=symbol)
    expected = symbol in SYMBOLS and count <= 12
    assert (candidate is not None) is expected
    if candidate:
        assert candidate["type"] == CONTINUATION_OUTSIDE_KC
    assert has_valid_wild_outer_confirmation(
        market, side, CONTINUATION_OUTSIDE_KC, symbol=symbol,
    ) is expected


@pytest.mark.parametrize("symbol", SYMBOLS)
@pytest.mark.parametrize("side", ["LONG", "SHORT"])
@pytest.mark.parametrize("distance,expected", [(3.5, True), (3.50001, False)])
def test_method_band_extension_boundary(symbol, side, distance, expected):
    market = post_close_market(side)
    sign = 1 if side == "LONG" else -1
    quote = float(market.iloc[-1]["kc_upper" if side == "LONG" else "kc_lower"]) + sign * distance
    market.loc[market.index[-1], "ma5"] = float(market.iloc[-1]["close"]) - sign * 0.1
    signal = evaluate_entry_method(
        market, quote=quote, symbol=symbol, expected_method=KC_TWO_BAR_BREAKOUT,
    )
    assert signal
    allowed, reason = revalidate_entry_method(
        market, symbol=symbol, side=side, quote=quote, context=method_context(signal),
    )
    assert allowed is expected, reason
    if not allowed:
        assert "BAND_EXTENSION" in reason


@pytest.mark.parametrize("symbol", SYMBOLS + ["CAP/USDT"])
@pytest.mark.parametrize("side", ["LONG", "SHORT"])
@pytest.mark.parametrize("distance", [3.0, 3.5, 3.50001])
def test_late_chase_ma5_limit_independent_of_band(symbol, side, distance):
    candidate, rows, up, low = chase_inputs(side)
    sign = 1 if side == "LONG" else -1
    candidate["ma5_current"] = candidate["price"] - sign * distance
    reason = Gate.late_chase_block_reason(candidate, rows, up, low, symbol=symbol)
    assert (reason is None) is (distance <= (3.5 if symbol in SYMBOLS else 3.0))
    if reason:
        assert "MA5_DISTANCE" in reason


@pytest.mark.parametrize("symbol", SYMBOLS + ["CAP/USDT"])
@pytest.mark.parametrize("side", ["LONG", "SHORT"])
@pytest.mark.parametrize("mode,confirmation", [
    ("BREAKOUT", "KC_OUTER_BREAKOUT"),
    ("CONTINUATION", "POST_CLOSE_KC_REENTRY"),
    ("CROSSOVER", "MA5_MA15_1M_CROSS"),
    ("REVERSAL", "CONFIRMED_THREE_BAR_PIVOT_REVERSAL"),
])
def test_macro_ma15_distance_is_removed_without_relaxing_cap(symbol, side, mode, confirmation):
    close = 101 if mode in {"BREAKOUT", "CONTINUATION"} else 100
    opened = close - 1
    bar = {"open": opened, "close": close, "high": close + 0.1, "low": opened - 0.1}
    indicators = {
        "kc_upper": 100.5 if mode in {"BREAKOUT", "CONTINUATION"} else 110,
        "kc_middle": 100, "kc_lower": 90, "ma15": 80, "atr": 1,
        "kc_middle_current": 100, "kc_middle_previous": 99.9,
    }
    if side == "SHORT":
        bar = {
            "open": 200 - bar["open"], "close": 200 - bar["close"],
            "high": 200 - bar["low"], "low": 200 - bar["high"],
        }
        upper = indicators["kc_upper"]
        indicators.update(
            kc_upper=200 - indicators["kc_lower"], kc_lower=200 - upper,
            ma15=120, kc_middle_previous=100.1,
        )
    allowed, reason = MacroCandleStructureGate().can_open(
        side, bar, indicators, symbol=symbol, entry_pattern=mode,
        structure_confirmation=confirmation, structure_confirmed=True,
        channel_expanding=True,
    )
    assert allowed is (symbol in SYMBOLS), reason
    if not allowed:
        assert "乖離過大" in reason


@pytest.mark.parametrize("symbol", SYMBOLS)
@pytest.mark.parametrize("value", [0, float("nan"), float("inf")])
def test_removing_distance_cap_does_not_allow_invalid_ma15(symbol, value):
    market = post_close_market("LONG")
    quote = float(market.iloc[-1]["close"])
    signal = evaluate_entry_method(
        market, quote=quote, symbol=symbol, expected_method=KC_TWO_BAR_BREAKOUT,
    )
    market.loc[market.index[-1], "ma15"] = value
    allowed, reason = revalidate_entry_method(
        market, symbol=symbol, side="LONG", quote=quote, context=method_context(signal),
    )
    assert not allowed, reason
