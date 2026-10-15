import asyncio

import pytest

from core.services.entry_firewall import validate_account_entry
from test_wild_swing_strategy import _frame


class FakeAccount:
    positions = {}
    closing_lock = set()
    pending_limit_orders = {}

    def __init__(self, frame):
        self.frame = frame

    async def entry_frame_provider(self, symbol):
        assert symbol == "LOBSTER/USDT"
        return self.frame.copy()


def _context(bar_id):
    return {
        "entry_mode": "WILD_SWING",
        "source": "WILD_SWING_1M",
        "entry_signal_code": "WILD_SWING_TREND_CONTINUATION",
        "channel_confirmation_bar_id": bar_id,
        "dual_tf_meta": {"bar_id": bar_id},
    }


def test_wild_swing_firewall_revalidates_closed_signal_and_candle_identity():
    frame = _frame("SHORT")
    frame.attrs["timeframe_ms"] = 60_000
    frame.attrs["entry_finality_verified"] = True
    bar_id = float(frame.iloc[-1]["timestamp"])

    result = asyncio.run(
        validate_account_entry(
            FakeAccount(frame), "LOBSTER/USDT", "SHORT", _context(bar_id),
        )
    )

    assert result["entry_mode"] == "WILD_SWING"
    assert result["side"] == "SHORT"
    assert result["confirmation_bar_id"] == bar_id


@pytest.mark.parametrize("change", ["bar_id", "unverified", "timeframe"])
def test_wild_swing_firewall_rejects_stale_or_unverified_market_data(change):
    frame = _frame("SHORT")
    frame.attrs["timeframe_ms"] = 60_000
    frame.attrs["entry_finality_verified"] = True
    bar_id = float(frame.iloc[-1]["timestamp"])
    context = _context(bar_id)
    if change == "bar_id":
        context["dual_tf_meta"]["bar_id"] -= 60_000
    elif change == "unverified":
        frame.attrs["entry_finality_verified"] = False
    else:
        frame.attrs["timeframe_ms"] = 300_000

    with pytest.raises(ValueError, match="WILD_SWING"):
        asyncio.run(
            validate_account_entry(
                FakeAccount(frame), "LOBSTER/USDT", "SHORT", context,
            )
        )
