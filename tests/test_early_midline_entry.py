"""Single closed midline impulses retain KC direction and final account safety."""

import asyncio
import time
from decimal import Decimal, ROUND_DOWN
from types import SimpleNamespace

import pytest

from core.services.entry_methods import (
    KC_EARLY_MIDLINE_BREAK, KC_TWO_BAR_BREAKOUT, MEME_GATE_ENTRY, evaluate_entry_method,
    revalidate_entry_method,
)
from test_entry_methods import post_close_market, method_context

SYMBOLS = ["龙虾/USDT", "CAP/USDT", "1000PEPE/USDT"]


def early_frame(side):
    frame = post_close_market("LONG")
    now = int(time.time() // 60) * 60_000
    frame["timestamp"] += now - 60_000 - int(frame.iloc[-1]["timestamp"])
    frame.attrs["snapshot_ms"] = time.time() * 1000
    previous_ma15 = float(frame.iloc[-2]["ma15"])
    frame.loc[frame.index[-2], ["ma5", "kc_middle", "atr"]] = [
        previous_ma15 + .1, 100.8, 2.0,
    ]
    frame.loc[frame.index[-2], ["kc_upper", "kc_lower"]] = [103.0, 98.8]
    frame.loc[frame.index[-1], ["open", "high", "low", "close", "ma5", "ma15", "kc_middle", "kc_upper", "kc_lower"]] = [
        100, 101.6, 99.9, 101.5, 101.2, 100.95, 100.9, 103, 98.8,
    ]
    if side == "SHORT":
        for key in ("open", "close", "ma5", "ma15", "kc_middle"):
            frame[key] = 200 - frame[key]
        high, upper = 200 - frame["low"], 200 - frame["kc_lower"]
        frame["low"], frame["high"] = 200 - frame["high"], high
        frame["kc_lower"], frame["kc_upper"] = 200 - frame["kc_upper"], upper
    frame.attrs["macro_trend"] = "BULLISH" if side == "LONG" else "BEARISH"
    return frame


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
@pytest.mark.parametrize("symbol", SYMBOLS)
@pytest.mark.parametrize("fault", [
    None, "live", "body", "wrong_kc", "flat_kc", "wrong_ma5", "flat_ma5",
    "bias_close", "bias_quote", "quote_inside", "quote_ma5", "gap", "stale",
    "no_snapshot", "invalid_atr", "after_close",
])
def test_single_closed_impulse_has_no_intrabar_or_direction_bypass(side, symbol, fault):
    frame = early_frame(side)
    last, prev = frame.index[-1], frame.index[-2]
    sign = 1 if side == "LONG" else -1
    quote = float(frame.iloc[-1]["close"])
    if fault == "live":
        frame.loc[last, "is_closed"] = False
    elif fault == "body":
        frame.loc[last, "open"] = quote - sign * 1.39
    elif fault in ("wrong_kc", "flat_kc"):
        frame.loc[prev, "kc_middle"] = frame.loc[last, "kc_middle"] + sign * (0.1 if fault == "wrong_kc" else 0)
    elif fault in ("wrong_ma5", "flat_ma5"):
        frame.loc[prev, "ma5"] = frame.loc[last, "ma5"] + sign * (0.1 if fault == "wrong_ma5" else 0)
    elif fault == "bias_close":
        frame.loc[last, "ma15"] = quote - sign * 2.01
    elif fault == "bias_quote":
        quote = float(frame.iloc[-1]["ma15"]) + sign * 2.01
    elif fault == "quote_inside":
        quote = float(frame.iloc[-1]["kc_middle"])
    elif fault == "quote_ma5":
        quote = float(frame.iloc[-1]["ma5"])
    elif fault == "gap":
        frame.loc[prev, "timestamp"] -= 1000
    elif fault == "stale":
        frame.attrs["snapshot_ms"] -= 200_000
    elif fault == "no_snapshot":
        frame.attrs.pop("snapshot_ms")
    elif fault == "invalid_atr":
        frame.loc[prev, "atr"] = float("nan")
    closed_at = float(frame.iloc[-1]["timestamp"]) / 1000 if fault == "after_close" else None
    signal = evaluate_entry_method(
        frame, quote=quote, symbol=symbol, expected_method=KC_EARLY_MIDLINE_BREAK,
        last_exit_timestamp=closed_at,
    )
    expected = fault is None
    assert (signal is not None) is expected
    if signal:
        assert signal["side"] == side
        assert signal["entry_atr"] == 2.0
        assert revalidate_entry_method(
            frame, symbol=symbol, side=side, quote=quote,
            context=method_context(signal, closed_at),
        )[0]


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
@pytest.mark.parametrize("body,expected", [(1.399999, False), (1.4, True), (1.400001, True)])
def test_body_uses_preceding_closed_atr_inclusive(side, body, expected):
    frame = early_frame(side)
    sign = 1 if side == "LONG" else -1
    frame.loc[frame.index[-1], "open"] = frame.iloc[-1]["close"] - sign * body
    frame.loc[frame.index[-1], "atr"] = 10.0
    assert (evaluate_entry_method(
        frame, quote=float(frame.iloc[-1]["close"]), symbol="CAP/USDT",
        expected_method=KC_EARLY_MIDLINE_BREAK,
    ) is not None) is expected


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
@pytest.mark.parametrize("bias,expected", [(1.999999, True), (2.0, True), (2.000001, False)])
def test_both_quote_and_close_have_inclusive_one_atr_limit(side, bias, expected):
    frame = early_frame(side)
    sign = 1 if side == "LONG" else -1
    quote = float(frame.iloc[-1]["ma15"]) + sign * bias
    signal = evaluate_entry_method(
        frame, quote=float(frame.iloc[-1]["close"]), symbol="CAP/USDT",
        expected_method=KC_EARLY_MIDLINE_BREAK,
    )
    allowed, reason = revalidate_entry_method(
        frame, symbol="CAP/USDT", side=side, quote=quote, context=method_context(signal),
    )
    assert allowed is expected


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
@pytest.mark.parametrize("symbol", SYMBOLS)
@pytest.mark.parametrize("account_type", ["paper", "testnet"])
@pytest.mark.parametrize("forming", [False, True])
@pytest.mark.parametrize("fault", [None, "ma15_alignment", "fresh_bias", "restart_close", "stale_close"])
@pytest.mark.parametrize("method", [KC_EARLY_MIDLINE_BREAK, MEME_GATE_ENTRY])
def test_shared_executor_full_account_open_uses_early_authority(
    side, symbol, account_type, forming, fault, method, tmp_path, monkeypatch,
):
    from core.paper_account import PaperAccount
    from core.testnet_account import BinanceTestnetAccount
    from core.services.execution_manager import ExecutionManager
    from test_testnet_account import FakeTestnetExchange

    frame = early_frame(side)
    if fault == "ma15_alignment":
        frame.loc[frame.index[-1], "ma15"] = frame.iloc[-1]["ma5"] + (
            0.001 if side == "LONG" else -0.001
        )
    quote = float(frame.iloc[-1]["close"])
    fresh_frame = frame.copy()
    if fault == "fresh_bias":
        fresh_frame.loc[fresh_frame.index[-1], "ma15"] = quote - (3.01 if side == "LONG" else -3.01)
    if forming:
        row = frame.iloc[-1].to_dict()
        row.update(timestamp=row["timestamp"] + 60_000, is_closed=False, open=quote)
        frame.loc[len(frame)] = row
    monkeypatch.setattr("core.paper_account.STATE_FILE", str(tmp_path / "paper.json"))
    monkeypatch.setattr("core.testnet_account.STATE_FILE", str(tmp_path / "testnet.json"))
    monkeypatch.setattr("core.services.order_audit_trail.AUDIT_LOG_FILE", str(tmp_path / "audit.jsonl"))
    monkeypatch.setattr(BinanceTestnetAccount, "credentials_configured", staticmethod(lambda: True))
    monkeypatch.setattr(ExecutionManager, "_last_exit_record", {})
    monkeypatch.setattr(ExecutionManager, "_last_trade_bar_time", {})
    monkeypatch.setattr(ExecutionManager, "_hourly_entry_records", {})
    if account_type == "paper":
        account = PaperAccount()
        account.balance = account.available_balance = 150
    else:
        exchange = FakeTestnetExchange()
        exchange.amount_to_precision = lambda _symbol, value: str(
            Decimal(str(value)).quantize(Decimal("0.001"), rounding=ROUND_DOWN)
        )
        account = BinanceTestnetAccount(exchange)
        asyncio.run(account.initialize())
    account.entry_frame_provider = lambda *_a, **_k: fresh_frame
    if fault in ("restart_close", "stale_close"):
        account.last_closed_at[symbol] = float(fresh_frame.iloc[-1]["timestamp"]) / 1000 + (
            -181 if fault == "restart_close" else 1
        )
    async def run():
        result = await ExecutionManager._evaluate_closed_entry_methods(
            symbol=symbol, account=account, engine_or_account=account, frame=frame,
            quote=quote, macro_trend="BULLISH" if side == "LONG" else "BEARISH",
            only_method=method,
        )
        if fault not in (None, "restart_close"):
            assert result is None or result["status"] != "ENTRY_EXECUTED"
            assert symbol not in account.positions
            return
        assert result["status"] == "ENTRY_EXECUTED", "\n".join(str(x.get("text")) for x in account.logs[-8:])
        again = await ExecutionManager._evaluate_closed_entry_methods(
            symbol=symbol, account=account, engine_or_account=account, frame=frame,
            quote=quote, macro_trend="BULLISH" if side == "LONG" else "BEARISH",
            only_method=method,
        )
        assert again is None or again["status"] != "ENTRY_EXECUTED"
    asyncio.run(run())
    if fault in (None, "restart_close"):
        assert account.positions[symbol]["entry_method_authority"] == method


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
@pytest.mark.parametrize("symbol", SYMBOLS)
@pytest.mark.parametrize("fault", [
    None, "position", "closing", "pending", "missing_provider", "changed_close",
    "forged_event", "changed_bar", "quote_bias", "provider_error",
])
def test_early_authority_cannot_bypass_final_account_firewall(side, symbol, fault):
    from core.services.entry_firewall import validate_account_entry

    frame = early_frame(side)
    quote = float(frame.iloc[-1]["close"])
    signal = evaluate_entry_method(
        frame, symbol=symbol, quote=quote, expected_method=KC_EARLY_MIDLINE_BREAK,
    )
    context = {**method_context(signal), "entry_gate_quote": quote}
    account = SimpleNamespace(
        positions={}, closing_lock=set(), pending_limit_orders={}, last_closed_at={},
        entry_frame_provider=lambda _symbol: frame,
    )
    if fault == "position":
        account.positions[symbol] = {"side": side}
    elif fault == "closing":
        account.closing_lock.add(symbol)
    elif fault == "pending":
        account.pending_limit_orders[symbol] = {"side": side}
    elif fault == "missing_provider":
        account.entry_frame_provider = None
    elif fault == "changed_close":
        account.last_closed_at[symbol] = time.time()
    elif fault == "forged_event":
        context["entry_method_event_id"] = "forged"
    elif fault == "changed_bar":
        context["entry_method_bar_id"] += 60_000
    elif fault == "quote_bias":
        context["entry_gate_quote"] = frame.iloc[-1]["ma15"] + (2.01 if side == "LONG" else -2.01)
    elif fault == "provider_error":
        def broken_provider(_symbol):
            raise RuntimeError("data unavailable")
        account.entry_frame_provider = broken_provider
    if fault:
        with pytest.raises(RuntimeError if fault == "provider_error" else ValueError):
            asyncio.run(validate_account_entry(account, symbol, side, context))
    else:
        result = asyncio.run(validate_account_entry(account, symbol, side, context))
        assert result["type"] == KC_EARLY_MIDLINE_BREAK


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
@pytest.mark.parametrize("symbol", SYMBOLS + ["BTC/USDT"])
@pytest.mark.parametrize("bias,expected_meme", [(0.999999, True), (1.0, True), (1.000001, False)])
def test_two_bar_one_atr_scope_and_inclusive_boundary(side, symbol, bias, expected_meme):
    frame = post_close_market(side)
    now = int(time.time() // 60) * 60_000
    frame["timestamp"] += now - 60_000 - int(frame.iloc[-1]["timestamp"])
    frame.attrs.update(snapshot_ms=time.time() * 1000, macro_trend="BULLISH" if side == "LONG" else "BEARISH")
    frame.loc[frame.index[-2], "open"] = 100.0
    quote = float(frame.iloc[-1]["close"])
    signal = evaluate_entry_method(
        frame, symbol=symbol, quote=quote, expected_method=KC_TWO_BAR_BREAKOUT,
    )
    assert signal is not None
    sign = 1 if side == "LONG" else -1
    atr = float(frame.iloc[-1]["atr"])
    frame.loc[frame.index[-1], "ma15"] = quote - sign * bias * atr
    allowed, reason = revalidate_entry_method(
        frame, symbol=symbol, side=side, quote=quote, context=method_context(signal),
    )
    assert allowed is (True if symbol == "BTC/USDT" else expected_meme), reason


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
@pytest.mark.parametrize("symbol", SYMBOLS)
def test_early_closed_setup_must_start_after_actual_previous_close(side, symbol):
    frame = early_frame(side)
    quote = float(frame.iloc[-1]["close"])
    setup_start = float(frame.iloc[-1]["timestamp"]) / 1000
    for closed_at, expected in ((setup_start - 1, True), (setup_start, False), (setup_start + 1, False)):
        signal = evaluate_entry_method(
            frame, symbol=symbol, quote=quote, expected_method=KC_EARLY_MIDLINE_BREAK,
            last_exit_timestamp=closed_at,
        )
        assert (signal is not None) is expected
        if signal:
            assert revalidate_entry_method(
                frame, symbol=symbol, side=side, quote=quote,
                context=method_context(signal, closed_at),
            )[0]
