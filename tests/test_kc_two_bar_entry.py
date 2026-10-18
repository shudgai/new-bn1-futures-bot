import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from core.services.entry_contract import evaluate_entry_contract
from core.services.entry_firewall import validate_account_entry
from core.services.symbol_runner import process_single_symbol_runner
from test_strict_entry_contract import candles


def make_execution_engine(monkeypatch, tmp_path, side, frame):
    from core.engine import TradingEngine
    from core.paper_account import PaperAccount
    from core.services.wait_entry_authority import WaitEntryAuthority

    monkeypatch.setattr(PaperAccount, "load_state", lambda self: None)
    monkeypatch.setattr(PaperAccount, "save_state", lambda self: None)
    account = PaperAccount()
    account.balance = 100.0
    engine = object.__new__(TradingEngine)
    engine.account = account
    symbol = "CAP/USDT"
    quote = float(frame.iloc[-1].close)
    engine.exchange = SimpleNamespace(
        fetch_time=AsyncMock(return_value=float(frame.iloc[-1].timestamp) + 10_000),
    )
    engine.tickers = {symbol: quote}
    engine._ticker_observed_at = {symbol: time.monotonic()}
    engine._channel_entry_quote_times = {symbol: time.time()}
    engine.fetch_klines = AsyncMock(return_value=frame)
    engine.strategy = SimpleNamespace(compute_indicators=lambda value: value)
    engine.symbol_rotation = SimpleNamespace(get_dynamic_leverage=lambda *args: 2)
    engine._execution_price_is_safe = AsyncMock(return_value=True)
    authority = WaitEntryAuthority(tmp_path / f"{side}.json")
    engine.wait_entry_authority = authority
    account.wait_entry_authority = authority
    account.entry_frame_provider = AsyncMock(return_value=frame)
    account.wait_quote_provider = lambda _symbol: {
        "price": quote, "observed_at": time.monotonic(),
    }
    return engine, account, symbol


def breakout_frame(side):
    """Use the current authorized KC2BAR fixture, including live confirmation."""
    return candles(side, live=True)


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_scanner_and_account_share_the_two_closed_bar_authority(
    monkeypatch, tmp_path, side,
):
    frame = breakout_frame(side)
    quote = float(frame.iloc[-1]["close"])
    code = f"KC_2BAR_CONFIRM_{side}"
    decision = evaluate_entry_contract(frame, quote, code=code, symbol="CAP/USDT")
    assert decision is not None

    engine, account, symbol = make_execution_engine(
        monkeypatch, tmp_path, side, frame,
    )
    asyncio.run(
        process_single_symbol_runner(
            engine, symbol, time.time(), None, False, exit_frame=frame
        )
    )
    assert account.positions[symbol]["side"] == side
    assert account.trades[-1]["action"] == f"OPEN_{side}"


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_scanner_rejects_breakout_that_was_revised_before_evaluation(
    monkeypatch, tmp_path, side,
):
    frame = breakout_frame(side)
    quote = float(frame.iloc[-1]["close"])
    decision = evaluate_entry_contract(
        frame, quote, code=f"KC_2BAR_CONFIRM_{side}", symbol="CAP/USDT",
    )
    assert decision is not None

    engine, account, symbol = make_execution_engine(
        monkeypatch, tmp_path, side, frame,
    )
    edge = "kc_upper" if side == "LONG" else "kc_lower"
    confirmation_index = frame.index[-3]
    frame.loc[confirmation_index, "close"] = frame.loc[confirmation_index, edge]

    asyncio.run(
        process_single_symbol_runner(
            engine, symbol, time.time(), None, False, exit_frame=frame,
        )
    )
    assert symbol not in account.positions
    assert not account.trades


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
@pytest.mark.parametrize(
    "code",
    [
        "KC_OUTER_PIVOT_LONG",
        "KC_OUTER_PIVOT_SHORT",
        "MA5_MA15_LIVE_CROSS_LONG",
        "KC_3BAR_CONFIRM_LONG",
        "KC_3BAR_CONFIRM_SHORT",
    ],
)
def test_retired_entry_authorities_cannot_pass_the_account_firewall(side, code):
    frame = breakout_frame(side)
    account = SimpleNamespace(
        positions={},
        trades=[],
        last_closed_at={},
        entry_frame_provider=AsyncMock(return_value=frame),
    )

    with pytest.raises(ValueError):
        asyncio.run(
            validate_account_entry(account, "CAP/USDT", side, {"entry_signal_code": code})
        )
