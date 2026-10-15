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




