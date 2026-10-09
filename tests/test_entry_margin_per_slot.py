import pytest

import core.config as config
from core.engine import TradingEngine


@pytest.mark.parametrize(
    ("slot_count", "expected"),
    [(1, 100.), (2, 50.)],
)
def test_entry_margin_respects_configured_slot_budget(
    monkeypatch, slot_count, expected
):
    monkeypatch.setattr(config, "MAX_SLOTS", slot_count)

    amount = TradingEngine._full_wallet_entry_margin(100., 100., 2.)

    expected = min(expected, 100. / (1. + 2. * config.TAKER_FEE_RATE))
    assert amount == pytest.approx(expected)


def test_entry_margin_never_exceeds_available_balance_after_fee(monkeypatch):
    monkeypatch.setattr(config, "MAX_SLOTS", 2)

    amount = TradingEngine._full_wallet_entry_margin(100., 20., 2.)

    assert amount == pytest.approx(20. / (1. + 2. * config.TAKER_FEE_RATE))
