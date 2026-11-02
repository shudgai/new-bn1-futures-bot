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


@pytest.mark.parametrize(
    ("side", "price", "expected"),
    [("LONG", 98.0, True), ("SHORT", 102.0, True),
     ("LONG", 98.01, False), ("SHORT", 101.99, False)],
)
def test_hard_stop_math_accepts_two_percent_loss_boundary(side, price, expected):
    position = {"side": side, "entry_price": 100.0, "qty": 1.0}

    allowed, _code, _details = validate_exit_math(
        position, None, price, close_reason="CIRCUIT_BREAKER_HARD_SL",
    )

    assert allowed is expected


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


def test_sub_1_5_atr_short_bounce_below_kc_midline_is_not_waterfall_exit():
    from core.gates.unified_pipeline_gates import ExitGate

    history = [
        {
            "open": 0.0700 - i * 0.0002,
            "high": 0.0701 - i * 0.0002,
            "low": 0.0697 - i * 0.0002,
            "close": 0.0698 - i * 0.0002,
        }
        for i in range(25)
    ]
    bars = pd.DataFrame(history + [
        {"open": 0.0650, "high": 0.0668, "low": 0.0649, "close": 0.0665},
        {"open": 0.0665, "high": 0.0680, "low": 0.0663, "close": 0.0678},
    ])
    position = {
        "side": "SHORT", "entry_price": 0.0700, "qty": 1000.0,
        "peak_roe": 0.05,
    }
    market = {
        "current_price": 0.0678,
        "atr": 0.0005,
        "kc_upper": 0.0690,
        "kc_middle": 0.0685,
        "kc_lower": 0.0655,
        "kc_middle_slope": -0.0003,
        "ema_slow": 0.0688,
        "raw_df": bars,
    }

    should_exit, reason, _details = ExitGate.evaluate(
        position, market, current_price=0.0678,
    )

    assert not should_exit, f"ordinary sub-1.5 ATR bounce must hold, got {reason}"


def test_1_5_atr_reversal_through_midline_remains_emergency_exit():
    from core.gates.unified_pipeline_gates import ExitGate

    bars = pd.DataFrame([
        {"open": 101.0, "high": 101.2, "low": 100.8, "close": 101.0,
         "kc_middle": 100.0, "atr": 1.0},
        {"open": 101.0, "high": 101.1, "low": 98.8, "close": 99.0,
         "kc_middle": 100.0, "atr": 1.0},
    ])
    position = {"side": "LONG", "entry_price": 100.0, "qty": 1.0}
    market = {
        "current_price": 99.0, "atr": 1.0, "kc_upper": 102.0,
        "kc_middle": 100.0, "kc_lower": 98.0,
        "kc_middle_slope": -0.1, "raw_df": bars,
    }

    should_exit, reason, _details = ExitGate.evaluate(
        position, market, current_price=99.0,
    )

    assert should_exit
    assert reason == "EXIT_BY_EXTREME_WATERFALL"


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
