"""Reproducible early-session decision benchmark from the local 2026-10-11 API snapshot.

The intrabar case is a tick-state simulation over a real candle's OHLC envelope,
not a historical tick replay. The long-wick doji is an explicit synthetic stress case.
"""

import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pandas as pd

from core.engine import TradingEngine
from core.exits.peak_valley_exit import PeakValleyExit
from core.gates.holding_protection_gate import HoldingProtectionExitGate
from core.gates.pipeline import pipeline


def _bar(timestamp, open_, high, low, close, atr, ma5, ma15,
         kc_middle, kc_upper, kc_lower, *, is_closed=True):
    return {
        "timestamp": timestamp,
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "atr": atr,
        "ma5": ma5,
        "ma15": ma15,
        "kc_middle": kc_middle,
        "kc_upper": kc_upper,
        "kc_lower": kc_lower,
        "is_closed": is_closed,
    }


def _cap_0822_to_0824():
    # Local /api/klines snapshot, CAP/USDT, 2026-10-11 08:22-08:24 UTC+8.
    return pd.DataFrame([
        _bar(1791678120000, 0.09176, 0.09177, 0.09129, 0.09150,
             0.000547, 0.091756, 0.09232533333333333,
             0.09225359783557135, 0.09280059783557136, 0.09170659783557135),
        _bar(1791678180000, 0.09150, 0.09198, 0.09149, 0.09181,
             0.000545, 0.091774, 0.09224466666666667,
             0.09221135042265981, 0.09275635042265981, 0.09166635042265980),
        _bar(1791678240000, 0.09178, 0.09216, 0.09146, 0.09159,
             0.000564, 0.091682, 0.092134,
             0.09215217419193031, 0.09271617419193032, 0.09158817419193030),
    ])


def _lobster_0915_to_0916(*, live=False, synthetic_long_wick=False):
    # Local /api/klines snapshot, LOBSTER/USDT, 2026-10-11 09:15-09:16 UTC+8.
    bars = [
        _bar(1791681300000, 0.04551, 0.04587, 0.04538, 0.04548,
             0.000226, 0.045526, 0.045772,
             0.04589216132800033, 0.04611816132800033, 0.04566616132800033),
        _bar(1791681360000, 0.04549, 0.04553, 0.04528, 0.04544,
             0.000236, 0.045496, 0.04572466666666666,
             0.04584909834438125, 0.04608509834438125, 0.04561309834438125,
             is_closed=not live),
    ]
    if live:
        # Plausible green intrabar quote based on the completed candle's OHLC;
        # the actual historical tick sequence is unavailable.
        bars[-1]["close"] = 0.04552
    if synthetic_long_wick:
        # Stress fixture on the same price scale; not historical market data.
        bars[-1].update(high=0.04560, low=0.04540, close=0.045485)
    return pd.DataFrame(bars)


def test_benchmark_1_cap_long_upper_wick_keeps_short_open():
    frame = _cap_0822_to_0824()
    position = {
        "side": "SHORT",
        "entry_price": 0.09300,
        "lowest_price": 0.09129,
        "symbol": "CAP/USDT",
    }
    current = frame.iloc[-1]
    upper_wick = current.high - max(current.open, current.close)

    peak_exit, _ = PeakValleyExit.evaluate(position, frame, float(current.close))
    gate_exit, _ = HoldingProtectionExitGate.evaluate(
        position, frame, float(current.close),
    )

    print(
        "BENCH 1 CAP 08:24 "
        f"upper_wick={upper_wick:.8f} atr={current.atr:.8f} "
        f"PeakValleyExit={peak_exit} HoldingProtectionExitGate={gate_exit}"
    )
    assert upper_wick >= 0.4 * current.atr
    assert peak_exit is None, (
        f"08:24 CAP: upper_wick={upper_wick:.8f}, ATR={current.atr:.8f}, "
        f"PeakValleyExit={peak_exit}"
    )
    assert gate_exit is None, (
        f"08:24 CAP: upper_wick={upper_wick:.8f}, ATR={current.atr:.8f}, "
        f"HoldingProtectionExitGate={gate_exit}"
    )


def test_benchmark_2_intrabar_small_green_bounce_waits_for_close():
    frame = _lobster_0915_to_0916(live=True)
    position = {
        "side": "SHORT",
        "entry_price": 0.04630,
        "lowest_price": 0.04528,
        "symbol": "LOBSTER/USDT",
    }
    quote = 0.04552
    previous = frame.iloc[-2]
    current = frame.iloc[-1]
    bounce = quote - current.low
    exit_reason, _ = HoldingProtectionExitGate.evaluate(position, frame, quote)
    peak_reason, _ = PeakValleyExit.evaluate(position, frame, quote)
    allowed, reject_reason, _ = HoldingProtectionExitGate.validate_exit(
        position, frame, quote, "EXIT_SHORT_ON_FRACTAL_VALLEY",
        details={"atr": float(previous.atr)},
    )

    print(
        "BENCH 2 LOBSTER 09:16 simulated-live "
        f"bounce={bounce:.8f} atr={previous.atr:.8f} "
        f"exit={exit_reason or peak_reason} allowed={allowed} reject={reject_reason}"
    )
    assert current.open < quote < current.open + 1.2 * previous.atr
    assert bounce < 1.2 * previous.atr
    assert exit_reason is None and peak_reason is None, (
        f"09:16 simulated live tick: bounce={bounce:.8f}, "
        f"ATR={previous.atr:.8f}, exit={exit_reason or peak_reason}"
    )
    assert allowed is False
    assert reject_reason == HoldingProtectionExitGate.WAIT_CLOSE_REJECT_REASON


def test_benchmark_2_real_0916_red_close_does_not_exit_short():
    frame = _lobster_0915_to_0916()
    position = {
        "side": "SHORT",
        "entry_price": 0.04630,
        "lowest_price": 0.04528,
        "symbol": "LOBSTER/USDT",
    }
    current = frame.iloc[-1]

    peak_reason, _ = PeakValleyExit.evaluate(position, frame, float(current.close))
    gate_reason, _ = HoldingProtectionExitGate.evaluate(
        position, frame, float(current.close),
    )

    print(
        "BENCH 2 LOBSTER 09:16 actual-close "
        f"open={current.open:.8f} close={current.close:.8f} "
        f"upper_wick={current.high - max(current.open, current.close):.8f} "
        f"exit={peak_reason or gate_reason}"
    )
    assert current.close < current.open
    assert peak_reason is None, f"09:16 closed bearish candle exited: {peak_reason}"
    assert gate_reason is None, f"09:16 closed bearish candle exited: {gate_reason}"


def test_benchmark_2_synthetic_closed_long_wick_doji_keeps_short_open():
    frame = _lobster_0915_to_0916(synthetic_long_wick=True)
    position = {
        "side": "SHORT",
        "entry_price": 0.04630,
        "lowest_price": 0.04528,
        "symbol": "LOBSTER/USDT",
    }
    current = frame.iloc[-1]
    upper_wick = current.high - max(current.open, current.close)
    body = abs(current.close - current.open)

    peak_reason, _ = PeakValleyExit.evaluate(position, frame, float(current.close))
    gate_reason, _ = HoldingProtectionExitGate.evaluate(
        position, frame, float(current.close),
    )

    print(
        "BENCH 2 synthetic long-wick doji "
        f"upper_wick={upper_wick:.8f} body={body:.8f} "
        f"PeakValleyExit={peak_reason} HoldingProtectionExitGate={gate_reason}"
    )
    assert current.close < current.open
    assert upper_wick >= 0.4 * frame.iloc[-2].atr
    assert upper_wick > 2 * body
    assert peak_reason is None, f"Synthetic long-wick doji exited: {peak_reason}"
    assert gate_reason is None, f"Synthetic long-wick doji exited: {gate_reason}"


def test_benchmark_3_inside_channel_trend_continuation_authorizes_short():
    # Real LOBSTER/USDT 1m candles from the 08:23-08:24 UTC+8 API snapshot.
    frame = pd.DataFrame([
        _bar(1791678180000, 0.04658, 0.04667, 0.04656, 0.04660,
             0.000163, 0.046602, 0.046700,
             0.04661014633338263, 0.04677314633338264, 0.04644714633338263),
        _bar(1791678240000, 0.04659, 0.04665, 0.04643, 0.04648,
             0.000171, 0.046566, 0.04668133333333333,
             0.04659775144448905, 0.04676875144448905, 0.04642675144448905),
    ])
    latest = frame.iloc[-1]
    diagnostics = {}

    decision = pipeline.authorize(
        None, frame, float(latest.close),
        symbol="LOBSTER/USDT", requested_side="SHORT",
        diagnostics=diagnostics,
    )

    print(
        "BENCH 3 LOBSTER 08:24 "
        f"KC=[{latest.kc_lower:.8f},{latest.kc_upper:.8f}] "
        f"MA5={latest.ma5:.8f} MA15={latest.ma15:.8f} "
        f"decision={decision and decision['type']} diagnostics={diagnostics}"
    )
    assert latest.kc_lower < latest.low < latest.high < latest.kc_upper
    assert latest.close < latest.ma5
    assert latest.ma5 < latest.ma15 < frame.iloc[-2].ma15
    assert decision is not None, f"08:24 continuation was blocked: {diagnostics}"
    assert decision["type"] == "AUTHORIZED_BY_TREND_CONTINUATION_SHORT"
    assert decision["side"] == "SHORT"
    assert decision["_is_authorized"] is True


def test_benchmark_4_two_second_pipeline_authorization_reaches_account_submit(monkeypatch):
    symbol = "LOBSTER/USDT"
    stamp = int(time.time() // 60) * 60_000
    frame = pd.DataFrame([
        {
            "timestamp": stamp - (3 - index) * 60_000,
            "open": 100.0,
            "high": 100.4,
            "low": 99.6,
            "close": 100.0,
            "atr": 1.0,
            "kc_middle": 100.0,
            "kc_upper": 102.0,
            "kc_lower": 98.0,
            "ma5": 100.0,
            "ma15": 100.0,
            "is_closed": True,
        }
        for index in range(3)
    ] + [{
        "timestamp": stamp,
        "open": 100.0,
        "high": 100.0,
        "low": 96.5,
        "close": 96.5,
        "atr": 1.0,
        "kc_middle": 100.0,
        "kc_upper": 102.0,
        "kc_lower": 98.0,
        "ma5": 100.0,
        "ma15": 100.0,
        "is_closed": False,
    }])
    frame.attrs["timeframe_ms"] = 60_000
    frame.attrs["entry_finality_verified"] = True
    authorized = pipeline.authorize(
        None, frame, 96.5, symbol=symbol, requested_side="SHORT",
    )
    assert authorized is not None

    account = SimpleNamespace(
        positions={},
        pending_limit_orders={},
        trades=[],
        logs=[],
        log=Mock(),
        daily_loss_limit_hit=lambda: (False, 0.0),
        get_wallet_balance=lambda: 1000.0,
        get_available_balance=lambda: 1000.0,
        open_position=AsyncMock(),
        breakout_qualification={},
        record_qualification=Mock(),
        consume_breakout_qualification=Mock(),
    )
    engine = object.__new__(TradingEngine)
    engine.account = account
    engine.tickers = {symbol: 96.5}
    engine._channel_entry_quote_times = {symbol: time.time()}
    engine.symbol_rotation = SimpleNamespace(get_dynamic_leverage=lambda *_args: 2)
    engine._execution_price_is_safe = AsyncMock(return_value=True)
    engine._entry_boundary_frame = AsyncMock(return_value=frame)
    calls = 0
    monotonic_now = [100.0]

    def transient_revalidation_failure(*_args, diagnostics=None, **_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            return dict(authorized)
        monotonic_now[0] = 102.0
        diagnostics["reason"] = "WAIT_PIPELINE_TRIGGER"
        return None

    monkeypatch.setattr("core.engine.DEFAULT_SYMBOLS", [symbol])
    monkeypatch.setattr("core.config.is_entry_disabled", lambda _symbol: False)
    monkeypatch.setattr(
        "core.services.entry_contract.evaluate_entry_contract",
        transient_revalidation_failure,
    )
    monkeypatch.setattr(
        "core.services.entry_contract.entry_direction_problem",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "core.services.pre_entry_space_shadow.record_pre_entry_space_shadow",
        lambda **_kwargs: None,
    )
    monkeypatch.setattr(
        "core.engine.time.monotonic", lambda: monotonic_now[0],
    )
    from core.services.entry_firewall import validate_account_entry

    async def open_with_firewall(**kwargs):
        await validate_account_entry(
            account, symbol, kwargs["side"], kwargs["entry_context"],
        )
        return True

    account.open_position.side_effect = open_with_firewall

    submitted = asyncio.run(engine._execute_confirmed_channel_break(
        symbol, frame, 96.5, "SHORT",
        v8_reason=authorized["type"],
        candidate_bar_id=authorized["confirmation_bar_id"],
    ))

    logged = "\n".join(str(call.args[0]) for call in account.log.call_args_list)
    print(
        "BENCH 4 revalidation "
        f"decision={authorized['type']} submitted={submitted} logs={logged}"
    )
    assert submitted is True, f"2s authorization revalidation failed; logs={logged}"
    assert calls == 2
    account.open_position.assert_awaited_once()
    assert "reason=PIPELINE_AUTH_TTL_GRACE" in logged
    assert "reason=ACCOUNT_SUBMIT" in logged
    assert "elapsed_seconds=2.0" in logged
