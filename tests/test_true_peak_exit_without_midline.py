import asyncio
import json

import pandas as pd
import pytest

from core.services.exits.authorized_position_exit import (
    automated_position_exit_allowed,
)
from core.services.exits.strict_true_pivot import (
    analyze_market_structure,
    confirmed_true_pivot_exit,
)


def _frame(rows, live_bar_ms):
    frame = pd.DataFrame(rows)
    frame.attrs["timeframe_ms"] = 60_000
    frame.attrs["snapshot_ms"] = live_bar_ms + 2_000
    return frame


def _long_peak_frame(live_bar_ms, *, ma5_turning_down, bearish_confirm):
    rows = [
        {
            "timestamp": live_bar_ms - (900_000 - index * 60_000),
            "open": 10.0, "high": 11.0, "low": 9.0, "close": 10.0,
            "ma5": 10.0, "ma15": 9.5, "kc_middle": 10.0,
            "is_closed": True,
        }
        for index in range(10)
    ]
    confirm_open = 14.0 if bearish_confirm else 13.7
    confirm_close = 12.0 if bearish_confirm else 14.1
    confirm_low = 11.8 if bearish_confirm else 13.0
    return _frame(
        rows + [
            {
                "timestamp": live_bar_ms - 300_000,
                "open": 11.0, "high": 12.0, "low": 10.5, "close": 11.5,
                "ma5": 11.4, "ma15": 10.0, "kc_middle": 12.0,
                "is_closed": True,
            },
            {
                "timestamp": live_bar_ms - 240_000,
                "open": 12.0, "high": 13.0, "low": 11.5, "close": 12.5,
                "ma5": 13.8, "ma15": 11.0, "kc_middle": 12.5,
                "is_closed": True,
            },
            {
                "timestamp": live_bar_ms - 180_000,
                "open": 13.0, "high": 14.5, "low": 12.5, "close": 14.2,
                "ma5": 14.0, "ma15": 12.0, "kc_middle": 13.0,
                "is_closed": True,
            },
            {
                "timestamp": live_bar_ms - 120_000,
                "open": 13.9, "high": 15.0, "low": 13.0, "close": 14.0,
                "ma5": 14.0, "ma15": 12.5, "kc_middle": 13.5,
                "is_closed": True,
            },
            {
                "timestamp": live_bar_ms - 60_000,
                "open": confirm_open, "high": 14.2 if bearish_confirm else 14.5,
                "low": confirm_low, "close": confirm_close,
                "ma5": 13.8 if ma5_turning_down else 14.2,
                "ma15": 13.0, "kc_middle": 13.0, "is_closed": True,
            },
            {
                "timestamp": live_bar_ms,
                "open": 14.1, "high": 14.3, "low": 13.9, "close": 14.0,
                "ma5": 14.0, "ma15": 13.0, "kc_middle": 13.0,
                "is_closed": False,
            },
        ],
        live_bar_ms,
    )


def _long_position(live_bar_ms):
    return {
        "side": "LONG",
        "entry_price": 12.0,
        "qty": 1.0,
        "open_timestamp": (live_bar_ms - 299_000) / 1_000,
    }


def _short_valley_frame(live_bar_ms):
    return _frame(
        [
            *[
                {
                    "timestamp": live_bar_ms - (900_000 - index * 60_000),
                    "open": 13.0, "high": 15.0, "low": 12.0, "close": 13.0,
                    "ma5": 13.0, "ma15": 13.5, "kc_middle": 12.0,
                    "is_closed": True,
                }
                for index in range(10)
            ],
            {
                "timestamp": live_bar_ms - 300_000,
                "open": 13.0, "high": 14.0, "low": 12.0, "close": 13.0,
                "ma5": 13.0, "ma15": 13.5, "kc_middle": 12.0,
                "is_closed": True,
            },
            {
                "timestamp": live_bar_ms - 240_000,
                "open": 11.5, "high": 12.0, "low": 11.0, "close": 11.5,
                "ma5": 11.0, "ma15": 12.0, "kc_middle": 11.5,
                "is_closed": True,
            },
            {
                "timestamp": live_bar_ms - 180_000,
                "open": 10.0, "high": 11.0, "low": 9.0, "close": 10.2,
                "ma5": 10.0, "ma15": 11.5, "kc_middle": 11.0,
                "is_closed": True,
            },
            {
                "timestamp": live_bar_ms - 120_000,
                "open": 10.4, "high": 10.9, "low": 7.4, "close": 8.0,
                "ma5": 10.0, "ma15": 11.2, "kc_middle": 11.0,
                "is_closed": True,
            },
            {
                "timestamp": live_bar_ms - 60_000,
                "open": 8.4, "high": 10.9, "low": 8.0, "close": 10.6,
                "ma5": 10.2, "ma15": 11.0, "kc_middle": 11.2,
                "is_closed": True,
            },
            {
                "timestamp": live_bar_ms,
                "open": 10.5, "high": 10.7, "low": 10.3, "close": 10.6,
                "ma5": 10.5, "ma15": 11.0, "kc_middle": 10.8,
                "is_closed": False,
            },
        ],
        live_bar_ms,
    )


def _short_position(live_bar_ms):
    position = _long_position(live_bar_ms)
    position.update(side="SHORT", entry_price=15.0)
    return position


def test_confirmed_peak_requires_body_break_and_ma5_turn_without_kc_middle():
    live_bar_ms = 1_800_000_000_000
    frame = _long_peak_frame(
        live_bar_ms,
        ma5_turning_down=True,
        bearish_confirm=True,
    )

    decision = confirmed_true_pivot_exit(
        _long_position(live_bar_ms), frame,
    )

    assert decision is not None
    reason, details = decision
    assert reason == "EXIT_BY_CONFIRMED_TRUE_PEAK_PIVOT"
    assert details["pivot_high"] == 15.0
    assert details["pivot_bar_id"] == live_bar_ms - 120_000
    assert details["confirm_bar_id"] == live_bar_ms - 60_000
    assert frame.iloc[-2]["close"] < frame.iloc[-2]["kc_middle"]


def test_peak_requires_a_higher_high_and_a_reversal_confirmation():
    live_bar_ms = 1_800_000_000_000
    frame = _long_peak_frame(
        live_bar_ms,
        ma5_turning_down=False,
        bearish_confirm=False,
    )
    frame.loc[12, "high"] = 15.0

    assert confirmed_true_pivot_exit(
        _long_position(live_bar_ms), frame,
    ) is None


def test_confirm_bar_must_be_closed_before_pivot_exit():
    live_bar_ms = 1_800_000_000_000
    frame = _long_peak_frame(
        live_bar_ms,
        ma5_turning_down=True,
        bearish_confirm=False,
    )
    frame.loc[14, "is_closed"] = False

    assert confirmed_true_pivot_exit(
        _long_position(live_bar_ms), frame,
    ) is None


def test_confirmed_valley_exits_without_waiting_for_kc_middle():
    live_bar_ms = 1_800_000_000_000
    decision = confirmed_true_pivot_exit(
        _short_position(live_bar_ms), _short_valley_frame(live_bar_ms),
    )

    assert decision is not None
    assert decision[0] == "EXIT_BY_CONFIRMED_TRUE_VALLEY_PIVOT"
    assert _short_valley_frame(live_bar_ms).iloc[-2]["close"] < (
        _short_valley_frame(live_bar_ms).iloc[-2]["kc_middle"]
    )


def test_three_point_valley_uses_authorized_kc_exit_ticket():
    from core.services.exits.kc_true_extreme_exit import (
        evaluate_kc_true_extreme_exit,
    )

    live_bar_ms = 1_800_000_000_000
    position = _short_position(live_bar_ms)
    frame = _short_valley_frame(live_bar_ms)

    decision, changed = evaluate_kc_true_extreme_exit(
        position, frame, 10.6, live_bar_ms + 2_000,
    )

    assert changed is True
    assert decision is not None
    assert decision[0] == "EXIT_BY_TRUE_VALLEY_EXTREME_REVERSAL"
    assert decision[1]["cause"] == "THREE_POINT_CONFIRMED_PIVOT"
    assert decision[1]["pivot_bar_id"] == live_bar_ms - 120_000
    assert decision[1]["confirm_bar_id"] == live_bar_ms - 60_000
    assert automated_position_exit_allowed(
        "LOBSTER/USDT",
        position,
        {},
        f"WILD_SWING {decision[0]}",
        10.6,
    )


def test_three_point_valley_does_not_exit_while_kc_middle_falls():
    from core.services.exits.kc_true_extreme_exit import (
        evaluate_kc_true_extreme_exit,
    )

    live_bar_ms = 1_800_000_000_000
    position = _short_position(live_bar_ms)
    frame = _short_valley_frame(live_bar_ms)
    frame.loc[frame.index[-3], "kc_middle"] = 11.2
    frame.loc[frame.index[-2], "kc_middle"] = 11.0

    decision, _ = evaluate_kc_true_extreme_exit(
        position, frame, 10.6, live_bar_ms + 2_000,
    )

    assert decision is None
    assert position["kc_true_extreme_exit_state"]["pending"] is None


def test_market_structure_requires_fifteen_completed_bars_and_classifies_pullback():
    live_bar_ms = 1_800_000_000_000
    frame = _short_valley_frame(live_bar_ms)
    assert analyze_market_structure(frame, "SHORT") == "TRUE_TROUGH"
    assert analyze_market_structure(frame.iloc[:-2], "SHORT") == "HOLD"

    frame.loc[14, "ma5"] = 9.8
    assert analyze_market_structure(frame, "SHORT") == "PULLBACK"
    assert confirmed_true_pivot_exit(_short_position(live_bar_ms), frame) is None


def test_three_point_peak_authorizes_realtime_exit(monkeypatch):
    from core.services.exits import realtime_profit_exit

    class Account:
        def __init__(self, position):
            self.positions = {"LOBSTER/USDT": position}
            self.position_meta = {}
            self.closed = []
            self.close_results = [True]
            self.logs = []

        def log(self, message, *_args):
            self.logs.append(message)

        def save_state(self):
            pass

        async def close_position(self, *args, **kwargs):
            self.closed.append((args, kwargs))
            return self.close_results.pop(0)

    class Engine:
        is_running = True

    live_bar_ms = 1_800_000_000_000
    quote_ms = live_bar_ms + 2_000
    position = _long_position(live_bar_ms)
    position["entry_mode"] = "CHANNEL_SWING"
    account = Account(position)
    engine = Engine()
    engine.account = account
    engine._channel_exit_frames = {
        "LOBSTER/USDT": _long_peak_frame(
            live_bar_ms,
            ma5_turning_down=True,
            bearish_confirm=True,
        ),
    }
    monkeypatch.setattr(
        realtime_profit_exit,
        "cached_tick_indicators",
        lambda *_args: ({
            "live_bar_ms": live_bar_ms,
            "timeframe_ms": 60_000,
            "ma5_history": [10.0, 10.0, 10.0],
            "ma15_history": [9.0, 9.0, 9.0],
        }, 1.0),
    )
    monkeypatch.setattr(
        realtime_profit_exit,
        "enforce_hard_stop",
        lambda *_args: asyncio.sleep(0, result=False),
    )
    monkeypatch.setattr(
        realtime_profit_exit,
        "_evaluate_profit_lock_only",
        lambda *_args: (None, False),
    )
    monkeypatch.setattr(
        realtime_profit_exit.time,
        "time",
        lambda: quote_ms / 1_000,
    )

    closed = asyncio.run(
        realtime_profit_exit.enforce_realtime_profit_exit(
            engine, "LOBSTER/USDT", 14.0, quote_ms,
        )
    )

    assert closed is True
    assert len(account.closed) == 1
    assert "EXIT_BY_TRUE_PEAK_EXTREME_REVERSAL" in account.closed[0][0][2]


def test_three_point_valley_authorizes_realtime_exit(monkeypatch):
    from core.services.exits import realtime_profit_exit

    class Account:
        def __init__(self, position):
            self.positions = {"LOBSTER/USDT": position}
            self.position_meta = {}
            self.closed = []
            self.close_results = [True]
            self.logs = []

        def log(self, message, *_args):
            self.logs.append(message)

        def save_state(self):
            pass

        async def close_position(self, *args, **kwargs):
            self.closed.append((args, kwargs))
            return self.close_results.pop(0)

    class Engine:
        is_running = True

    live_bar_ms = 1_800_000_000_000
    quote_ms = live_bar_ms + 2_000
    position = _short_position(live_bar_ms)
    position["entry_mode"] = "CHANNEL_SWING"
    account = Account(position)
    engine = Engine()
    engine.account = account
    engine._channel_exit_frames = {
        "LOBSTER/USDT": _short_valley_frame(live_bar_ms),
    }
    monkeypatch.setattr(
        realtime_profit_exit,
        "cached_tick_indicators",
        lambda *_args: ({
            "live_bar_ms": live_bar_ms,
            "timeframe_ms": 60_000,
            "ma5_history": [10.0, 10.0, 10.0],
            "ma15_history": [9.0, 9.0, 9.0],
        }, 1.0),
    )
    monkeypatch.setattr(
        realtime_profit_exit,
        "enforce_hard_stop",
        lambda *_args: asyncio.sleep(0, result=False),
    )
    monkeypatch.setattr(
        realtime_profit_exit,
        "_evaluate_profit_lock_only",
        lambda *_args: (None, False),
    )
    monkeypatch.setattr(
        realtime_profit_exit.time,
        "time",
        lambda: quote_ms / 1_000,
    )

    closed = asyncio.run(
        realtime_profit_exit.enforce_realtime_profit_exit(
            engine, "LOBSTER/USDT", 10.6, quote_ms,
        )
    )

    assert closed is True
    assert len(account.closed) == 1
    assert "EXIT_BY_TRUE_VALLEY_EXTREME_REVERSAL" in account.closed[0][0][2]


def test_wild_swing_exit_replays_kc_reversal_after_json_restore(monkeypatch):
    from core.services.strategies import defensive_trend_strategy
    from core.services.strategies.wild_swing_strategy import WildSwingStrategy

    monkeypatch.setattr(
        defensive_trend_strategy,
        "check_long_exit",
        lambda *_args, **_kwargs: (False, None, {}),
    )
    strategy = WildSwingStrategy()
    live_bar_ms = 1_800_000_000_000
    position = _long_position(live_bar_ms)
    position["entry_mode"] = "WILD_SWING"
    frame = _long_peak_frame(
        live_bar_ms,
        ma5_turning_down=True,
        bearish_confirm=True,
    )
    for column, value in {
        "kc_upper": 12.0,
        "kc_middle": 10.0,
        "kc_lower": 8.0,
        "ma15": 13.0,
        "atr": 1.0,
        "volume": 1.0,
    }.items():
        frame[column] = value
    frame.iloc[-2, frame.columns.get_loc("open")] = 11.5
    frame.iloc[-2, frame.columns.get_loc("high")] = 12.6
    frame.iloc[-2, frame.columns.get_loc("low")] = 11.4
    frame.iloc[-2, frame.columns.get_loc("close")] = 11.8
    frame.iloc[-1, frame.columns.get_loc("open")] = 11.0
    frame.iloc[-1, frame.columns.get_loc("high")] = 11.9
    frame.iloc[-1, frame.columns.get_loc("low")] = 11.0
    frame.iloc[-1, frame.columns.get_loc("close")] = 11.9
    frame.attrs["snapshot_ms"] = live_bar_ms + 2_000

    first_decision = strategy.evaluate_exit(
        "LOBSTER/USDT", position, frame, quote=11.9,
    )

    assert first_decision is not None
    assert first_decision["reason"] == "EXIT_BY_TRUE_PEAK_EXTREME_REVERSAL"
    restored_position = json.loads(json.dumps(position))

    retry_decision = strategy.evaluate_exit(
        "LOBSTER/USDT", restored_position, pd.DataFrame(), quote=13.9,
    )

    assert retry_decision is not None
    assert retry_decision["reason"] == first_decision["reason"]
    assert retry_decision["bar_id"] == first_decision["bar_id"]
