import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pandas as pd
import pytest

from core.engine import TradingEngine
from core.services.entry_contract import (
    evaluate_continuation_entry,
    evaluate_entry_contract,
    entry_direction_problem,
)
from core.services import entry_contract
from core.services.entry_firewall import validate_account_entry


def live_frame(*, upper=103.0, ma15=102.4):
    stamp = int(time.time() // 60) * 60_000
    rows = []
    for offset in (180_000, 120_000, 60_000):
        rows.append({
            "timestamp": stamp - offset,
            "open": 100.0,
            "high": 101.0,
            "low": 99.0,
            "close": 100.1,
            "atr": 1.0,
            "ma5": 100.0,
            "ma15": 100.2,
            "kc_lower": 98.0,
            "kc_middle": 100.0,
            "kc_upper": upper,
            "is_closed": True,
        })
    rows.append({
        "timestamp": stamp,
        "open": 102.5,
        "high": 103.2,
        "low": 102.4,
        "close": 102.8,
        "atr": 1.0,
        "ma5": 102.6,
        "ma15": ma15,
        "kc_lower": 99.0,
        "kc_middle": 101.0,
        "kc_upper": upper,
        "is_closed": False,
    })
    frame = pd.DataFrame(rows)
    frame.attrs["timeframe_ms"] = 60_000
    return frame


def short_live_frame():
    stamp = int(time.time() // 60) * 60_000
    rows = []
    for offset in (180_000, 120_000, 60_000):
        rows.append({
            "timestamp": stamp - offset,
            "open": 100.0,
            "high": 100.2,
            "low": 99.8,
            "close": 99.9,
            "atr": 1.0,
            "ma3": 100.0,
            "ma5": 100.0,
            "ma15": 100.2,
            "kc_lower": 97.2 - (180_000 - offset) / 600_000,
            "kc_middle": 100.0 - (180_000 - offset) / 600_000,
            "kc_upper": 103.0 - (180_000 - offset) / 600_000,
            "is_closed": True,
        })
    rows.append({
        "timestamp": stamp,
        "open": 97.5,
        "high": 97.6,
        "low": 96.3,
        "close": 97.0,
        "atr": 1.0,
        "ma3": 97.4,
        "ma5": 97.4,
        "ma15": 97.2,
        "kc_lower": 96.9,
        "kc_middle": 99.7,
        "kc_upper": 102.7,
        "is_closed": False,
    })
    frame = pd.DataFrame(rows)
    frame.attrs["timeframe_ms"] = 60_000
    return frame


def close_account(
    frame, *, side="LONG", symbol="CAP/USDT",
    status="CLOSED", close_offset_ms=1_000,
):
    stamp = float(frame.iloc[-1]["timestamp"])
    close_id = stamp - close_offset_ms
    return SimpleNamespace(
        positions={},
        trades=[{
            "id": close_id,
            "symbol": symbol,
            "action": f"CLOSE_{side}",
            "side": side,
            "status": status,
            "price": 97.1 if side == "SHORT" else 103.0,
            "reason": "Channel Swing PROFIT_PROTECTION test",
        }],
    ), close_id


def test_post_close_long_reclaim_without_completed_rail_break_is_rejected():
    frame = live_frame()
    account, _ = close_account(frame)
    diagnostics = {}

    decision = evaluate_entry_contract(
        frame, 103.1, account=account, symbol="CAP/USDT", diagnostics=diagnostics,
    )

    assert decision is None
    assert diagnostics["reason"] == "BLOCKED_THREE_BAR_LONG_NOT_BROKEN_UPPER_RAIL"


def test_post_close_short_reclaim_without_completed_rail_break_is_rejected():
    frame = short_live_frame()
    account, _ = close_account(
        frame, side="SHORT", symbol="龙虾/USDT",
    )
    diagnostics = {}

    decision = evaluate_entry_contract(
        frame, 97.0, account=account, symbol="龙虾/USDT", diagnostics=diagnostics,
    )

    assert decision is None
    assert diagnostics["reason"] == "BLOCKED_INSIDE_KC_BANDS"


@pytest.mark.parametrize(
    ("side", "quote", "prior_ma5", "current_ma5", "expected_reason"),
    [
        ("LONG", 103.1, 102.7, 102.6, "BLOCKED_BY_MA5_DOWNWARD_SLOPE"),
        ("LONG", 103.1, 102.66, 102.6, "BLOCKED_BY_MA5_DOWNWARD_SLOPE"),
        ("LONG", 102.4, 100.0, 102.6, "BLOCKED_BY_BEARISH_CANDLE"),
        ("LONG", 102.7, 100.0, 102.8, "BLOCKED_BY_BEARISH_CANDLE"),
        ("SHORT", 97.1, 97.3, 97.4, "BLOCKED_BY_MA5_UPWARD_SLOPE"),
        ("SHORT", 97.1, 97.42, 97.4, "BLOCKED_BY_MA5_UPWARD_SLOPE"),
        ("SHORT", 97.6, 100.0, 97.4, "BLOCKED_BY_BULLISH_CANDLE"),
        ("SHORT", 97.4, 100.0, 97.0, "BLOCKED_BY_BULLISH_CANDLE"),
    ],
)
def test_post_close_continuation_hard_blocks_wrong_ma5_or_candle(
    side, quote, prior_ma5, current_ma5, expected_reason,
):
    frame = live_frame() if side == "LONG" else short_live_frame()
    frame.loc[frame.index[-2], "ma5"] = prior_ma5
    frame.loc[frame.index[-1], "ma5"] = current_ma5
    account, _ = close_account(frame, side=side)
    diagnostics = {}

    decision = evaluate_entry_contract(
        frame, quote, account=account, symbol="CAP/USDT", diagnostics=diagnostics,
    )

    assert decision is None
    assert diagnostics["reason"] == expected_reason


@pytest.mark.parametrize(
    ("side", "opening", "close", "ma5", "previous_ma5", "expected"),
    [
        ("LONG", 100, 102, 101, 100, None),
        ("LONG", 100, 102, 101, 101, "BLOCKED_BY_MA5_DOWNWARD_SLOPE"),
        ("LONG", 100, 102, 101, 102, "BLOCKED_BY_MA5_DOWNWARD_SLOPE"),
        ("LONG", 102, 101, 100, 99, "BLOCKED_BY_BEARISH_CANDLE"),
        ("LONG", 100, 101, 101, 99, "BLOCKED_BY_BEARISH_CANDLE"),
        ("SHORT", 102, 100, 101, 102, None),
        ("SHORT", 102, 100, 101, 101, "BLOCKED_BY_MA5_UPWARD_SLOPE"),
        ("SHORT", 98, 99, 100, 101, "BLOCKED_BY_BULLISH_CANDLE"),
    ],
)
def test_hard_direction_firewall_is_shared_and_strict(
    side, opening, close, ma5, previous_ma5, expected,
):
    stamp = int(time.time() // 60) * 60_000
    frame = pd.DataFrame([
        dict(timestamp=stamp - 60_000, is_closed=True, open=100, high=103,
             low=98, close=100, ma5=previous_ma5, atr=1),
        dict(timestamp=stamp, is_closed=True, open=opening, high=max(opening, close),
             low=min(opening, close), close=close, ma5=ma5, atr=1),
    ])
    frame.attrs["timeframe_ms"] = 60_000

    assert entry_direction_problem(frame, close, side) == expected


@pytest.mark.parametrize(
    ("opening", "close", "ma5", "previous_ma5", "expected"),
    [
        (100, 102, 101, 101, "BLOCKED_BY_MA5_DOWNWARD_SLOPE"),
        (102, 101, 100, 99, "BLOCKED_BY_BEARISH_CANDLE"),
    ],
)
def test_regular_breakout_candidate_is_checked_by_shared_firewall(
    monkeypatch, opening, close, ma5, previous_ma5, expected,
):
    stamp = int(time.time() // 60) * 60_000
    frame = pd.DataFrame([
        dict(timestamp=stamp - 120_000, is_closed=True, open=99, high=100,
             low=98, close=99.5, ma5=98, atr=1),
        dict(timestamp=stamp - 60_000, is_closed=True, open=99, high=101,
             low=98, close=100, ma5=previous_ma5, atr=1),
        dict(timestamp=stamp, is_closed=False, open=opening, high=max(opening, close),
             low=min(opening, close), close=close, ma5=ma5, atr=1),
    ])
    frame.attrs["timeframe_ms"] = 60_000
    monkeypatch.setattr(entry_contract, "evaluate_golden_cross_fast_lane", lambda *_a, **_k: None)
    monkeypatch.setattr(entry_contract, "evaluate_bearish_instant_breakout", lambda *_a, **_k: None)
    monkeypatch.setattr(entry_contract, "evaluate_continuation_entry", lambda *_a, **_k: None)
    monkeypatch.setattr(entry_contract, "detect_raw_triggers", lambda *_a, **_k: ("LONG", "TRIGGER_A_KC_BREAKOUT"))
    monkeypatch.setattr(entry_contract, "three_bar_rail_gate_problem", lambda *_a, **_k: None)
    monkeypatch.setattr(entry_contract, "evaluate_three_bar_outer_breakout", lambda *_a, **_k: dict(
        action="ENTER", side="LONG", type="TRIGGER_A_KC_BREAKOUT",
        entry_phase="KC_THREE_BAR_BREAKOUT",
    ))
    monkeypatch.setattr(entry_contract, "check_entry_gates", lambda *_a, **_k: (True, "PASSED"))
    monkeypatch.setattr(entry_contract, "excessive_upper_shadow_problem", lambda *_a, **_k: None)
    diagnostics = {}

    assert evaluate_entry_contract(frame, diagnostics=diagnostics) is None
    assert diagnostics["reason"] == expected


def test_short_post_close_continuation_without_rail_break_is_rejected_before_revalidation():
    async def run():
        frame = short_live_frame()
        frame.attrs["entry_finality_verified"] = True
        account, _ = close_account(frame, side="SHORT")
        account.entry_frame_provider = AsyncMock(return_value=frame)
        decision = evaluate_entry_contract(
            frame, 97.0, account=account, symbol="CAP/USDT", diagnostics={},
        )
        assert decision is None
        account.entry_frame_provider.assert_not_awaited()

    asyncio.run(run())


def test_lobster_runner_does_not_send_post_short_close_without_rail_break():
    async def run():
        from core.services.symbol_runner import process_single_symbol_runner

        symbol = "龙虾/USDT"
        frame = short_live_frame()
        account, _ = close_account(frame, side="SHORT", symbol=symbol)
        account.log = Mock()
        engine = SimpleNamespace(
            account=account,
            strategy=SimpleNamespace(),
            tickers={symbol: 97.0},
            _execute_confirmed_channel_break=AsyncMock(return_value=True),
        )

        await process_single_symbol_runner(
            engine, symbol, time.time(), None, False,
            exit_frame=frame, exit_quote=97.0,
        )

        engine._execute_confirmed_channel_break.assert_not_awaited()

    asyncio.run(run())


@pytest.mark.parametrize(
    ("quote", "expected"),
    [(97.5, False), (97.4, False), (97.1, True)],
)
def test_post_short_close_continuation_checks_live_weakness(quote, expected):
    frame = short_live_frame()
    account, _ = close_account(frame, side="SHORT")

    decision = evaluate_continuation_entry(
        frame, quote, code="TRIGGER_C_CONTINUATION",
        symbol="CAP/USDT", account=account,
    )

    assert (decision is not None) is expected


@pytest.mark.parametrize(
    ("quote", "upper", "ma15", "expected"),
    [
        (102.4, 103.0, 102.4, False),  # not bullish / no MA5 reclaim
        (102.54, 104.0, 103.0, False),  # remains below MA5
        (103.0, 104.0, 102.4, True),   # above MA15 even inside the rail
    ],
)
def test_post_close_continuation_checks_live_strength(quote, upper, ma15, expected):
    frame = live_frame(upper=upper, ma15=ma15)
    account, _ = close_account(frame)

    decision = evaluate_continuation_entry(
        frame, quote, code="TRIGGER_C_CONTINUATION",
        symbol="CAP/USDT", account=account,
    )

    assert (decision is not None) is expected


@pytest.mark.parametrize(
    ("status", "close_offset_ms"),
    [("FAILED", 1_000), ("CLOSED", 61_000)],
)
def test_post_close_continuation_requires_filled_close_in_immediately_prior_bar(
    status, close_offset_ms,
):
    frame = live_frame()
    account, _ = close_account(
        frame, status=status, close_offset_ms=close_offset_ms,
    )

    assert evaluate_entry_contract(
        frame, 103.1, account=account, symbol="CAP/USDT",
    ) is None


def test_ticketed_profit_reentry_tries_same_side_continuation_before_old_cooldown_gate():
    async def run():
        frame = live_frame()
        account, close_id = close_account(frame)
        token = "profit-lock-token"
        reason = f"Channel Swing PROFIT_PROTECTION {token}"
        account.trades[0]["reason"] = reason
        ticket = {
            "phase": "closed",
            "side": "LONG",
            "old_side": "LONG",
            "mode": "outer_cycle",
            "token": token,
            "close_reason": reason,
            "close_requested_at_ms": close_id,
            "exit_bar_id": float(frame.iloc[-2]["timestamp"]),
        }
        account.channel_profit_reentries = {"CAP/USDT": ticket}
        account.save_state = Mock()
        account.log = Mock()
        engine = TradingEngine.__new__(TradingEngine)
        engine.account = account
        engine._place_structured_entry = AsyncMock(return_value=True)
        engine._profit_reentry_ready = Mock(
            side_effect=AssertionError("cooldown gate must not own TRIGGER_C"),
        )

        await engine._try_profit_reentry_locked(
            "CAP/USDT", frame, 103.1, daily_halt=False,
        )

        engine._place_structured_entry.assert_awaited_once()
        signal = engine._place_structured_entry.await_args.args[1]
        assert signal["signal_code"] == "TRIGGER_C_CONTINUATION"
        assert "CAP/USDT" not in account.channel_profit_reentries
        engine._profit_reentry_ready.assert_not_called()

    asyncio.run(run())


def test_ticketed_short_profit_reentry_uses_same_side_continuation_before_cooldown():
    async def run():
        frame = short_live_frame()
        symbol = "龙虾/USDT"
        account, close_id = close_account(
            frame, side="SHORT", symbol=symbol,
        )
        token = "short-profit-lock-token"
        reason = f"Channel Swing PROFIT_PROTECTION {token}"
        account.trades[0]["reason"] = reason
        ticket = {
            "phase": "closed",
            "side": "SHORT",
            "old_side": "SHORT",
            "mode": "outer_cycle",
            "token": token,
            "close_reason": reason,
            "close_requested_at_ms": close_id,
            "exit_bar_id": float(frame.iloc[-2]["timestamp"]),
        }
        account.channel_profit_reentries = {symbol: ticket}
        account.save_state = Mock()
        account.log = Mock()
        engine = TradingEngine.__new__(TradingEngine)
        engine.account = account
        engine._place_structured_entry = AsyncMock(return_value=True)
        engine._profit_reentry_ready = Mock(
            side_effect=AssertionError("cooldown gate must not own post-close TRIGGER_C"),
        )

        await engine._try_profit_reentry_locked(
            symbol, frame, 97.0, daily_halt=False,
        )

        engine._place_structured_entry.assert_awaited_once()
        signal = engine._place_structured_entry.await_args.args[1]
        assert signal["side"] == "SHORT"
        assert signal["signal_code"] == "TRIGGER_C_CONTINUATION"
        assert "POST_CLOSE_BEARISH_CONTINUATION" in signal["reason"]
        assert symbol not in account.channel_profit_reentries
        engine._profit_reentry_ready.assert_not_called()

    asyncio.run(run())
