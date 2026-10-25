import numpy as np
import pandas as pd

from core.services.market_consolidation_filter import (
    evaluate_market_blacklist,
    is_market_in_consolidation,
)
from core.gates.central_entry_gate import CentralEntryGate


def _base_frame(size=24):
    idx = np.arange(size, dtype=float)
    close = 100.0 + idx * 0.12
    middle = 100.0 + idx * 0.10
    frame = pd.DataFrame({
        "timestamp": 1_800_000_000_000 + idx * 60_000,
        "open": close - 0.20,
        "high": close + 0.25,
        "low": close - 0.23,
        "close": close,
        "volume": np.full(size, 1000.0),
        "atr": np.full(size, 0.50),
        "adx": np.full(size, 25.0),
        "ma3": middle + 1.0,
        "ma15": middle,
        "kc_middle": middle,
        "kc_upper": middle + 2.0,
        "kc_lower": middle - 2.0,
        "is_closed": np.ones(size, dtype=bool),
    })
    return frame


def test_mock_fast_ma3_midline_traversals_are_blocked():
    frame = _base_frame()
    # Three low-amplitude closed-bar traversals within the 10-bar window.
    offsets = np.array([0.10, -0.10, 0.10, -0.10, 0.10, 0.20, 0.30, 0.40, 0.50, 0.60])
    frame.loc[14:, "ma3"] = frame.loc[14:, "kc_middle"].to_numpy() + offsets
    blocked, reason, details = evaluate_market_blacklist(frame, -1)
    assert blocked is True
    assert reason == "VETO_MA3_KC_MIDLINE_CHOP"
    assert details["ma3_midline_crossings"] >= 3
    assert is_market_in_consolidation(frame, -1) is True


def test_mock_kc_big_mouth_expansion_is_blocked():
    frame = _base_frame()
    frame.loc[frame.index[-1], "kc_upper"] += 6.0
    blocked, reason, details = evaluate_market_blacklist(frame, -1)
    assert blocked is True
    assert reason == "VETO_KC_BIG_MOUTH_EXPANSION"
    assert details["kc_big_mouth_ratio"] >= 2.0


def test_unclosed_signal_bar_fails_closed():
    frame = _base_frame()
    frame.loc[frame.index[-1], "is_closed"] = False
    blocked, reason, _ = evaluate_market_blacklist(frame, -1)
    assert blocked is True
    assert reason == "VETO_CONSOLIDATION_INVALID_OR_OPEN_BAR"


def test_central_entry_gate_runs_blacklist_before_signal_checks():
    frame = _base_frame()
    offsets = np.array([0.10, -0.10, 0.10, -0.10, 0.10, 0.20, 0.30, 0.40, 0.50, 0.60])
    frame.loc[14:, "ma3"] = frame.loc[14:, "kc_middle"].to_numpy() + offsets
    result = CentralEntryGate.verify_and_authorize(
        symbol="MOCK/USDT", side="LONG", price=float(frame.iloc[-1]["close"]),
        klines_or_snapshot=frame, source="MOCK_MA_CROSS",
    )
    assert result.passed is False
    assert result.reason == "VETO_MA3_KC_MIDLINE_CHOP"


def test_volume_contraction_macro_conflict_and_cooldown_are_vetoes():
    frame = _base_frame()
    frame.loc[frame.index[-1], "volume"] = 700.0
    blocked, reason, _ = evaluate_market_blacklist(frame, -1)
    assert blocked and reason == "VETO_VOLUME_CONTRACTION"

    frame = _base_frame()
    blocked, reason, _ = evaluate_market_blacklist(frame, -1, side="LONG", macro_trend="BEARISH")
    assert blocked and reason == "VETO_MACRO_TREND_CONFLICT"

    current_bar = int(frame.iloc[-1]["timestamp"] // 60_000)
    blocked, reason, _ = evaluate_market_blacklist(
        frame, -1, last_closed_trade={"bar_index": current_bar - 2},
    )
    assert blocked and reason == "VETO_TRADE_COOLDOWN"
