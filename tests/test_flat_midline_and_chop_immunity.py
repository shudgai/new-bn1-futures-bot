import math
import pandas as pd
import pytest

from core.services import mtf_kc_state_machine as mtf


def test_closed_kc_direction_detects_flat_when_slope_is_tiny():
    # Midline moves by 0.00000001 per bar -> slope is flat
    df = pd.DataFrame([
        {"timestamp": 1000, "kc_middle": 0.02000001, "atr": 0.0005, "is_closed": True},
        {"timestamp": 2000, "kc_middle": 0.02000002, "atr": 0.0005, "is_closed": True},
        {"timestamp": 3000, "kc_middle": 0.02000003, "atr": 0.0005, "is_closed": True},
    ])
    assert mtf._closed_kc_direction(df) == "FLAT"


def test_ltf_outer_band_turn_state_rejects_flat_and_chop():
    # 25 bars where price bounced off lower band 20 bars ago, but has traded completely flat since
    rows = []
    for i in range(25):
        low = 0.0189 if i == 5 else 0.0198
        rows.append({
            "timestamp": 1000 + i * 60000,
            "open": 0.02, "high": 0.0202, "low": low, "close": 0.02005,
            "kc_middle": 0.02 + i * 0.00000001,
            "kc_upper": 0.021, "kc_lower": 0.019,
            "atr": 0.0005,
            "ma5": 0.02005, "ma15": 0.02000,
            "is_closed": True,
        })
    df = pd.DataFrame(rows)
    # Must return None (not UPTREND!)
    assert mtf._detect_ltf_outer_band_turn_state(df) is None


def test_evaluate_mtf_entry_blocks_when_midline_is_flat():
    # 20 bars with flat kc_middle and full bands
    closed_rows = []
    for i in range(20):
        closed_rows.append({
            "timestamp": 1000 + i * 60000,
            "open": 0.02, "high": 0.0202, "low": 0.0198, "close": 0.02005,
            "kc_middle": 0.02000001,
            "kc_upper": 0.021, "kc_lower": 0.019,
            "atr": 0.0005,
            "ma5": 0.02005, "ma15": 0.02000,
            "is_closed": True,
        })
    closed = pd.DataFrame(closed_rows)
    live = pd.Series({
        "open": 0.02005, "high": 0.0201, "low": 0.0199, "close": 0.02006,
        "timestamp": 1000 + 20 * 60000,
        "is_closed": False,
    })
    frame_1m = pd.DataFrame([live])
    frame_1m.attrs["entry_finality_verified"] = True

    # Monkeypatch _valid_live_frame to return closed and live
    import unittest.mock as mock
    with mock.patch("core.services.mtf_kc_state_machine._valid_live_frame", return_value=(closed, live)):
        res = mtf.evaluate_mtf_entry(
            frame_1m,
            pd.DataFrame(),
            quote=0.02006,
            side="LONG",
        )
        assert res["allowed"] is False
        assert "VETO_KC_MIDLINE_FLAT" in res["reason"] or "BLOCKED" in res["reason"]


def test_short_exit_confirmation_protects_position_below_midline_and_ma15():
    # Holding short, a 1m candle closes green (close > open) BUT still below kc_middle and ma15
    closed = pd.DataFrame([
        {
            "timestamp": 60000,
            "open": 0.02010,
            "high": 0.02015,
            "low": 0.02005,
            "close": 0.02012,  # Green bar (0.02012 > 0.02010)
            "kc_middle": 0.02050,  # Below mid (0.02012 < 0.02050)
            "ma15": 0.02040,       # Below ma15 (0.02012 < 0.02040)
            "atr": 0.0005,
            "is_closed": True,
        }
    ])
    live = pd.Series({
        "timestamp": 120000,
        "open": 0.02012,
        "high": 0.02014,
        "low": 0.02010,
        "close": 0.02011,
        "is_closed": False,
    })
    frame_1m = pd.DataFrame([live])
    frame_1m.attrs["timeframe_ms"] = 60000.0

    position = {
        "symbol": "龙虾/USDT",
        "side": "SHORT",
        "open_timestamp": 10000,
        "entry_price": 0.02030,
        "lowest_price": 0.02005,
        "entry_atr": 0.0005,
    }
    metadata = {
        "mtf_pending_closed_bar_exit": {
            "reason": "MTF_KC:UPTREND_REVERSAL_EXIT",
            "side": "SHORT",
            "position_open_timestamp": 10000,
            "bar_id": 60000,
            "confirmed": False,
        }
    }

    import unittest.mock as mock
    with mock.patch("core.services.mtf_kc_state_machine._valid_live_frame", return_value=(closed, live)):
        decision = mtf.evaluate_mtf_exit_confirmation(
            frame_1m,
            position,
            metadata,
            reason=None,
        )
        # Must NOT be allowed to exit because price is still suppressed below mid and ma15!
        assert decision["allowed"] is False
        assert metadata.get("mtf_pending_closed_bar_exit") is None  # Pending exit cleared
