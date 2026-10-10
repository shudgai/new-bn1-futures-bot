import time

import pandas as pd
import pytest
from types import SimpleNamespace

from core.services.entry_contract import (
    GOLDEN_CROSS_FAST_LONG_CODE,
    evaluate_entry_contract,
    evaluate_golden_cross_fast_lane,
    excessive_upper_shadow_problem,
)
from core.services.exits.peak_trailing_exit import (
    ABNORMAL_REASON,
    evaluate_peak_trailing,
)


def live_cross_frame():
    current = int(time.time() // 60) * 60000
    rows = []
    for index in range(12):
        rows.append(dict(
            timestamp=current - (12 - index) * 60000,
            open=99.8, high=100.2, low=99.7, close=100.,
            atr=1., ma5=100., ma15=100.2,
            kc_lower=99., kc_middle=100., kc_upper=101.,
            is_closed=True,
        ))
    rows[-1].update(ma5=100., ma15=100.2)
    rows.append(dict(
        timestamp=current, open=100.5, high=101.5, low=100.5, close=101.5,
        atr=1., ma5=100.3, ma15=100.2,
        kc_lower=99., kc_middle=100., kc_upper=101.,
        is_closed=False,
    ))
    frame = pd.DataFrame(rows)
    frame.attrs["timeframe_ms"] = 60000
    return frame


def test_live_golden_cross_fast_lane_is_qualified_and_shared():
    frame = live_cross_frame()
    decision = evaluate_golden_cross_fast_lane(frame, 101.5, "CAP/USDT")

    assert decision is not None
    assert decision["type"] == GOLDEN_CROSS_FAST_LONG_CODE
    assert decision["intrabar"] is True
    assert decision["entry_phase"] == "MA_CROSS_FAST_LANE"


@pytest.mark.parametrize("failure", ["no_cross", "weak_body", "gap_open", "long_wick"])
def test_golden_cross_fast_lane_fails_closed(failure):
    frame = live_cross_frame()
    live = frame.index[-1]
    quote = 101.5
    if failure == "no_cross":
        frame.loc[live, "ma5"] = 100.1
    elif failure == "weak_body":
        frame.loc[live, ["open", "low"]] = [101.2, 101.2]
    elif failure == "gap_open":
        frame.loc[live, "open"] = 101.1
    else:
        frame.loc[live, "high"] = 102.3
    assert evaluate_golden_cross_fast_lane(frame, quote, "CAP/USDT") is None


def test_fast_lane_is_revalidated_by_shared_entry_contract(monkeypatch):
    import core.services.entry_contract as contract

    monkeypatch.setattr(
        contract, "check_entry_gates",
        lambda *args, **kwargs: (True, "GATE_PASSED_TEST"),
    )
    frame = live_cross_frame()
    result = evaluate_entry_contract(
        frame, 101.5, GOLDEN_CROSS_FAST_LONG_CODE, symbol="CAP/USDT",
    )
    assert result is not None
    assert result["type"] == GOLDEN_CROSS_FAST_LONG_CODE
    assert evaluate_entry_contract(
        frame, 101.5, GOLDEN_CROSS_FAST_LONG_CODE, symbol="CAP/USDT",
    )["pending_signal_id"] == result["pending_signal_id"]


def test_account_firewall_revalidates_fast_lane(monkeypatch):
    import asyncio
    import core.services.entry_contract as contract
    from core.services.entry_firewall import validate_account_entry

    monkeypatch.setattr(
        contract, "check_entry_gates",
        lambda *args, **kwargs: (True, "GATE_PASSED_TEST"),
    )
    frame = live_cross_frame()
    frame.attrs["entry_finality_verified"] = True
    frame.attrs["entry_finality_server_ms"] = float(frame.iloc[-1]["timestamp"])

    async def provide_frame(_symbol):
        return frame

    account = SimpleNamespace(
        positions={}, trades=[], entry_frame_provider=provide_frame,
    )
    decision = asyncio.run(validate_account_entry(
        account, "CAP/USDT", "LONG", {
            "entry_signal_code": GOLDEN_CROSS_FAST_LONG_CODE,
            "channel_confirmation_bar_id": float(frame.iloc[-1]["timestamp"]),
        },
    ))
    assert decision["type"] == GOLDEN_CROSS_FAST_LONG_CODE
    assert decision["intrabar"] is True


def test_upper_shadow_filter_is_strictly_seventy_percent():
    frame = live_cross_frame()
    live = frame.index[-1]
    frame.loc[live, "high"] = 102.2
    assert excessive_upper_shadow_problem(frame, 101.5, "LONG") == (
        "BLOCKED_BY_EXCESSIVE_UPPER_SHADOW"
    )
    assert excessive_upper_shadow_problem(frame, 101.5, "SHORT") is None


def test_shared_contract_blocks_long_upper_shadow(monkeypatch):
    import core.services.entry_contract as contract

    monkeypatch.setattr(
        contract, "check_entry_gates",
        lambda *args, **kwargs: (True, "GATE_PASSED_TEST"),
    )
    monkeypatch.setattr(
        contract, "detect_raw_triggers",
        lambda *args, **kwargs: ("LONG", "TRIGGER_A_KC_BREAKOUT"),
    )
    frame = live_cross_frame()
    frame.loc[frame.index[-1], "high"] = 102.3
    diagnostics = {}
    result = evaluate_entry_contract(frame, 101.5, diagnostics=diagnostics)
    assert result is None
    assert diagnostics["reason"] == "BLOCKED_BY_EXCESSIVE_UPPER_SHADOW"


def exit_position(side):
    return dict(
        symbol="CAP/USDT", side=side, entry_price=100., qty=1.,
        margin=100., leverage=1., open_timestamp=60.,
        entry_mode="CHANNEL_SWING", entry_atr=1.,
    )


def reversal_snapshot(side):
    if side == "LONG":
        reversal = dict(ms=180000., o=100.2, h=100.3, l=99.7, c=99.8, ma5=100.)
        live_open, price = 99.8, 99.7
    else:
        reversal = dict(ms=180000., o=99.8, h=100.3, l=99.7, c=100.2, ma5=100.)
        live_open, price = 100.2, 100.3
    return price, dict(
        quote_ms=240001., snapshot_bar_id=180000.,
        live_bar_ms=240000., closed_bar_ms=180000.,
        live_open=live_open, live_high=max(live_open, price),
        live_low=min(live_open, price), atr=1.,
        ma5=100., ma15=99.5, kc_middle=99.5,
        kc_upper=110., kc_lower=90.,
        history_5=[
            dict(ms=120000., o=100., h=101., l=99., c=100.1, ma5=100.),
            reversal,
        ],
    )


@pytest.mark.parametrize(
    ("side", "expected"),
    [
        ("LONG", "EXIT_DOJI_BEARISH_CONFIRMATION"),
        ("SHORT", "EXIT_DOJI_BULLISH_CONFIRMATION"),
    ],
)
def test_closed_doji_then_adverse_ma5_break_authorizes_close(side, expected):
    price, snapshot = reversal_snapshot(side)
    result = evaluate_peak_trailing(exit_position(side), price, snapshot, fee=0., slippage=0.)
    assert result is not None
    assert result["type"] == ABNORMAL_REASON
    assert result["trigger"] == expected


def test_long_lower_wick_above_support_vetoes_soft_exit(monkeypatch):
    import core.services.exits.peak_trailing_exit as exit_contract

    monkeypatch.setattr(
        exit_contract, "three_point_pivot_exit",
        lambda *args, **kwargs: {"trigger": "THREE_POINT_PIVOT"},
    )
    monkeypatch.setattr(exit_contract, "channel_pivot_trend_confirmed", lambda *args: True)
    price, snapshot = reversal_snapshot("LONG")
    snapshot.update(
        quote_ms=240002., live_open=101., live_high=101.2, live_low=98.5,
        ma15=99., kc_middle=100.,
        history_5=[],
    )
    result = evaluate_peak_trailing(
        exit_position("LONG"), 100.8, snapshot, fee=0., slippage=0.,
    )
    assert result is None
