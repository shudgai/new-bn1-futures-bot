import pandas as pd
import pytest
import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from core.gates.holding_protection_gate import (
    CHANNEL_MIDLINE_EXIT,
    CHANNEL_PEAK_PROFIT_EXIT,
    HOLDING_THROUGH_CHOP_REJECT,
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
def test_channel_swing_peak_lock_uses_climax_giveback_threshold(
    side, quote,
):
    position = _position(side)
    frame = _frame(side, close=quote, high=105.0, low=95.0)
    peak = 105.0 if side == "LONG" else 95.0

    HoldingProtectionExitGate.evaluate(position, frame, peak, True)
    decision, details = HoldingProtectionExitGate.evaluate(
        position, frame, quote, True,
    )

    assert decision is None


def test_channel_swing_holds_on_small_pullback_inside_upper_band():
    position = _position("LONG")
    peak_frame = _frame("LONG", close=112.0, high=112.0, low=99.0)
    peak_frame.loc[1, "kc_upper"] = 111.0
    HoldingProtectionExitGate.evaluate(position, peak_frame, 112.0, True)

    pullback_frame = _frame(
        "LONG", close=111.5, is_closed=True, high=112.0, low=109.0,
    )
    pullback_frame.loc[1, "open"] = 111.0
    pullback_frame.loc[1, "kc_upper"] = 111.0
    decision, _ = HoldingProtectionExitGate.evaluate(
        position, pullback_frame, 111.5, True,
    )

    assert decision is None


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

    assert decision is None
    allowed, _, _ = HoldingProtectionExitGate.validate_exit(
        position, frame, quote, decision, channel_swing=True,
    )
    assert allowed is False


@pytest.mark.parametrize("close", [100.0, 101.0])
def test_channel_swing_blocks_long_exit_when_closed_body_holds_kc_midline(close):
    frame = _frame("LONG", close=close, is_closed=True, high=105.0, low=99.0)
    frame.loc[1, "open"] = close + 1.0
    position = _position("LONG")

    allowed, reason, _ = HoldingProtectionExitGate.validate_exit(
        position,
        frame,
        quote=99.0,
        candidate_reason=CHANNEL_MIDLINE_EXIT["LONG"],
        channel_swing=True,
    )

    assert allowed is False
    assert reason == HoldingProtectionExitGate.REJECT_REASON


@pytest.mark.parametrize("reason", [
    "BLOCKED_BY_AI_CHOP_REGIME",
    "BLOCKED_MA5_MA15_GAP_TOO_NARROW",
    "BLOCKED_KC_SQUEEZE",
])
def test_entry_chop_vetoes_can_never_authorize_a_position_exit(reason):
    allowed, reject_reason, _ = HoldingProtectionExitGate.validate_exit(
        _position("LONG"), _frame("LONG", is_closed=True), 100.0, reason,
        channel_swing=True,
    )

    assert allowed is False
    assert reject_reason == HOLDING_THROUGH_CHOP_REJECT


@pytest.mark.parametrize(
    "side,close",
    [("LONG", 100.0), ("SHORT", 100.0)],
)
def test_choppy_position_can_still_execute_profit_lock(
    monkeypatch, side, close,
):
    from core.gates.pipeline import pipeline

    monkeypatch.setattr(pipeline, "market_regime_provider", lambda _symbol: "CHOPPY")
    frame = _frame(side, close=close, is_closed=True)
    frame.loc[:, "kc_upper"] = 100.2
    frame.loc[:, "kc_lower"] = 99.8
    position = _position(side)
    position["symbol"] = "TEST/USDT"
    position.update(
        margin=100.0,
        highest_price=105.0 if side == "LONG" else 100.0,
        lowest_price=95.0 if side == "SHORT" else 100.0,
    )

    allowed, reason, _ = HoldingProtectionExitGate.validate_exit(
        position, frame, close, "EXIT_BY_RATCHET_PROFIT_LOCK",
        channel_swing=True,
    )

    assert allowed is True
    assert reason == "EXIT_BY_RATCHET_PROFIT_LOCK"


@pytest.mark.parametrize(
    "side,opening,close",
    [("LONG", 100.0, 99.0), ("SHORT", 100.0, 101.0)],
)
def test_choppy_position_allows_only_adverse_closed_body_through_kc_midline(
    monkeypatch, side, opening, close,
):
    from core.gates.pipeline import pipeline

    monkeypatch.setattr(pipeline, "market_regime_provider", lambda _symbol: "CHOPPY")
    frame = _frame(side, close=close, is_closed=True)
    frame.loc[1, "open"] = opening
    frame.loc[:, "kc_upper"] = 100.2
    frame.loc[:, "kc_lower"] = 99.8
    position = _position(side)
    position["symbol"] = "TEST/USDT"

    allowed, reason, _ = HoldingProtectionExitGate.validate_exit(
        position, frame, close, CHANNEL_MIDLINE_EXIT[side],
        channel_swing=True,
    )

    assert allowed is False
    assert reason == HoldingProtectionExitGate.REJECT_REASON


def test_channel_swing_peak_exit_uses_observed_giveback_ratio():
    position = _position("LONG")
    frame = _frame("LONG", close=113.0, high=120.0, low=90.0)
    frame.loc[1, "kc_upper"] = 110.0

    HoldingProtectionExitGate.evaluate(position, frame, 120.0, True)
    decision, details = HoldingProtectionExitGate.evaluate(
        position, frame, 113.0, True,
    )

    assert decision is None


@pytest.mark.parametrize(
    "side,peak,quote,expected_ratio,expected_atr",
    [
        ("LONG", 105.0, 104.2, 0.16, 0.4),
        ("SHORT", 95.0, 95.8, 0.16, 0.4),
        ("LONG", 120.0, 115.0, 0.25, 2.5),
        ("SHORT", 80.0, 85.0, 0.25, 2.5),
    ],
)
def test_channel_climax_exits_on_either_peak_giveback_limit(
    side, peak, quote, expected_ratio, expected_atr,
):
    position = _position(side)
    frame = _frame(
        side, close=quote, high=max(110.0, peak, quote),
        low=min(90.0, peak, quote),
    )
    if side == "LONG" and peak > 110.0:
        frame.loc[1, "kc_upper"] = 110.0
    if side == "SHORT" and peak < 90.0:
        frame.loc[1, "kc_lower"] = 90.0

    HoldingProtectionExitGate.evaluate(position, frame, peak, True)
    decision, details = HoldingProtectionExitGate.evaluate(
        position, frame, quote, True,
    )

    assert decision is None


@pytest.mark.parametrize(
    "side,quote,outer_price",
    [
        ("LONG", 103.0, 104.0),
        ("SHORT", 97.0, 96.0),
    ],
)
def test_channel_climax_arms_only_after_profit_and_outer_rail(
    side, quote, outer_price,
):
    position = _position(side)
    frame = _frame(side, close=quote, high=105.0, low=95.0)

    HoldingProtectionExitGate.evaluate(position, frame, quote, True)
    assert "channel_peak_profit_exit_state" not in position

    frame.loc[1, "close"] = outer_price
    HoldingProtectionExitGate.evaluate(position, frame, outer_price, True)
    assert "channel_peak_profit_exit_state" not in position


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


def test_realtime_path_does_not_close_on_nonpivot_peak_giveback(monkeypatch):
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
        assert "channel_peak_profit_exit_state" not in account.position_meta["X"]
        refreshed = _position("LONG")
        refreshed.update(
            symbol="X",
            open_timestamp=position["open_timestamp"],
        )
        account.positions["X"] = refreshed
        assert not await realtime_profit_exit.enforce_realtime_profit_exit(
            engine, "X", 103.2, stamp + 1.0,
        )
        account.close_position.assert_not_awaited()

    asyncio.run(run())


@pytest.mark.parametrize(
    "side,peak,quote,opening,high,low",
    [
        ("LONG", 114.0, 113.6, 113.7, 114.4, 113.4),
        ("SHORT", 86.0, 86.4, 86.3, 86.6, 85.6),
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

    assert decision is None
    allowed, _, _ = HoldingProtectionExitGate.validate_exit(
        position, closed, quote, decision, details, channel_swing=True,
    )
    assert allowed is False


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
            "timestamp": 120_000.0, "open": 114.5, "high": 115.0,
            "low": 112.0, "close": 113.5, "atr": 2.0,
            "kc_upper": 112.0, "kc_middle": 100.0,
            "kc_lower": 88.0, "is_closed": True,
        },
    ])

    decision, _ = HoldingProtectionExitGate.evaluate(
        position, closed, 113.5, True,
    )

    assert decision is None
