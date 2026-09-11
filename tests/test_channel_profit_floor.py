"""保底停利：淨利峰值達門檻後，出場價不得低於保底值。"""
import pytest

from core import config
from core.services.exits import profit_protection_service as service


def _position():
    return {"side": "LONG", "entry_price": 100.0, "qty": 3.75, "open_timestamp": 1.0}


def _protection(price):
    return service.protection(_position(), price, 0.0, 0.0)


@pytest.mark.parametrize("price,peak_net,expected", [
    (100.2, 0.75, None),      # 未達保底啟動門檻
    (100.6, 2.25, 0.3),       # 已達 2U：鎖住 +0.3U
    (101.2, 4.50, 2.0),       # 已達階梯門檻：由階梯鎖住 2U
])
def test_floor_protects_small_peaks(price, peak_net, expected, monkeypatch):
    monkeypatch.setattr(config, "CHANNEL_SWING_PROFIT_LADDER_ARM_NET_USDT", 4.0)
    monkeypatch.setattr(config, "CHANNEL_SWING_PROFIT_LADDER_LOCK_OFFSET_USDT", 2.0)
    monkeypatch.setattr(config, "CHANNEL_SWING_PROFIT_FLOOR_ARM_NET_USDT", 2.0)
    monkeypatch.setattr(config, "CHANNEL_SWING_PROFIT_FLOOR_NET_USDT", 0.3)
    result = _protection(price)
    if expected is None:
        assert result is None
    else:
        assert result["locked_net"] == pytest.approx(expected)


def test_floor_can_be_disabled(monkeypatch):
    monkeypatch.setattr(config, "CHANNEL_SWING_PROFIT_FLOOR_NET_USDT", 0.0)
    assert _protection(100.6) is None
