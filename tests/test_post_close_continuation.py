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
)
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
            "high": 101.0,
            "low": 99.0,
            "close": 99.9,
            "atr": 1.0,
            "ma5": 100.0,
            "ma15": 100.2,
            "kc_lower": 97.0,
            "kc_middle": 100.0,
            "kc_upper": 103.0,
            "is_closed": True,
        })
    rows.append({
        "timestamp": stamp,
        "open": 97.5,
        "high": 97.6,
        "low": 96.8,
        "close": 97.0,
        "atr": 1.0,
        "ma5": 97.4,
        "ma15": 97.2,
        "kc_lower": 96.0,
        "kc_middle": 99.0,
        "kc_upper": 102.0,
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
            "reason": "Channel Swing PROFIT_PROTECTION test",
        }],
    ), close_id


def test_successful_long_close_allows_next_live_bullish_ma5_reclaim():
    frame = live_frame()
    account, close_id = close_account(frame)

    decision = evaluate_entry_contract(
        frame, 103.1, account=account, symbol="CAP/USDT",
    )

    assert decision is not None
    assert decision["type"] == "TRIGGER_C_CONTINUATION"
    assert decision["side"] == "LONG"
    assert decision["entry_phase"] == "POST_CLOSE_CONTINUATION_ENTRY"
    assert decision["post_close_continuation_close_id"] == close_id


def test_successful_short_close_allows_next_live_bearish_ma5_reclaim():
    frame = short_live_frame()
    account, close_id = close_account(
        frame, side="SHORT", symbol="龙虾/USDT",
    )

    decision = evaluate_entry_contract(
        frame, 97.0, account=account, symbol="龙虾/USDT",
    )

    assert decision is not None
    assert decision["type"] == "TRIGGER_C_CONTINUATION"
    assert decision["side"] == "SHORT"
    assert decision["entry_phase"] == "POST_CLOSE_CONTINUATION_ENTRY"
    assert decision["reason"] == "POST_CLOSE_BEARISH_CONTINUATION"
    assert decision["post_close_continuation_close_id"] == close_id


def test_short_post_close_continuation_passes_fresh_account_revalidation():
    async def run():
        frame = short_live_frame()
        frame.attrs["entry_finality_verified"] = True
        account, _ = close_account(frame, side="SHORT")
        account.entry_frame_provider = AsyncMock(return_value=frame)
        decision = evaluate_entry_contract(
            frame, 97.0, account=account, symbol="CAP/USDT",
        )
        assert decision is not None
        signal_id = decision["pending_signal_id"]
        bar_id = decision["confirmation_bar_id"]
        context = {
            "entry_signal_code": "TRIGGER_C_CONTINUATION",
            "signal_id": signal_id,
            "candidate_bar_id": bar_id,
            "channel_confirmation_bar_id": bar_id,
            "entry_snapshot": {
                "symbol": "CAP/USDT",
                "side": "SHORT",
                "signal_code": "TRIGGER_C_CONTINUATION",
                "signal_id": signal_id,
                "candidate_bar_id": bar_id,
                "closed_bar": bar_id,
                "pending_signal_id": signal_id,
            },
        }

        validated = await validate_account_entry(
            account, "CAP/USDT", "SHORT", context,
        )

        assert validated["side"] == "SHORT"
        assert validated["type"] == "TRIGGER_C_CONTINUATION"
        account.entry_frame_provider.assert_awaited_once_with("CAP/USDT")

    asyncio.run(run())


def test_lobster_runner_sends_post_short_close_continuation_candidate():
    async def run():
        from core.services.symbol_runner import process_single_symbol_runner

        symbol = "龙虾/USDT"
        frame = short_live_frame()
        account, _ = close_account(frame, side="SHORT", symbol=symbol)
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

        engine._execute_confirmed_channel_break.assert_awaited_once()
        args = engine._execute_confirmed_channel_break.await_args
        assert args.args[0] == symbol
        assert args.args[3] == "SHORT"
        assert args.kwargs["v8_reason"] == "TRIGGER_C_CONTINUATION"

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
