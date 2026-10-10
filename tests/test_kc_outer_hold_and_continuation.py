import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from core.engine import TradingEngine
from core.services.entry_contract import (
    BEARISH_INSTANT_BREAKOUT_CODE,
    evaluate_bearish_instant_breakout,
    evaluate_entry_contract,
)
from core.services.exits.peak_trailing_exit import (
    ABNORMAL_REASON,
    evaluate_peak_trailing,
)
from core.services.entry_firewall import validate_account_entry
from test_breakout_only_entry import breakout_frame
from test_kc_golden_cross_and_doji_exit import exit_position, reversal_snapshot


def expanding_live_frame(side):
    frame = breakout_frame(side)
    live = frame.index[-1]
    previous = frame.iloc[-2]
    middle = (float(previous["kc_upper"]) + float(previous["kc_lower"])) / 2
    upper, lower = middle + 1.4, middle - 1.4
    if side == "LONG":
        opening, close, quote, ma5 = upper - 0.6, upper - 0.5, upper + 0.2, upper - 0.1
        high, low = close, opening
    else:
        opening, close, quote, ma5 = lower + 0.6, lower + 0.5, lower - 0.2, lower + 0.1
        high, low = opening, close
    frame.loc[live, ["open", "close", "high", "low", "kc_upper",
                     "kc_lower", "ma5"]] = [
        opening, close, high, low, upper, lower, ma5,
    ]
    return frame, quote


def bearish_instant_frame():
    frame = breakout_frame("LONG")
    live = frame.index[-1]
    frame.loc[live, ["open", "close", "high", "low", "kc_upper",
                     "kc_middle", "kc_lower", "ma5"]] = [
        100.5, 99.4, 100.5, 99.4, 101.5, 100.0, 99.0, 99.8,
    ]
    stamp = float(frame.iloc[-1]["timestamp"])
    quote = 98.4
    account = SimpleNamespace(
        positions={},
        trades=[dict(
            id=int(time.time() * 1000), symbol="CAP/USDT",
            action="CLOSE_LONG", status="CLOSED",
            reason="Channel Swing EXIT_ADVERSE_ABNORMAL_BODY WATERFALL_DROP",
        )],
    )
    return frame, quote, account


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_continuation_is_revalidated_by_shared_contract(monkeypatch, side):
    import core.services.entry_contract as contract

    strategy_gate = Mock(return_value=(False, "BLOCKED_BY_UNRELATED_CLOSED_CANDLE"))
    monkeypatch.setattr(
        contract, "check_entry_gates", strategy_gate,
    )
    frame, quote = expanding_live_frame(side)
    decision = evaluate_entry_contract(
        frame, quote, "TRIGGER_C_CONTINUATION", symbol="CAP/USDT",
    )
    assert decision is not None
    assert decision["type"] == "TRIGGER_C_CONTINUATION"
    assert decision["side"] == side
    assert decision["confirmation_bar_id"] == float(frame.iloc[-1]["timestamp"])
    strategy_gate.assert_not_called()

    frame.loc[frame.index[-1], "close"] = quote
    frame.loc[frame.index[-1], "high"] = max(
        float(frame.iloc[-1]["high"]), quote,
    )
    frame.loc[frame.index[-1], "low"] = min(
        float(frame.iloc[-1]["low"]), quote,
    )

    async def provide_frame(_symbol):
        return frame

    account = SimpleNamespace(
        positions={}, trades=[], entry_frame_provider=provide_frame,
    )
    firewall_decision = asyncio.run(validate_account_entry(
        account, "CAP/USDT", side, {
            "entry_signal_code": "TRIGGER_C_CONTINUATION",
            "channel_confirmation_bar_id": float(frame.iloc[-1]["timestamp"]),
        },
    ))
    assert firewall_decision["type"] == "TRIGGER_C_CONTINUATION"


def test_bearish_instant_reverse_requires_same_bar_successful_waterfall_close():
    frame, quote, account = bearish_instant_frame()

    decision = evaluate_bearish_instant_breakout(
        frame, quote, "CAP/USDT", account,
    )

    assert decision is not None
    assert decision["type"] == BEARISH_INSTANT_BREAKOUT_CODE
    assert decision["side"] == "SHORT"
    assert decision["live_body_atr"] >= 0.5

    account.trades[0]["status"] = "FAILED"
    assert evaluate_bearish_instant_breakout(
        frame, quote, "CAP/USDT", account,
    ) is None


def test_bearish_instant_reverse_fails_closed_without_break_or_matching_close():
    frame, quote, account = bearish_instant_frame()

    assert evaluate_bearish_instant_breakout(
        frame, float(frame.iloc[-1]["kc_lower"]), "CAP/USDT", account,
    ) is None
    account.trades[0]["reason"] = "manual close"
    assert evaluate_bearish_instant_breakout(
        frame, quote, "CAP/USDT", account,
    ) is None


def test_bearish_instant_reverse_is_shared_contract_authority():
    frame, quote, account = bearish_instant_frame()

    decision = evaluate_entry_contract(
        frame, quote, BEARISH_INSTANT_BREAKOUT_CODE,
        account=account, symbol="CAP/USDT",
    )

    assert decision is not None
    assert decision["type"] == BEARISH_INSTANT_BREAKOUT_CODE
    assert decision["side"] == "SHORT"


def test_bearish_instant_reverse_survives_account_firewall_revalidation():
    frame, quote, account = bearish_instant_frame()
    frame.loc[frame.index[-1], "close"] = quote
    frame.loc[frame.index[-1], "low"] = quote
    frame.attrs["entry_finality_verified"] = True
    frame.attrs["entry_finality_server_ms"] = float(frame.iloc[-1]["timestamp"])

    async def provide_frame(_symbol):
        return frame

    account.entry_frame_provider = provide_frame
    result = asyncio.run(validate_account_entry(
        account, "CAP/USDT", "SHORT", {
            "entry_signal_code": BEARISH_INSTANT_BREAKOUT_CODE,
            "channel_confirmation_bar_id": float(frame.iloc[-1]["timestamp"]),
        },
    ))

    assert result["type"] == BEARISH_INSTANT_BREAKOUT_CODE
    assert result["side"] == "SHORT"


def test_successful_waterfall_close_evaluates_reverse_in_same_runner_cycle(monkeypatch):
    import core.services.exits.realtime_profit_exit as exit_service
    from core.services.symbol_runner import process_single_symbol_runner

    frame, quote, account = bearish_instant_frame()
    account.positions["CAP/USDT"] = dict(
        symbol="CAP/USDT", side="LONG", entry_price=100.,
        amount=1., max_pnl_usdt=0., entry_mode="CHANNEL_SWING",
    )
    account.trades.clear()

    async def close_waterfall(_engine, symbol, _price):
        account.positions.pop(symbol)
        _, _, closed_trade_account = bearish_instant_frame()
        account.trades.insert(0, closed_trade_account.trades[0])
        return True

    monkeypatch.setattr(
        exit_service, "enforce_realtime_profit_exit", close_waterfall,
    )
    engine = SimpleNamespace(
        account=account,
        _execute_confirmed_channel_break=AsyncMock(return_value=True),
    )

    asyncio.run(process_single_symbol_runner(
        engine, "CAP/USDT", None, None, False,
        exit_frame=frame, exit_quote=quote,
    ))

    engine._execute_confirmed_channel_break.assert_awaited_once()
    assert engine._execute_confirmed_channel_break.await_args.args[3] == "SHORT"
    assert engine._execute_confirmed_channel_break.await_args.kwargs["v8_reason"] == (
        BEARISH_INSTANT_BREAKOUT_CODE
    )


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_continuation_bypasses_only_market_crash_cooldown(monkeypatch, side):
    import core.services.entry_contract as contract

    stamp = int(time.time() // 60) * 60_000
    decision = dict(
        type="TRIGGER_C_CONTINUATION", side=side,
        entry_phase="KC_CONTINUATION_ENTRY",
        confirmation_bar_id=float(stamp), pending_signal_id=f"CAP:{side}:{stamp}",
    )
    monkeypatch.setattr(contract, "evaluate_entry_contract", lambda *args, **kwargs: decision)
    frame = breakout_frame(side)
    frame.loc[frame.index[-1], "timestamp"] = float(stamp)
    account = SimpleNamespace(
        positions={}, breakout_qualification={}, log=Mock(),
    )
    engine = TradingEngine.__new__(TradingEngine)
    engine.account = account
    engine._market_crash_entries_paused = lambda _now: True
    engine._market_crash_entry_cooldown_until = time.time() + 600
    engine._place_structured_entry = AsyncMock(return_value=True)

    result = asyncio.run(engine._execute_confirmed_channel_break(
        "CAP/USDT", frame, 100.0, side, v8_reason="TRIGGER_C_CONTINUATION",
    ))

    assert result is True
    engine._place_structured_entry.assert_awaited_once()


def test_bearish_instant_reverse_bypasses_market_cooldown(monkeypatch):
    import core.services.entry_contract as contract

    stamp = int(time.time() // 60) * 60_000
    decision = dict(
        type=BEARISH_INSTANT_BREAKOUT_CODE, side="SHORT",
        entry_phase="ATOMIC_BEARISH_REVERSE",
        confirmation_bar_id=float(stamp), pending_signal_id=f"CAP:SHORT:{stamp}",
    )
    monkeypatch.setattr(contract, "evaluate_entry_contract", lambda *args, **kwargs: decision)
    frame = breakout_frame("SHORT")
    frame.loc[frame.index[-1], "timestamp"] = float(stamp)
    account = SimpleNamespace(
        positions={}, breakout_qualification={}, log=Mock(),
    )
    engine = TradingEngine.__new__(TradingEngine)
    engine.account = account
    engine._market_crash_entries_paused = lambda _now: True
    engine._market_crash_entry_cooldown_until = time.time() + 600
    engine._place_structured_entry = AsyncMock(return_value=True)

    result = asyncio.run(engine._execute_confirmed_channel_break(
        "CAP/USDT", frame, 98.4, "SHORT",
        v8_reason=BEARISH_INSTANT_BREAKOUT_CODE,
    ))

    assert result is True
    engine._place_structured_entry.assert_awaited_once()


@pytest.mark.parametrize(
    ("side", "outside_close", "trigger"),
    [
        ("LONG", True, "THREE_POINT_PIVOT"),
        ("SHORT", False, "THREE_POINT_PIVOT"),
    ],
)
def test_outer_band_close_vetoes_soft_exit(monkeypatch, side, outside_close, trigger):
    import core.services.exits.peak_trailing_exit as exit_contract

    monkeypatch.setattr(
        exit_contract, "three_point_pivot_exit",
        lambda *args, **kwargs: {"trigger": trigger},
    )
    monkeypatch.setattr(exit_contract, "channel_pivot_trend_confirmed", lambda *args: True)
    price, snapshot = reversal_snapshot(side)
    candle = snapshot["history_5"][-1]
    if side == "LONG":
        candle.update(c=100.2, kc_upper=100.1, kc_lower=90.0)
    else:
        candle.update(c=99.8, kc_upper=110.0, kc_lower=99.9)
    snapshot["snapshot_bar_id"] = candle["ms"]
    position = exit_position(side)

    result = evaluate_peak_trailing(
        position, price, snapshot, fee=0., slippage=0.,
    )

    assert result is None


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_return_inside_kc_releases_outer_hold_lock(monkeypatch, side):
    import core.services.exits.peak_trailing_exit as exit_contract

    monkeypatch.setattr(
        exit_contract, "three_point_pivot_exit",
        lambda *args, **kwargs: {"trigger": "THREE_POINT_PIVOT"},
    )
    monkeypatch.setattr(exit_contract, "channel_pivot_trend_confirmed", lambda *args: True)
    price, snapshot = reversal_snapshot(side)
    candle = snapshot["history_5"][-1]
    candle.update(c=100., kc_upper=101., kc_lower=99.)
    snapshot["snapshot_bar_id"] = candle["ms"]

    result = evaluate_peak_trailing(
        exit_position(side), price, snapshot, fee=0., slippage=0.,
    )

    assert result is not None
    assert result["trigger"] == "THREE_POINT_PIVOT"


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_outer_band_hold_does_not_block_emergency_exit(monkeypatch, side):
    import core.services.exits.peak_trailing_exit as exit_contract

    monkeypatch.setattr(
        exit_contract, "two_closed_adverse_abnormal_exit",
        lambda *args, **kwargs: {"trigger": "TWO_CLOSED_ADVERSE_ABNORMAL"},
    )
    price, snapshot = reversal_snapshot(side)
    candle = snapshot["history_5"][-1]
    if side == "LONG":
        candle.update(c=100.2, kc_upper=100.1, kc_lower=90.0)
    else:
        candle.update(c=99.8, kc_upper=110.0, kc_lower=99.9)
    snapshot["snapshot_bar_id"] = candle["ms"]

    result = evaluate_peak_trailing(
        exit_position(side), price, snapshot, fee=0., slippage=0.,
    )

    assert result is not None
    assert result["type"] == ABNORMAL_REASON
    assert result["trigger"] == "TWO_CLOSED_ADVERSE_ABNORMAL"


def test_oversold_breakout_exit_has_priority_and_can_reverse():
    position = exit_position("LONG")
    price, snapshot = reversal_snapshot("LONG")
    snapshot.update(
        quote_ms=240002., live_bar_ms=240000., closed_bar_ms=180000.,
        live_open=100.5, live_high=100.5, live_low=98.4,
        kc_upper=101.5, kc_middle=100.0, kc_lower=99.0, ma5=99.8,
        history_5=[
            dict(ms=120000., o=100., h=101., l=99., c=100.8,
                 ma5=100., kc_upper=101., kc_lower=99.),
            dict(ms=180000., o=100.2, h=100.3, l=98.4, c=98.4,
                 ma5=99.8, kc_upper=101.5, kc_lower=99.),
        ],
    )
    price = 98.4

    result = evaluate_peak_trailing(
        position, price, snapshot, fee=0., slippage=0.,
    )

    assert result is not None
    assert result["type"] == ABNORMAL_REASON
    assert result["trigger"] == "BEARISH_INSTANT_BREAKOUT"


def test_confirmed_doji_reversal_overrides_waterfall_reason():
    position = exit_position("LONG")
    price, snapshot = reversal_snapshot("LONG")
    snapshot.update(
        quote_ms=240002., live_bar_ms=240000., closed_bar_ms=180000.,
        live_open=100.5, live_high=100.5, live_low=98.4,
        kc_upper=101.5, kc_middle=100.0, kc_lower=99.0, ma5=99.8,
    )
    price = 98.4

    result = evaluate_peak_trailing(
        position, price, snapshot, fee=0., slippage=0.,
    )

    assert result is not None
    assert result["trigger"] == "EXIT_DOJI_BEARISH_CONFIRMATION"


def test_confirmed_doji_exit_is_not_blocked_by_kc_outer_hold_lock():
    position = exit_position("LONG")
    price, snapshot = reversal_snapshot("LONG")
    snapshot["history_5"][-1].update(kc_upper=99.7, kc_lower=90.0)

    result = evaluate_peak_trailing(
        position, price, snapshot, fee=0., slippage=0.,
    )

    assert result is not None
    assert result["trigger"] == "EXIT_DOJI_BEARISH_CONFIRMATION"
