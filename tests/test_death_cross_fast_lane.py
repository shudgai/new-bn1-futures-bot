import asyncio
import time
from types import SimpleNamespace

import pandas as pd

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
