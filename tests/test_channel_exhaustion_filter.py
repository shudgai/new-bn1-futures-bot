"""末端防追單過濾（2026-09-12 使用者要求）：乖離過大、反向長影線。"""
import pandas as pd
import pytest

from core import config
from core.services.strategies import outer_strategy as outer
from core.services.strategies.outer_strategy import channel_exhaustion_block


@pytest.fixture(autouse=True)
def _enable_filter(monkeypatch):
    monkeypatch.setattr(outer, "CHANNEL_EXHAUSTION_FILTER_ENABLED", True, raising=False)
    monkeypatch.setattr(outer, "CHANNEL_EXHAUSTION_MAX_RAIL_DEVIATION_PCT", 0.015, raising=False)
    monkeypatch.setattr(outer, "CHANNEL_EXHAUSTION_WICK_BODY_RATIO", 1.0, raising=False)
    monkeypatch.setattr(outer, "CHANNEL_EXHAUSTION_APPLIES_SPECIAL_K", True, raising=False)


def _frame(price, *, opened, kc_upper, kc_lower, high=None, low=None):
    row = {
        "timestamp": 0, "open": opened, "close": price,
        "high": max(opened, price) if high is None else high,
        "low": min(opened, price) if low is None else low,
        "atr": 0.001, "ema_20": opened, "kc_middle": opened,
        "kc_upper": kc_upper, "kc_lower": kc_lower, "ma3": opened, "ma15": opened,
    }
    return pd.DataFrame([row, dict(row)])


def _block(price, **kw):
    side = kw.pop("side", "LONG")
    frame = _frame(price, **kw)
    return channel_exhaustion_block(frame, price, side)


def test_blocks_when_price_far_above_upper_rail():
    assert _block(0.1060, opened=0.1000, kc_upper=0.1000, kc_lower=0.0960) == "KC_EXHAUSTION_RAIL_DEVIATION"


def test_allows_small_deviation_without_wick():
    assert _block(0.1010, opened=0.0995, kc_upper=0.1000, kc_lower=0.0960) is None


def test_blocks_long_with_long_upper_wick():
    # 上影線 0.006 > 實體 0.001 × 1 倍
    assert _block(0.1001, opened=0.1000, kc_upper=0.10005, kc_lower=0.0960,
                  high=0.1060, low=0.0999) == "KC_EXHAUSTION_REJECTION_WICK"


def test_blocks_short_with_long_lower_wick():
    assert _block(0.0999, opened=0.1000, kc_upper=0.1040, kc_lower=0.09995,
                  high=0.1001, low=0.0940, side="SHORT") == "KC_EXHAUSTION_REJECTION_WICK"


def test_disabled_filter_never_blocks(monkeypatch):
    monkeypatch.setattr(outer, "CHANNEL_EXHAUSTION_FILTER_ENABLED", False, raising=False)
    assert _block(0.1200, opened=0.1000, kc_upper=0.1000, kc_lower=0.0960) is None
