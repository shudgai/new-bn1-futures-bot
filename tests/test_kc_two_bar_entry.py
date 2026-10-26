import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from core.services.entry_contract import evaluate_entry_contract
from core.services.entry_firewall import validate_account_entry
from core.services.symbol_runner import process_single_symbol_runner
from test_strict_entry_contract import candles
from core.services.kc_two_bar_entry import evaluate_kc_two_bar_entry


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


@pytest.mark.parametrize(
    "side,first,second",
    [
        ("LONG", (100, 101, 100.5, 102, 99.5, 1, 100.8, 99.2, 100.4),
         (102, 104, 103, 105, 101.5, 1, 103.8, 99.5, 101.8)),
        ("SHORT", (100, 99, 99.5, 100.5, 98.5, 1, 102, 99.2, 100.4),
         (98, 96, 97, 98.5, 95, 1, 100.5, 96.2, 98.2)),
    ],
)
def test_closed_two_bar_breakout_requires_second_close_outside_kc_and_ma5(
    side, first, second,
):
    import pandas as pd

    columns = [
        "timestamp", "open", "close", "ma5", "high", "low", "atr", "kc_upper",
        "kc_lower", "ma15",
    ]
    frame = pd.DataFrame([
        dict(zip(columns, (60_000, *first))),
        dict(zip(columns, (120_000, *second))),
    ])
    signal = evaluate_kc_two_bar_entry(frame, quote=second[1])
    assert signal and signal["side"] == side

    # KC still confirms, but MA5 does not: the setup must fail closed.
    frame.loc[1, "ma5"] = second[1]
    assert evaluate_kc_two_bar_entry(frame, quote=second[1]) is None


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_closed_two_bar_breakout_requires_first_candle_open_inside_channel(side):
    import pandas as pd

    if side == "LONG":
        rows = [
            (103, 104, 101, 105, 102, 1, 102, 98, 100),
            (104, 106, 103, 107, 103, 1, 104, 99, 101),
        ]
    else:
        rows = [
            (97, 96, 99, 98, 95, 1, 102, 98, 100),
            (96, 94, 97, 97, 93, 1, 101, 96, 99),
        ]
    columns = [
        "timestamp", "open", "close", "ma5", "high", "low", "atr", "kc_upper",
        "kc_lower", "ma15",
    ]
    frame = pd.DataFrame([
        dict(zip(columns, (60_000 * (index + 1), *row)))
        for index, row in enumerate(rows)
    ])
    assert evaluate_kc_two_bar_entry(frame, quote=rows[-1][1]) is None


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_scanner_and_account_share_the_two_closed_bar_authority(
    monkeypatch, tmp_path, side,
):
    from core.services.execution_manager import ExecutionManager

    frame = breakout_frame(side).copy()
    # DataPipeline recomputes KC from OHLC. Keep K2 just beyond that canonical
    # rail while the live quote remains within the anti-chase distance.
    if side == "LONG":
        frame.loc[frame.index[-2], ["close", "high"]] = [101.9, 102.0]
        frame.loc[frame.index[-1], ["close", "high"]] = [102.2, 102.25]
    else:
        frame.loc[frame.index[-2], ["close", "low"]] = [98.1, 98.0]
        frame.loc[frame.index[-1], ["close", "low"]] = [97.8, 97.75]
    import pandas as pd
    sign = 1 if side == "LONG" else -1
    base = float(frame.iloc[0].close)
    first_timestamp = int(frame.iloc[0].timestamp)
    history = []
    for offset in range(30, 0, -1):
        close = base - sign * 0.01 * offset
        row = frame.iloc[0].to_dict()
        row.update(
            timestamp=first_timestamp - offset * 60_000,
            open=close - sign * 0.03,
            high=close + 0.10,
            low=close - 0.10,
            close=close,
            volume=100.0,
            is_closed=True,
        )
        history.append(row)
    frame = pd.concat([pd.DataFrame(history), frame], ignore_index=True)
    frame.attrs.update(timeframe_ms=60_000, entry_finality_verified=True)
    ExecutionManager._last_trade_bar_time.pop("CAP/USDT", None)
    ExecutionManager._hourly_entry_records.pop("CAP/USDT", None)
    ExecutionManager._last_exit_record.pop("CAP/USDT", None)
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
