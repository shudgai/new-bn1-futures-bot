"""量能衰退平倉與 MA3 在軌外時的單根反向異常K即時平倉。"""
import pandas as pd
import pytest

from core import config
from core.guards import abnormal_guard
from core.guards.abnormal_guard import channel_adverse_exit_reason
from core.strategy import has_volume_divergence

ATR = 1.0


def _frame(ma3, lower, closed_bodies, live_body):
    rows = [{"open": 100.0, "close": 100.0 + body, "ma3": ma3,
             "kc_lower": lower, "kc_upper": lower + 10.0, "volume": 100.0}
            for body in closed_bodies]
    rows.append({"open": 100.0, "close": 100.0 + live_body, "ma3": ma3,
                 "kc_lower": lower, "kc_upper": lower + 10.0, "volume": 100.0})
    return pd.DataFrame(rows)


@pytest.mark.parametrize("side,sign", [("SHORT", 1.0), ("LONG", -1.0)])
def test_single_adverse_bar_exits_when_ma3_is_outside_the_rail(side, sign):
    ma3 = 90.0 if side == "SHORT" else 110.0
    lower = 95.0 if side == "SHORT" else 95.0
    frame = _frame(ma3, lower, [0.2 * sign, 1.2 * sign], 0.1 * sign)
    price = 100.0 + 0.1 * sign
    assert channel_adverse_exit_reason(frame, side, price, ATR) == \
        "EMERGENCY_EXIT_MA3_OUTSIDE_ADVERSE_BAR"


@pytest.mark.parametrize("side,sign", [("SHORT", 1.0), ("LONG", -1.0)])
def test_no_single_exit_while_ma3_is_inside_the_channel(side, sign):
    frame = _frame(97.0, 95.0, [0.2 * sign, 1.2 * sign], 0.1 * sign)
    price = 100.0 + 0.1 * sign
    assert channel_adverse_exit_reason(frame, side, price, ATR) is None


def test_single_adverse_exit_can_be_disabled(monkeypatch):
    monkeypatch.setattr(abnormal_guard, "CHANNEL_SINGLE_ADVERSE_EXIT_ENABLED", False)
    frame = _frame(90.0, 95.0, [0.2, 1.2], 0.1)
    assert channel_adverse_exit_reason(frame, "SHORT", 100.1, ATR) is None


def test_two_closed_bodies_still_exit_first():
    frame = _frame(90.0, 95.0, [1.2, 1.4], 0.1)
    assert channel_adverse_exit_reason(frame, "SHORT", 100.1, ATR) == \
        "EMERGENCY_EXIT_2_CANDLE_ADVERSE"


def _volume_frame(recent_volume):
    """20 根 K；後 10 根量能改變，且最後一根創新高／新低。"""
    volumes = [100.0] * 10 + [recent_volume] * 10
    lows = [90.0] * 20
    highs = [110.0] * 20
    lows[-1] = 89.0    # 空單方向：價格創新低
    highs[-1] = 111.0  # 多單方向：價格創新高
    return pd.DataFrame({"low": lows, "high": highs, "volume": volumes})


def test_volume_decay_detects_bottom_exhaustion_for_shorts():
    assert has_volume_divergence(_volume_frame(recent_volume=20.0), 1) is True
    assert has_volume_divergence(_volume_frame(recent_volume=120.0), 1) is False


def test_volume_decay_detects_top_exhaustion_for_longs():
    assert has_volume_divergence(_volume_frame(recent_volume=20.0), -1) is True
    assert has_volume_divergence(_volume_frame(recent_volume=120.0), -1) is False
