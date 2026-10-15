import asyncio
from types import SimpleNamespace
from unittest.mock import Mock

import pandas as pd
import pytest

from core.engine import TradingEngine
from core.services.entry_contract import evaluate_continuation_entry
from test_breakout_only_entry import breakout_frame, live_outer_frame


@pytest.mark.parametrize(
    ("case", "expected_reason"),
    [
        ("unknown_channel", "REJECT_FLAT_CHANNEL"),
        ("flat_upper", "REJECT_FLAT_CHANNEL"),
        ("unclosed_breakout", "BLOCKED_CONTINUATION_NOT_CLOSED_CONFIRMATION"),
    ],
)
def test_long_continuation_requires_rising_known_kc_and_closed_confirmation(
    case, expected_reason,
):
    frame = breakout_frame("LONG")
    live = frame.index[-1]
    frame.loc[live, ["open", "close", "high", "low",
                     "kc_upper", "kc_middle", "kc_lower"]] = [
        101.4, 101.6, 101.6, 101.4, 101.8, 100.3, 98.8,
    ]
    quote = 101.9
    latest_closed = frame.index[-2]
    previous_closed = frame.index[-3]

    if case == "unknown_channel":
        middle = float(frame.loc[latest_closed, "kc_middle"])
        frame.loc[latest_closed, ["close", "ma5", "ma15"]] = [
            middle, middle, middle,
        ]
        frame.loc[previous_closed, ["kc_middle", "ma5", "ma15"]] = [
            middle, middle, middle,
        ]
    elif case == "flat_upper":
        frame.loc[latest_closed, "kc_upper"] = frame.loc[previous_closed, "kc_upper"]
    else:
        frame.loc[latest_closed, "close"] = (
            frame.loc[latest_closed, "kc_upper"] - 0.1
        )

    diagnostics = {}
    decision = evaluate_continuation_entry(
        frame, quote, symbol="SYM", account=SimpleNamespace(positions={}, trades=[]),
        diagnostics=diagnostics,
    )

    assert quote > float(frame.iloc[-1]["kc_upper"])
    assert decision is None
    assert diagnostics["reason"] == expected_reason




@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_legacy_live_body_breakout_never_creates_qualification(side):
    frame = breakout_frame(side)
    quote = float(frame.iloc[-1].open) + (.9 if side == 'LONG' else -.9)
    account = SimpleNamespace(positions={}, trades=[], last_closed_at={},
                              breakout_qualification={}, log=Mock(), record_qualification=Mock())
    engine = TradingEngine.__new__(TradingEngine)
    engine.account = account
    asyncio.run(engine._execute_confirmed_channel_break(
        'SYM', frame, quote, side, daily_halt=True,
        v8_reason='KC_LIVE_BODY_BREAKOUT_'+side))
    account.record_qualification.assert_not_called()
