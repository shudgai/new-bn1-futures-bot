import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from core.services.exits.hard_stop_service import (
    HardStopExitStrategy,
    enforce_hard_stop,
    hard_stop_reason,
)


def test_wild_swing_ignores_position_hard_stop_even_if_a_legacy_stop_is_pending():
    position = {
        "side": "LONG",
        "entry_mode": "WILD_SWING",
        "entry_price": 100.0,
        "qty": 1.0,
        "margin": 10.0,
        "leverage": 10.0,
        "channel_hard_stop_pending": "PRICE_LOSS",
    }
    account = SimpleNamespace(
        positions={"LOBSTER/USDT": position},
        position_meta={"LOBSTER/USDT": {"entry_mode": "WILD_SWING"}},
        save_state=Mock(),
        close_position=AsyncMock(return_value=True),
    )

    assert hard_stop_reason(position, 1.0) is None
    assert HardStopExitStrategy().evaluate_exit(position, None, 1.0) is None
    assert not asyncio.run(enforce_hard_stop(account, "LOBSTER/USDT", 1.0))
    account.close_position.assert_not_awaited()


def test_metadata_identified_wild_swing_skips_position_hard_stop():
    position = {
        "side": "SHORT",
        "entry_price": 100.0,
        "qty": 1.0,
        "margin": 10.0,
        "leverage": 10.0,
    }
    account = SimpleNamespace(
        positions={"CAP/USDT": position},
        position_meta={
            "CAP/USDT": {
                "entry_mode": "WILD_SWING",
                "channel_hard_stop_pending": "MARGIN_LOSS",
            },
        },
        save_state=Mock(),
        close_position=AsyncMock(return_value=True),
    )

    assert not asyncio.run(enforce_hard_stop(account, "CAP/USDT", 1000.0))
    account.close_position.assert_not_awaited()
