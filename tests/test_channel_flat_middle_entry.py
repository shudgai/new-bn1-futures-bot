"""KC 中軌走平（平行／盤整）禁開的入口測試。

授權：2026-09-11 使用者要求「KC 走平行時不要開倉」。
判定用「最近兩根已收線中軌位移 ÷ 軌寬」，與價格尺度無關，
因此低價幣（例如 1000PEPE）不會被固定百分比誤擋。
"""
import pandas as pd
import pytest

from core.services.strategies import outer_strategy
from core.services.strategies.outer_strategy import (
    FLAT_MIDDLE_REASON,
    aligned_entry,
    channel_middle_is_flat,
)
from channel_test_frames import closed_outer_entry_frame

SYMBOL_SCALE = 1e-5


def _entry_frame(side, ratio, breakout=False, scale=1.0):
    """共用進場框架；ratio = 已收線中軌位移 ÷ 軌寬，可切換成即時破軌入口。"""
    f = closed_outer_entry_frame(side)
    sign = 1 if side == "LONG" else -1
    width = float(f["kc_upper"].iloc[-2]) - float(f["kc_lower"].iloc[-2])
    latest = float(f["kc_middle"].iloc[-2])
    f.loc[f.index[-3], "kc_middle"] = latest - sign * ratio * width
    if breakout:
        row = f.index[-1]
        rail = float(f["kc_upper"].iloc[-1]) if side == "LONG" else float(f["kc_lower"].iloc[-1])
        atr = float(f["atr"].iloc[-1])
        opened = rail - sign * 0.35 * atr
        f.loc[row, "open"] = opened
        f.loc[row, "close"] = rail + sign * 0.35 * atr
        f.loc[row, "high"] = max(opened, float(f.loc[row, "close"])) + atr * 0.1
        f.loc[row, "low"] = min(opened, float(f.loc[row, "close"])) - atr * 0.1
    if scale != 1.0:
        for key in ("open", "close", "high", "low", "kc_upper", "kc_lower",
                    "kc_middle", "ema_20", "ma3", "ma15"):
            f[key] = f[key].astype(float) * scale
    return f, float(f["close"].iloc[-1])


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_flat_middle_blocks_trend_entry(side):
    frame, price = _entry_frame(side, 0.01)
    decision = aligned_entry(frame, price)
    assert decision["action"] == "WAIT"
    assert decision["reason"] == FLAT_MIDDLE_REASON


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_flat_middle_blocks_live_body_breakout_entry(side):
    sloped, sloped_price = _entry_frame(side, 0.20, breakout=True)
    assert aligned_entry(sloped, sloped_price)["action"] == "ENTER"
    flat, flat_price = _entry_frame(side, 0.005, breakout=True)
    decision = aligned_entry(flat, flat_price)
    assert decision["action"] == "WAIT"
    assert decision["reason"] == FLAT_MIDDLE_REASON


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_sloped_middle_still_enters(side):
    frame, price = _entry_frame(side, 0.20)
    decision = aligned_entry(frame, price)
    assert decision["action"] == "ENTER"
    assert decision["side"] == side


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
@pytest.mark.parametrize("ratio_multiple,expected", [(0.9, True), (1.1, False), (4.0, False)])
def test_threshold_boundary(side, ratio_multiple, expected):
    from core import config
    frame, _ = _entry_frame(side, config.CHANNEL_FLAT_MIDDLE_RATIO * ratio_multiple)
    assert channel_middle_is_flat(frame) is expected


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_flat_rule_is_price_scale_invariant(side):
    flat_big, _ = _entry_frame(side, 0.01)
    flat_small, _ = _entry_frame(side, 0.01, scale=SYMBOL_SCALE)
    assert channel_middle_is_flat(flat_big) is True
    assert channel_middle_is_flat(flat_small) is True
    sloped_big, _ = _entry_frame(side, 0.20)
    sloped_small, _ = _entry_frame(side, 0.20, scale=SYMBOL_SCALE)
    assert channel_middle_is_flat(sloped_big) is False
    assert channel_middle_is_flat(sloped_small) is False


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_zero_ratio_disables_the_filter(side, monkeypatch):
    monkeypatch.setattr(outer_strategy, "CHANNEL_FLAT_MIDDLE_RATIO", 0.0)
    frame, price = _entry_frame(side, 0.01)
    assert channel_middle_is_flat(frame) is False
    assert aligned_entry(frame, price)["action"] == "ENTER"


def test_invalid_or_missing_data_is_not_flagged_flat():
    assert channel_middle_is_flat(None) is False
    assert channel_middle_is_flat(pd.DataFrame()) is False
    broken = pd.DataFrame({"kc_middle": [1.0, 2.0, 3.0, 4.0]})
    assert channel_middle_is_flat(broken) is False
    nan_frame = closed_outer_entry_frame("LONG")
    nan_frame.loc[nan_frame.index[-2], "kc_middle"] = float("nan")
    assert channel_middle_is_flat(nan_frame) is False
