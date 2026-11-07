import time
import pandas as pd
import pytest

from core.gates.ultimate_short_exit_gatekeeper import UltimateShortExitGatekeeper
from core.services.math_execution_gate import validate_exit_math
from core.gates.unified_pipeline_gates import ExitGate
from core.gates.holding_protection_gate import HoldingProtectionExitGate


def test_ultimate_short_exit_gatekeeper_trend_reversal_long():
    """Verify holding LONG exits on confirmed KC downward + MA5 < MA15 dead cross below mid."""
    now = time.time()
    t1 = (int(now // 60) - 2) * 60_000
    t2 = (int(now // 60) - 1) * 60_000

    rows = [
        {
            "timestamp": t1, "open": 0.01760, "high": 0.01765, "low": 0.01750,
            "close": 0.01752, "ma5": 0.01755, "ma15": 0.01750, "atr": 0.00010,
            "kc_upper": 0.01770, "kc_middle": 0.01755, "kc_lower": 0.01740,
            "is_closed": True,
        },
        {
            "timestamp": t2, "open": 0.01750, "high": 0.01750, "low": 0.01735,
            "close": 0.01738, "ma5": 0.01742, "ma15": 0.01748, "atr": 0.00010,
            "kc_upper": 0.01765, "kc_middle": 0.01750, "kc_lower": 0.01735,
            "is_closed": True,
        },
    ]
    df = pd.DataFrame(rows)
    df.attrs["timeframe_ms"] = 60_000
    df.attrs["snapshot_ms"] = t2 + 5000
    df.attrs["macro_trend"] = "BEARISH"

    position = {
        "symbol": "龙虾/USDT",
        "side": "LONG",
        "entry_price": 0.01755,
        "qty": 1000.0,
        "open_timestamp": (now - 300),
    }

    verdict = UltimateShortExitGatekeeper.evaluate(
        "龙虾/USDT", position, {}, "STRUCTURE_EXIT_PROBE", 0.01738, frame=df, now=now,
    )
    assert verdict.allowed is True
    assert verdict.code == "EXIT_LONG_TREND_REVERSAL"


def test_ultimate_short_exit_gatekeeper_trend_reversal_short():
    """Verify holding SHORT exits on confirmed KC upward + MA5 > MA15 golden cross above mid."""
    now = time.time()
    t1 = (int(now // 60) - 2) * 60_000
    t2 = (int(now // 60) - 1) * 60_000

    rows = [
        {
            "timestamp": t1, "open": 0.01740, "high": 0.01745, "low": 0.01735,
            "close": 0.01742, "ma5": 0.01740, "ma15": 0.01745, "atr": 0.00010,
            "kc_upper": 0.01760, "kc_middle": 0.01745, "kc_lower": 0.01730,
            "is_closed": True,
        },
        {
            "timestamp": t2, "open": 0.01745, "high": 0.01765, "low": 0.01745,
            "close": 0.01760, "ma5": 0.01755, "ma15": 0.01748, "atr": 0.00010,
            "kc_upper": 0.01765, "kc_middle": 0.01750, "kc_lower": 0.01735,
            "is_closed": True,
        },
    ]
    df = pd.DataFrame(rows)
    df.attrs["timeframe_ms"] = 60_000
    df.attrs["snapshot_ms"] = t2 + 5000
    df.attrs["macro_trend"] = "BULLISH"

    position = {
        "symbol": "1000PEPE/USDT",
        "side": "SHORT",
        "entry_price": 0.01745,
        "qty": 1000.0,
        "open_timestamp": (now - 300),
    }

    verdict = UltimateShortExitGatekeeper.evaluate(
        "1000PEPE/USDT", position, {}, "STRUCTURE_EXIT_PROBE", 0.01760, frame=df, now=now,
    )
    assert verdict.allowed is True
    assert verdict.code == "EXIT_SHORT_TREND_REVERSAL"


def test_validate_exit_math_trend_reversal():
    """Verify validate_exit_math authorizes trend reversal exits."""
    rows = [
        {"timestamp": 1000, "open": 100, "high": 101, "low": 99, "close": 99, "ma5": 99.5, "ma15": 100, "atr": 1.0, "kc_upper": 102, "kc_middle": 100, "kc_lower": 98},
        {"timestamp": 2000, "open": 99, "high": 99.5, "low": 97, "close": 97.5, "ma5": 98, "ma15": 99.5, "atr": 1.0, "kc_upper": 101, "kc_middle": 99, "kc_lower": 97},
    ]
    df = pd.DataFrame(rows)
    pos = {"side": "LONG", "entry_price": 100.0, "open_timestamp": 0}

    allowed, code, details = validate_exit_math(
        pos, df, 97.5, close_reason="EXIT_LONG_TREND_REVERSAL",
    )
    assert allowed is True
    assert "TREND_REVERSAL" in code


def test_exit_gate_trend_reversal_safety_reason():
    """Verify EXIT_LONG_TREND_REVERSAL and EXIT_SHORT_TREND_REVERSAL are in ABSOLUTE_SAFETY_REASONS."""
    assert "EXIT_LONG_TREND_REVERSAL" in ExitGate.ABSOLUTE_SAFETY_REASONS
    assert "EXIT_SHORT_TREND_REVERSAL" in ExitGate.ABSOLUTE_SAFETY_REASONS
    assert "EXIT_LONG_TREND_REVERSAL" in HoldingProtectionExitGate.WHITELIST_CODES
    assert "EXIT_SHORT_TREND_REVERSAL" in HoldingProtectionExitGate.WHITELIST_CODES
