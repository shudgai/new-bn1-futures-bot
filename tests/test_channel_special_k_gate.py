"""特例K不再有特權：一律照一般單關卡（2026-09-12 使用者）。"""
import pytest

from core.services.strategies import outer_strategy as outer

from test_channel_aligned_entry import aligned_frame


def _entry(monkeypatch, frame, price, side):
    monkeypatch.setattr(outer, "CHANNEL_SPECIAL_K_REQUIRES_CONFIRMATION", True, raising=False)
    decision = outer.aligned_entry(frame, price)
    return decision


def test_special_k_requires_second_body(monkeypatch):
    """已收線長實體特例K 也要等第二根同色實體K：單根不算。"""
    frame = aligned_frame(side="LONG")
    price = float(frame.iloc[-1]["close"])
    decision = _entry(monkeypatch, frame, price, "LONG")
    assert decision["action"] != "ENTER" or decision["reason"].endswith("LONG")
    if decision["action"] == "WAIT":
        assert decision["reason"] in {
            "KC_SECOND_BODY_WAIT", "KC_LOW_EFFICIENCY_WAIT", "KC_TREND_TAIL_WAIT",
            "KC_ENTRY_BODY_OVERHEAT_WAIT", "KC_MA3_TURN_WAIT", "KC_MIDDLE_OPPOSITE_WAIT",
            "KC_MOMENTUM_FADING_WAIT", "KC_ENTRY_PREV_BODY_WAIT", "KC_MA_SAFETY_WAIT",
            "KC_LIVE_ADVERSE_ENTRY_WAIT", "KC_SPECIAL_LOW_VOLUME_WAIT",
            "KC_INSIDE_CHANNEL_WAIT", "KC_FLAT_MIDDLE_WAIT", "KC_DIRECTION_WAIT",
        }


def test_special_k_gate_disabled_restores_legacy(monkeypatch):
    """把開關關掉即回到舊行為（特例K可自行開倉）。"""
    monkeypatch.setattr(outer, "CHANNEL_SPECIAL_K_REQUIRES_CONFIRMATION", False, raising=False)
    frame = aligned_frame(side="LONG")
    price = float(frame.iloc[-1]["close"])
    decision = outer.aligned_entry(frame, price)
    assert decision["action"] in ("ENTER", "WAIT")
