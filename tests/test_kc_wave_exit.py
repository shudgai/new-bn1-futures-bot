import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pandas as pd
import pytest

from core.services.exits.authorized_position_exit import automated_position_exit_allowed
from core.services.exits.trend_lock import KC_WAVE_EXIT_REASON, confirmed_kc_wave_exit
from core.services.math_execution_gate import validate_exit_at_execution


def wave_frame(side, *, reversed_direction=True, live=False):
    sign = 1 if side == "LONG" else -1
    start = int(time.time() // 60) * 60_000 - 30 * 60_000
    rows = []
    for index in range(30):
        middle = 100 + sign * index * 0.01
        rows.append(dict(
            timestamp=start + index * 60_000,
            open=100.1, high=100.4, low=99.8, close=100.2,
            ma3=100.2, ma5=100.1, ma15=100.0,
            kc_middle=middle, kc_upper=middle + 2, kc_lower=middle - 2,
            atr=1.0, volume=100, is_closed=True,
        ))
    if reversed_direction:
        rows[-1]["kc_middle"] = rows[-2]["kc_middle"] - sign * 0.01
    result = pd.DataFrame(rows)
    if live:
        result.loc[result.index[-1], "is_closed"] = False
    result.attrs.update(
        timeframe_ms=60_000, snapshot_ms=start + 30 * 60_000,
        macro_trend="BULLISH" if side == "LONG" else "BEARISH",
    )
    return result


def position(side, frame):
    return dict(
        symbol="CAP/USDT", side=side, entry_price=100.0,
        qty=1.0, margin=20.0, leverage=5,
        open_timestamp=float(frame.iloc[0]["timestamp"]) / 1000,
        entry_mode="WILD_SWING",
    )


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_closed_kc_direction_is_evidence_but_no_longer_exit_authority(side):
    frame = wave_frame(side)
    pos = position(side, frame)
    evidence = confirmed_kc_wave_exit(pos, frame)
    assert evidence is not None
    assert evidence["is_closed"]
    assert evidence["reason"] == KC_WAVE_EXIT_REASON
    account = SimpleNamespace(
        _channel_exit_frames={"CAP/USDT": frame}, position_meta={},
    )
    assert not automated_position_exit_allowed(
        "CAP/USDT", pos, {}, KC_WAVE_EXIT_REASON, 100.2, frame=frame,
    )
    allowed, reason, details = validate_exit_at_execution(
        account, "CAP/USDT", pos, 100.2, KC_WAVE_EXIT_REASON,
    )
    assert not allowed
    assert reason == "BLOCKED_TREND_LOCK_OR_UNCONFIRMED_EMERGENCY"


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
@pytest.mark.parametrize("fault", [
    "live", "flat", "aligned", "stale", "invalid", "pre_entry", "missing_entry",
    "invalid_ohlc", "future", "gap", "missing_finality", "missing_snapshot",
])
def test_wave_exit_rejects_unconfirmed_or_invalid_reversal(side, fault):
    frame = wave_frame(side, live=fault == "live", reversed_direction=fault != "aligned")
    pos = position(side, frame)
    if fault == "flat":
        frame.loc[frame.index[-1], "kc_middle"] = frame.iloc[-2]["kc_middle"]
    elif fault == "stale":
        frame.attrs["snapshot_ms"] += 600_000
    elif fault == "invalid":
        frame.loc[frame.index[-1], "kc_middle"] = float("nan")
    elif fault == "pre_entry":
        pos["open_timestamp"] = frame.attrs["snapshot_ms"] / 1000
    elif fault == "missing_entry":
        pos.pop("open_timestamp")
    elif fault == "invalid_ohlc":
        frame.loc[frame.index[-1], "low"] = 101.0
    elif fault == "future":
        frame.attrs["snapshot_ms"] -= 1
    elif fault == "gap":
        frame.loc[frame.index[-2], "timestamp"] -= 60_000
    elif fault == "missing_finality":
        frame = frame.drop(columns=["is_closed"])
    elif fault == "missing_snapshot":
        frame.attrs.pop("snapshot_ms")
    assert confirmed_kc_wave_exit(pos, frame) is None
    assert not automated_position_exit_allowed(
        "CAP/USDT", pos, {}, KC_WAVE_EXIT_REASON, 100.2, frame=frame,
    )


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
@pytest.mark.parametrize("price", [0.0, -1.0, float("nan"), float("inf"), None])
def test_wave_exit_boundary_rejects_invalid_quote(side, price):
    frame = wave_frame(side)
    assert not automated_position_exit_allowed(
        "CAP/USDT", position(side, frame), {}, KC_WAVE_EXIT_REASON, price, frame=frame,
    )


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
@pytest.mark.parametrize("reason", [
    "EXIT_BY_PULLBACK", "EARLY_PIVOT_PEAK_EXIT", "EARLY_PIVOT_VALLEY_EXIT",
    "EXIT_BY_CONFIRMED_TRUE_PEAK_PIVOT", "EXIT_BY_CONFIRMED_TRUE_VALLEY_PIVOT",
    "EXIT_BY_TREND_BROKEN", "KC_MIDLINE_RECLAIM_CLOSE",
])
def test_ordinary_labels_and_old_pending_pivots_do_not_end_wave(side, reason):
    frame = wave_frame(side, reversed_direction=False)
    pos = position(side, frame)
    assert not automated_position_exit_allowed(
        "CAP/USDT", pos, {"pending_exit": reason}, reason, 100.2, frame=frame,
    )


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
@pytest.mark.parametrize("reversal,filled", [(True, True), (True, False), (False, True)])
def test_pipeline_closes_true_pivot_and_holds_plain_wave(
    monkeypatch, side, reversal, filled,
):
    from core.services.data_pipeline import DataPipeline
    from core.services.execution_manager import ExecutionManager
    from test_profit_pivot_exit import pivot_frame
    from core.services.exits.strict_true_pivot import profitable_true_pivot_exit

    frame = pivot_frame(side) if reversal else wave_frame(side, reversed_direction=False)
    pos = position(side, frame)
    quote = 100.2 if side == "LONG" else 99.8
    snapshot = DataPipeline.calculate_snapshot("CAP/USDT", frame, current_price=quote)
    assert snapshot is not None
    snapshot.raw_df = frame
    account = SimpleNamespace(
        positions={"CAP/USDT": pos}, position_meta={}, entry_filter_last={},
        save_state=lambda **kwargs: None,
    )
    engine = SimpleNamespace(
        account=account, tickers={"CAP/USDT": quote},
        fetch_klines=AsyncMock(return_value=frame),
    )
    monkeypatch.setattr(DataPipeline, "calculate_snapshot", staticmethod(lambda *args, **kwargs: snapshot))
    close = AsyncMock(return_value=filled)
    monkeypatch.setattr(ExecutionManager, "close_position", close)
    result = asyncio.run(ExecutionManager.process_symbol_pipeline(
        "CAP/USDT", engine, klines_df=frame, quote=quote, frame_15m=frame,
    ))
    if not reversal:
        assert result["status"] == "HOLD", result
        close.assert_not_awaited()
        return
    assert result["status"] == ("EXIT_EXECUTED" if filled else "HOLD"), result
    assert result["success"] is filled
    assert close.await_args.kwargs["reason"] == profitable_true_pivot_exit(pos, frame)[0]
    assert close.await_args.kwargs["price"] == quote


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_real_testnet_wave_close_uses_authoritative_frame_and_reduce_only(
    tmp_path, monkeypatch, side,
):
    import core.testnet_account as tm
    from test_testnet_account import FakeTestnetExchange
    from test_profit_pivot_exit import pivot_frame
    from core.services.exits.strict_true_pivot import profitable_true_pivot_exit

    monkeypatch.setattr(tm, "notify_email", lambda *args, **kwargs: None)
    exchange = FakeTestnetExchange()
    account = tm.BinanceTestnetAccount(exchange, state_file=str(tmp_path / "wave.json"))
    frame = pivot_frame(side)
    pos = position(side, frame)
    account.positions["CAP/USDT"] = pos
    account.position_meta["CAP/USDT"] = {}
    account._channel_exit_frames = {"CAP/USDT": frame}
    account.refresh = AsyncMock()
    exchange.positions = [{
        "symbol": "CAPUSDT",
        "positionAmt": "1" if side == "LONG" else "-1",
        "entryPrice": "100", "markPrice": "100.2",
    }]
    reason = profitable_true_pivot_exit(pos, frame)[0]
    assert asyncio.run(account.close_position("CAP/USDT", 100.2 if side == "LONG" else 99.8, reason))
    assert "CAP/USDT" not in account.positions
    assert len(account.trades) == 1
    assert exchange.orders
    assert all(order["params"].get("reduceOnly") for order in exchange.orders)


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_real_paper_wave_close_records_only_the_existing_position(tmp_path, monkeypatch, side):
    import core.paper_account as pm
    from test_profit_pivot_exit import pivot_frame
    from core.services.exits.strict_true_pivot import profitable_true_pivot_exit

    monkeypatch.setattr(pm, "STATE_FILE", str(tmp_path / "paper-wave.json"))
    account = pm.PaperAccount()
    frame = pivot_frame(side)
    account.positions["CAP/USDT"] = position(side, frame)
    account.position_meta["CAP/USDT"] = {}
    account._channel_exit_frames = {"CAP/USDT": frame}
    reason = profitable_true_pivot_exit(account.positions["CAP/USDT"], frame)[0]
    assert asyncio.run(account.close_position("CAP/USDT", 100.2 if side == "LONG" else 99.8, reason))
    assert "CAP/USDT" not in account.positions
    assert len(account.trades) == 1
    reloaded = pm.PaperAccount()
    assert "CAP/USDT" not in reloaded.positions
    assert len(reloaded.trades) == 1


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_failed_wave_close_retries_without_opening_another_position(monkeypatch, side):
    from core.services.data_pipeline import DataPipeline
    from core.services.execution_manager import ExecutionManager
    from test_profit_pivot_exit import pivot_frame

    frame = pivot_frame(side)
    pos = position(side, frame)
    quote = 100.2 if side == "LONG" else 99.8
    snapshot = DataPipeline.calculate_snapshot("CAP/USDT", frame, current_price=quote)
    snapshot.raw_df = frame
    account = SimpleNamespace(
        positions={"CAP/USDT": pos}, position_meta={}, entry_filter_last={},
        save_state=lambda **kwargs: None,
    )
    engine = SimpleNamespace(account=account, tickers={"CAP/USDT": quote})
    monkeypatch.setattr(DataPipeline, "calculate_snapshot", staticmethod(lambda *args, **kwargs: snapshot))
    close = AsyncMock(side_effect=[False, True])
    monkeypatch.setattr(ExecutionManager, "close_position", close)
    opened = AsyncMock()
    monkeypatch.setattr(ExecutionManager, "open_position", opened)

    async def run():
        results = []
        for _ in range(2):
            results.append(await ExecutionManager.process_symbol_pipeline(
                "CAP/USDT", engine, klines_df=frame, quote=quote, frame_15m=frame,
            ))
        return results

    failed, retried = asyncio.run(run())
    assert failed["status"] == "HOLD"
    assert retried["status"] == "EXIT_EXECUTED"
    assert close.await_count == 2
    opened.assert_not_awaited()


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
@pytest.mark.parametrize("emergency", [
    "CIRCUIT_BREAKER_HARD_SL", "EXIT_BY_TWO_CLOSED_ADVERSE_ABNORMAL",
])
def test_disabled_strategy_safety_labels_cannot_replace_true_pivot(monkeypatch, side, emergency):
    from core.gates.unified_pipeline_gates import ExitGate

    frame = wave_frame(side)
    monkeypatch.setattr(ExitGate, "_evaluate_core", staticmethod(
        lambda *args, **kwargs: (True, emergency, {"safety_evidence": True}),
    ))
    should_exit, reason, evidence = ExitGate.evaluate(
        position(side, frame), {"raw_df": frame, "current_price": 100.2},
    )
    assert not should_exit
    assert reason == "VETO_HOLD"
    assert evidence["safety_evidence"]
    assert emergency in ExitGate.ABSOLUTE_SAFETY_REASONS


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_disabled_two_closed_abnormal_does_not_restore_a_profit_lock_exit(monkeypatch, side):
    from core.gates.unified_pipeline_gates import ExitGate

    frame = wave_frame(side)
    monkeypatch.setattr(ExitGate, "_evaluate_core", staticmethod(
        lambda *args, **kwargs: (True, "EXIT_BY_RATCHET_PROFIT_LOCK", {}),
    ))
    monkeypatch.setattr(
        "core.services.exits.abnormal_candle_exit.two_closed_adverse_abnormal_evidence",
        lambda *args, **kwargs: {"safety_evidence": True},
    )
    should_exit, reason, evidence = ExitGate.evaluate(
        position(side, frame), {"raw_df": frame, "current_price": 100.2},
    )
    assert not should_exit
    assert reason == "VETO_HOLD"
    assert evidence["suppressed_exit_reason"] == "EXIT_BY_RATCHET_PROFIT_LOCK"
