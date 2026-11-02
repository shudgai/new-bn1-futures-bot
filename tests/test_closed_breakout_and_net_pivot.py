"""Closed breakout authorization and positive-net-only ordinary pivot exits."""

import asyncio
from types import SimpleNamespace

import pytest

from core.services.entry_methods import KC_TWO_BAR_BREAKOUT, evaluate_entry_method
from core.services.exits.strict_true_pivot import (
    PROFIT_PIVOT_PENDING_KEY, profitable_true_pivot_exit, store_confirmed_pivot_exit,
)
from test_entry_methods import post_close_market, method_context
from test_kc_wave_exit import position
from test_profit_pivot_exit import pivot_frame

SYMBOLS = ["龙虾/USDT", "CAP/USDT", "1000PEPE/USDT"]


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
@pytest.mark.parametrize("symbol", SYMBOLS)
@pytest.mark.parametrize("fault", [None, "second_unclosed", "retreated_quote"])
@pytest.mark.parametrize("account_type", ["paper", "testnet"])
def test_real_paper_two_closed_breakout_does_not_require_third_bar_close(
    side, symbol, fault, account_type, monkeypatch, tmp_path,
):
    from core.paper_account import PaperAccount
    from core.services.execution_manager import ExecutionManager
    import time

    frame = post_close_market(side)
    last = frame.index[-1]
    # K1 starts inside the channel and closes outside; K2 confirms outside.
    frame.loc[frame.index[-2], "open"] = 100.0
    now = int(time.time() // 60) * 60_000
    frame["timestamp"] += now - 60_000 - int(frame.iloc[-1]["timestamp"])
    frame.attrs.update(snapshot_ms=time.time() * 1000)
    quote = float(frame.iloc[-1]["close"])
    signal = evaluate_entry_method(
        frame, quote=quote, symbol=symbol, expected_method=KC_TWO_BAR_BREAKOUT,
    )
    assert signal is not None
    payload = method_context(signal)
    row = frame.iloc[-1].to_dict()
    row.update(timestamp=now, is_closed=False, open=quote, high=quote + 0.01,
               low=quote - 0.01, close=quote)
    frame.loc[len(frame)] = row
    if fault == "second_unclosed":
        frame.loc[last, "is_closed"] = False
    elif fault == "retreated_quote":
        quote = float(frame.loc[last, "kc_upper" if side == "LONG" else "kc_lower"])
    frame.attrs.update(entry_method_context={"symbol": symbol, **payload}, entry_method_quote=quote)
    monkeypatch.setattr("core.paper_account.STATE_FILE", str(tmp_path / "paper.json"))
    monkeypatch.setattr("core.services.order_audit_trail.AUDIT_LOG_FILE", str(tmp_path / "audit.jsonl"))
    monkeypatch.setattr(ExecutionManager, "_last_exit_record", {})
    monkeypatch.setattr(ExecutionManager, "_last_trade_bar_time", {})
    monkeypatch.setattr(ExecutionManager, "_hourly_entry_records", {})
    if account_type == "paper":
        account = PaperAccount()
    else:
        from core.testnet_account import BinanceTestnetAccount
        from test_testnet_account import FakeTestnetExchange
        from decimal import Decimal, ROUND_DOWN
        monkeypatch.setattr("core.testnet_account.STATE_FILE", str(tmp_path / "testnet.json"))
        monkeypatch.setattr(BinanceTestnetAccount, "credentials_configured", staticmethod(lambda: True))
        exchange = FakeTestnetExchange()
        exchange.amount_to_precision = lambda _symbol, value: str(
            Decimal(str(value)).quantize(Decimal("0.001"), rounding=ROUND_DOWN)
        )
        account = BinanceTestnetAccount(exchange)
        asyncio.run(account.initialize())
    account.balance = account.available_balance = 150.0
    account.entry_frame_provider = lambda *_a, **_kw: frame
    opened = asyncio.run(ExecutionManager.open_position(
        account, symbol, side, quote, "EntryMethod:" + KC_TWO_BAR_BREAKOUT,
        meta={**payload, "raw_df": frame, "source": "UNIFIED_PIPELINE|ENTRY_METHOD"},
    ))
    assert opened is (fault is None), account.logs[-5:]
    assert (symbol in account.positions) is (fault is None)


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
@pytest.mark.parametrize("saved", [False, True])
def test_exchange_boundary_rechecks_net_before_submitting(side, saved, monkeypatch, tmp_path):
    from unittest.mock import AsyncMock
    from test_close_deduplication import account_for

    async def run():
        account, exchange = await account_for(side, tmp_path, monkeypatch)
        frame = pivot_frame(side)
        pos = position(side, frame)
        pos.update(qty=0.25, margin=25.0)
        pivot = profitable_true_pivot_exit(pos, frame)
        if saved:
            assert store_confirmed_pivot_exit(pos, pivot, key=PROFIT_PIVOT_PENDING_KEY)
        account.positions["DOGE/USDT"] = pos
        account.position_meta["DOGE/USDT"] = dict(pos)
        account._channel_exit_frames["DOGE/USDT"] = frame
        exchange.create_order = AsyncMock(side_effect=AssertionError("losing close submitted"))
        assert not await account.close_position("DOGE/USDT", 100.0, pivot[0])
        exchange.create_order.assert_not_awaited()
        assert "DOGE/USDT" in account.positions
    asyncio.run(run())


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
@pytest.mark.parametrize("symbol", SYMBOLS)
@pytest.mark.parametrize("saved", [False, True])
@pytest.mark.parametrize("transport", ["final_account", "realtime", "account_poll", "scan"])
def test_peak_history_never_authorizes_losing_or_fee_only_pivot_close(
    side, symbol, saved, transport, tmp_path, monkeypatch,
):
    from core.paper_account import PaperAccount
    from core.services.exits.realtime_profit_exit import enforce_realtime_profit_exit
    from core.services.exits.entry_atr_protection import enforce_atr_protection
    from core.gates.unified_pipeline_gates import ExitGate

    monkeypatch.setattr("core.paper_account.STATE_FILE", str(tmp_path / "paper.json"))
    frame = pivot_frame(side)
    pos = position(side, frame)
    pivot = profitable_true_pivot_exit(pos, frame)
    assert pivot is not None
    if saved:
        assert store_confirmed_pivot_exit(pos, pivot, key=PROFIT_PIVOT_PENDING_KEY)
    account = PaperAccount()
    account.positions[symbol] = pos
    account._channel_exit_frames = {symbol: frame}
    engine = SimpleNamespace(is_running=True, account=account, _channel_exit_frames={symbol: frame})
    for quote in (100.0, 100.01 if side == "LONG" else 99.99):
        assert profitable_true_pivot_exit(pos, frame, quote=quote) is None
        if transport == "final_account":
            result = asyncio.run(account.close_position(symbol, quote, pivot[0]))
        elif transport == "realtime":
            result = asyncio.run(enforce_realtime_profit_exit(engine, symbol, quote))
        elif transport == "account_poll":
            result = asyncio.run(enforce_atr_protection(account, symbol, quote))
        else:
            result = ExitGate.evaluate(pos, {
                "raw_df": frame, "current_price": quote, "kc_upper": 110,
                "kc_lower": 90, "kc_middle": 100, "atr": 1,
            })[0]
        assert not result
        assert symbol in account.positions
        assert not account.trades
    quote = 100.3 if side == "LONG" else 99.7
    assert asyncio.run(account.close_position(symbol, quote, pivot[0]))
    assert account.trades[0]["pnl"] > 0


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_zero_net_boundary_is_strictly_blocked(side, monkeypatch):
    from core.services.exits.strict_true_pivot import profit_pivot_net_positive

    monkeypatch.setattr(
        "core.services.exits.staged_profit_lock.calculate_staged_net_pnl_and_roe",
        lambda *_args: (0.0, 0.0, 0.0, 0.0),
    )
    assert not profit_pivot_net_positive({"side": side}, 100)


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_positive_net_includes_fee_on_slipped_fill(side):
    from core.config import SLIPPAGE_PCT, TAKER_FEE_RATE
    from core.services.exits.strict_true_pivot import profit_pivot_net_positive

    entry, fee, slip = 100.0, TAKER_FEE_RATE, SLIPPAGE_PCT
    boundary = (
        entry * (1 + fee) / ((1 - slip) * (1 - fee)) if side == "LONG"
        else entry * (1 - fee) / ((1 + slip) * (1 + fee))
    )
    direction = 1 if side == "LONG" else -1
    pos = {"side": side, "entry_price": entry, "qty": 2.0}
    assert profit_pivot_net_positive(pos, boundary * (1 + direction * 1e-8))
    assert not profit_pivot_net_positive(pos, boundary * (1 - direction * 1e-8))


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
@pytest.mark.parametrize("fault", [
    "forged", "position", "closing", "pending", "no_provider", "new_close", "unclosed",
])
def test_breakout_firewall_requires_exact_fresh_pattern_and_safe_account(side, fault):
    from core.services.entry_firewall import validate_account_entry

    frame = post_close_market(side)
    frame.loc[frame.index[-2], "open"] = 100.0
    quote = float(frame.iloc[-1]["close"])
    signal = evaluate_entry_method(
        frame, symbol="CAP/USDT", quote=quote, expected_method=KC_TWO_BAR_BREAKOUT,
    )
    assert signal is not None
    payload = {**method_context(signal), "entry_gate_quote": quote}
    account = SimpleNamespace(
        positions={}, closing_lock=set(), pending_limit_orders={},
        entry_frame_provider=lambda _symbol: frame,
    )
    if fault == "forged":
        payload["entry_method_event_id"] += "|FORGED"
    elif fault == "position":
        account.positions["CAP/USDT"] = {"side": side}
    elif fault == "closing":
        account.closing_lock.add("CAP/USDT")
    elif fault == "pending":
        account.pending_limit_orders["CAP/USDT"] = {}
    elif fault == "no_provider":
        account.entry_frame_provider = None
    elif fault == "unclosed":
        frame.loc[frame.index[-1], "is_closed"] = False
    else:
        account.last_closed_at = {"CAP/USDT": float(frame.iloc[-1]["timestamp"]) / 1000}
    with pytest.raises(ValueError, match="KC_TWO_BAR_BREAKOUT"):
        asyncio.run(validate_account_entry(account, "CAP/USDT", side, payload))
