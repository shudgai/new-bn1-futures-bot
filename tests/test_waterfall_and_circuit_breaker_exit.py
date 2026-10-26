"""Unit tests verifying waterfall, circuit breaker exits, and ma3_slope definition."""

import math
import pandas as pd
import pytest

from core.gates.holding_protection_gate import HoldingProtectionExitGate
from core.services.math_execution_gate import validate_exit_math, is_emergency_exit
from core.services.exits.authorized_position_exit import automated_position_exit_allowed


def test_circuit_breaker_is_emergency_exit():
    assert is_emergency_exit("CIRCUIT_BREAKER_HARD_SL") is True
    assert is_emergency_exit("EXIT_BY_CIRCUIT_BREAKER_HARD_SL") is True
    assert is_emergency_exit("EXIT_BY_EXTREME_WATERFALL") is True


def test_circuit_breaker_intrabar_holding_protection_pass():
    """Circuit breaker must not be blocked by wait_for_close in intrabar."""
    pos = {
        "symbol": "1000LOBSTER/USDT",
        "side": "LONG",
        "entry_price": 1.0,
        "amount": 100,
        "leverage": 10,
    }
    # Unclosed candle
    df = pd.DataFrame([
        {"open": 1.0, "high": 1.02, "low": 0.90, "close": 0.91, "atr": 0.03, "kc_middle": 1.0, "is_closed": False},
        {"open": 0.91, "high": 0.92, "low": 0.85, "close": 0.86, "atr": 0.03, "kc_middle": 0.98, "is_closed": False},
    ])
    # Severe loss -14% (> 3 ATR = 0.09)
    quote = 0.86
    ok, auth_code, details = HoldingProtectionExitGate.validate_exit(
        pos, df, quote, "CIRCUIT_BREAKER_HARD_SL",
    )
    assert ok is True
    assert "CIRCUIT_BREAKER" in auth_code


def test_extreme_waterfall_closed_candle_authorized():
    """A closed extreme waterfall candle (> 1.5 ATR crossing kc_middle) must be authorized."""
    pos = {
        "symbol": "1000LOBSTER/USDT",
        "side": "LONG",
        "entry_price": 1.0,
        "amount": 100,
        "leverage": 10,
    }
    # Previous bar dropped from 1.05 to 0.95 (body 0.10 >= 1.5 * 0.03 = 0.045), crossing mid 1.0
    df = pd.DataFrame([
        {"open": 1.05, "high": 1.06, "low": 0.94, "close": 0.95, "atr": 0.03, "kc_middle": 1.0, "is_closed": True},
        {"open": 0.95, "high": 0.96, "low": 0.94, "close": 0.95, "atr": 0.03, "kc_middle": 0.99, "is_closed": False},
    ])
    quote = 0.95
    ok, auth_code, details = HoldingProtectionExitGate.validate_exit(
        pos, df, quote, "EXIT_BY_EXTREME_WATERFALL",
    )
    assert ok is True
    assert auth_code == "EXIT_BY_EXTREME_WATERFALL"

    # Also math gate should authorize it
    allowed, code, math_details = validate_exit_math(
        pos, df, quote, close_reason="EXIT_BY_EXTREME_WATERFALL",
    )
    assert allowed is True
    assert "EMERGENCY_EXIT" in code


def test_execution_manager_ma3_slope_defined():
    """Verify ExecutionManager.process_symbol_pipeline executes without NameError."""
    import asyncio
    from unittest.mock import MagicMock, AsyncMock
    from core.services.execution_manager import ExecutionManager

    bars = []
    for i in range(35):
        price = 1.0 + i * 0.01
        bars.append({
            "open": price - 0.005,
            "high": price + 0.01,
            "low": price - 0.01,
            "close": price,
            "volume": 100 + i * 10,
            "timestamp": 1000 + i * 60000,
            "is_closed": True,
        })
    df = pd.DataFrame(bars)

    account = MagicMock()
    account.positions = {}
    account.position_meta = {}
    account.close_position = AsyncMock(return_value=True)

    res = asyncio.run(ExecutionManager.process_symbol_pipeline(
        symbol="1000LOBSTER/USDT",
        engine_or_account=account,
        klines_df=df,
        quote=df.iloc[-1]["close"],
    ))
    # Should not raise NameError and should return a dict
    assert isinstance(res, dict)
    assert "status" in res
