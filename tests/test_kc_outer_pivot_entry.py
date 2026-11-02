"""Independent outside-KC reversals retain shared execution and exit safety."""

import asyncio
import copy
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from core.services.entry_methods import (
    KC_OUTER_PIVOT_REVERSAL, evaluate_entry_method, revalidate_entry_method,
)
from test_profit_pivot_exit import pivot_frame

SYMBOLS = ["龙虾/USDT", "CAP/USDT", "1000PEPE/USDT"]


def market(side):
    frame = pivot_frame("SHORT" if side == "LONG" else "LONG")
    for offset, index in enumerate(frame.index[:-3]):
        close = 98.5 + offset * 0.06
        frame.loc[index, ["open", "high", "low", "close", "ma5", "ma15"]] = [
            close - 0.04, close + 0.01, close - 0.05, close, close - 0.1, close - 0.2,
        ]
    frame["kc_middle"] = [98.55 + i * 0.05 for i in range(len(frame))]
    frame["kc_upper"], frame["kc_lower"] = frame["kc_middle"] + 0.8, frame["kc_middle"] - 0.8
    frame["ma15"] = 100.0
    frame.loc[frame.index[-2], "ma5"] = 99.1 if side == "LONG" else 100.9
    if side == "LONG":
        # Mirror the historical path as well as the completed valley fixture.
        for column in ("kc_middle", "ma15", "ma5"):
            if column != "ma5":
                frame[column] = 200 - frame[column]
        for index in frame.index[:-3]:
            opened, high, low, close, ma5 = frame.loc[index, ["open", "high", "low", "close", "ma5"]]
            frame.loc[index, ["open", "high", "low", "close", "ma5"]] = [
                200 - opened, 200 - low, 200 - high, 200 - close, 200 - ma5,
            ]
        upper = 200 - frame["kc_lower"]
        frame["kc_lower"], frame["kc_upper"] = 200 - frame["kc_upper"], upper
    return frame


def context(frame, side, symbol):
    quote = float(frame.iloc[-1]["close"])
    signal = evaluate_entry_method(
        frame, quote=quote, symbol=symbol, expected_method=KC_OUTER_PIVOT_REVERSAL,
    )
    assert signal is not None
    return {
        "symbol": symbol,
        "entry_method_authority": KC_OUTER_PIVOT_REVERSAL,
        "entry_method_signal": signal,
        "entry_method_bar_id": signal["confirmation_bar_id"],
        "entry_method_event_id": signal["event_id"],
    }


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
@pytest.mark.parametrize("symbol", SYMBOLS)
@pytest.mark.parametrize("flat_kc", [False, True])
@pytest.mark.parametrize("live_bar", [False, True])
def test_independent_reversal_passes_final_math_and_central_boundary(side, symbol, flat_kc, live_bar):
    from core.gates.central_entry_gate import CentralEntryGate
    from core.services.math_execution_gate import validate_entry_math, validate_entry_at_execution

    frame = market(side)
    quote = float(frame.iloc[-1]["close"])
    if flat_kc:
        frame["kc_middle"], frame["kc_upper"], frame["kc_lower"] = 100.0, 100.8, 99.2
    if live_bar:
        row = frame.iloc[-1].to_dict()
        row.update(
            timestamp=row["timestamp"] + 60_000, is_closed=False,
            open=quote, high=quote + 0.01, low=quote - 0.01, close=quote,
        )
        frame.loc[len(frame)] = row
    payload = context(frame, side, symbol)
    assert revalidate_entry_method(frame, symbol=symbol, side=side, quote=quote, context=payload)[0]
    frame.attrs["entry_method_context"] = payload
    frame.attrs["entry_method_quote"] = quote
    allowed, reason = validate_entry_math(frame, side, symbol=symbol)
    assert allowed, reason
    result = CentralEntryGate.verify_and_authorize(
        symbol, side, quote, frame, source="UNIFIED_PIPELINE|ENTRY_METHOD",
        allow_intrabar=True,
    )
    assert result.passed, result
    allowed, reason = asyncio.run(validate_entry_at_execution(
        SimpleNamespace(), symbol, side, {**payload, "entry_gate_frame": frame},
        execution_price=quote,
    ))
    assert allowed, reason


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
@pytest.mark.parametrize("fault", [
    "inside", "touch", "live", "midpoint", "ma5", "same_color", "flat_extreme",
    "gap", "old_snapshot", "broken_quote", "returned_quote", "invalid_ohlc", "ma5_slope",
])
def test_unconfirmed_or_destroyed_reversal_has_no_authority(side, fault):
    frame = market(side)
    quote = float(frame.iloc[-1]["close"])
    pivot, confirm = frame.index[-2], frame.index[-1]
    edge = "low" if side == "LONG" else "high"
    band = "kc_lower" if side == "LONG" else "kc_upper"
    if fault == "inside":
        frame[band] = 98 if side == "LONG" else 102
    elif fault == "touch":
        frame.loc[pivot, band] = frame.loc[pivot, edge]
    elif fault == "live":
        frame.loc[confirm, "is_closed"] = False
    elif fault == "midpoint":
        frame.loc[confirm, "close"] = (
            frame.loc[pivot, "open"] + frame.loc[pivot, "close"]
        ) / 2
    elif fault == "ma5":
        frame.loc[confirm, "ma5"] = frame.loc[confirm, "close"]
    elif fault == "ma5_slope":
        frame.loc[pivot, "ma5"] = frame.loc[confirm, "ma5"]
    elif fault == "same_color":
        frame.loc[confirm, "open"] = frame.loc[confirm, "close"]
    elif fault == "flat_extreme":
        frame.loc[pivot, edge] = frame.loc[confirm, edge]
    elif fault == "gap":
        frame.loc[pivot, "timestamp"] -= 10_000
    elif fault == "old_snapshot":
        frame["timestamp"] -= 600_000
        frame.attrs["snapshot_ms"] -= 600_000
    elif fault == "broken_quote":
        quote = frame.loc[pivot, edge]
    elif fault == "returned_quote":
        quote = frame.loc[confirm, "ma5"]
    elif fault == "invalid_ohlc":
        frame.loc[confirm, "high"] = frame.loc[confirm, "low"] - 1
    assert evaluate_entry_method(
        frame, quote=quote, symbol="CAP/USDT", expected_method=KC_OUTER_PIVOT_REVERSAL,
    ) is None


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_reversal_revalidates_exact_identity_and_rejects_preclose_pattern(side):
    frame = market(side)
    payload = context(frame, side, "CAP/USDT")
    quote = float(frame.iloc[-1]["close"])
    forged = copy.deepcopy(payload)
    forged["entry_method_event_id"] += "|FORGED"
    assert not revalidate_entry_method(
        frame, symbol="CAP/USDT", side=side, quote=quote, context=forged,
    )[0]
    assert evaluate_entry_method(
        frame, quote=quote, symbol="CAP/USDT", expected_method=KC_OUTER_PIVOT_REVERSAL,
        last_exit_timestamp=float(frame.iloc[-3]["timestamp"]) / 1000 + 1,
    ) is None


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
@pytest.mark.parametrize("symbol", SYMBOLS)
def test_reversal_uses_shared_submit_and_bar_claim_even_after_prior_close(side, symbol, monkeypatch):
    from core.services.execution_manager import ExecutionManager

    frame = market(side)
    account = SimpleNamespace(positions={}, entry_filter_last={}, save_state=Mock())
    key = ExecutionManager._get_account_key(account, symbol)
    monkeypatch.setattr(ExecutionManager, "_last_exit_record", {
        key: {"timestamp": float(frame.iloc[-4]["timestamp"]) / 1000},
    })
    monkeypatch.setattr(ExecutionManager, "_last_trade_bar_time", {})
    monkeypatch.setattr(ExecutionManager, "_hourly_entry_records", {})
    submit = AsyncMock(return_value=True)
    monkeypatch.setattr(ExecutionManager, "open_position", submit)

    async def evaluate():
        return await ExecutionManager._evaluate_closed_entry_methods(
            symbol=symbol, account=account, engine_or_account=account, frame=frame,
            quote=float(frame.iloc[-1]["close"]), macro_trend="NEUTRAL",
        )

    assert asyncio.run(evaluate())["reason"] == KC_OUTER_PIVOT_REVERSAL
    assert asyncio.run(evaluate())["reason"] == "ENTRY_METHOD_BAR_ALREADY_TRADED"
    submit.assert_awaited_once()
    assert submit.call_args.kwargs["side"] == side
    assert submit.call_args.kwargs["meta"]["entry_method_authority"] == KC_OUTER_PIVOT_REVERSAL


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
@pytest.mark.parametrize("symbol", SYMBOLS)
def test_real_paper_open_then_holds_entry_pivot_and_numeric_loss(
    side, symbol, monkeypatch, tmp_path,
):
    from core.paper_account import PaperAccount
    from core.services.data_pipeline import DataPipeline
    from core.services.exits.strict_true_pivot import profitable_true_pivot_exit
    from core.services.execution_manager import ExecutionManager

    monkeypatch.setattr("core.paper_account.STATE_FILE", str(tmp_path / "paper.json"))
    monkeypatch.setattr("core.services.order_audit_trail.AUDIT_LOG_FILE", str(tmp_path / "audit.jsonl"))
    monkeypatch.setattr(ExecutionManager, "_last_exit_record", {})
    monkeypatch.setattr(ExecutionManager, "_last_trade_bar_time", {})
    monkeypatch.setattr(ExecutionManager, "_hourly_entry_records", {})
    account = PaperAccount()
    account.balance = account.available_balance = 150.0
    frame = market(side)
    quote = float(frame.iloc[-1]["close"])
    account.entry_frame_provider = lambda *_args, **_kwargs: frame
    snapshot = DataPipeline.calculate_snapshot(symbol, frame, current_price=quote)
    snapshot.raw_df = frame
    snapshot.curr_k, snapshot.prev_k = frame.iloc[-1].to_dict(), frame.iloc[-2].to_dict()
    monkeypatch.setattr(DataPipeline, "calculate_snapshot", staticmethod(lambda *a, **k: snapshot))
    result = asyncio.run(ExecutionManager.process_symbol_pipeline(
        symbol, account, klines_df=frame, quote=quote,
    ))
    assert result["status"] == "ENTRY_EXECUTED", "\n".join(
        str(event.get("text")) for event in account.logs[-8:]
    )
    position = account.positions[symbol]
    assert position["side"] == side
    assert position["entry_method_authority"] == KC_OUTER_PIVOT_REVERSAL
    assert profitable_true_pivot_exit(position, frame) is None
    assert not asyncio.run(account.close_position(
        symbol, quote * (0.95 if side == "LONG" else 1.05), "CIRCUIT_BREAKER_HARD_SL",
    ))
    assert symbol in account.positions
    assert len(account.trades) == 1
    future = pivot_frame(side)
    future["timestamp"] += 300_000
    future.attrs["snapshot_ms"] += 300_000
    monkeypatch.setattr(
        "core.services.exits.strict_true_pivot.time.time",
        lambda: future.attrs["snapshot_ms"] / 1000,
    )
    account._channel_exit_frames = {symbol: future}
    decision = profitable_true_pivot_exit(position, future)
    assert decision is not None
    assert asyncio.run(account.close_position(
        symbol, float(future.iloc[-1]["close"]), decision[0],
    ))
    assert symbol not in account.positions
    assert [trade["action"] for trade in account.trades] == ["CLOSE_" + side, "OPEN_" + side]


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
@pytest.mark.parametrize("symbol", SYMBOLS)
def test_real_exchange_adapter_revalidates_independent_reversal(
    side, symbol, monkeypatch, tmp_path,
):
    import core.testnet_account as module
    from test_testnet_account import FakeTestnetExchange
    from core.testnet_account import BinanceTestnetAccount
    from core.services.execution_manager import ExecutionManager

    monkeypatch.setattr(module, "STATE_FILE", str(tmp_path / "exchange.json"))
    monkeypatch.setattr(BinanceTestnetAccount, "credentials_configured", staticmethod(lambda: True))
    monkeypatch.setattr("core.services.order_audit_trail.AUDIT_LOG_FILE", str(tmp_path / "audit.jsonl"))
    frame = market(side)
    quote = float(frame.iloc[-1]["close"])
    payload = context(frame, side, symbol)
    frame.attrs.update(entry_method_context=payload, entry_method_quote=quote)
    ex = FakeTestnetExchange()
    from decimal import Decimal, ROUND_DOWN
    ex.amount_to_precision = lambda _symbol, value: str(
        Decimal(str(value)).quantize(Decimal("0.001"), rounding=ROUND_DOWN)
    )
    account = BinanceTestnetAccount(ex)
    account.entry_frame_provider = lambda *_args, **_kwargs: frame
    monkeypatch.setattr(ExecutionManager, "_last_exit_record", {})
    monkeypatch.setattr(ExecutionManager, "_last_trade_bar_time", {})
    monkeypatch.setattr(ExecutionManager, "_hourly_entry_records", {})

    async def run():
        await account.initialize()
        return await ExecutionManager._evaluate_closed_entry_methods(
            symbol=symbol, account=account, engine_or_account=account, frame=frame,
            quote=quote, macro_trend="NEUTRAL", only_method=KC_OUTER_PIVOT_REVERSAL,
        )

    result = asyncio.run(run())
    assert result["status"] == "ENTRY_EXECUTED", "\n".join(
        str(event.get("text")) for event in account.logs[-8:]
    )
    entries = [order for order in ex.orders if not order["params"].get("reduceOnly")]
    assert len(entries) == 1
    assert entries[0]["side"] == ("buy" if side == "LONG" else "sell")
    assert account.positions[symbol]["entry_method_authority"] == KC_OUTER_PIVOT_REVERSAL


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
@pytest.mark.parametrize("fault", [
    "forged", "fresh_quote_invalid", "position", "closing", "pending", "no_provider", "new_close",
])
def test_firewall_revalidates_reversal_and_preserves_account_safety(side, fault):
    from core.services.entry_firewall import validate_account_entry

    frame = market(side)
    payload = context(frame, side, "CAP/USDT")
    payload["entry_gate_quote"] = float(frame.iloc[-1]["close"])
    account = SimpleNamespace(
        positions={}, closing_lock=set(), pending_limit_orders={},
        entry_frame_provider=lambda _symbol: frame,
    )
    if fault == "forged":
        payload["entry_method_event_id"] += "|FORGED"
    elif fault == "fresh_quote_invalid":
        changed = frame.copy()
        changed.loc[changed.index[-1], "ma5"] = changed.iloc[-1]["close"]
        account.entry_frame_provider = lambda _symbol: changed
    elif fault == "position":
        account.positions["CAP/USDT"] = {"side": side}
    elif fault == "closing":
        account.closing_lock.add("CAP/USDT")
    elif fault == "pending":
        account.pending_limit_orders["CAP/USDT"] = {}
    elif fault == "new_close":
        account.last_closed_at = {"CAP/USDT": float(frame.iloc[-1]["timestamp"]) / 1000}
    else:
        account.entry_frame_provider = None
    with pytest.raises(ValueError, match="KC_OUTER_PIVOT"):
        asyncio.run(validate_account_entry(account, "CAP/USDT", side, payload))


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_restart_close_record_prevents_reusing_a_preclose_reversal(side, monkeypatch):
    from core.services.execution_manager import ExecutionManager

    frame = market(side)
    account = SimpleNamespace(
        positions={}, entry_filter_last={}, save_state=Mock(),
        last_closed_at={"CAP/USDT": float(frame.iloc[-1]["timestamp"]) / 1000 + 1},
    )
    monkeypatch.setattr(ExecutionManager, "_last_exit_record", {})
    submit = AsyncMock()
    monkeypatch.setattr(ExecutionManager, "open_position", submit)
    result = asyncio.run(ExecutionManager._evaluate_closed_entry_methods(
        symbol="CAP/USDT", account=account, engine_or_account=account, frame=frame,
        quote=float(frame.iloc[-1]["close"]), macro_trend="NEUTRAL",
        only_method=KC_OUTER_PIVOT_REVERSAL,
    ))
    assert result is None
    submit.assert_not_awaited()
