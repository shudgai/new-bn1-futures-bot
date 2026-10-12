import pandas as pd
import pytest
import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from core.gates.holding_protection_gate import (
    CHANNEL_MIDLINE_EXIT,
    CHANNEL_PEAK_PROFIT_EXIT,
    HoldingProtectionExitGate,
)


def _position(side):
    return {
        "side": side,
        "entry_mode": "CHANNEL_SWING",
        "open_timestamp": 60.0,
        "entry_price": 100.0,
        "qty": 1.0,
    }


def _frame(side, *, close=None, is_closed=False, high=None, low=None):
    opening = 100.0
    close = opening if close is None else close
    return pd.DataFrame([
        {
            "timestamp": 60_000.0,
            "open": 100.0,
            "high": 101.0,
            "low": 99.0,
            "close": 100.0,
            "atr": 2.0,
            "kc_upper": 104.0,
            "kc_middle": 100.0,
            "kc_lower": 96.0,
            "is_closed": True,
        },
        {
            "timestamp": 120_000.0,
            "open": opening,
            "high": 110.0 if high is None else high,
            "low": 90.0 if low is None else low,
            "close": close,
            "atr": 2.0,
            "kc_upper": 104.0,
            "kc_middle": 100.0,
            "kc_lower": 96.0,
            "is_closed": is_closed,
        },
    ])


@pytest.mark.parametrize(
    "side,quote",
    [("LONG", 101.0), ("SHORT", 99.0)],
)
def test_channel_swing_ignores_intrabar_noise(side, quote):
    decision, _ = HoldingProtectionExitGate.evaluate(
        _position(side), _frame(side), quote, True,
    )

    assert decision is None


@pytest.mark.parametrize(
    "side,quote",
    [("LONG", 103.2), ("SHORT", 96.8)],
)
def test_channel_swing_peak_lock_requires_35_percent_peak_profit_giveback(
    side, quote,
):
    position = _position(side)
    frame = _frame(side, close=quote, high=105.0, low=95.0)
    peak = 105.0 if side == "LONG" else 95.0

    HoldingProtectionExitGate.evaluate(position, frame, peak, True)
    decision, details = HoldingProtectionExitGate.evaluate(
        position, frame, quote, True,
    )

    assert decision == CHANNEL_PEAK_PROFIT_EXIT
    assert details["giveback_ratio"] >= 0.35


@pytest.mark.parametrize(
    "side,opening,close,quote",
    [
        ("LONG", 101.0, 99.0, 99.0),
        ("SHORT", 99.0, 101.0, 101.0),
    ],
)
def test_channel_swing_midline_exit_requires_adverse_closed_body(
    side, opening, close, quote,
):
    frame = _frame(side, close=close, is_closed=True)
    frame.loc[1, "open"] = opening
    position = _position(side)

    decision, _ = HoldingProtectionExitGate.evaluate(position, frame, quote, True)

    assert decision == CHANNEL_MIDLINE_EXIT[side]
    allowed, _, _ = HoldingProtectionExitGate.validate_exit(
        position, frame, quote, decision, channel_swing=True,
    )
    assert allowed


@pytest.mark.parametrize(
    "side,opening,close,quote",
    [
        ("LONG", 99.0, 101.0, 99.0),
        ("SHORT", 101.0, 99.0, 101.0),
    ],
)
def test_channel_swing_does_not_exit_on_wick_only_midline_touch(
    side, opening, close, quote,
):
    frame = _frame(side, close=close, is_closed=True)
    frame.loc[1, "open"] = opening

    decision, _ = HoldingProtectionExitGate.evaluate(
        _position(side), frame, quote, True,
    )

    assert decision is None


def test_realtime_path_closes_only_after_channel_peak_giveback(monkeypatch):
    from core.services.exits import realtime_profit_exit

    async def run():
        stamp = time.time() * 1000
        live_bar_ms = stamp // 60_000 * 60_000
        frame = pd.DataFrame([
            {
                "timestamp": live_bar_ms - 60_000,
                "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0,
                "atr": 2.0, "kc_upper": 104.0, "kc_middle": 100.0,
                "kc_lower": 96.0, "is_closed": True,
            },
            {
                "timestamp": live_bar_ms,
                "open": 100.0, "high": 105.0, "low": 99.0, "close": 100.0,
                "atr": 2.0, "kc_upper": 104.0, "kc_middle": 100.0,
                "kc_lower": 96.0, "is_closed": False,
            },
        ])
        position = _position("LONG")
        position.update(
            symbol="X",
            open_timestamp=time.time() - 180.0,
        )
        account = SimpleNamespace(
            positions={"X": position},
            position_meta={},
            save_state=Mock(),
            close_position=AsyncMock(return_value=True),
            log=Mock(),
        )
        engine = SimpleNamespace(
            is_running=True,
            account=account,
            _channel_exit_frames={"X": frame},
        )
        monkeypatch.setattr(
            realtime_profit_exit, "enforce_hard_stop",
            AsyncMock(return_value=False),
        )
        monkeypatch.setattr(
            realtime_profit_exit, "cached_tick_indicators",
            lambda *_args, **_kwargs: (
                {"reason": None, "quote_ms": stamp, "history_5": []}, 2.0,
            ),
        )
        monkeypatch.setattr(
            realtime_profit_exit, "_evaluate_realtime_core_exit_gates",
            lambda *_args, **_kwargs: (None, False),
        )
        monkeypatch.setattr(
            "core.exits.peak_valley_exit.PeakValleyExit.evaluate",
            lambda *_args: (None, {}),
        )

        assert not await realtime_profit_exit.enforce_realtime_profit_exit(
            engine, "X", 105.0, stamp,
        )
        assert "channel_peak_profit_exit_state" in account.position_meta["X"]
        refreshed = _position("LONG")
        refreshed.update(
            symbol="X",
            open_timestamp=position["open_timestamp"],
        )
        account.positions["X"] = refreshed
        assert await realtime_profit_exit.enforce_realtime_profit_exit(
            engine, "X", 103.2, stamp + 1.0,
        )
        account.close_position.assert_awaited_once_with(
            "X", 103.2, f"Channel Swing {CHANNEL_PEAK_PROFIT_EXIT}",
            is_manual=True,
        )

    asyncio.run(run())


@pytest.mark.parametrize(
    "side,peak,quote,opening,high,low",
    [
        ("LONG", 114.0, 109.7, 110.0, 115.0, 109.5),
        ("SHORT", 86.0, 90.3, 90.0, 90.5, 85.0),
    ],
)
def test_mature_channel_peak_can_exit_on_closed_reversal_wick(
    side, peak, quote, opening, high, low,
):
    position = _position(side)
    live = _frame(
        side, close=peak, high=max(101.0, peak), low=min(99.0, peak),
    )
    HoldingProtectionExitGate.evaluate(position, live, peak, True)
    closed = pd.DataFrame([
        {
            "timestamp": 60_000.0, "open": 100.0, "high": 101.0,
            "low": 99.0, "close": 100.0, "atr": 2.0,
            "kc_upper": 112.0, "kc_middle": 100.0,
            "kc_lower": 88.0, "is_closed": True,
        },
        {
            "timestamp": 120_000.0, "open": opening, "high": high,
            "low": low, "close": quote, "atr": 2.0,
            "kc_upper": 112.0, "kc_middle": 100.0,
            "kc_lower": 88.0, "is_closed": True,
        },
    ])

    decision, details = HoldingProtectionExitGate.evaluate(
        position, closed, quote, True,
    )

    assert decision == "EXIT_CHANNEL_PEAK_WICK_REVERSAL"
    assert details["closed_reversal_confirmation"]
    allowed, _, _ = HoldingProtectionExitGate.validate_exit(
        position, closed, quote, decision, details, channel_swing=True,
    )
    assert allowed


def test_peak_wick_reversal_requires_six_tenths_of_closed_bar_range():
    position = _position("LONG")
    live = _frame("LONG", close=114.0, high=114.0, low=99.0)
    HoldingProtectionExitGate.evaluate(position, live, 114.0, True)
    closed = pd.DataFrame([
        {
            "timestamp": 60_000.0, "open": 100.0, "high": 101.0,
            "low": 99.0, "close": 100.0, "atr": 2.0,
            "kc_upper": 112.0, "kc_middle": 100.0,
            "kc_lower": 88.0, "is_closed": True,
        },
        {
            "timestamp": 120_000.0, "open": 110.0, "high": 115.0,
            "low": 100.0, "close": 109.7, "atr": 2.0,
            "kc_upper": 112.0, "kc_middle": 100.0,
            "kc_lower": 88.0, "is_closed": True,
        },
    ])

    decision, _ = HoldingProtectionExitGate.evaluate(
        position, closed, 109.7, True,
    )

    assert decision is None
