import time
import pandas as pd
import pytest

from core.gates.ultimate_short_exit_gatekeeper import UltimateShortExitGatekeeper
from core.gates.master_gate_funnel import MasterGateFunnel, MarketSnapshot


def test_long_position_in_bullish_trend_vetoes_exit():
    """Verify that if macro trend is bullish or KC middle is upward, premature long exits are vetoed."""
    now = time.time()
    t1 = (int(now // 60) - 2) * 60_000
    t2 = (int(now // 60) - 1) * 60_000

    rows = [
        {
            "timestamp": t1, "open": 0.01750, "high": 0.01755, "low": 0.01748,
            "close": 0.01752, "ma5": 0.01750, "ma15": 0.01745, "atr": 0.00010,
            "kc_upper": 0.01770, "kc_middle": 0.01750, "kc_lower": 0.01730,
            "is_closed": True,
        },
        {
            "timestamp": t2, "open": 0.01752, "high": 0.01754, "low": 0.01744,
            "close": 0.01746, "ma5": 0.01749, "ma15": 0.01746, "atr": 0.00010,
            "kc_upper": 0.01775, "kc_middle": 0.01753, "kc_lower": 0.01731,
            "is_closed": True,
        },
    ]
    df = pd.DataFrame(rows)
    df.attrs["timeframe_ms"] = 60_000
    df.attrs["snapshot_ms"] = t2 + 5000
    df.attrs["macro_trend"] = "BULLISH"

    position = {
        "symbol": "龙虾/USDT",
        "side": "LONG",
        "entry_price": 0.01750,
        "qty": 1000.0,
        "open_timestamp": (now - 300),
    }

    verdict = UltimateShortExitGatekeeper.evaluate(
        "龙虾/USDT", position, {}, "EXIT_BY_PULLBACK", 0.01746, frame=df, now=now,
    )
    assert verdict.allowed is False
    assert "VETO_HOLD" in verdict.code


def test_short_position_in_bearish_trend_vetoes_exit():
    """Verify that if macro trend is bearish or KC middle is downward, premature short exits are vetoed."""
    now = time.time()
    t1 = (int(now // 60) - 2) * 60_000
    t2 = (int(now // 60) - 1) * 60_000

    rows = [
        {
            "timestamp": t1, "open": 0.01750, "high": 0.01755, "low": 0.01745,
            "close": 0.01748, "ma5": 0.01750, "ma15": 0.01755, "atr": 0.00010,
            "kc_upper": 0.01770, "kc_middle": 0.01750, "kc_lower": 0.01730,
            "is_closed": True,
        },
        {
            "timestamp": t2, "open": 0.01748, "high": 0.01754, "low": 0.01746,
            "close": 0.01752, "ma5": 0.01751, "ma15": 0.01754, "atr": 0.00010,
            "kc_upper": 0.01765, "kc_middle": 0.01747, "kc_lower": 0.01729,
            "is_closed": True,
        },
    ]
    df = pd.DataFrame(rows)
    df.attrs["timeframe_ms"] = 60_000
    df.attrs["snapshot_ms"] = t2 + 5000
    df.attrs["macro_trend"] = "BEARISH"

    position = {
        "symbol": "1000PEPE/USDT",
        "side": "SHORT",
        "entry_price": 0.01750,
        "qty": 1000.0,
        "open_timestamp": (now - 300),
    }

    verdict = UltimateShortExitGatekeeper.evaluate(
        "1000PEPE/USDT", position, {}, "EXIT_BY_PULLBACK", 0.01752, frame=df, now=now,
    )
    assert verdict.allowed is False
    assert "VETO_HOLD" in verdict.code


def test_waterfall_emergency_exit_vetoed_in_bullish_trend():
    """Verify that unclosed live drop is vetoed when macro trend is bullish."""
    now = time.time()
    t1 = (int(now // 60) - 1) * 60_000
    t2 = int(now // 60) * 60_000

    rows = [
        {"timestamp": t1, "open": 0.01750, "high": 0.01755, "low": 0.01748, "close": 0.01752, "atr": 0.00010, "kc_middle": 0.01750, "ma15": 0.01745, "is_closed": True},
        {"timestamp": t2, "open": 0.01752, "high": 0.01753, "low": 0.01730, "close": 0.01732, "atr": 0.00010, "kc_middle": 0.01752, "ma15": 0.01746, "is_closed": False},
    ]
    df = pd.DataFrame(rows)
    df.attrs["macro_trend"] = "BULLISH"

    position = {"symbol": "龙虾/USDT", "side": "LONG", "entry_price": 0.01750}
    hit, reason, evidence = UltimateShortExitGatekeeper.check_waterfall_emergency_exit(
        "龙虾/USDT", position, {}, 0.01732, frame=df,
    )
    assert hit is False
    assert reason == "VETO_HOLD_WATERFALL_IN_BULLISH_TREND"


def test_surge_emergency_exit_vetoed_in_bearish_trend():
    """Verify that unclosed live pump is vetoed when macro trend is bearish."""
    now = time.time()
    t1 = (int(now // 60) - 1) * 60_000
    t2 = int(now // 60) * 60_000

    rows = [
        {"timestamp": t1, "open": 0.01750, "high": 0.01755, "low": 0.01745, "close": 0.01748, "atr": 0.00010, "kc_middle": 0.01750, "ma15": 0.01755, "is_closed": True},
        {"timestamp": t2, "open": 0.01748, "high": 0.01770, "low": 0.01748, "close": 0.01768, "atr": 0.00010, "kc_middle": 0.01748, "ma15": 0.01754, "is_closed": False},
    ]
    df = pd.DataFrame(rows)
    df.attrs["macro_trend"] = "BEARISH"

    position = {"symbol": "1000PEPE/USDT", "side": "SHORT", "entry_price": 0.01750}
    hit, reason, evidence = UltimateShortExitGatekeeper.check_waterfall_emergency_exit(
        "1000PEPE/USDT", position, {}, 0.01768, frame=df,
    )
    assert hit is False
    assert reason == "VETO_HOLD_SURGE_IN_BEARISH_TREND"
