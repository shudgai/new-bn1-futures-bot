"""測試專用固定值。

測試必須驗證「程式行為」，不能因為營運中的 .env 被調整就跟著紅燈——
否則每次改設定都要重寫測試，就會再次出現「新規則與舊測試並存」。
需要驗證開關本身行為的測試，請在測試內用 monkeypatch 覆寫。
"""
import pytest

from core import config
from core.services.exits import fading_exit_service, profit_protection_service
from core.services.strategies import outer_strategy

TEST_DEFAULTS = {
    "CHANNEL_FLAT_MIDDLE_RATIO": 0.05,
    "CHANNEL_TAIL_MAX_TREND_BARS": 12,
    "CHANNEL_LIVE_BODY_BREAKOUT_ENABLED": True,
    "CHANNEL_FADING_MA3_EXIT_ENABLED": True,
    "CHANNEL_SWING_PROFIT_LADDER_ARM_NET_USDT": 4.0,
    "CHANNEL_SWING_PROFIT_LADDER_LOCK_OFFSET_USDT": 2.0,
    "CHANNEL_SWING_PROFIT_FLOOR_ARM_NET_USDT": 2.0,
    "CHANNEL_SWING_PROFIT_FLOOR_NET_USDT": 0.3,
    "CHANNEL_ATR_EXIT_ENABLED": False,
    "CHANNEL_MIN_DIRECTION_EFFICIENCY": 0.0,
    "CHANNEL_LONG_BODY_ENTRY_ATR": 0.0,
    "LIVE_BREAKOUT_BODY_ATR": 0.5,
    "CHANNEL_ATR_LONG_BODY_TARGET_MULT": 1.0,
    "CHANNEL_PROFIT_ROOM_ENABLED": False,
    "CHANNEL_MIN_ATR_PCT": 0.0,
    "CHANNEL_PROFIT_REENTRY_MAX_CHASE_PCT": 0.0,
    "CHANNEL_PROFIT_ROOM_ATR_IN_STRONG_TREND": False,
}


@pytest.fixture(autouse=True)
def _pin_strategy_switches(monkeypatch):
    for name, value in TEST_DEFAULTS.items():
        for module in (config, outer_strategy, fading_exit_service, profit_protection_service):
            monkeypatch.setattr(module, name, value, raising=False)
    yield
