import pandas as pd

from core.engine import TradingEngine


def frame(side="LONG", mature=True):
    rows = 5
    values = list(range(rows))
    sign = 1 if side == "LONG" else -1
    middle = [100.0 + sign * value for value in values]
    if mature:
        ma3 = [middle_value + sign * 1.0 for middle_value in middle]
        ma15 = middle
        upper = [110.0 + sign * value for value in values]
        lower = [90.0 + sign * value for value in values]
    else:
        ma3 = [middle_value + sign * 1.0 for middle_value in middle]
        ma15 = middle
        upper = [110.0] * rows
        lower = [90.0] * rows
    return pd.DataFrame({
        "open": middle,
        "high": [value + sign * 1.0 for value in middle],
        "low": [value - sign * 1.0 for value in middle],
        "close": [value + sign * 0.5 for value in middle],
        "ma3": ma3,
        "ma15": ma15,
        "atr": [1.0] * rows,
        "volume": [1.0] * rows,
        "kc_upper": upper,
        "kc_lower": lower,
    })


def test_mature_long_outer_entry_waits_for_clear_ck(monkeypatch):
    monkeypatch.setattr(
        "core.engine.aligned_entry",
        lambda frame, price: {"action": "ENTER", "side": "LONG", "reason": "KC_OUTSIDE_LONG"},
    )
    result = TradingEngine._channel_swing_action(frame("LONG"), 112.0)
    assert result == {"action": "WAIT", "side": None, "reason": "KC_TREND_END_WAIT"}


def test_mature_short_outer_entry_waits_for_clear_ck(monkeypatch):
    monkeypatch.setattr(
        "core.engine.aligned_entry",
        lambda frame, price: {"action": "ENTER", "side": "SHORT", "reason": "KC_OUTSIDE_SHORT"},
    )
    result = TradingEngine._channel_swing_action(frame("SHORT"), 88.0)
    assert result == {"action": "WAIT", "side": None, "reason": "KC_TREND_END_WAIT"}


def test_non_mature_entry_is_not_blocked_by_trend_end_guard(monkeypatch):
    monkeypatch.setattr(
        "core.engine.aligned_entry",
        lambda frame, price: {"action": "ENTER", "side": "LONG", "reason": "KC_OUTSIDE_LONG"},
    )
    result = TradingEngine._channel_swing_action(frame("LONG", mature=False), 112.0)
    assert result == {"action": "ENTER", "side": "LONG", "reason": "KC_OUTSIDE_LONG"}


def test_confirmed_volume_recovery_releases_mature_guard(monkeypatch):
    monkeypatch.setattr(
        "core.engine.aligned_entry",
        lambda frame, price: {"action": "ENTER", "side": "SHORT", "reason": "KC_OUTSIDE_SHORT"},
    )
    recovered = frame("SHORT")
    recovered.loc[recovered.index[-2], "volume"] = 2.0
    result = TradingEngine._channel_swing_action(recovered, 88.0)
    assert result == {"action": "ENTER", "side": "SHORT", "reason": "KC_OUTSIDE_SHORT"}
