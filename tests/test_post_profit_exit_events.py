import time
from types import SimpleNamespace

from core.services.post_profit_lock_gate import (
    post_profit_lock_reason,
    profit_exit_fields,
)
from test_strict_entry_gates_live import frame_for


def test_profitable_pivot_records_actual_net_without_cached_pnl():
    position = {"side": "LONG", "peak_trailing_state": {"peak_price": 110}}

    fields = profit_exit_fields(
        position,
        "THREE_POINT_PIVOT",
        time.time() * 1000,
        net_pnl=2.4,
    )

    assert fields["last_profit_exit_peak_price"] == 110
    assert profit_exit_fields(
        position,
        "THREE_POINT_PIVOT",
        time.time() * 1000,
        net_pnl=-1,
    ) == {}


def test_legacy_profitable_pivot_cannot_skip_post_profit_gate():
    now = time.time() * 1000
    account = SimpleNamespace(
        trades=[
            {
                "id": now - 120_000,
                "symbol": "CAP/USDT",
                "status": "CLOSED",
                "action": "CLOSE_LONG",
                "pnl": 2.45,
                "reason": "THREE_POINT_PIVOT",
            },
        ],
    )
    frame = frame_for().iloc[:-1]

    assert post_profit_lock_reason(
        account, "CAP/USDT", frame, "LONG", now,
    ) == "BLOCKED_BY_POST_PROFIT_COOLDOWN"
    assert post_profit_lock_reason(
        account, "CAP/USDT", frame, "LONG", now + 240_000,
    ) == "BLOCKED_BY_POST_PROFIT_INVALID_STATE"
    assert post_profit_lock_reason(
        account, "CAP/USDT", frame, "LONG", now + 900_000,
    ) is None
