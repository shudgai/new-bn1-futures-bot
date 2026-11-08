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


def test_lobster_0909_pullback_holds_and_exits_at_box2_climax_peak():
    """Verify exact 2026-11-08 龙虾/USDT candles:
    1. At 09:08 / 09:09 (Box 1 pullback), LONG opened at 0.01754175 MUST NOT exit because MA5, MA15,
       and KC middle are all rising and MA5-MA15 spread is expanding.
    2. At 09:12 / 09:13, ceiling chase inside KC upper with only 5.7% room left MUST BE BLOCKED.
    3. At 09:15 (Box 2 2nd-wave climax peak) and 09:16 (breaking MA5 & MA15), LONG MUST EXIT!
    """
    base_min = 29_900_000
    raw = [
        # min_offset, open, high, low, close, ma5, ma15, kcu, kcm, kcl, atr
        (5, 0.017590, 0.017670, 0.017580, 0.017660, 0.017586, 0.017531, 0.017608, 0.017539, 0.017469, 0.000035),
        (6, 0.017650, 0.017720, 0.017650, 0.017700, 0.017614, 0.017547, 0.017629, 0.017554, 0.017479, 0.000037),
        (7, 0.017710, 0.017730, 0.017650, 0.017650, 0.017634, 0.017559, 0.017638, 0.017563, 0.017488, 0.000038),
        (8, 0.017660, 0.017670, 0.017620, 0.017620, 0.017644, 0.017568, 0.017646, 0.017569, 0.017491, 0.000039),
        (9, 0.017630, 0.017670, 0.017610, 0.017650, 0.017656, 0.017579, 0.017656, 0.017576, 0.017497, 0.000040),
        (10, 0.017650, 0.017650, 0.017590, 0.017610, 0.017646, 0.017587, 0.017663, 0.017580, 0.017496, 0.000041),
        (11, 0.017610, 0.017620, 0.017580, 0.017610, 0.017628, 0.017595, 0.017660, 0.017582, 0.017505, 0.000039),
        (12, 0.017600, 0.017660, 0.017600, 0.017660, 0.017630, 0.017603, 0.017669, 0.017590, 0.017511, 0.000040),
        (13, 0.017650, 0.017680, 0.017640, 0.017650, 0.017636, 0.017609, 0.017675, 0.017596, 0.017516, 0.000040),
        (14, 0.017650, 0.017770, 0.017640, 0.017700, 0.017646, 0.017621, 0.017693, 0.017605, 0.017518, 0.000044),
        (15, 0.017700, 0.017740, 0.017660, 0.017680, 0.017660, 0.017631, 0.017705, 0.017613, 0.017520, 0.000046),
        (16, 0.017680, 0.017680, 0.017630, 0.017630, 0.017664, 0.017635, 0.017707, 0.017614, 0.017521, 0.000046),
    ]
    rows = [
        {
            "timestamp": (base_min + m) * 60_000,
            "open": o, "high": h, "low": l, "close": c,
            "ma5": ma5, "ma15": ma15,
            "kc_upper": kcu, "kc_middle": kcm, "kc_lower": kcl,
            "atr": atr, "is_closed": True,
        }
        for (m, o, h, l, c, ma5, ma15, kcu, kcm, kcl, atr) in raw
    ]

    position = {
        "symbol": "龙虾/USDT",
        "side": "LONG",
        "entry_price": 0.01754175,
        "qty": 1000.0,
        "open_timestamp": (base_min - 3) * 60.0,
    }

    # 1. Check Box 1 (09:08 closed bar evaluated at 09:09:00): MUST HOLD!
    df_0908 = pd.DataFrame(rows[:4])
    df_0908.attrs["timeframe_ms"] = 60_000
    df_0908.attrs["snapshot_ms"] = (base_min + 9) * 60_000 + 1000
    df_0908.attrs["macro_trend"] = "BULLISH"
    verdict_0909 = UltimateShortExitGatekeeper.evaluate(
        "龙虾/USDT",
        position,
        {"peak_price": 0.017730},
        "STRUCTURE_EXIT_PROBE",
        0.017620,
        frame=df_0908,
        now=(base_min + 9) * 60.0 + 1.0,
    )
    assert not verdict_0909.allowed, f"09:09 pullback in Box 1 must NOT exit LONG, got: {verdict_0909.code}"

    # Also verify 09:09, 09:10, 09:11 pullbacks do NOT exit
    for idx, m_off in enumerate((9, 10, 11), start=5):
        df_sub = pd.DataFrame(rows[:idx])
        df_sub.attrs["timeframe_ms"] = 60_000
        df_sub.attrs["snapshot_ms"] = (base_min + m_off + 1) * 60_000 + 1000
        df_sub.attrs["macro_trend"] = "BULLISH"
        v_sub = UltimateShortExitGatekeeper.evaluate(
            "龙虾/USDT",
            position,
            {"peak_price": 0.017730},
            "STRUCTURE_EXIT_PROBE",
            rows[idx - 1]["close"],
            frame=df_sub,
            now=(base_min + m_off + 1) * 60.0 + 1.0,
        )
        assert not v_sub.allowed, f"09:{m_off+1:02d} pullback must NOT exit LONG, got: {v_sub.code}"

    # 2. Check 09:13 entry (evaluating 09:12 closed bar at 0.017660 right under kc_upper=0.017669): MUST BLOCK!
    m_0912 = MarketSnapshot(
        symbol="龙虾/USDT",
        quote=0.017660,
        open_price=0.017600,
        close_price=0.017660,
        high_price=0.017660,
        low_price=0.017600,
        ma5_curr=0.017630,
        ma5_prev=0.017628,
        ma15_curr=0.017603,
        ma15_prev=0.017595,
        kc_upper=0.017669,
        kc_middle=0.017590,
        kc_middle_prev=0.017582,
        kc_lower=0.017511,
        atr=0.000040,
        timeframe_ms=60000,
        is_closed=True,
        ma_cross_count_8=0,
        kc_width_max_20=0.000180,
        macro_trend="BULLISH",
        ma3_curr=0.017640,
        ma3_prev=0.017620,
    )
    dec_0913 = MasterGateFunnel.evaluate_open_long(m_0912, is_in_cooldown=False)
    assert not dec_0913.can_execute, f"09:13 ceiling chase should be blocked, got: {dec_0913.reason}"
    assert "INSUFFICIENT_KC_ROOM" in dec_0913.reason

    # 3. Check Box 2 (09:15 closed bar evaluated at 09:16:00): MUST EXIT LONG at 2nd-wave climax peak!
    df_0915 = pd.DataFrame(rows[:11])
    df_0915.attrs["timeframe_ms"] = 60_000
    df_0915.attrs["snapshot_ms"] = (base_min + 16) * 60_000 + 1000
    df_0915.attrs["macro_trend"] = "BULLISH"
    verdict_0915 = UltimateShortExitGatekeeper.evaluate(
        "龙虾/USDT",
        position,
        {"peak_price": 0.017770},
        "STRUCTURE_EXIT_PROBE",
        0.017680,
        frame=df_0915,
        now=(base_min + 16) * 60.0 + 1.0,
    )
    assert verdict_0915.allowed, f"Box 2 (09:15) 2nd-wave climax peak MUST exit LONG, got: {verdict_0915.code}"
    assert verdict_0915.code == "EXIT_LONG_TRUE_PEAK_TOP"

