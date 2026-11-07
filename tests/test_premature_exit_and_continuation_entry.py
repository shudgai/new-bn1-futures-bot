import time
import pandas as pd
import pytest
from core.gates.ultimate_short_exit_gatekeeper import UltimateShortExitGatekeeper
from core.gates.master_gate_funnel import MasterGateFunnel, MarketSnapshot


def test_bearish_trend_prevents_premature_bottom_green_candle_exit():
    """Verify that a short in a bearish trend below KC middle is NOT exited on a simple 1m green bar."""
    now = time.time()
    t1 = (int(now // 60) - 2) * 60_000
    t2 = (int(now // 60) - 1) * 60_000

    rows = [
        {
            "timestamp": t1, "open": 0.0095, "high": 0.0096, "low": 0.0089,
            "close": 0.0090, "ma5": 0.0092, "ma15": 0.0095, "atr": 0.0003,
            "kc_upper": 0.0105, "kc_middle": 0.0098, "kc_lower": 0.0091,
            "is_closed": True,
        },
        {
            # 1m green bar that crosses MA5 up, but close is still well below KC middle (0.0092 < 0.0097)
            # and KC middle is clearly downward (0.0097 < 0.0098)
            "timestamp": t2, "open": 0.0090, "high": 0.0093, "low": 0.0089,
            "close": 0.0092, "ma5": 0.0091, "ma15": 0.0094, "atr": 0.0003,
            "kc_upper": 0.0104, "kc_middle": 0.0097, "kc_lower": 0.0090,
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
        "entry_price": 0.0095,
        "qty": 1000.0,
        "open_timestamp": (now - 300),
    }
    metadata = {"trough_price": 0.0089}

    verdict = UltimateShortExitGatekeeper.evaluate(
        "1000PEPE/USDT",
        position,
        metadata,
        "STRUCTURE_EXIT_PROBE",
        0.0092,
        frame=df,
        now=now,
    )
    # Must NOT allow premature exit in bearish trend
    assert not verdict.allowed, f"Expected HOLD but got allowed: {verdict.code}"


def test_bullish_trend_prevents_premature_top_red_candle_exit():
    """Verify that a long in a bullish trend above KC middle is NOT exited on a simple 1m red bar."""
    now = time.time()
    t1 = (int(now // 60) - 2) * 60_000
    t2 = (int(now // 60) - 1) * 60_000

    rows = [
        {
            "timestamp": t1, "open": 0.0173, "high": 0.0177, "low": 0.0172,
            "close": 0.0176, "ma5": 0.0175, "ma15": 0.0172, "atr": 0.0002,
            "kc_upper": 0.0177, "kc_middle": 0.0172, "kc_lower": 0.0167,
            "is_closed": True,
        },
        {
            # 1m red bar that dips below MA5, but close is still well above KC middle (0.0174 > 0.0173)
            # and KC middle is clearly upward (0.0173 > 0.0172)
            "timestamp": t2, "open": 0.0176, "high": 0.0176, "low": 0.0173,
            "close": 0.0174, "ma5": 0.0175, "ma15": 0.0173, "atr": 0.0002,
            "kc_upper": 0.0178, "kc_middle": 0.0173, "kc_lower": 0.0168,
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
        "entry_price": 0.0172,
        "qty": 1000.0,
        "open_timestamp": (now - 300),
    }
    metadata = {"peak_price": 0.0177}

    verdict = UltimateShortExitGatekeeper.evaluate(
        "龙虾/USDT",
        position,
        metadata,
        "STRUCTURE_EXIT_PROBE",
        0.0174,
        frame=df,
        now=now,
    )
    # Must NOT allow premature exit in bullish trend
    assert not verdict.allowed, f"Expected HOLD but got allowed: {verdict.code}"


def test_continuation_entry_bypasses_cooldown():
    """Verify that continuation entry along the trend outside KC bypasses post-exit cooldown."""
    m_short = MarketSnapshot(
        symbol="1000PEPE/USDT",
        quote=0.0090,
        open_price=0.0093,
        close_price=0.0090,
        high_price=0.0094,
        low_price=0.0089,
        ma5_curr=0.0091,
        ma5_prev=0.0093,
        ma15_curr=0.0094,
        ma15_prev=0.0095,
        kc_upper=0.0105,
        kc_middle=0.0098,
        kc_middle_prev=0.0100,
        kc_lower=0.0091,
        atr=0.0003,
        timeframe_ms=60000,
        is_closed=True,
        ma_cross_count_8=0,
        kc_width_max_20=0.0020,
        macro_trend="BEARISH",
        ma3_curr=0.0090,
        ma3_prev=0.0092,
    )

    decision = MasterGateFunnel.evaluate_open_short(m_short, is_in_cooldown=True)
    assert decision.can_execute, f"Expected authorization during continuation, but blocked by: {decision.reason}"
    assert decision.action == "OPEN_SHORT" or "AUTHORIZED_SHORT" in decision.reason
