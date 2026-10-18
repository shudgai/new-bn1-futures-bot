import asyncio

import pandas as pd
import pytest

from core.services.exits.authorized_position_exit import (
    automated_position_exit_allowed,
)
from core.services.exits.kc_true_extreme_exit import (
    STATE_KEY,
    evaluate_kc_true_extreme_exit,
)


def _frame(
    live_bar_ms,
    *,
    side="LONG",
    live_price=11.5,
    live_high=None,
    live_low=None,
    closed_candle=None,
    previous_candle=None,
    kc_middle_values=(10.0, 10.0, 10.0),
):
    previous = previous_candle or {
        "open": 10.0, "high": 11.2, "low": 9.8, "close": 11.0,
    }
    closed = closed_candle or {
        "open": 11.0, "high": 11.5, "low": 10.5, "close": 11.2,
    }
    lower, middle, upper = 8.0, 10.0, 12.0
    rows = []
    for (timestamp, candle), middle_value in zip(
        (
            (live_bar_ms - 120_000, previous),
            (live_bar_ms - 60_000, closed),
        ),
        kc_middle_values[:2],
    ):
        rows.append({
            "timestamp": timestamp,
            **candle,
            "atr": 1.0,
            "kc_upper": upper,
            "kc_middle": middle_value,
            "kc_lower": lower,
            "is_closed": True,
        })
    opened = 11.0 if side == "LONG" else 9.0
    high = max(opened, live_price, live_high or live_price)
    low = min(opened, live_price, live_low or live_price)
    rows.append({
        "timestamp": live_bar_ms,
        "open": opened,
        "high": high,
        "low": low,
        "close": live_price,
        "atr": 1.0,
        "kc_upper": upper,
        "kc_middle": kc_middle_values[2],
        "kc_lower": lower,
        "is_closed": False,
    })
    frame = pd.DataFrame(rows)
    frame.attrs["timeframe_ms"] = 60_000
    frame.attrs["snapshot_ms"] = live_bar_ms + 2_000
    return frame


def _position(live_bar_ms, side="LONG", entry_mode="CHANNEL_SWING"):
    return {
        "side": side,
        "entry_price": 11.0 if side == "LONG" else 9.0,
        "qty": 1.0,
        "open_timestamp": (live_bar_ms - 179_000) / 1_000,
        "entry_mode": entry_mode,
    }


def test_long_peak_requires_outer_extension_and_intraday_pullback():
    live_bar_ms = 1_800_000_000_000
    position = _position(live_bar_ms)
    frame = _frame(
        live_bar_ms, live_price=12.6, live_high=12.6,
    )

    first, changed = evaluate_kc_true_extreme_exit(
        position, frame, 12.6, live_bar_ms + 2_000,
    )

    assert first is None
    assert changed
    assert position[STATE_KEY]["armed"] is True
    assert position[STATE_KEY]["peak_or_trough"] == pytest.approx(12.6)

    frame = _frame(
        live_bar_ms, live_price=12.3, live_high=12.6,
    )
    second, _ = evaluate_kc_true_extreme_exit(
        position, frame, 12.3, live_bar_ms + 3_000,
    )
    assert second is None

    frame = _frame(
        live_bar_ms, live_price=12.2, live_high=12.6,
    )
    third, _ = evaluate_kc_true_extreme_exit(
        position, frame, 12.2, live_bar_ms + 4_000,
    )
    assert third is not None
    assert third[0] == "EXIT_BY_TRUE_PEAK_EXTREME_REVERSAL"
    assert third[1]["cause"] == "INTRADAY_PEAK_PULLBACK"


def test_long_rejection_wick_needs_outer_extension_but_not_a_red_candle():
    live_bar_ms = 1_800_000_000_000
    frame = _frame(
        live_bar_ms,
        live_price=11.9,
        closed_candle={
            "open": 11.5, "high": 12.6, "low": 11.4, "close": 11.8,
        },
    )

    decision, _ = evaluate_kc_true_extreme_exit(
        _position(live_bar_ms), frame, 11.9, live_bar_ms + 2_000,
    )

    assert decision is not None
    assert decision[0] == "EXIT_BY_TRUE_PEAK_EXTREME_REVERSAL"
    assert decision[1]["cause"] == "LONG_UPPER_WICK"


def test_bearish_engulfing_requires_outer_extension():
    live_bar_ms = 1_800_000_000_000
    frame = _frame(
        live_bar_ms,
        live_price=11.4,
        previous_candle={
            "open": 11.0, "high": 12.3, "low": 10.9, "close": 12.0,
        },
        closed_candle={
            "open": 12.1, "high": 12.6, "low": 10.9, "close": 11.0,
        },
    )

    decision, _ = evaluate_kc_true_extreme_exit(
        _position(live_bar_ms), frame, 11.4, live_bar_ms + 2_000,
    )

    assert decision is not None
    assert decision[1]["cause"] == "BEARISH_ENGULFING"


def test_bearish_engulfing_after_prior_outer_extension_exits_long():
    live_bar_ms = 1_800_000_000_000
    position = _position(live_bar_ms)
    extended_green = {
        "open": 11.0, "high": 12.6, "low": 10.9, "close": 12.4,
    }
    frame = _frame(
        live_bar_ms,
        live_price=12.4,
        previous_candle={"open": 10.0, "high": 11.2, "low": 9.8, "close": 11.0},
        closed_candle=extended_green,
    )
    initial, _ = evaluate_kc_true_extreme_exit(
        position, frame, 12.4, live_bar_ms + 2_000,
    )
    assert initial is None
    assert position[STATE_KEY]["armed"] is True

    bearish_engulfing = {
        "open": 12.4, "high": 12.4, "low": 10.9, "close": 11.0,
    }
    frame = _frame(
        live_bar_ms + 60_000,
        live_price=11.4,
        previous_candle=extended_green,
        closed_candle=bearish_engulfing,
    )
    decision, _ = evaluate_kc_true_extreme_exit(
        position, frame, 11.4, live_bar_ms + 62_000,
    )

    assert decision is not None
    assert decision[0] == "EXIT_BY_TRUE_PEAK_EXTREME_REVERSAL"
    assert decision[1]["cause"] == "BEARISH_ENGULFING"


def test_bullish_engulfing_after_prior_outer_extension_exits_short():
    live_bar_ms = 1_800_000_000_000
    position = _position(live_bar_ms, side="SHORT")
    extended_red = {
        "open": 9.0, "high": 9.1, "low": 7.4, "close": 8.0,
    }
    frame = _frame(
        live_bar_ms,
        side="SHORT",
        live_price=7.5,
        previous_candle={"open": 10.0, "high": 10.2, "low": 9.8, "close": 10.0},
        closed_candle=extended_red,
    )
    initial, _ = evaluate_kc_true_extreme_exit(
        position, frame, 7.5, live_bar_ms + 2_000,
    )
    assert initial is None
    assert position[STATE_KEY]["armed"] is True

    bullish_engulfing = {
        "open": 8.0, "high": 9.1, "low": 8.0, "close": 9.0,
    }
    frame = _frame(
        live_bar_ms + 60_000,
        side="SHORT",
        live_price=8.6,
        previous_candle=extended_red,
        closed_candle=bullish_engulfing,
    )
    decision, _ = evaluate_kc_true_extreme_exit(
        position, frame, 8.6, live_bar_ms + 62_000,
    )

    assert decision is not None
    assert decision[0] == "EXIT_BY_TRUE_VALLEY_EXTREME_REVERSAL"
    assert decision[1]["cause"] == "BULLISH_ENGULFING"


def test_short_valley_uses_mirrored_extension_and_lower_wick():
    live_bar_ms = 1_800_000_000_000
    frame = _frame(
        live_bar_ms,
        side="SHORT",
        live_price=8.1,
        closed_candle={
            "open": 8.5, "high": 8.6, "low": 7.4, "close": 8.2,
        },
    )

    decision, _ = evaluate_kc_true_extreme_exit(
        _position(live_bar_ms, side="SHORT"), frame, 8.1, live_bar_ms + 2_000,
    )

    assert decision is not None
    assert decision[0] == "EXIT_BY_TRUE_VALLEY_EXTREME_REVERSAL"
    assert decision[1]["cause"] == "SHORT_LOWER_WICK"


def test_short_lower_wick_without_urgent_rebound_is_suppressed_while_kc_middle_falls():
    live_bar_ms = 1_800_000_000_000
    frame = _frame(
        live_bar_ms,
        side="SHORT",
        live_price=7.7,
        closed_candle={
            "open": 8.5, "high": 8.6, "low": 7.4, "close": 8.2,
        },
        kc_middle_values=(10.3, 10.2, 10.1),
    )
    position = _position(live_bar_ms, side="SHORT")

    decision, changed = evaluate_kc_true_extreme_exit(
        position, frame, 7.7, live_bar_ms + 2_000,
    )

    assert decision is None
    assert changed
    assert position[STATE_KEY]["pending"] is None


def test_short_rebound_beyond_lower_band_exits_during_falling_kc_middle():
    live_bar_ms = 1_800_000_000_000
    position = _position(live_bar_ms, side="SHORT")
    frame = _frame(
        live_bar_ms,
        side="SHORT",
        live_price=7.9,
        live_low=7.9,
        kc_middle_values=(10.3, 10.2, 10.1),
    )

    first, _ = evaluate_kc_true_extreme_exit(
        position, frame, 7.9, live_bar_ms + 2_000,
    )
    assert first is None
    assert position[STATE_KEY]["armed"] is True

    frame = _frame(
        live_bar_ms,
        side="SHORT",
        live_price=8.3,
        live_low=7.9,
        kc_middle_values=(10.3, 10.2, 10.1),
    )
    decision, _ = evaluate_kc_true_extreme_exit(
        position, frame, 8.3, live_bar_ms + 3_000,
    )
    assert decision is not None
    assert decision[0] == "EXIT_BY_TRUE_VALLEY_EXTREME_REVERSAL"
    assert decision[1]["cause"] == "INTRADAY_TROUGH_REBOUND"

    repeated, _ = evaluate_kc_true_extreme_exit(
        position, frame, 8.3, live_bar_ms + 4_000,
    )
    assert repeated == decision


def test_short_bullish_engulfing_is_not_vetoed_by_falling_kc_middle():
    live_bar_ms = 1_800_000_000_000
    position = _position(live_bar_ms, side="SHORT")
    bearish_candle = {
        "open": 8.5, "high": 8.6, "low": 7.9, "close": 7.95,
    }
    frame = _frame(
        live_bar_ms,
        side="SHORT",
        live_price=8.0,
        live_low=7.9,
        closed_candle=bearish_candle,
        kc_middle_values=(10.3, 10.2, 10.1),
    )
    first, _ = evaluate_kc_true_extreme_exit(
        position, frame, 8.0, live_bar_ms + 2_000,
    )
    assert first is None
    assert position[STATE_KEY]["armed"] is True

    bullish_engulfing = {
        "open": 7.9, "high": 8.7, "low": 7.8, "close": 8.6,
    }
    frame = _frame(
        live_bar_ms + 60_000,
        side="SHORT",
        live_price=8.4,
        live_low=8.3,
        previous_candle=bearish_candle,
        closed_candle=bullish_engulfing,
        kc_middle_values=(10.3, 10.2, 10.1),
    )
    decision, _ = evaluate_kc_true_extreme_exit(
        position, frame, 8.4, live_bar_ms + 62_000,
    )
    assert decision is not None
    assert decision[0] == "EXIT_BY_TRUE_VALLEY_EXTREME_REVERSAL"
    assert decision[1]["cause"] == "BULLISH_ENGULFING"

    repeated, _ = evaluate_kc_true_extreme_exit(
        position, frame, 8.4, live_bar_ms + 63_000,
    )
    assert repeated == decision


def test_short_midline_retrace_is_suppressed_while_closed_kc_middle_falls():
    live_bar_ms = 1_800_000_000_000
    frame = _frame(
        live_bar_ms,
        side="SHORT",
        live_price=10.0,
        kc_middle_values=(10.3, 10.2, 10.0),
    )
    position = _position(live_bar_ms, side="SHORT")

    decision, _ = evaluate_kc_true_extreme_exit(
        position, frame, 10.0, live_bar_ms + 2_000,
    )

    assert decision is None
    assert position[STATE_KEY]["pending"] is None


def test_pending_short_pivot_is_cleared_when_kc_middle_falls():
    live_bar_ms = 1_800_000_000_000
    position = _position(live_bar_ms, side="SHORT")
    frame = _frame(
        live_bar_ms,
        side="SHORT",
        live_price=8.0,
        kc_middle_values=(10.3, 10.2, 10.1),
    )
    evaluate_kc_true_extreme_exit(position, frame, 8.0, live_bar_ms + 2_000)
    position[STATE_KEY]["pending"] = {
        "reason": "EXIT_BY_TRUE_VALLEY_EXTREME_REVERSAL",
        "details": {"cause": "THREE_POINT_CONFIRMED_PIVOT"},
    }

    decision, changed = evaluate_kc_true_extreme_exit(
        position, frame, 8.0, live_bar_ms + 3_000,
    )

    assert decision is None
    assert changed
    assert position[STATE_KEY]["pending"] is None


@pytest.mark.parametrize(
    ("side", "price"),
    [("LONG", 10.0), ("SHORT", 10.0)],
)
def test_ordinary_retracement_exits_only_at_kc_midline(side, price):
    live_bar_ms = 1_800_000_000_000
    frame = _frame(live_bar_ms, side=side, live_price=price)

    decision, _ = evaluate_kc_true_extreme_exit(
        _position(live_bar_ms, side=side), frame, price, live_bar_ms + 2_000,
    )

    assert decision is not None
    assert decision[0] == "EXIT_BY_KC_MIDLINE_RETRACE"
    assert decision[1]["cause"] == "KC_MIDLINE_TOUCH"


def test_subthreshold_pullback_holds_until_midline():
    live_bar_ms = 1_800_000_000_000
    position = _position(live_bar_ms)
    frame = _frame(live_bar_ms, live_price=12.4, live_high=12.4)
    evaluate_kc_true_extreme_exit(position, frame, 12.4, live_bar_ms + 2_000)

    frame = _frame(live_bar_ms, live_price=12.3, live_high=12.4)
    decision, _ = evaluate_kc_true_extreme_exit(
        position, frame, 12.3, live_bar_ms + 3_000,
    )
    assert decision is None

    frame = _frame(live_bar_ms, live_price=10.0, live_high=12.4)
    decision, _ = evaluate_kc_true_extreme_exit(
        position, frame, 10.0, live_bar_ms + 4_000,
    )
    assert decision is not None
    assert decision[0] == "EXIT_BY_KC_MIDLINE_RETRACE"


@pytest.mark.parametrize("entry_mode", ["CHANNEL_SWING", "WILD_SWING", "BREAKOUT"])
def test_realtime_exit_uses_true_peak_authority_for_auto_positions(
    monkeypatch, entry_mode,
):
    from core.services.exits import realtime_profit_exit

    live_bar_ms = 1_800_000_000_000
    quote_ms = live_bar_ms + 2_000
    position = _position(live_bar_ms, entry_mode=entry_mode)
    frame = _frame(
        live_bar_ms,
        live_price=11.9,
        closed_candle={
            "open": 11.5, "high": 12.6, "low": 11.4, "close": 11.8,
        },
    )

    class Account:
        def __init__(self):
            self.positions = {"LOBSTER/USDT": position}
            self.position_meta = {}
            self.closed = []

        def save_state(self):
            pass

        def log(self, *_args):
            pass

        async def close_position(self, symbol, price, reason, *, is_manual=False):
            assert automated_position_exit_allowed(
                symbol,
                self.positions[symbol],
                self.position_meta[symbol],
                reason,
                price,
                is_manual=is_manual,
            )
            self.closed.append(reason)
            return True

    class Engine:
        is_running = True

    account = Account()
    engine = Engine()
    engine.account = account
    engine._channel_exit_frames = {"LOBSTER/USDT": frame}
    monkeypatch.setattr(
        realtime_profit_exit.time, "time", lambda: quote_ms / 1000 + 1,
    )
    monkeypatch.setattr(
        realtime_profit_exit, "enforce_hard_stop",
        lambda *_args, **_kwargs: asyncio.sleep(0, result=False),
    )

    closed = asyncio.run(
        realtime_profit_exit.enforce_realtime_profit_exit(
            engine, "LOBSTER/USDT", 11.9, quote_ms,
        )
    )

    assert closed is True
    assert len(account.closed) == 1
    assert account.closed[0].endswith("EXIT_BY_TRUE_PEAK_EXTREME_REVERSAL")


def test_realtime_exit_retries_persisted_true_peak_without_market_frame(monkeypatch):
    from core.services.exits import realtime_profit_exit

    live_bar_ms = 1_800_000_000_000
    quote_ms = live_bar_ms + 2_000
    position = _position(live_bar_ms)
    frame = _frame(
        live_bar_ms,
        live_price=11.9,
        closed_candle={
            "open": 11.5, "high": 12.6, "low": 11.4, "close": 11.8,
        },
    )

    class Account:
        def __init__(self):
            self.positions = {"CAP/USDT": position}
            self.position_meta = {}
            self.close_results = [False, True]
            self.close_reasons = []

        def save_state(self):
            pass

        def log(self, *_args):
            pass

        async def close_position(self, symbol, price, reason, *, is_manual=False):
            assert automated_position_exit_allowed(
                symbol,
                self.positions[symbol],
                self.position_meta[symbol],
                reason,
                price,
                is_manual=is_manual,
            )
            self.close_reasons.append(reason)
            return self.close_results.pop(0)

    class Engine:
        is_running = True

    account = Account()
    engine = Engine()
    engine.account = account
    engine._channel_exit_frames = {"CAP/USDT": frame}
    monkeypatch.setattr(
        realtime_profit_exit.time, "time", lambda: quote_ms / 1000 + 1,
    )
    monkeypatch.setattr(
        realtime_profit_exit, "enforce_hard_stop",
        lambda *_args, **_kwargs: asyncio.sleep(0, result=False),
    )

    first = asyncio.run(
        realtime_profit_exit.enforce_realtime_profit_exit(
            engine, "CAP/USDT", 11.9, quote_ms,
        )
    )
    engine._channel_exit_frames = {}
    second = asyncio.run(
        realtime_profit_exit.enforce_realtime_profit_exit(
            engine, "CAP/USDT", 11.8, quote_ms + 1_000,
        )
    )

    assert first is True
    assert second is True
    assert len(account.close_reasons) == 2
    assert account.close_reasons[0] == account.close_reasons[1]


def test_retired_three_point_pivot_cannot_authorize_exit():
    position = {
        "side": "LONG",
        "entry_price": 10.0,
        "qty": 1.0,
        "open_timestamp": 1_800_000_000.0,
        "confirmed_true_pivot_exit_pending": {
            "reason": "EXIT_BY_CONFIRMED_TRUE_PEAK_PIVOT",
        },
    }

    assert not automated_position_exit_allowed(
        "LOBSTER/USDT",
        position,
        {},
        "CHANNEL_SWING EXIT_BY_CONFIRMED_TRUE_PEAK_PIVOT",
        11.0,
        is_manual=True,
    )
