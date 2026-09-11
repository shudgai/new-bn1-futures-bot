"""進場開關：即時破軌入口與末端禁開都必須可切換且可驗證。"""
import pytest

from core.services.strategies import outer_strategy
from test_channel_flat_middle_entry import _entry_frame


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_breakout_entry_can_be_disabled(side, monkeypatch):
    frame, price = _entry_frame(side, 0.20, breakout=True)
    monkeypatch.setattr(outer_strategy, "CHANNEL_LIVE_BODY_BREAKOUT_ENABLED", True)
    assert outer_strategy.aligned_entry(frame, price)["action"] == "ENTER"
    monkeypatch.setattr(outer_strategy, "CHANNEL_LIVE_BODY_BREAKOUT_ENABLED", False)
    assert outer_strategy.aligned_entry(frame, price)["action"] == "WAIT"


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_one_way_run_blocks_only_when_the_tail_rule_is_enabled(side, monkeypatch):
    frame, _ = _entry_frame(side, 0.20)
    sign = 1 if side == "LONG" else -1
    frame.loc[frame.index[-14:-1], "kc_middle"] = [100.0 + sign * 0.6 * step for step in range(13)]
    monkeypatch.setattr(outer_strategy, "CHANNEL_TAIL_MAX_TREND_BARS", 12)
    assert outer_strategy.channel_tail_entry_blocked(frame, side) is True
    monkeypatch.setattr(outer_strategy, "CHANNEL_TAIL_MAX_TREND_BARS", 0)
    assert outer_strategy.channel_tail_entry_blocked(frame, side) is False
