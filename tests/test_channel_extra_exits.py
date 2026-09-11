"""量能衰退平倉與 MA3 轉進軌內後的單根反向異常K即時平倉。"""
import pandas as pd
import pytest

from core.guards import abnormal_guard
from core.guards.abnormal_guard import channel_adverse_exit_reason
from core.strategy import has_volume_divergence

ATR = 1.0
REASON = "EMERGENCY_EXIT_MA3_ENTERED_RAIL_ADVERSE_BAR"


def _frame(ma3_before, ma3_last, lower, closed_bodies, live_body):
    """兩根已收線K（iloc[-3]、iloc[-2]）加一根即時K；軌道固定。"""
    rows = [
        {"open": 100.0, "close": 100.0 + closed_bodies[0], "ma3": ma3_before,
         "kc_lower": lower, "kc_upper": lower + 10.0},
        {"open": 100.0, "close": 100.0 + closed_bodies[1], "ma3": ma3_last,
         "kc_lower": lower, "kc_upper": lower + 10.0},
        {"open": 100.0, "close": 100.0 + live_body, "ma3": ma3_last,
         "kc_lower": lower, "kc_upper": lower + 10.0},
    ]
    return pd.DataFrame(rows)


@pytest.mark.parametrize("side,sign,ma3_before,ma3_last", [
    ("SHORT", 1.0, 90.0, 96.0),    # MA3 由下軌外轉進軌內
    ("LONG", -1.0, 110.0, 104.0),  # MA3 由上軌外轉進軌內
])
def test_single_adverse_bar_exits_after_ma3_reenters_the_rail(side, sign, ma3_before, ma3_last):
    frame = _frame(ma3_before, ma3_last, 95.0, [0.2 * sign, 1.2 * sign], 0.1 * sign)
    assert channel_adverse_exit_reason(frame, side, 100.0 + 0.1 * sign, ATR) == REASON


@pytest.mark.parametrize("side,sign,ma3_before,ma3_last", [
    ("SHORT", 1.0, 90.0, 92.0),   # 仍在下軌外
    ("LONG", -1.0, 110.0, 108.0),  # 仍在上軌外
])
def test_no_single_exit_while_ma3_stays_outside_the_rail(side, sign, ma3_before, ma3_last):
    frame = _frame(ma3_before, ma3_last, 95.0, [0.2 * sign, 1.2 * sign], 0.1 * sign)
    assert channel_adverse_exit_reason(frame, side, 100.0 + 0.1 * sign, ATR) is None


@pytest.mark.parametrize("side,sign", [("SHORT", 1.0), ("LONG", -1.0)])
def test_no_single_exit_when_ma3_never_left_the_rail(side, sign):
    frame = _frame(97.0, 97.0, 95.0, [0.2 * sign, 1.2 * sign], 0.1 * sign)
    assert channel_adverse_exit_reason(frame, side, 100.0 + 0.1 * sign, ATR) is None


def test_single_adverse_exit_can_be_disabled(monkeypatch):
    monkeypatch.setattr(abnormal_guard, "CHANNEL_SINGLE_ADVERSE_EXIT_ENABLED", False)
    frame = _frame(90.0, 96.0, 95.0, [0.2, 1.2], 0.1)
    assert channel_adverse_exit_reason(frame, "SHORT", 100.1, ATR) is None


def test_two_closed_bodies_still_exit_first():
    frame = _frame(90.0, 96.0, 95.0, [1.2, 1.4], 0.1)
    assert channel_adverse_exit_reason(frame, "SHORT", 100.1, ATR) == "EMERGENCY_EXIT_2_CANDLE_ADVERSE"


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


def _decay_frame(recent_volume, ma3_before, ma3_last):
    frame = _volume_frame(recent_volume)
    frame["ma3"] = [100.0] * 20
    frame.loc[frame.index[-3], "ma3"] = ma3_before
    frame.loc[frame.index[-2], "ma3"] = ma3_last
    return frame


@pytest.mark.parametrize("side,ma3_before,ma3_last", [
    ("LONG", 101.0, 100.5),   # 多單 MA3 轉下
    ("SHORT", 100.0, 100.6),  # 空單 MA3 轉上
])
def test_volume_decay_exits_even_in_loss(side, ma3_before, ma3_last):
    from core.services.swing_service import volume_decay_exit_ready

    frame = _decay_frame(20.0, ma3_before, ma3_last)
    assert volume_decay_exit_ready(frame, side, net_profitable=False) is True
    assert volume_decay_exit_ready(frame, side, net_profitable=False, require_profit=True) is False


def test_volume_decay_requires_ma3_to_turn():
    from core.services.swing_service import volume_decay_exit_ready

    flat = _decay_frame(20.0, 100.0, 100.0)
    assert volume_decay_exit_ready(flat, "SHORT", net_profitable=False) is False


def test_volume_decay_requires_shrinking_volume():
    from core.services.swing_service import volume_decay_exit_ready

    loud = _decay_frame(120.0, 100.0, 100.6)
    assert volume_decay_exit_ready(loud, "SHORT", net_profitable=False) is False
