import asyncio
import math
from types import SimpleNamespace

import pytest

from core.gates.central_entry_gate import CentralEntryGate
from core.gates.direction_master_lock import check_direction_lock_from_frame
from core.gates.pipeline import fast_cross_evidence
from core.services.entry_methods import (
    KC_TWO_BAR_BREAKOUT, MA5_MA15_CROSS, POST_CLOSE_KC_REENTRY,
    evaluate_entry_method, revalidate_entry_method,
)
from core.services.math_execution_gate import validate_entry_at_execution, validate_entry_math
from test_entry_methods import method_context, post_close_market
from test_symmetric_fast_cross_and_momentum_burst import make_test_frame_for_cross


SYMBOLS = ["1000PEPE/USDT", "龙虾/USDT", "CAP/USDT"]


@pytest.mark.parametrize("symbol", SYMBOLS)
@pytest.mark.parametrize("side", ["LONG", "SHORT"])
@pytest.mark.parametrize("method", [KC_TWO_BAR_BREAKOUT, POST_CLOSE_KC_REENTRY])
def test_entry_and_execution_ignore_conflicting_ma3(symbol, side, method):
    market = post_close_market(side)
    quote = float(market.iloc[-1]["close"])
    last_exit = 1_265_000 if method == POST_CLOSE_KC_REENTRY else None
    expected = evaluate_entry_method(
        market, quote=quote, symbol=symbol, expected_method=method,
        last_exit_timestamp=last_exit,
    )
    assert expected
    # MA3 is deliberately invalid and must not be required by any entry layer.
    market["ma3"] = math.nan
    signal = evaluate_entry_method(
        market, quote=quote, symbol=symbol, expected_method=method,
        last_exit_timestamp=last_exit,
    )
    assert signal == expected
    context = method_context(signal, last_exit)
    market.attrs["entry_method_context"] = {"symbol": symbol, **context}
    market.attrs["entry_method_quote"] = quote
    checks = [
        revalidate_entry_method(market, symbol=symbol, side=side, quote=quote, context=context),
        validate_entry_math(market, side),
        asyncio.run(validate_entry_at_execution(
            SimpleNamespace(), symbol, side, {**context, "entry_gate_frame": market},
            execution_price=quote,
        )),
    ]
    central = CentralEntryGate.verify_and_authorize(
        symbol, side, quote, market,
    )
    checks.append((central.passed, central.reason))
    for passed, reason in checks:
        assert passed, reason


@pytest.mark.parametrize("symbol", SYMBOLS)
@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_new_cross_authority_uses_ma5_and_rejects_old_literal(symbol, side):
    market = post_close_market(side)
    sign = 1 if side == "LONG" else -1
    market.loc[market.index[-2], "ma5"] = market.iloc[-2]["ma15"] - sign * 0.1
    market.loc[market.index[-1], "ma5"] = market.iloc[-1]["ma15"] + sign * 0.1
    market["ma3"] = 1_000 if side == "SHORT" else 0.001
    quote = float(market.iloc[-1]["close"])
    signal = evaluate_entry_method(
        market, symbol=symbol, quote=quote, expected_method=MA5_MA15_CROSS,
    )
    assert signal and signal["method"] == "MA5_MA15_CROSS"
    assert signal["side"] == side
    assert "MA5_MA15_CROSS" in signal["event_id"]
    assert evaluate_entry_method(
        market, symbol=symbol, quote=quote, expected_method="MA3_MA15_CROSS",
    ) is None
    old_context = method_context(signal)
    old_context["entry_method_authority"] = "MA3_MA15_CROSS"
    allowed, reason = revalidate_entry_method(
        market, symbol=symbol, side=side, quote=quote, context=old_context,
    )
    assert not allowed and reason == "BLOCKED_ENTRY_METHOD_UNSUPPORTED"
    market.loc[market.index[-1], "ma5"] = market.iloc[-1]["ma15"]
    assert evaluate_entry_method(
        market, symbol=symbol, quote=quote, expected_method=MA5_MA15_CROSS,
    ) is None


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
@pytest.mark.parametrize("invalid", [math.nan, math.inf, 0])
def test_ma3_cannot_rescue_invalid_ma5(side, invalid):
    market = post_close_market(side)
    market["ma3"] = market["ma5"]
    market.loc[market.index[-1], "ma5"] = invalid
    assert evaluate_entry_method(
        market, symbol=SYMBOLS[0], quote=float(market.iloc[-1]["close"]),
        expected_method=KC_TWO_BAR_BREAKOUT,
    ) is None


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_direction_lock_uses_ma5_not_ma3(side):
    market = post_close_market(side)
    market["ma3"] = market["ma5"].iloc[::-1].to_numpy()
    allowed, reason, details = check_direction_lock_from_frame(market, side)
    assert allowed, reason
    assert details["ma5_curr"] == market.iloc[-1]["ma5"]
    market.loc[market.index[-1], "ma5"] = market.iloc[-2]["ma5"]
    allowed, reason, _ = check_direction_lock_from_frame(market, side)
    assert not allowed and "SLOPE" in reason


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_fast_cross_does_not_read_ma3(side):
    market, quote = make_test_frame_for_cross(side)
    expected = fast_cross_evidence(market, quote, side)
    assert expected
    market = market.drop(columns=["ma3"])
    assert fast_cross_evidence(market, quote, side) == expected


def test_live_ma5_uses_five_period_quote_adjustment():
    market = post_close_market("LONG")
    market.loc[market.index[-1], "is_closed"] = False
    price = float(market.iloc[-1]["close"]) + 0.5
    metrics = CentralEntryGate._extract_market_metrics(market, price, allow_intrabar=True)
    assert metrics["valid"], metrics
    assert metrics["ma5_curr"] == pytest.approx(market.iloc[-1]["ma5"] + 0.5 / 5)
    assert metrics["ma15_curr"] == pytest.approx(market.iloc[-1]["ma15"] + 0.5 / 15)
    assert "ma3_curr" not in metrics


def test_missing_ma5_is_calculated_from_five_closes_not_three():
    market = post_close_market("LONG").drop(columns=["ma5"])
    price = float(market.iloc[-1]["close"])
    metrics = CentralEntryGate._extract_market_metrics(market, price)
    assert metrics["ma5_curr"] == pytest.approx(market["close"].tail(5).mean())
    assert metrics["ma5_prev"] == pytest.approx(market["close"].iloc[-6:-1].mean())
    short_history = market.tail(4)
    metrics = CentralEntryGate._extract_market_metrics(short_history, price)
    assert math.isnan(metrics["ma5_curr"])
    assert not check_direction_lock_from_frame(short_history, "LONG")[0]


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
@pytest.mark.parametrize("invalid", [math.nan, math.inf, 0, -1])
def test_direction_lock_rejects_invalid_ma5_even_with_valid_ma3(side, invalid):
    market = post_close_market(side)
    market["ma3"] = market["ma5"]
    market.loc[market.index[-1], "ma5"] = invalid
    assert not check_direction_lock_from_frame({"raw_df": market}, side)[0]
