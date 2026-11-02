from types import SimpleNamespace

import pytest

from core.services.entry_contract import is_entry_doji


@pytest.mark.parametrize(
    ("close", "expected"),
    [
        (100.99, True),
        (99.01, True),
        (100.9999999999, True),
        (100.9999999999995, False),
        (101.0, False),
        (101.01, False),
    ],
)
def test_entry_doji_uses_strict_ten_percent_boundary(close, expected):
    candle = SimpleNamespace(open=100.0, high=105.0, low=95.0, close=close)

    assert is_entry_doji(candle) is expected
