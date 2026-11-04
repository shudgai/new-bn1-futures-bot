import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pandas as pd
import pytest

from core.services.entry_methods import (
    KC_TWO_BAR_BREAKOUT,
    MA5_MA15_CROSS,
    POST_CLOSE_KC_REENTRY,
    THREE_BAR_PIVOT_REVERSAL,
    evaluate_entry_method,
    revalidate_entry_method,
)


def frame(rows):
    result = pd.DataFrame(rows).copy()
    result["timestamp"] += int(time.time() // 60) * 60_000 - 60_000 - int(result.iloc[-1]["timestamp"])
    result.attrs.update(timeframe_ms=60_000, snapshot_ms=time.time() * 1000)
    return result


def candle(timestamp, *, opened, high, low, close, upper, lower, middle,
           short_ma, ma15, ma5=None, atr=1.0, is_closed=True):
    return {
        "timestamp": timestamp,
        "open": opened,
        "high": high,
        "low": low,
        "close": close,
        "kc_upper": upper,
        "kc_lower": lower,
        "kc_middle": middle,
        "ma5": short_ma if ma5 is None else ma5,
        "ma15": ma15,
        "atr": atr,
        "is_closed": is_closed,
    }


@pytest.mark.parametrize(
    "side,rows,quote",
    [
        (
            "LONG",
            [
                candle(60_000, opened=100, high=103.2, low=99.8, close=103,
                       upper=102, lower=98, middle=100, short_ma=101, ma15=100,
                       atr=2),
                candle(120_000, opened=103, high=105.2, low=102.5, close=105,
                       upper=104, lower=100, middle=102, short_ma=104, ma15=103.2,
                       atr=2),
            ],
            105.1,
        ),
        (
            "SHORT",
            [
                candle(60_000, opened=100, high=100.2, low=96.8, close=97,
                       upper=102, lower=98, middle=100, short_ma=99, ma15=100,
                       atr=2),
                candle(120_000, opened=97, high=97.3, low=94.8, close=95,
                       upper=100, lower=96, middle=98, short_ma=96, ma15=96.8,
                       atr=2),
            ],
            94.9,
        ),
    ],
)
def test_two_bar_breakout_requires_body_break_same_color_kc_short_ma_and_slope(
    side, rows, quote,
):
    market = frame(rows)
    signal = evaluate_entry_method(market, quote=quote, symbol="BTC/USDT", expected_method=KC_TWO_BAR_BREAKOUT)
    assert signal is not None
    assert signal["method"] == KC_TWO_BAR_BREAKOUT
    assert signal["side"] == side
    assert signal["source_bar_ids"] == market["timestamp"].tolist()

    wrong_color = [dict(row) for row in rows]
    wrong_color[-1]["close"] = wrong_color[-1]["open"]
    assert evaluate_entry_method(
        frame(wrong_color), quote=quote, symbol="BTC/USDT",
        expected_method=KC_TWO_BAR_BREAKOUT,
    ) is None

    unclosed = [dict(row) for row in rows]
    unclosed[-1]["is_closed"] = False
    assert evaluate_entry_method(
        frame(unclosed), quote=quote, symbol="BTC/USDT",
        expected_method=KC_TWO_BAR_BREAKOUT,
    ) is None

    returned_to_band = rows[-1]["kc_upper" if side == "LONG" else "kc_lower"]
    assert evaluate_entry_method(
        frame(rows), quote=returned_to_band, symbol="BTC/USDT",
        expected_method=KC_TWO_BAR_BREAKOUT,
    ) is None


@pytest.mark.parametrize(
    "side,rows,quote",
    [
        (
            "LONG",
            [
                candle(60_000, opened=100, high=101, low=98, close=99,
                       upper=110, lower=95, middle=100, short_ma=102, ma15=100),
                candle(120_000, opened=99, high=100, low=95, close=97,
                       upper=109, lower=94, middle=99, short_ma=100, ma15=100),
                candle(180_000, opened=97, high=102, low=96, close=101,
                       upper=113, lower=99, middle=101, short_ma=101, ma15=100),
            ],
            101,
        ),
        (
            "SHORT",
            [
                candle(60_000, opened=100, high=102, low=99, close=101,
                       upper=105, lower=90, middle=100, short_ma=98, ma15=100),
                candle(120_000, opened=101, high=105, low=100, close=103,
                       upper=106, lower=90, middle=101, short_ma=100, ma15=100),
                candle(180_000, opened=103, high=104, low=98, close=99,
                       upper=101, lower=87, middle=99.5, short_ma=99, ma15=100),
            ],
            99,
        ),
    ],
)
@pytest.mark.parametrize("symbol", ["1000PEPE/USDT", "龙虾/USDT", "CAP/USDT"])
def test_three_bar_pivot_requires_closed_extreme_and_confirmed_short_ma_turn(
    side, rows, quote, symbol,
):
    market = frame(rows)
    market["ma3"] = float("nan")
    signal = evaluate_entry_method(
        market, quote=quote, symbol=symbol,
        expected_method=THREE_BAR_PIVOT_REVERSAL,
    )
    # A geometric pivot cannot bypass the shared meme data/location filter.
    assert signal is None
    signal = evaluate_entry_method(
        market, quote=quote, symbol="BTC/USDT",
        expected_method=THREE_BAR_PIVOT_REVERSAL,
    )
    assert signal is not None and signal["side"] == side
    allowed, reason = revalidate_entry_method(
        market,
        symbol="BTC/USDT",
        side=side,
        quote=quote,
        context={
            "entry_method_authority": signal["method"],
            "entry_method_signal": signal,
            "entry_method_bar_id": signal["confirmation_bar_id"],
            "entry_method_event_id": signal["event_id"],
        },
    )
    assert allowed, reason

    invalid = [dict(row) for row in rows]
    invalid[-1]["ma5"] = invalid[-2]["ma5"]
    assert evaluate_entry_method(
        frame(invalid), quote=quote, symbol=symbol,
        expected_method=THREE_BAR_PIVOT_REVERSAL,
    ) is None


@pytest.mark.parametrize(
    "side,previous,current,quote",
    [
        (
            "LONG",
            candle(60_000, opened=99, high=101, low=98, close=100,
                   upper=110, lower=90, middle=100, short_ma=99, ma15=100),
            candle(120_000, opened=100, high=102, low=99, close=101,
                   upper=111, lower=91, middle=101, short_ma=101, ma15=100),
            101,
        ),
        (
            "SHORT",
            candle(60_000, opened=101, high=102, low=99, close=100,
                   upper=110, lower=90, middle=100, short_ma=101, ma15=100),
            candle(120_000, opened=100, high=101, low=98, close=99,
                   upper=109, lower=89, middle=99, short_ma=99, ma15=100),
            99,
        ),
    ],
)
def test_short_ma_ma15_cross_uses_two_closed_bars_without_extra_confirmation(
    side, previous, current, quote,
):
    signal = evaluate_entry_method(
        frame([previous, current]), quote=quote, symbol="BTC/USDT",
        expected_method=MA5_MA15_CROSS,
    )
    assert signal is not None
    assert signal["side"] == side


def test_post_close_reentry_uses_only_a_new_closed_bar_and_directional_short_ma():
    rows = [
        candle(60_000, opened=100, high=101, low=99, close=100,
               upper=102, lower=98, middle=100, short_ma=99, ma15=100),
        candle(120_000, opened=100, high=104, low=99, close=103,
               upper=102, lower=98, middle=100, short_ma=101, ma15=100),
    ]
    market = frame(rows)
    closed_before_exit = evaluate_entry_method(
        market, quote=103, symbol="BTC/USDT",
        last_exit_timestamp=float(market.iloc[-1]["timestamp"]) + 5000,
        expected_method=POST_CLOSE_KC_REENTRY,
    )
    assert closed_before_exit is None

    rows.append(
        candle(180_000, opened=103, high=105, low=102, close=104,
               upper=103, lower=99, middle=101, short_ma=102, ma15=100)
    )
    market = frame(rows)
    signal = evaluate_entry_method(
        market, quote=104, symbol="BTC/USDT",
        last_exit_timestamp=float(market.iloc[-2]["timestamp"]) + 5000,
        expected_method=POST_CLOSE_KC_REENTRY,
    )
    assert signal is not None
    assert signal["method"] == POST_CLOSE_KC_REENTRY
    assert signal["side"] == "LONG"


def test_breakout_method_rechecks_one_atr_bias_at_submission():
    rows = [
        candle(60_000, opened=100, high=103.2, low=99.8, close=103,
               upper=102, lower=98, middle=100, short_ma=101, ma15=100, atr=2),
        candle(120_000, opened=103, high=105.2, low=102.5, close=105,
               upper=104, lower=100, middle=102, short_ma=104, ma15=103.2, atr=2),
    ]
    market = post_close_market("LONG")
    signal = evaluate_entry_method(market, quote=101.5, symbol="CAP/USDT", expected_method=KC_TWO_BAR_BREAKOUT)
    assert signal is not None
    context = {
        "entry_method_authority": signal["method"],
        "entry_method_signal": signal,
        "entry_method_bar_id": signal["confirmation_bar_id"],
        "entry_method_event_id": signal["event_id"],
    }
    market.loc[market.index[-1], "ma15"] = 99.0
    allowed, reason = revalidate_entry_method(
        market, symbol="CAP/USDT", side="LONG", quote=101.5, context=context,
    )
    assert not allowed
    assert reason == "BLOCKED_LONG_MA15_EXTENSION_1_ATR"


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
@pytest.mark.parametrize("symbol", ["龙虾/USDT", "1000PEPE/USDT"])
@pytest.mark.parametrize(
    "change,allowed,expected_reason",
    [
        ("boundary", True, None),
        ("quote_band", False, "MA15_EXTENSION_1_ATR"),
        ("close_band", True, None),
        ("quote_ma5", True, None),
        ("close_ma5", True, None),
        ("missing_ma5", False, "BREAKOUT_MA5_INVALID"),
        ("nan_ma5", False, "BREAKOUT_MA5_INVALID"),
        ("zero_ma5", False, "BREAKOUT_MA5_INVALID"),
        ("bias", False, "MA15_EXTENSION_1_ATR"),
        ("wick", True, None),
    ],
)
def test_breakout_specific_macro_limits(side, symbol, change, allowed, expected_reason):
    rows = [
        candle(60_000, opened=100, high=101.2, low=99.8, close=101,
               upper=100.8, lower=98, middle=99.4, short_ma=100.5, ma15=100,
               ma5=100.4, atr=2),
        candle(120_000, opened=101, high=101.6, low=100.9, close=101.5,
               upper=101, lower=98.2, middle=99.6, short_ma=101, ma15=100.3,
               ma5=100.5, atr=2),
    ]
    quote = 101.5
    if change == "quote_band":
        quote += 2.0
    elif change == "close_band":
        rows[-1]["close"] += 0.001
    elif change in {"quote_ma5", "close_ma5"}:
        rows[-1]["kc_upper"] = 101.1
        rows[-1]["ma5"] = 100.499
        if change == "close_ma5":
            quote = 101.4
    elif change == "missing_ma5":
        for row in rows:
            row.pop("ma5")
    elif change == "nan_ma5":
        rows[-1]["ma5"] = float("nan")
    elif change == "zero_ma5":
        rows[-1]["ma5"] = 0 if side == "LONG" else 200
    elif change == "bias":
        rows[-1]["ma15"] = 99.4
    elif change == "wick":
        rows[-1]["high"] = 102.1
    if side == "SHORT":
        for row in rows:
            for key in ("open", "close", "ma5", "ma15", "kc_middle"):
                if key in row:
                    row[key] = 200 - row[key]
            row["high"], row["low"] = 200 - row["low"], 200 - row["high"]
            row["kc_upper"], row["kc_lower"] = 200 - row["kc_lower"], 200 - row["kc_upper"]
        quote = 200 - quote
        if expected_reason == "MACRO_LONG_EXHAUSTION_WICK":
            expected_reason = "MACRO_SHORT_EXHAUSTION_WICK"
    history = []
    for offset in range(20, 0, -1):
        row = dict(rows[0], timestamp=(21 - offset) * 60_000, volume=100.0)
        shift = (-1 if side == "LONG" else 1) * 0.01 * offset
        for key in ("open", "high", "low", "close", "ma5", "ma15",
                    "kc_upper", "kc_middle", "kc_lower"):
            if key in row:
                row[key] += shift
        history.append(row)
    for row in rows:
        row["timestamp"] += 20 * 60_000
        row["volume"] = 100.0
    rows = history + rows
    market = frame(rows)
    market.attrs["macro_trend"] = "BULLISH" if side == "LONG" else "BEARISH"
    signal = evaluate_entry_method(
        market, quote=quote, symbol=symbol, expected_method=KC_TWO_BAR_BREAKOUT,
    )
    if change in {"missing_ma5", "nan_ma5", "zero_ma5"}:
        assert signal is None
        return
    if change in {"quote_band", "bias"}:
        assert signal is None
        return
    assert signal is not None
    result, reason = revalidate_entry_method(
        market, symbol=symbol, side=side, quote=quote,
        context={
            "entry_method_authority": signal["method"],
            "entry_method_signal": signal,
            "entry_method_bar_id": signal["confirmation_bar_id"],
            "entry_method_event_id": signal["event_id"],
        },
    )
    assert result is allowed, reason
    if expected_reason:
        assert reason == "BLOCKED_ENTRY_METHOD_" + expected_reason
    if allowed:
        from core.gates.central_entry_gate import CentralEntryGate
        from core.services.math_execution_gate import validate_entry_math

        market.attrs["entry_method_context"] = {
            "symbol": symbol,
            "entry_method_authority": signal["method"],
            "entry_method_signal": signal,
            "entry_method_bar_id": signal["confirmation_bar_id"],
            "entry_method_event_id": signal["event_id"],
        }
        market.attrs["entry_method_quote"] = quote
        math_allowed, math_reason = validate_entry_math(market, side)
        assert math_allowed, math_reason
        central_result = CentralEntryGate.verify_and_authorize(
            symbol=symbol, side=side, price=quote, klines_or_snapshot=market,
        )
        assert central_result.passed, central_result.reason
        rejected, stale_reason = revalidate_entry_method(
            market, symbol=symbol, side=side, quote=quote,
            context={
                "entry_method_authority": KC_TWO_BAR_BREAKOUT,
                "entry_method_signal": signal,
                "entry_method_bar_id": signal["confirmation_bar_id"],
                "entry_method_event_id": "stale",
            },
        )
        assert not rejected
        assert stale_reason == "BLOCKED_ENTRY_METHOD_SIGNAL_STALE_OR_INVALID"


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_non_breakout_keeps_macro_relative_location(side):
    rows = [
        candle(60_000, opened=99, high=100.5, low=98.5, close=100,
               upper=102, lower=98, middle=100, short_ma=99, ma15=100),
        candle(120_000, opened=100, high=101.2, low=99.8, close=101,
               upper=102, lower=98, middle=100.1, short_ma=101, ma15=100),
    ]
    if side == "SHORT":
        for row in rows:
            for key in ("open", "close", "ma5", "ma15", "kc_middle"):
                row[key] = 200 - row[key]
            row["high"], row["low"] = 200 - row["low"], 200 - row["high"]
    market = frame(rows)
    quote = rows[-1]["close"]
    signal = evaluate_entry_method(
        market, quote=quote, symbol="BTC/USDT", expected_method=MA5_MA15_CROSS,
    )
    assert signal is not None
    allowed, reason = revalidate_entry_method(
        market, symbol="BTC/USDT", side=side, quote=quote,
        context={
            "entry_method_authority": signal["method"],
            "entry_method_signal": signal,
            "entry_method_bar_id": signal["confirmation_bar_id"],
            "entry_method_event_id": signal["event_id"],
        },
    )
    assert not allowed
    assert reason == "BLOCKED_ENTRY_METHOD_MACRO_KC_RELATIVE_LOCATION"


@pytest.mark.parametrize(
    "side,position,allowed",
    [
        ("SHORT", 0.29, True),
        ("SHORT", 0.25, True),
        ("SHORT", 0.249, False),
        ("LONG", 0.71, True),
        ("LONG", 0.75, True),
        ("LONG", 0.751, False),
    ],
)
def test_meme_pivot_revalidation_uses_relaxed_symmetric_kc_bounds(
    side, position, allowed, monkeypatch,
):
    import core.services.entry_methods as entry_methods
    from core.gates.macro_candle_structure_gate import MacroCandleStructureGate
    from core.gates.master_gate_funnel import GateDecision

    lower, upper = 100.0, 104.0
    close = lower + position * (upper - lower)
    opened = close + 0.5 if side == "SHORT" else close - 0.5
    ma15 = close + 0.5 if side == "SHORT" else close - 0.5
    rows = [
        candle(
            60_000, opened=opened, high=max(opened, close) + 0.02,
            low=min(opened, close) - 0.02, close=close,
            upper=upper, lower=lower, middle=102.0,
            short_ma=close, ma15=ma15, atr=1.0,
        ),
        candle(
            120_000, opened=opened, high=max(opened, close) + 0.02,
            low=min(opened, close) - 0.02, close=close,
            upper=upper, lower=lower, middle=102.0,
            short_ma=close, ma15=ma15, atr=1.0,
        ),
    ]
    market = frame(rows)
    signal = {
        "method": THREE_BAR_PIVOT_REVERSAL,
        "side": side,
        "confirmation_bar_id": market.iloc[-1]["timestamp"],
        "event_id": "pivot-revalidation-test",
        "channel_expanding": False,
    }
    monkeypatch.setattr(
        "core.gates.master_gate_funnel.evaluate_meme_entry_frame",
        lambda *args, **kwargs: GateDecision(True, "FILTER_PASS", "PASS", {}),
    )
    monkeypatch.setattr(
        entry_methods, "evaluate_entry_method", lambda *args, **kwargs: signal,
    )
    monkeypatch.setattr(
        MacroCandleStructureGate, "can_open",
        lambda *args, **kwargs: (True, "PASS_TEST_MACRO_GATE"),
    )
    result, reason = revalidate_entry_method(
        market, symbol="1000PEPE/USDT", side=side, quote=close,
        context={
            "entry_method_authority": THREE_BAR_PIVOT_REVERSAL,
            "entry_method_signal": signal,
            "entry_method_bar_id": signal["confirmation_bar_id"],
            "entry_method_event_id": signal["event_id"],
        },
    )
    assert result is allowed, reason
    if not allowed:
        assert reason == "BLOCKED_ENTRY_METHOD_MACRO_KC_RELATIVE_LOCATION"


def test_method_revalidation_requires_same_event():
    rows = [
        candle(60_000, opened=99, high=100.5, low=98.5, close=100,
               upper=110, lower=90, middle=100, short_ma=99, ma15=100),
        candle(120_000, opened=100, high=101.2, low=99.8, close=101,
               upper=112, lower=98, middle=101, short_ma=101, ma15=100),
    ]
    market = frame(rows)
    signal = evaluate_entry_method(
        market, quote=101, symbol="BTC/USDT",
        expected_method=MA5_MA15_CROSS,
    )
    assert signal is not None
    context = {
        "entry_method_authority": signal["method"],
        "entry_method_signal": signal,
        "entry_method_bar_id": signal["confirmation_bar_id"],
        "entry_method_event_id": signal["event_id"],
    }
    allowed, reason = revalidate_entry_method(
        market, symbol="BTC/USDT", side="LONG", quote=101, context=context,
    )
    assert allowed, reason

    context["entry_method_event_id"] = "stale"
    allowed, reason = revalidate_entry_method(
        market, symbol="BTC/USDT", side="LONG", quote=101, context=context,
    )
    assert not allowed
    assert reason == "BLOCKED_ENTRY_METHOD_SIGNAL_STALE_OR_INVALID"


def post_close_market(side):
    rows = [
        candle(1_260_000, opened=100, high=101.2, low=99.8, close=101,
               upper=100.8, lower=98, middle=99.4, short_ma=100.5, ma15=100,
               ma5=100.4),
        candle(1_320_000, opened=101, high=101.6, low=100.9, close=101.5,
               upper=101, lower=98.2, middle=99.6, short_ma=101, ma15=100.5,
               ma5=100.6),
    ]
    history = []
    for offset in range(20, 0, -1):
        row = dict(rows[0], timestamp=(21 - offset) * 60_000, volume=100.0)
        for key in ("open", "high", "low", "close", "ma5", "ma15",
                    "kc_upper", "kc_middle", "kc_lower"):
            row[key] -= 0.01 * offset
        history.append(row)
    rows = history + rows
    for row in rows:
        row["volume"] = 100.0
    if side == "SHORT":
        for row in rows:
            for key in ("open", "close", "ma5", "ma15", "kc_middle"):
                row[key] = 200 - row[key]
            row["high"], row["low"] = 200 - row["low"], 200 - row["high"]
            row["kc_upper"], row["kc_lower"] = (
                200 - row["kc_lower"], 200 - row["kc_upper"],
            )
    market = frame(rows)
    market.attrs["macro_trend"] = "BULLISH" if side == "LONG" else "BEARISH"
    return market


def method_context(signal, last_exit_timestamp=None):
    return {
        "entry_method_authority": signal["method"],
        "entry_method_signal": signal,
        "entry_method_bar_id": signal["confirmation_bar_id"],
        "entry_method_event_id": signal["event_id"],
        "entry_method_last_exit_timestamp": last_exit_timestamp,
    }


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
@pytest.mark.parametrize("symbol", ["龙虾/USDT", "CAP/USDT", "1000PEPE/USDT"])
def test_post_close_reentry_passes_shared_gates_outside_its_own_rail(side, symbol):
    from core.gates.central_entry_gate import CentralEntryGate
    from core.services.math_execution_gate import (
        validate_entry_at_execution, validate_entry_math,
    )

    market = post_close_market(side)
    last_exit = float(market.iloc[-4]["timestamp"]) / 1000
    quote = float(market.iloc[-1]["close"])
    signal = evaluate_entry_method(
        market, quote=quote, symbol=symbol, last_exit_timestamp=last_exit,
        expected_method=POST_CLOSE_KC_REENTRY,
    )
    assert signal is not None
    context = method_context(signal, last_exit)
    allowed, reason = revalidate_entry_method(
        market, symbol=symbol, side=side, quote=quote, context=context,
    )
    assert allowed, reason
    market.attrs["entry_method_context"] = {"symbol": symbol, **context}
    market.attrs["entry_method_quote"] = quote
    allowed, reason = validate_entry_math(market, side)
    assert allowed, reason
    central = CentralEntryGate.verify_and_authorize(
        symbol=symbol, side=side, price=quote, klines_or_snapshot=market,
    )
    assert central.passed, central.reason
    account = SimpleNamespace()
    payload = {**context, "entry_gate_frame": market}
    allowed, reason = asyncio.run(validate_entry_at_execution(
        account, symbol, side, payload, execution_price=quote,
    ))
    assert allowed, reason
    retreat = float(market.iloc[-1]["kc_upper" if side == "LONG" else "kc_lower"])
    allowed, reason = asyncio.run(validate_entry_at_execution(
        account, symbol, side, payload, execution_price=retreat,
    ))
    assert not allowed
    assert reason == "BLOCKED_ENTRY_METHOD_SIGNAL_STALE_OR_INVALID"


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
@pytest.mark.parametrize(
    "change,reason_fragment",
    [
        ("bias", "MA15_EXTENSION_1_ATR"),
        ("wick", "EXHAUSTION_WICK"),
        ("same_bar_exit", "SIGNAL_STALE_OR_INVALID"),
        ("quote_at_rail", "SIGNAL_STALE_OR_INVALID"),
        ("short_ma_reversed", "MA5_DIRECTION_OR_ALIGNMENT"),
        ("invalid_atr", "DATA_BLOCK"),
        ("stale_event", "SIGNAL_STALE_OR_INVALID"),
        ("opposite_color", "DIRECTIONAL_CANDLE"),
    ],
)
def test_post_close_reentry_retains_other_vetoes(side, change, reason_fragment):
    market = post_close_market(side)
    last_exit = float(market.iloc[-4]["timestamp"]) / 1000
    quote = float(market.iloc[-1]["close"])
    signal = evaluate_entry_method(
        market, quote=quote, symbol="CAP/USDT", last_exit_timestamp=last_exit,
        expected_method=POST_CLOSE_KC_REENTRY,
    )
    assert signal is not None
    context = method_context(signal, last_exit)
    index = market.index[-1]
    sign = 1 if side == "LONG" else -1
    if change == "bias":
        market.loc[index, "ma15"] = quote - sign * 1.51
    elif change == "wick":
        market.loc[index, "high" if side == "LONG" else "low"] = quote + sign
    elif change == "same_bar_exit":
        context["entry_method_last_exit_timestamp"] = float(market.iloc[-1]["timestamp"]) / 1000 + 5
    elif change == "quote_at_rail":
        quote = float(market.loc[index, "kc_upper" if side == "LONG" else "kc_lower"])
    elif change == "short_ma_reversed":
        market.loc[index, "ma5"] = market.iloc[-2]["ma5"]
    elif change == "invalid_atr":
        market.loc[index, "atr"] = 0.0
    elif change == "stale_event":
        context["entry_method_event_id"] = "stale"
    elif change == "opposite_color":
        market.loc[index, "open"] = quote + sign * 0.05
        market.loc[index, "high" if side == "LONG" else "low"] = quote + sign * 0.06
    allowed, reason = revalidate_entry_method(
        market, symbol="CAP/USDT", side=side, quote=quote, context=context,
    )
    assert not allowed
    assert reason_fragment in reason


@pytest.mark.parametrize(
    "reason",
    [
        "BLOCKED_HOURLY_ENTRY_LIMIT_EXCEEDED",
        "ENTRY_METHOD_BAR_ALREADY_TRADED",
        "ENTRY_METHOD_BAR_CLAIM_STORE_UNAVAILABLE",
        "ENTRY_METHOD_BAR_CLAIM_PERSISTENCE_FAILED",
        "ENTRY_METHOD_FINAL_GATE_VETO",
        "BLOCKED_ENTRY_METHOD_MACRO_MA15_BIAS",
        "BLOCKED_ENTRY_METHOD_MACRO_LONG_EXHAUSTION_WICK",
        "BLOCKED_ENTRY_METHOD_MACRO_DATA_INVALID",
    ],
)
def test_pipeline_does_not_fall_through_common_vetoes(monkeypatch, caplog, reason):
    from core.services.data_pipeline import DataPipeline
    from core.services.execution_manager import ExecutionManager

    market = pd.DataFrame([
        {
            "timestamp": 60_000 * (index + 1), "open": 100 + index * 0.1,
            "close": 100.05 + index * 0.1, "high": 100.2 + index * 0.1,
            "low": 99.9 + index * 0.1, "volume": 100, "is_closed": True,
        }
        for index in range(35)
    ])
    market.attrs["timeframe_ms"] = 60_000
    account = SimpleNamespace(positions={})
    result = {"status": "VETO", "symbol": "CAP/USDT", "reason": reason}
    if reason.startswith("BLOCKED_ENTRY_METHOD_MACRO_"):
        result.update(
            reason="BLOCKED_ENTRY_METHOD_MACRO_GATE",
            entry_rejections=[{"reason": reason}],
        )
    method = AsyncMock(return_value=result)
    monkeypatch.setattr(ExecutionManager, "_evaluate_closed_entry_methods", method)
    submit = AsyncMock()
    monkeypatch.setattr(ExecutionManager, "open_position", submit)
    assert DataPipeline.calculate_snapshot("CAP/USDT", market) is not None
    actual = asyncio.run(ExecutionManager.process_symbol_pipeline(
        symbol="CAP/USDT", engine_or_account=account, klines_df=market,
        quote=float(market.iloc[-1]["close"]),
    ))
    assert actual == result
    method.assert_awaited_once()
    assert "ENTRY_METHOD_ALTERNATIVE_EVALUATION" not in caplog.text
    submit.assert_not_awaited()


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
@pytest.mark.parametrize("symbol", ["龙虾/USDT", "1000PEPE/USDT"])
@pytest.mark.parametrize("submit_success", [True, False])
def test_priority_authority_rejection_cannot_fall_back_to_legacy_entry(
    monkeypatch, caplog, side, symbol, submit_success,
):
    from core.services.data_pipeline import DataPipeline, MarketDataSnapshot
    from core.services.execution_manager import ExecutionManager

    market = post_close_market(side)
    sign = 1 if side == "LONG" else -1
    market.loc[market.index[-1], "open"] = market.iloc[-1]["close"] - sign * 0.8
    market.loc[market.index[-1], "high"] = max(market.iloc[-1]["open"], market.iloc[-1]["close"]) + .1
    market.loc[market.index[-1], "low"] = min(market.iloc[-1]["open"], market.iloc[-1]["close"]) - .1
    for key in ("open", "high", "low", "close", "ma5", "ma15",
                "kc_upper", "kc_middle", "kc_lower"):
        market[key] += 9900
    current, previous = market.iloc[-1].to_dict(), market.iloc[-2].to_dict()
    quote = current["close"]
    snapshot = MarketDataSnapshot(
        symbol=symbol, current_price=quote, curr_k=current, prev_k=previous,
        ma5=current["ma5"], ma15=current["ma15"],
        ma3=current["ma5"], prev_ma3=previous["ma5"],
        ema_fast=current["ma5"], ema_slow=current["ma15"],
        ma_spread_pct=0.1, fast_slope=sign * 0.2, slow_slope=sign * 0.2,
        kc_upper=current["kc_upper"], kc_lower=current["kc_lower"],
        kc_middle=current["kc_middle"], kc_middle_slope=sign * 0.2,
        atr=1.0, macro_trend="BULLISH" if side == "LONG" else "BEARISH",
        macro_slope=sign * 0.2, slow_slope_5=sign * 0.2, raw_df=market,
    )
    rejection = {
        "status": "VETO", "symbol": symbol,
        "reason": "BLOCKED_ENTRY_METHOD_MACRO_GATE",
        "entry_rejections": [{
            "method": MA5_MA15_CROSS, "side": side,
            "reason": "BLOCKED_ENTRY_METHOD_MACRO_KC_RELATIVE_LOCATION",
            "event_id": "rejected-cross",
        }],
    }
    method = AsyncMock(return_value=rejection)
    monkeypatch.setattr(ExecutionManager, "_evaluate_closed_entry_methods", method)
    monkeypatch.setattr(DataPipeline, "calculate_snapshot", lambda *_args, **kwargs: snapshot)
    for name in ("_last_exit_record", "_last_trade_bar_time", "_hourly_entry_records"):
        monkeypatch.setattr(ExecutionManager, name, {})
    submit = AsyncMock(return_value=submit_success)
    monkeypatch.setattr(ExecutionManager, "open_position", submit)
    account = SimpleNamespace(positions={}, entry_filter_last={}, save_state=Mock())
    with caplog.at_level("INFO", logger="ExecutionManager"):
        result = asyncio.run(ExecutionManager.process_symbol_pipeline(
            symbol=symbol, engine_or_account=account, klines_df=market, quote=quote,
        ))
    assert result == rejection
    method.assert_awaited_once()
    assert method.call_args.kwargs["only_method"] == "MEME_GATE_ENTRY"
    submit.assert_not_awaited()
    assert "ENTRY_METHOD_ALTERNATIVE_EVALUATION" not in caplog.text


def test_flat_kc_blocks_before_method_or_alternative_evaluation(monkeypatch):
    from core.services.execution_manager import ExecutionManager

    market = pd.DataFrame([
        {
            "timestamp": 60_000 * (index + 1), "open": 100, "close": 100,
            "high": 100.2, "low": 99.8, "volume": 100, "is_closed": True,
        }
        for index in range(35)
    ])
    market.attrs["timeframe_ms"] = 60_000
    rejected = {
        "status": "VETO", "symbol": "CAP/USDT",
        "reason": "BLOCKED_ENTRY_METHOD_MACRO_GATE",
        "entry_rejections": [{
            "reason": "BLOCKED_ENTRY_METHOD_MACRO_KC_RELATIVE_LOCATION",
        }],
    }
    monkeypatch.setattr(
        ExecutionManager, "_evaluate_closed_entry_methods",
        AsyncMock(return_value=rejected),
    )
    submit = AsyncMock()
    monkeypatch.setattr(ExecutionManager, "open_position", submit)
    actual = asyncio.run(ExecutionManager.process_symbol_pipeline(
        symbol="CAP/USDT", engine_or_account=SimpleNamespace(positions={}),
        klines_df=market, quote=100,
    ))
    assert actual == {
        "status": "VETO", "symbol": "CAP/USDT",
        "reason": "BLOCKED_ENTRY_KC_DIRECTION_FLAT",
    }
    ExecutionManager._evaluate_closed_entry_methods.assert_not_awaited()
    submit.assert_not_awaited()


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
@pytest.mark.parametrize("outcome", ["success", "claimed_bar", "hourly_limit", "final_veto"])
def test_reentry_execution_retains_claim_and_hourly_limits(monkeypatch, side, outcome):
    import time
    from core.services.execution_manager import ExecutionManager

    symbol = "CAP/USDT"
    market = post_close_market(side)
    quote = float(market.iloc[-1]["close"])
    bar_id = int(market.iloc[-1]["timestamp"])
    account = SimpleNamespace(positions={}, entry_filter_last={}, save_state=Mock())
    account_key = ExecutionManager._get_account_key(account, symbol)
    monkeypatch.setattr(ExecutionManager, "_last_exit_record", {
        account_key: {"timestamp": float(market.iloc[-4]["timestamp"]) / 1000},
    })
    monkeypatch.setattr(ExecutionManager, "_last_trade_bar_time", {})
    monkeypatch.setattr(ExecutionManager, "_hourly_entry_records", {})
    if outcome == "claimed_bar":
        account.entry_filter_last["strict_ma3_ma15_last_trade_bar"] = {symbol: bar_id}
    elif outcome == "hourly_limit":
        ExecutionManager._hourly_entry_records[account_key] = (
            [time.time()] * ExecutionManager.MAX_ENTRIES_PER_HOUR
        )
    submit = AsyncMock(return_value=outcome == "success")
    monkeypatch.setattr(ExecutionManager, "open_position", submit)

    async def evaluate():
        return await ExecutionManager._evaluate_closed_entry_methods(
            symbol=symbol, account=account, engine_or_account=account,
            frame=market, quote=quote,
            macro_trend="BULLISH" if side == "LONG" else "BEARISH",
            only_method=POST_CLOSE_KC_REENTRY,
        )

    result = asyncio.run(evaluate())
    expected_reasons = {
        "success": POST_CLOSE_KC_REENTRY,
        "claimed_bar": "ENTRY_METHOD_BAR_ALREADY_TRADED",
        "hourly_limit": "BLOCKED_HOURLY_ENTRY_LIMIT_EXCEEDED",
        "final_veto": "ENTRY_METHOD_FINAL_GATE_VETO",
    }
    assert result["reason"] == expected_reasons[outcome]
    if outcome in {"claimed_bar", "hourly_limit"}:
        submit.assert_not_awaited()
        account.save_state.assert_not_called()
    else:
        submit.assert_awaited_once()
        account.save_state.assert_called_once_with(strict=True)
        assert submit.call_args.kwargs["meta"]["entry_method_authority"] == POST_CLOSE_KC_REENTRY
        again = asyncio.run(evaluate())
        assert again["reason"] == "ENTRY_METHOD_BAR_ALREADY_TRADED"
        submit.assert_awaited_once()


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
@pytest.mark.parametrize("error, released", [
    ("[FORBIDDEN_ENTRY] MEME_GATE_FRESH_FRAME_UNAVAILABLE", True),
    ("[FORBIDDEN_ENTRY] MEME_GATE_DATA_BLOCK", False),
    ("[FORBIDDEN_ENTRY] VETO_VOLUME_CONTRACTION", False),
])
def test_transient_frame_failure_releases_bar_claim(monkeypatch, side, error, released):
    from core.services.execution_manager import ExecutionManager

    symbol = "CAP/USDT"
    market = post_close_market(side)
    quote = float(market.iloc[-1]["close"])
    account = SimpleNamespace(positions={}, entry_filter_last={}, save_state=Mock())
    account_key = ExecutionManager._get_account_key(account, symbol)
    monkeypatch.setattr(ExecutionManager, "_last_exit_record", {
        account_key: {"timestamp": float(market.iloc[-4]["timestamp"]) / 1000},
    })
    monkeypatch.setattr(ExecutionManager, "_last_trade_bar_time", {})
    monkeypatch.setattr(ExecutionManager, "_hourly_entry_records", {})
    monkeypatch.setattr(ExecutionManager, "_last_open_error", {})

    async def failing_submit(**kwargs):
        ExecutionManager._last_open_error[kwargs["symbol"]] = error
        return False

    submit = AsyncMock(side_effect=failing_submit)
    monkeypatch.setattr(ExecutionManager, "open_position", submit)

    async def evaluate():
        return await ExecutionManager._evaluate_closed_entry_methods(
            symbol=symbol, account=account, engine_or_account=account,
            frame=market, quote=quote,
            macro_trend="BULLISH" if side == "LONG" else "BEARISH",
            only_method=POST_CLOSE_KC_REENTRY,
        )

    first = asyncio.run(evaluate())
    assert first["reason"] == "ENTRY_METHOD_FINAL_GATE_VETO"
    again = asyncio.run(evaluate())
    if released:
        assert again["reason"] == "ENTRY_METHOD_FINAL_GATE_VETO"
        assert submit.await_count == 2
        assert symbol not in account.entry_filter_last["strict_ma3_ma15_last_trade_bar"]
    else:
        assert again["reason"] == "ENTRY_METHOD_BAR_ALREADY_TRADED"
        assert submit.await_count == 1
