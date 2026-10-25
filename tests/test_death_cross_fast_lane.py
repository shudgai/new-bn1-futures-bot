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
            "ma3": 102.4 - index * 0.13,
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
        "ma3": 100.2,
        # MA5 has crossed below a flat MA15; no upward MA15 veto on SHORT.
        "ma5": 100.2,
        "ma15": 100.3,
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
            ("ma3", "ma3"),
            ("ma5", "ma5"),
            ("ma15", "ma15"),
        ):
            frame[target] = 200.0 - source[original]
        quote = 200.0 - quote
    if narrow_gap:
        live = frame.index[-1]
        if side == "LONG":
            frame.loc[live, ["ma3", "ma5", "ma15"]] = [99.705, 99.705, 99.7]
        else:
            frame.loc[live, ["ma3", "ma5", "ma15"]] = [100.295, 100.295, 100.3]
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


@pytest.mark.parametrize(
    ("side", "code"),
    [
        ("LONG", entry_contract.GOLDEN_CROSS_FAST_LONG_CODE),
        ("SHORT", entry_contract.DEATH_CROSS_FAST_SHORT_CODE),
    ],
)
def test_fresh_fast_cross_gets_only_single_cross_gap_exemption(side, code):
    frame, quote = fast_cross_frame(side, narrow_gap=True)
    diagnostics = {}

    decision = entry_contract.evaluate_entry_contract(
        frame, quote, code, symbol="LOBSTER/USDT", diagnostics=diagnostics,
    )

    assert decision is not None, diagnostics
    assert decision["type"] == code


@pytest.mark.parametrize(
    ("side", "code"),
    [
        ("LONG", entry_contract.GOLDEN_CROSS_FAST_LONG_CODE),
        ("SHORT", entry_contract.DEATH_CROSS_FAST_SHORT_CODE),
    ],
)
def test_fast_cross_never_exempts_repeated_whipsaw(side, code):
    frame, quote = fast_cross_frame(side, narrow_gap=True)
    spreads = (
        ((-5, -0.2), (-4, 0.2), (-3, -0.2), (-2, -0.2))
        if side == "LONG"
        else ((-5, -0.2), (-4, 0.2), (-3, -0.2), (-2, -0.7))
    )
    for offset, spread in spreads:
        ma15 = float(frame.iloc[offset]["ma15"])
        frame.loc[frame.index[offset], "ma5"] = ma15 + (
            spread if side == "LONG" else -spread
        )
    diagnostics = {}

    decision = entry_contract.evaluate_entry_contract(
        frame, quote, code, symbol="LOBSTER/USDT", diagnostics=diagnostics,
    )

    assert decision is None
    assert diagnostics["reason"] == "BLOCKED_MA5_MA15_CROSS_CHOP"

    frame = death_cross_frame()
    frame.loc[frame.index[-1], "is_closed"] = True
    assert entry_contract.evaluate_death_cross_fast_lane(
        frame, 99.7, "LOBSTER/USDT",
    ) is None


def test_death_cross_fast_lane_revalidates_and_uses_shared_authorization(monkeypatch):
    frame = death_cross_frame()
    monkeypatch.setattr(
        entry_contract.pipeline, "detect_realtime_breakout",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        entry_contract.pipeline, "detect_midline_break_short",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        entry_contract.pipeline, "detect_midline_break_long",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        entry_contract.pipeline, "market_regime_problem",
        lambda *_args, **_kwargs: "BLOCKED_BY_TEST_REGIME",
    )
    monkeypatch.setattr(
        entry_contract.pipeline, "chop_lockout_problem",
        lambda *_args, **_kwargs: "BLOCKED_BY_TEST_CHOP",
    )

    diagnostics = {}
    decision = entry_contract.evaluate_entry_contract(
        frame, 99.7, account=None, symbol="LOBSTER/USDT",
        diagnostics=diagnostics,
    )

    assert decision is not None, diagnostics
    assert decision["type"] == entry_contract.DEATH_CROSS_FAST_SHORT_CODE
    assert decision["side"] == "SHORT"
    assert decision["_is_authorized"] is True


def test_requested_death_cross_route_fails_closed_when_live_cross_disappears():
    frame = death_cross_frame()
    frame.loc[frame.index[-1], "ma5"] = 101.2
    diagnostics = {}

    decision = entry_contract.evaluate_entry_contract(
        frame, 99.7, entry_contract.DEATH_CROSS_FAST_SHORT_CODE,
        diagnostics=diagnostics,
    )

    assert decision is None
    assert diagnostics["reason"] == "BLOCKED_DEATH_CROSS_FAST_LANE_NOT_QUALIFIED"


def test_entry_firewall_revalidates_live_death_cross(monkeypatch):
    frame = death_cross_frame()
    current_bar = (time.time() * 1000 // 60_000) * 60_000
    frame["timestamp"] += current_bar - float(frame.iloc[-1]["timestamp"])
    frame.attrs["entry_finality_verified"] = True
    monkeypatch.setattr(
        entry_contract.pipeline, "detect_realtime_breakout",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        entry_contract.pipeline, "detect_midline_break_short",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        entry_contract.pipeline, "detect_midline_break_long",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        entry_contract.pipeline, "market_regime_problem",
        lambda *_args, **_kwargs: "BLOCKED_BY_TEST_REGIME",
    )
    monkeypatch.setattr(
        entry_contract.pipeline, "chop_lockout_problem",
        lambda *_args, **_kwargs: None,
    )

    async def provide_frame(_symbol):
        return frame

    account = SimpleNamespace(
        entry_frame_provider=provide_frame,
        positions={},
        last_closed_at={},
        last_closed_side={},
        trades=[],
    )
    context = {
        "entry_signal_code": entry_contract.DEATH_CROSS_FAST_SHORT_CODE,
        "channel_confirmation_bar_id": float(frame.iloc[-1]["timestamp"]),
    }

    asyncio.run(validate_account_entry(
        account, "LOBSTER/USDT", "SHORT", context,
    ))


def test_entry_firewall_revalidates_live_golden_cross():
    frame, quote = fast_cross_frame("LONG", narrow_gap=True)
    current_bar = (time.time() * 1000 // 60_000) * 60_000
    frame["timestamp"] += current_bar - float(frame.iloc[-1]["timestamp"])
    frame.loc[frame.index[-1], "close"] = quote
    frame.attrs["entry_finality_verified"] = True

    async def provide_frame(_symbol):
        return frame

    account = SimpleNamespace(
        entry_frame_provider=provide_frame,
        positions={},
        last_closed_at={},
        last_closed_side={},
        trades=[],
    )
    context = {
        "entry_signal_code": entry_contract.GOLDEN_CROSS_FAST_LONG_CODE,
        "entry_phase": "MA_GOLDEN_CROSS_FAST_LANE",
        "channel_confirmation_bar_id": float(frame.iloc[-1]["timestamp"]),
    }

    decision = asyncio.run(validate_account_entry(
        account, "LOBSTER/USDT", "LONG", context,
    ))

    assert decision["type"] == entry_contract.GOLDEN_CROSS_FAST_LONG_CODE
