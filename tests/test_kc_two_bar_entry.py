import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from core.services.entry_contract import evaluate_entry_contract
from core.services.entry_firewall import validate_account_entry
from core.services.symbol_runner import process_single_symbol_runner
from test_breakout_only_entry import breakout_frame


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_scanner_and_account_share_the_two_closed_bar_authority(side):
    frame = breakout_frame(side)
    quote = float(frame.iloc[-1]["close"])
    decision = evaluate_entry_contract(frame, quote, symbol="TEST")
    assert decision is not None

    account = SimpleNamespace(
        positions={},
        trades=[],
        last_closed_at={},
        entry_frame_provider=AsyncMock(return_value=frame),
    )
    context = dict(
        entry_signal_code=decision["type"],
        channel_confirmation_bar_id=decision["confirmation_bar_id"],
    )
    verified = asyncio.run(
        validate_account_entry(account, "TEST", side, context)
    )
    assert verified["pending_signal_id"] == decision["pending_signal_id"]

    engine = SimpleNamespace(
        account=account,
        tickers={"TEST": quote},
        _execute_confirmed_channel_break=AsyncMock(),
    )
    asyncio.run(
        process_single_symbol_runner(
            engine, "TEST", time.time(), None, False, exit_frame=frame
        )
    )
    engine._execute_confirmed_channel_break.assert_awaited_once()
    assert engine._execute_confirmed_channel_break.await_args.kwargs["v8_reason"] == decision["type"]


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_account_revalidation_rejects_breakout_that_changed_after_scan(side):
    frame = breakout_frame(side)
    quote = float(frame.iloc[-1]["close"])
    decision = evaluate_entry_contract(frame, quote, symbol="TEST")
    assert decision is not None

    account = SimpleNamespace(
        positions={},
        trades=[],
        last_closed_at={},
        entry_frame_provider=AsyncMock(return_value=frame),
    )
    context = dict(
        entry_signal_code=decision["type"],
        channel_confirmation_bar_id=decision["confirmation_bar_id"],
    )
    edge = "kc_upper" if side == "LONG" else "kc_lower"
    frame.loc[2, "close"] = frame.loc[2, edge]

    with pytest.raises(ValueError, match="最新入口行情不符"):
        asyncio.run(validate_account_entry(account, "TEST", side, context))


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

    with pytest.raises(ValueError, match="合法入口白名單"):
        asyncio.run(
            validate_account_entry(account, "TEST", side, {"entry_signal_code": code})
        )
