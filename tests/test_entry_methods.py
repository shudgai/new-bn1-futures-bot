import pandas as pd
import pytest

from core.services.entry_methods import (
    KC_TWO_BAR_BREAKOUT,
    MA3_MA15_CROSS,
    POST_CLOSE_KC_REENTRY,
    THREE_BAR_PIVOT_REVERSAL,
    evaluate_entry_method,
    revalidate_entry_method,
)


def frame(rows):
    result = pd.DataFrame(rows)
    result.attrs["timeframe_ms"] = 60_000
    return result


def candle(timestamp, *, opened, high, low, close, upper, lower, middle,
           ma3, ma15, ma5=None, atr=1.0, is_closed=True):
    return {
        "timestamp": timestamp,
        "open": opened,
        "high": high,
        "low": low,
        "close": close,
        "kc_upper": upper,
        "kc_lower": lower,
        "kc_middle": middle,
        "ma3": ma3,
        "ma5": ma3 if ma5 is None else ma5,
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
                       upper=102, lower=98, middle=100, ma3=101, ma15=100,
                       atr=2),
                candle(120_000, opened=103, high=105.2, low=102.5, close=105,
                       upper=104, lower=100, middle=102, ma3=103, ma15=101,
                       atr=2),
            ],
            105.1,
        ),
        (
            "SHORT",
            [
                candle(60_000, opened=100, high=100.2, low=96.8, close=97,
                       upper=102, lower=98, middle=100, ma3=99, ma15=100,
                       atr=2),
                candle(120_000, opened=97, high=97.3, low=94.8, close=95,
                       upper=100, lower=96, middle=98, ma3=97, ma15=99,
                       atr=2),
            ],
            94.9,
        ),
    ],
)
def test_two_bar_breakout_requires_body_break_same_color_kc_ma3_and_slope(
    side, rows, quote,
):
    signal = evaluate_entry_method(frame(rows), quote=quote, symbol="CAP/USDT")
    assert signal is not None
    assert signal["method"] == KC_TWO_BAR_BREAKOUT
    assert signal["side"] == side
    assert signal["source_bar_ids"] == [60_000, 120_000]

    wrong_color = [dict(row) for row in rows]
    wrong_color[-1]["close"] = wrong_color[-1]["open"]
    assert evaluate_entry_method(
        frame(wrong_color), quote=quote, symbol="CAP/USDT",
        expected_method=KC_TWO_BAR_BREAKOUT,
    ) is None


@pytest.mark.parametrize(
    "side,rows,quote",
    [
        (
            "LONG",
            [
                candle(60_000, opened=100, high=101, low=98, close=99,
                       upper=110, lower=95, middle=100, ma3=102, ma15=100),
                candle(120_000, opened=99, high=100, low=95, close=97,
                       upper=109, lower=94, middle=99, ma3=100, ma15=100),
                candle(180_000, opened=97, high=102, low=96, close=101,
                       upper=113, lower=99, middle=101, ma3=101, ma15=100),
            ],
            101,
        ),
        (
            "SHORT",
            [
                candle(60_000, opened=100, high=102, low=99, close=101,
                       upper=105, lower=90, middle=100, ma3=98, ma15=100),
                candle(120_000, opened=101, high=105, low=100, close=103,
                       upper=106, lower=90, middle=101, ma3=100, ma15=100),
                candle(180_000, opened=103, high=104, low=98, close=99,
                       upper=101, lower=87, middle=99.5, ma3=99, ma15=100),
            ],
            99,
        ),
    ],
)
def test_three_bar_pivot_requires_closed_extreme_and_confirmed_ma3_turn(
    side, rows, quote,
):
    signal = evaluate_entry_method(
        frame(rows), quote=quote, symbol="CAP/USDT",
        expected_method=THREE_BAR_PIVOT_REVERSAL,
    )
    assert signal is not None
    assert signal["side"] == side
    allowed, reason = revalidate_entry_method(
        frame(rows),
        symbol="CAP/USDT",
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
    invalid[-1]["ma3"] = invalid[-2]["ma3"]
    assert evaluate_entry_method(
        frame(invalid), quote=quote, symbol="CAP/USDT",
        expected_method=THREE_BAR_PIVOT_REVERSAL,
    ) is None


@pytest.mark.parametrize(
    "side,previous,current,quote",
    [
        (
            "LONG",
            candle(60_000, opened=99, high=101, low=98, close=100,
                   upper=110, lower=90, middle=100, ma3=99, ma15=100),
            candle(120_000, opened=100, high=102, low=99, close=101,
                   upper=111, lower=91, middle=101, ma3=101, ma15=100),
            101,
        ),
        (
            "SHORT",
            candle(60_000, opened=101, high=102, low=99, close=100,
                   upper=110, lower=90, middle=100, ma3=101, ma15=100),
            candle(120_000, opened=100, high=101, low=98, close=99,
                   upper=109, lower=89, middle=99, ma3=99, ma15=100),
            99,
        ),
    ],
)
def test_ma3_ma15_cross_uses_two_closed_bars_without_extra_confirmation(
    side, previous, current, quote,
):
    signal = evaluate_entry_method(
        frame([previous, current]), quote=quote, symbol="CAP/USDT",
        expected_method=MA3_MA15_CROSS,
    )
    assert signal is not None
    assert signal["side"] == side


def test_post_close_reentry_uses_only_a_new_closed_bar_and_directional_ma3():
    rows = [
        candle(60_000, opened=100, high=101, low=99, close=100,
               upper=102, lower=98, middle=100, ma3=99, ma15=100),
        candle(120_000, opened=100, high=104, low=99, close=103,
               upper=102, lower=98, middle=100, ma3=101, ma15=100),
    ]
    closed_before_exit = evaluate_entry_method(
        frame(rows), quote=103, symbol="CAP/USDT",
        last_exit_timestamp=125_000,
        expected_method=POST_CLOSE_KC_REENTRY,
    )
    assert closed_before_exit is None

    rows.append(
        candle(180_000, opened=103, high=105, low=102, close=104,
               upper=103, lower=99, middle=101, ma3=102, ma15=100)
    )
    signal = evaluate_entry_method(
        frame(rows), quote=104, symbol="CAP/USDT",
        last_exit_timestamp=125_000,
        expected_method=POST_CLOSE_KC_REENTRY,
    )
    assert signal is not None
    assert signal["method"] == POST_CLOSE_KC_REENTRY
    assert signal["side"] == "LONG"


def test_breakout_method_is_vetoed_by_band_extension():
    rows = [
        candle(60_000, opened=100, high=103.2, low=99.8, close=103,
               upper=102, lower=98, middle=100, ma3=101, ma15=100, atr=2),
        candle(120_000, opened=103, high=105.2, low=102.5, close=105,
               upper=104, lower=100, middle=102, ma3=103, ma15=101, atr=2),
    ]
    market = frame(rows)
    signal = evaluate_entry_method(market, quote=105.1, symbol="CAP/USDT")
    assert signal is not None
    context = {
        "entry_method_authority": signal["method"],
        "entry_method_signal": signal,
        "entry_method_bar_id": signal["confirmation_bar_id"],
        "entry_method_event_id": signal["event_id"],
    }
    allowed, reason = revalidate_entry_method(
        market, symbol="CAP/USDT", side="LONG", quote=105.1, context=context,
    )
    assert not allowed
    assert reason == "BLOCKED_ENTRY_METHOD_BREAKOUT_BAND_EXTENSION"


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
@pytest.mark.parametrize("symbol", ["龙虾/USDT", "1000PEPE/USDT"])
@pytest.mark.parametrize(
    "change,allowed,expected_reason",
    [
        ("boundary", True, None),
        ("quote_band", False, "BREAKOUT_BAND_EXTENSION"),
        ("close_band", False, "BREAKOUT_BAND_EXTENSION"),
        ("quote_ma5", False, "BREAKOUT_MA5_DISTANCE"),
        ("close_ma5", False, "BREAKOUT_MA5_DISTANCE"),
        ("missing_ma5", False, "BREAKOUT_MA5_INVALID"),
        ("nan_ma5", False, "BREAKOUT_MA5_INVALID"),
        ("zero_ma5", False, "BREAKOUT_MA5_INVALID"),
        ("bias", False, "MACRO_MA15_BIAS"),
        ("wick", False, "MACRO_LONG_EXHAUSTION_WICK"),
    ],
)
def test_breakout_specific_macro_limits(side, symbol, change, allowed, expected_reason):
    rows = [
        candle(60_000, opened=100, high=101.2, low=99.8, close=101,
               upper=100.8, lower=98, middle=99.4, ma3=100.5, ma15=100,
               ma5=100.4),
        candle(120_000, opened=101, high=101.6, low=100.9, close=101.5,
               upper=101, lower=98.2, middle=99.6, ma3=101, ma15=100.5,
               ma5=100.5),
    ]
    quote = 101.5
    if change == "quote_band":
        quote += 0.001
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
        rows[-1]["ma15"] = 100.2
    elif change == "wick":
        rows[-1]["high"] = 102.1
    if side == "SHORT":
        for row in rows:
            for key in ("open", "close", "ma3", "ma15", "ma5", "kc_middle"):
                if key in row:
                    row[key] = 200 - row[key]
            row["high"], row["low"] = 200 - row["low"], 200 - row["high"]
            row["kc_upper"], row["kc_lower"] = 200 - row["kc_lower"], 200 - row["kc_upper"]
        quote = 200 - quote
        if expected_reason == "MACRO_LONG_EXHAUSTION_WICK":
            expected_reason = "MACRO_SHORT_EXHAUSTION_WICK"
    market = frame(rows)
    signal = evaluate_entry_method(
        market, quote=quote, symbol=symbol, expected_method=KC_TWO_BAR_BREAKOUT,
    )
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
               upper=102, lower=98, middle=100, ma3=99, ma15=100),
        candle(120_000, opened=100, high=101.2, low=99.8, close=101,
               upper=102, lower=98, middle=100.1, ma3=101, ma15=100),
    ]
    if side == "SHORT":
        for row in rows:
            for key in ("open", "close", "ma3", "ma15", "ma5", "kc_middle"):
                row[key] = 200 - row[key]
            row["high"], row["low"] = 200 - row["low"], 200 - row["high"]
    market = frame(rows)
    quote = rows[-1]["close"]
    signal = evaluate_entry_method(
        market, quote=quote, symbol="CAP/USDT", expected_method=MA3_MA15_CROSS,
    )
    assert signal is not None
    allowed, reason = revalidate_entry_method(
        market, symbol="CAP/USDT", side=side, quote=quote,
        context={
            "entry_method_authority": signal["method"],
            "entry_method_signal": signal,
            "entry_method_bar_id": signal["confirmation_bar_id"],
            "entry_method_event_id": signal["event_id"],
        },
    )
    assert not allowed
    assert reason == "BLOCKED_ENTRY_METHOD_MACRO_KC_RELATIVE_LOCATION"


def test_method_revalidation_requires_same_event():
    rows = [
        candle(60_000, opened=99, high=100.5, low=98.5, close=100,
               upper=110, lower=90, middle=100, ma3=99, ma15=100),
        candle(120_000, opened=100, high=101.2, low=99.8, close=101,
               upper=112, lower=98, middle=101, ma3=101, ma15=100),
    ]
    market = frame(rows)
    signal = evaluate_entry_method(
        market, quote=101, symbol="CAP/USDT",
        expected_method=MA3_MA15_CROSS,
    )
    assert signal is not None
    context = {
        "entry_method_authority": signal["method"],
        "entry_method_signal": signal,
        "entry_method_bar_id": signal["confirmation_bar_id"],
        "entry_method_event_id": signal["event_id"],
    }
    allowed, reason = revalidate_entry_method(
        market, symbol="CAP/USDT", side="LONG", quote=101, context=context,
    )
    assert allowed, reason

    context["entry_method_event_id"] = "stale"
    allowed, reason = revalidate_entry_method(
        market, symbol="CAP/USDT", side="LONG", quote=101, context=context,
    )
    assert not allowed
    assert reason == "BLOCKED_ENTRY_METHOD_SIGNAL_STALE_OR_INVALID"
