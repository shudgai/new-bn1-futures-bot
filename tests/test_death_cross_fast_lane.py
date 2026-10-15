import asyncio
import time
from types import SimpleNamespace

import pandas as pd
import pytest

from core.services import entry_contract
from core.services.entry_firewall import validate_account_entry


def death_cross_frame(quote=99.7):
    rows = []
    start = 1_800_000_000_000
    for index in range(12):
        middle = 101.7 - index * 0.15
        close = middle + 0.2
        rows.append({
            "timestamp": start + index * 60_000,
            "open": close + 0.5,
            "high": close + 0.8,
            "low": close - 0.3,
            "close": close,
            "atr": 1.0,
            "ma5": 102.4 - index * 0.13,
            "ma15": 101.4 - index * 0.1,
            "kc_upper": middle + 4.0,
            "kc_middle": middle,
            "kc_lower": middle - 4.0,
            "is_closed": True,
        })

    middle = 100.05
    rows.append({
        "timestamp": start + 12 * 60_000,
        "open": 100.2,
        "high": 100.5,
        "low": min(99.6, quote),
        "close": quote,
        "atr": 1.0,
        "ma5": 100.8,
        "ma15": 100.9,
        "kc_upper": middle + 4.0,
        "kc_middle": middle,
        "kc_lower": middle - 4.0,
        "is_closed": False,
    })
    return pd.DataFrame(rows)


def fast_cross_frame(side, *, narrow_gap=False):
    frame = death_cross_frame()
    quote = 99.7
    if side == "LONG":
        source = frame.copy()
        for target, original in (
            ("open", "open"),
            ("close", "close"),
            ("high", "low"),
            ("low", "high"),
            ("kc_upper", "kc_lower"),
            ("kc_lower", "kc_upper"),
            ("kc_middle", "kc_middle"),
            ("ma5", "ma5"),
            ("ma15", "ma15"),
        ):
            frame[target] = 200.0 - source[original]
        quote = 200.0 - quote
    if narrow_gap:
        live = frame.index[-1]
        if side == "LONG":
            frame.loc[live, ["ma5", "ma15"]] = [99.705, 99.7]
        else:
            frame.loc[live, ["ma5", "ma15"]] = [100.895, 100.9]
    return frame, quote


def test_death_cross_fast_lane_requires_live_cross_and_25_percent_lower_room():
    frame = death_cross_frame()
    decision = entry_contract.evaluate_death_cross_fast_lane(
        frame, 99.7, "LOBSTER/USDT",
    )

    assert decision is not None
    assert decision["side"] == "SHORT"
    assert decision["entry_phase"] == "MA_DEATH_CROSS_FAST_LANE"
    assert decision["pending_signal_id"].endswith(str(int(frame.iloc[-1]["timestamp"])))

    frame.loc[frame.index[-1], "kc_lower"] = 99.1
    assert entry_contract.evaluate_death_cross_fast_lane(
        frame, 99.7, "LOBSTER/USDT",
    ) is None


def test_golden_cross_fast_lane_is_mirrored_above_middle():
    frame, quote = fast_cross_frame("LONG")
    decision = entry_contract.evaluate_golden_cross_fast_lane(
        frame, quote, "LOBSTER/USDT",
    )

    assert decision is not None
    assert decision["side"] == "LONG"
    assert decision["entry_phase"] == "MA_GOLDEN_CROSS_FAST_LANE"
    assert decision["body_atr"] >= 0.4
    assert decision["upper_rail_space_ratio"] >= 0.25












