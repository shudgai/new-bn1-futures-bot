import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from core.testnet_account import BinanceTestnetAccount


@pytest.mark.parametrize("filled", [0.0, 0.5])
def test_countertrend_pending_order_is_cancelled_and_any_race_fill_is_flattened(
    monkeypatch, filled,
):
    from core import config
    from core.services import math_execution_gate

    monkeypatch.setattr(config, "ENTRY_SUBMISSION_ENABLED", True)
    monkeypatch.setattr(config, "is_entry_disabled", lambda _symbol: False)
    monkeypatch.setattr(
        math_execution_gate,
        "validate_entry_at_execution",
        AsyncMock(return_value=(False, "BLOCKED_LONG_ON_BEARISH_TREND")),
    )

    exchange = SimpleNamespace(
        fetch_order=AsyncMock(side_effect=[
            {"status": "open", "filled": 0.0},
            {"status": "canceled", "filled": filled, "average": 0.0708},
        ]),
        cancel_order=AsyncMock(return_value={"status": "canceled"}),
    )
    account = SimpleNamespace(
        pending_limit_orders={
            "CAP/USDT": {
                "order_id": "entry-1", "side": "LONG", "target_price": 0.071,
                "qty": 1.0, "entry_context": {"entry_mode": "WILD_SWING"},
            },
        },
        exchange=exchange,
        log=Mock(),
        _pending_retry_streak={},
        _emergency_flatten=AsyncMock(),
        _finalize_new_position=AsyncMock(),
    )

    asyncio.run(BinanceTestnetAccount.check_pending_limit_orders(account))

    assert account.pending_limit_orders == {}
    exchange.cancel_order.assert_awaited_once_with("entry-1", "CAP/USDT")
    if filled:
        account._emergency_flatten.assert_awaited_once_with("CAP/USDT", "LONG", filled)
    else:
        account._emergency_flatten.assert_not_awaited()
    account._finalize_new_position.assert_not_awaited()
