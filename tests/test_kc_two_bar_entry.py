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
