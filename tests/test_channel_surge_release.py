"""Later closed green activity replaces an older surge veto, not other gates."""
import pandas as pd
import pytest

from core.services.strategies.outer_strategy import aligned_entry, outside_reentry
from core.channel_surge_entry import surge_recovery_entry
from test_channel_aligned_entry import aligned_frame


def frame_with_surge():
    frame = aligned_frame()
    frame.loc[17, ["open", "high", "low", "close"]] = [95., 105., 94., 104.]
    return frame


@pytest.mark.parametrize("case", ["green", "red", "doji", "small", "live_only", "new_surge", "invalid"])
def test_only_later_closed_effective_green_releases_surge(case):
    frame = frame_with_surge()
    if case == "red":
        frame.loc[18, ["open", "close"]] = [100.3, 100.2]
    elif case == "doji":
        frame.loc[18, "open"] = frame.loc[18, "close"]
    elif case == "small":
        frame.loc[18, "open"] = 100.29
    elif case == "live_only":
        frame = frame.iloc[:-1]
    elif case == "new_surge":
        frame.loc[19, ["open", "high", "low", "close"]] = [95., 105., 94., 104.]
    elif case == "invalid":
        frame.loc[18, "low"] = float("nan")
    result = surge_recovery_entry(frame, float(frame.iloc[-1]["close"]))
    if case == "green":
        assert result is None
    else:
        assert result is not None and result["action"] == "WAIT"


def test_release_uses_current_alignment_without_requiring_a_trough():
    frame = frame_with_surge()
    price = float(frame.iloc[-1]["close"])
    assert aligned_entry(frame, price)["side"] == "LONG"
    assert outside_reentry(frame, price, "LONG")["side"] == "LONG"
    frame.loc[18, "ma15"] = frame.loc[17, "ma15"]
    assert aligned_entry(frame, price)["side"] is None
    assert outside_reentry(frame, price, "LONG")["side"] is None


def test_release_survives_later_red_but_not_a_new_surge():
    frame = frame_with_surge()
    frame = pd.concat([frame, frame.iloc[[-1]]], ignore_index=True)
    frame.loc[19, ["open", "close"]] = [100.4, 100.3]
    assert surge_recovery_entry(frame, 100.4) is None
    frame.loc[19, ["open", "high", "low", "close"]] = [95., 105., 94., 104.]
    assert surge_recovery_entry(frame, 100.4)["action"] == "WAIT"
