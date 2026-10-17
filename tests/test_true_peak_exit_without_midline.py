import asyncio
import json

import pandas as pd
import pytest

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
                "open": 13.0, "high": 15.0, "low": 12.5, "close": 14.5,
                "ma5": 14.0, "ma15": 12.0, "kc_middle": 13.0,
                "is_closed": True,
            },
            {
                "timestamp": live_bar_ms - 120_000,
                "open": 13.9, "high": 14.8, "low": 13.0, "close": 14.0,
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
                "open": 10.0, "high": 11.0, "low": 9.0, "close": 10.5,
                "ma5": 10.0, "ma15": 11.5, "kc_middle": 11.0,
                "is_closed": True,
            },
            {
                "timestamp": live_bar_ms - 120_000,
                "open": 10.4, "high": 10.9, "low": 9.2, "close": 10.0,
                "ma5": 10.0, "ma15": 11.2, "kc_middle": 11.2,
                "is_closed": True,
            },
            {
                "timestamp": live_bar_ms - 60_000,
                "open": 9.6, "high": 10.9, "low": 9.4, "close": 10.6,
                "ma5": 10.2, "ma15": 11.0, "kc_middle": 11.0,
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
    assert frame.iloc[-2]["close"] < frame.iloc[-2]["kc_middle"]


def test_peak_requires_a_higher_high_and_a_reversal_confirmation():
    live_bar_ms = 1_800_000_000_000
    frame = _long_peak_frame(
        live_bar_ms,
        ma5_turning_down=False,
        bearish_confirm=False,
    )
    frame.loc[11, "high"] = 15.0

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


def test_market_structure_requires_fifteen_completed_bars_and_classifies_pullback():
    live_bar_ms = 1_800_000_000_000
    frame = _short_valley_frame(live_bar_ms)
    assert analyze_market_structure(frame, "SHORT") == "TRUE_TROUGH"
    assert analyze_market_structure(frame.iloc[:-2], "SHORT") == "HOLD"

    frame.loc[10, "low"] = 8.0
    assert analyze_market_structure(frame, "SHORT") == "PULLBACK"
    assert confirmed_true_pivot_exit(_short_position(live_bar_ms), frame) is None


@pytest.mark.parametrize("close_result", [True, False])
def test_confirmed_peak_closes_channel_position_without_kc_midline_gate(
    monkeypatch, close_result,
):
    from core.services.exits import realtime_profit_exit

    class Account:
        def __init__(self, position):
            self.positions = {"LOBSTER/USDT": position}
            self.position_meta = {}
            self.closed = []
            self.close_results = [close_result]
            if not close_result:
                self.close_results.append(True)
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
        lambda *_args: ({"live_bar_ms": live_bar_ms, "timeframe_ms": 60_000}, 1.0),
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

    assert closed is close_result
    assert len(account.closed) == 1
    assert "EXIT_BY_CONFIRMED_TRUE_PEAK_PIVOT" in account.closed[0][0][2]
    if not close_result:
        assert any("REALTIME_EXIT_RETRY_PENDING" in message for message in account.logs)
        retried = asyncio.run(
            realtime_profit_exit.enforce_realtime_profit_exit(
                engine, "LOBSTER/USDT", 14.0, quote_ms,
            )
        )
        assert retried is True
        assert len(account.closed) == 2


@pytest.mark.parametrize("close_result", [True, False])
def test_confirmed_valley_closes_channel_position_without_kc_midline_gate(
    monkeypatch, close_result,
):
    from core.services.exits import realtime_profit_exit

    class Account:
        def __init__(self, position):
            self.positions = {"LOBSTER/USDT": position}
            self.position_meta = {}
            self.closed = []
            self.close_results = [close_result]
            if not close_result:
                self.close_results.append(True)
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
        lambda *_args: ({"live_bar_ms": live_bar_ms, "timeframe_ms": 60_000}, 1.0),
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

    assert closed is close_result
    assert len(account.closed) == 1
    assert "EXIT_BY_CONFIRMED_TRUE_VALLEY_PIVOT" in account.closed[0][0][2]
    if not close_result:
        assert any("REALTIME_EXIT_RETRY_PENDING" in message for message in account.logs)
        retried = asyncio.run(
            realtime_profit_exit.enforce_realtime_profit_exit(
                engine, "LOBSTER/USDT", 10.6, quote_ms,
            )
        )
        assert retried is True
        assert len(account.closed) == 2


def test_failed_confirmed_peak_close_retries_after_new_bar_and_state_restore(
    monkeypatch,
):
    from core.services.exits import realtime_profit_exit

    class Account:
        def __init__(self, position, meta, close_results):
            self.positions = {"LOBSTER/USDT": position}
            self.position_meta = {"LOBSTER/USDT": meta}
            self.close_results = close_results
            self.closed = []
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
    first_account = Account(position, {}, [False])
    first_engine = Engine()
    first_engine.account = first_account
    first_engine._channel_exit_frames = {
        "LOBSTER/USDT": _long_peak_frame(
            live_bar_ms,
            ma5_turning_down=True,
            bearish_confirm=True,
        ),
    }
    clock_ms = [quote_ms]
    monkeypatch.setattr(
        realtime_profit_exit,
        "cached_tick_indicators",
        lambda *_args: ({"live_bar_ms": live_bar_ms, "timeframe_ms": 60_000}, 1.0),
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
        lambda: clock_ms[0] / 1_000,
    )

    first_result = asyncio.run(
        realtime_profit_exit.enforce_realtime_profit_exit(
            first_engine, "LOBSTER/USDT", 14.0, quote_ms,
        )
    )

    assert first_result is False
    pending_key = realtime_profit_exit.CONFIRMED_PIVOT_PENDING_KEY
    assert pending_key in position
    assert pending_key in first_account.position_meta["LOBSTER/USDT"]

    restored_position = json.loads(json.dumps(position))
    restored_meta = json.loads(
        json.dumps(first_account.position_meta["LOBSTER/USDT"])
    )
    restored_account = Account(restored_position, restored_meta, [True])
    restored_engine = Engine()
    restored_engine.account = restored_account
    restored_engine._channel_exit_frames = {}
    clock_ms[0] += 60_000

    retry_result = asyncio.run(
        realtime_profit_exit.enforce_realtime_profit_exit(
            restored_engine, "LOBSTER/USDT", 13.9, clock_ms[0],
        )
    )

    assert retry_result is True
    assert len(restored_account.closed) == 1
    assert "EXIT_BY_CONFIRMED_TRUE_PEAK_PIVOT" in restored_account.closed[0][0][2]
    assert any("REALTIME_EXIT_RETRY" in message for message in restored_account.logs)


def test_wild_swing_exit_replays_pending_pivot_after_json_restore(monkeypatch):
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
        "kc_upper": 16.0,
        "kc_lower": 8.0,
        "ma15": 13.0,
        "volume": 1.0,
    }.items():
        frame[column] = value

    first_decision = strategy.evaluate_exit(
        "LOBSTER/USDT", position, frame, quote=14.0,
    )

    assert first_decision is not None
    assert first_decision["reason"] == "EXIT_BY_CONFIRMED_TRUE_PEAK_PIVOT"
    restored_position = json.loads(json.dumps(position))

    retry_decision = strategy.evaluate_exit(
        "LOBSTER/USDT", restored_position, pd.DataFrame(), quote=13.9,
    )

    assert retry_decision is not None
    assert retry_decision["reason"] == first_decision["reason"]
    assert retry_decision["bar_id"] == first_decision["bar_id"]
