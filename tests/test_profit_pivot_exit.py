"""Ordinary exits require a profitable, post-entry completed three-bar pivot."""

import asyncio
import copy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from core.gates.unified_pipeline_gates import ExitGate
from core.services.data_pipeline import DataPipeline
from core.services.exits.authorized_position_exit import automated_position_exit_allowed
from core.services.exits.strict_true_pivot import (
    PROFIT_PIVOT_PENDING_KEY, PROFIT_PIVOT_EXIT_POLICY,
    profitable_true_pivot_exit, store_confirmed_pivot_exit,
)
from core.services.exits.trend_lock import KC_WAVE_EXIT_REASON
from core.services.math_execution_gate import validate_exit_at_execution
from test_kc_wave_exit import position, wave_frame

SYMBOLS = ["1000PEPE/USDT", "龙虾/USDT", "CAP/USDT"]


def pivot_frame(side, *, gain=1.0, atr=1.0):
    frame = wave_frame(side, reversed_direction=False)
    frame["kc_upper"], frame["kc_lower"], frame["atr"] = 110.0, 90.0, atr
    frame[["open", "close", "ma5"]] = 100.0
    frame["high"], frame["low"] = 100.0 + 0.1 * gain, 100.0 - 0.1 * gain
    candles = [
        (0.2, 0.7, 0.1, 0.6),
        (0.6, 1.0, 0.5, 0.9),
        (0.8, 0.85, 0.1, 0.3),
    ]
    for index, values in zip(frame.index[-3:], candles):
        opened, high, low, close = (100.0 + gain * value for value in values)
        if side == "SHORT":
            opened, high, low, close = 200 - opened, 200 - low, 200 - high, 200 - close
        frame.loc[index, ["open", "high", "low", "close"]] = [opened, high, low, close]
    frame.loc[frame.index[-1], "ma5"] = 100 + (0.5 * gain if side == "LONG" else -0.5 * gain)
    return frame


@pytest.mark.parametrize("symbol", SYMBOLS)
@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_profitable_pivot_overrides_aligned_kc_hold_at_every_boundary(symbol, side):
    frame = pivot_frame(side)
    pos = position(side, frame)
    pos["symbol"] = symbol
    pivot = profitable_true_pivot_exit(pos, frame)
    assert pivot is not None
    account = SimpleNamespace(position_meta={}, _channel_exit_frames={symbol: frame})
    quote = float(frame.iloc[-1]["close"])
    assert automated_position_exit_allowed(symbol, pos, {}, pivot[0], quote, frame=frame)
    allowed, code, details = validate_exit_at_execution(account, symbol, pos, quote, pivot[0])
    assert allowed and code == "PROFIT_CONFIRMED_TRUE_PIVOT_APPROVED"
    assert details["exit_policy"] == PROFIT_PIVOT_EXIT_POLICY
    snapshot = DataPipeline.calculate_snapshot(symbol, frame, current_price=quote, position=pos)
    snapshot.raw_df = frame
    snapshot.curr_k, snapshot.prev_k = frame.iloc[-1].to_dict(), frame.iloc[-2].to_dict()
    snapshot.kc_upper, snapshot.kc_lower, snapshot.atr = 110.0, 90.0, 1.0
    allowed, reason, _ = ExitGate.evaluate(pos, snapshot)
    assert allowed and reason == pivot[0]


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
@pytest.mark.parametrize("fault", [
    "no_profit", "live", "stale", "flat_peak", "midpoint", "ma5", "same_color",
    "invalid_ohlc", "gap", "pre_entry", "entry_candle", "unknown_finality", "no_snapshot",
    "old_snapshot",
])
def test_pullbacks_and_invalid_or_unconfirmed_pivots_hold(side, fault):
    frame = pivot_frame(side, gain=0.4 if fault == "no_profit" else 1.0)
    pos = position(side, frame)
    pos["leverage"] = 1.0
    confirm, pivot = frame.index[-1], frame.index[-2]
    if fault == "live":
        frame.loc[confirm, "is_closed"] = False
    elif fault == "stale":
        frame.attrs["snapshot_ms"] += 600_000
    elif fault == "flat_peak":
        edge = "high" if side == "LONG" else "low"
        frame.loc[pivot, edge] = frame.loc[confirm, edge]
    elif fault == "midpoint":
        frame.loc[confirm, "close"] = (frame.loc[pivot, "open"] + frame.loc[pivot, "close"]) / 2
    elif fault == "ma5":
        frame.loc[confirm, "ma5"] = frame.loc[confirm, "close"]
    elif fault == "same_color":
        frame.loc[confirm, "open"] = frame.loc[confirm, "close"]
    elif fault == "invalid_ohlc":
        frame.loc[confirm, "high"] = frame.loc[confirm, "low"] - 1
    elif fault == "gap":
        frame.loc[pivot, "timestamp"] -= 60_000
    elif fault in ("pre_entry", "entry_candle"):
        pos["open_timestamp"] = float(frame.iloc[-3]["timestamp"]) / 1000 + (
            120 if fault == "pre_entry" else 1
        )
    elif fault == "unknown_finality":
        frame = frame.drop(columns=["is_closed"])
    elif fault == "no_snapshot":
        frame.attrs.pop("snapshot_ms")
    elif fault == "old_snapshot":
        frame["timestamp"] -= 600_000
        frame.attrs["snapshot_ms"] -= 600_000
        pos["open_timestamp"] -= 600
    assert profitable_true_pivot_exit(pos, frame) is None


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
@pytest.mark.parametrize("gain,roe,expected", [
    (0.5, 0.0, True), (0.499, 0.0, False),
    (0.2, 0.05, True), (0.2, 0.0499, False),
])
def test_exact_profit_thresholds(side, gain, roe, expected):
    frame = pivot_frame(side, gain=gain)
    pos = position(side, frame)
    pos.update(leverage=1.0, peak_roe=roe)
    assert (profitable_true_pivot_exit(pos, frame) is not None) is expected


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_earlier_post_entry_profit_still_qualifies_later_pivot(side):
    frame = pivot_frame(side, gain=0.2)
    frame.loc[frame.index[-5], "high" if side == "LONG" else "low"] = (
        101.0 if side == "LONG" else 99.0
    )
    pos = position(side, frame)
    pos["leverage"] = 1.0
    assert profitable_true_pivot_exit(pos, frame) is not None


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_kc_reversal_alone_cannot_close(side):
    frame = wave_frame(side)
    pos = position(side, frame)
    assert not automated_position_exit_allowed(
        "CAP/USDT", pos, {}, KC_WAVE_EXIT_REASON, 100.2, frame=frame,
    )
    assert not ExitGate.evaluate(pos, {"raw_df": frame, "current_price": 100.2})[0]


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_pending_ticket_survives_but_never_transfers_to_another_position(side):
    frame = pivot_frame(side)
    pos = position(side, frame)
    pivot = profitable_true_pivot_exit(pos, frame)
    assert store_confirmed_pivot_exit(pos, pivot, key=PROFIT_PIVOT_PENDING_KEY)
    assert profitable_true_pivot_exit(pos, None) == pivot
    for field in ("entry_price", "qty", "open_timestamp"):
        other = copy.deepcopy(pos)
        other[field] += 1
        assert profitable_true_pivot_exit(other, None) is None
    pos[PROFIT_PIVOT_PENDING_KEY]["details"].pop("exit_policy")
    assert profitable_true_pivot_exit(pos, None) is None


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
@pytest.mark.parametrize("field,value", [
    ("reference_atr", 0), ("peak_gain", float("nan")),
    ("confirmation_close", 100.5), ("confirmation_ma5", None),
])
def test_invalid_saved_pivot_evidence_cannot_authorize_close(side, field, value, caplog):
    frame = pivot_frame(side)
    pos = position(side, frame)
    pivot = profitable_true_pivot_exit(pos, frame)
    assert store_confirmed_pivot_exit(pos, pivot, key=PROFIT_PIVOT_PENDING_KEY)
    if field == "confirmation_close":
        value = 101.0 if side == "LONG" else 99.0
    pos[PROFIT_PIVOT_PENDING_KEY]["details"][field] = value
    assert profitable_true_pivot_exit(pos, None) is None
    assert "profit-pivot pending evidence" in caplog.text


@pytest.mark.parametrize("symbol", SYMBOLS)
@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_pipeline_closes_real_paper_position_on_pivot_without_reversal(
    symbol, side, tmp_path, monkeypatch,
):
    from core.paper_account import PaperAccount
    from core.services.execution_manager import ExecutionManager

    monkeypatch.setattr("core.paper_account.STATE_FILE", str(tmp_path / "paper.json"))
    frame = pivot_frame(side)
    pos = position(side, frame)
    pos["symbol"] = symbol
    quote = float(frame.iloc[-1]["close"])
    snapshot = DataPipeline.calculate_snapshot(symbol, frame, current_price=quote, position=pos)
    snapshot.raw_df = frame
    snapshot.curr_k, snapshot.prev_k = frame.iloc[-1].to_dict(), frame.iloc[-2].to_dict()
    snapshot.kc_upper, snapshot.kc_lower, snapshot.atr = 110.0, 90.0, 1.0
    monkeypatch.setattr(DataPipeline, "calculate_snapshot", staticmethod(lambda *a, **k: snapshot))
    account = PaperAccount()
    account.positions[symbol] = pos
    result = asyncio.run(ExecutionManager.process_symbol_pipeline(
        symbol, account, klines_df=frame, quote=quote,
    ))
    assert result["status"] == "EXIT_EXECUTED", result
    assert symbol not in account.positions
    assert [trade["action"] for trade in account.trades] == ["CLOSE_" + side]


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_failed_exchange_pivot_close_persists_retry_across_restart(side, tmp_path, monkeypatch):
    from core.testnet_account import BinanceTestnetAccount
    from test_close_deduplication import account_for

    async def run():
        account, ex = await account_for(side, tmp_path, monkeypatch)
        frame = pivot_frame(side)
        pos = position(side, frame)
        pos.update(symbol="DOGE/USDT", qty=0.25, margin=25.0, leverage=1)
        account.positions["DOGE/USDT"] = pos
        account.position_meta["DOGE/USDT"] = dict(pos)
        account._channel_exit_frames["DOGE/USDT"] = frame
        reason = profitable_true_pivot_exit(pos, frame)[0]
        create = ex.create_order
        ex.create_order = AsyncMock(side_effect=RuntimeError("order rejected"))
        quote = 100.2 if side == "LONG" else 99.8
        assert not await account.close_position("DOGE/USDT", quote, reason, True)
        assert PROFIT_PIVOT_PENDING_KEY in account.position_meta["DOGE/USDT"]
        restored = BinanceTestnetAccount(ex)
        await restored.initialize()
        assert profitable_true_pivot_exit(
            restored.positions["DOGE/USDT"], None, restored.position_meta["DOGE/USDT"],
        ) is not None
        ex.create_order = create
        assert await restored.close_position("DOGE/USDT", quote, reason, True)
        assert "DOGE/USDT" not in restored.positions
        assert len(restored.trades) == 1

    asyncio.run(run())


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
@pytest.mark.parametrize("symbol", SYMBOLS)
@pytest.mark.parametrize("pivot_ready", [False, True])
@pytest.mark.parametrize("transport", ["realtime", "account_poll"])
def test_quote_and_account_poll_use_same_pivot_authority_and_account_boundary(
    side, symbol, pivot_ready, transport, tmp_path, monkeypatch,
):
    from core.paper_account import PaperAccount
    from core.services.exits.realtime_profit_exit import enforce_realtime_profit_exit
    from core.services.exits.entry_atr_protection import enforce_atr_protection

    monkeypatch.setattr("core.paper_account.STATE_FILE", str(tmp_path / "paper.json"))
    monkeypatch.setattr("core.paper_account.DISABLE_STOP_LOSS", True)
    monkeypatch.setattr("core.paper_account.ONLY_CLOSE_ON_PROFIT", True)
    account = PaperAccount()
    frame = pivot_frame(side) if pivot_ready else wave_frame(side)
    pos = position(side, frame)
    pos["symbol"] = symbol
    account.positions[symbol] = pos
    account._channel_exit_frames = {symbol: frame}
    engine = SimpleNamespace(
        is_running=True, account=account, _channel_exit_frames={symbol: frame},
    )
    quote = 100.2 if side == "LONG" else 99.8
    operation = (
        enforce_realtime_profit_exit(engine, symbol, quote)
        if transport == "realtime" else enforce_atr_protection(account, symbol, quote)
    )
    closed = asyncio.run(operation)
    assert closed is pivot_ready
    assert (symbol not in account.positions) is pivot_ready
    assert len(account.trades) == int(pivot_ready)


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
@pytest.mark.parametrize("symbol", SYMBOLS)
@pytest.mark.parametrize("transport", ["realtime", "account_poll"])
def test_saved_pivot_retries_without_cached_frame(side, symbol, transport, tmp_path, monkeypatch):
    from core.paper_account import PaperAccount
    from core.services.exits.realtime_profit_exit import enforce_realtime_profit_exit
    from core.services.exits.entry_atr_protection import enforce_atr_protection

    monkeypatch.setattr("core.paper_account.STATE_FILE", str(tmp_path / "paper.json"))
    account = PaperAccount()
    frame = pivot_frame(side)
    pos = position(side, frame)
    pos["symbol"] = symbol
    pivot = profitable_true_pivot_exit(pos, frame)
    assert store_confirmed_pivot_exit(pos, pivot, key=PROFIT_PIVOT_PENDING_KEY)
    account.positions[symbol] = pos
    engine = SimpleNamespace(is_running=True, account=account, _channel_exit_frames={})
    quote = 100.2 if side == "LONG" else 99.8
    operation = (
        enforce_realtime_profit_exit(engine, symbol, quote)
        if transport == "realtime" else enforce_atr_protection(account, symbol, quote)
    )
    assert asyncio.run(operation)
    assert symbol not in account.positions
    assert len(account.trades) == 1


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_exchange_pivot_cannot_submit_when_intent_persistence_fails(side, tmp_path, monkeypatch):
    from test_close_deduplication import account_for

    async def run():
        account, ex = await account_for(side, tmp_path, monkeypatch)
        frame = pivot_frame(side)
        pos = position(side, frame)
        pos.update(symbol="DOGE/USDT", qty=0.25, margin=25.0, leverage=1)
        account.positions["DOGE/USDT"] = pos
        account.position_meta["DOGE/USDT"] = dict(pos)
        account._channel_exit_frames["DOGE/USDT"] = frame
        reason = profitable_true_pivot_exit(pos, frame)[0]
        create = AsyncMock()
        monkeypatch.setattr(ex, "create_order", create)

        original_save = account.save_state

        def fail_save(*, strict=False):
            if strict:
                raise OSError("state write failed")
            return original_save()

        monkeypatch.setattr(account, "save_state", fail_save)
        with pytest.raises(OSError, match="state write failed"):
            await account.close_position("DOGE/USDT", 100.2 if side == "LONG" else 99.8, reason)
        create.assert_not_awaited()
        assert "DOGE/USDT" in account.positions

    asyncio.run(run())
