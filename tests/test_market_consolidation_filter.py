import numpy as np
import pandas as pd
import pytest

from core.services.market_consolidation_filter import (
    check_entry_signal,
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
        "ma5": middle + 1.0,
        "ma15": middle,
        "kc_middle": middle,
        "kc_upper": middle + 2.0,
        "kc_lower": middle - 2.0,
        "is_closed": np.ones(size, dtype=bool),
    })
    return frame


def _low_volume_directional_frame(side, body_atr=0.512):
    frame = _base_frame()
    if side == "SHORT":
        for key in ("open", "close", "ma5", "ma15", "kc_middle"):
            frame[key] = 200.0 - frame[key]
        frame["high"], frame["low"] = 200.0 - frame["low"], 200.0 - frame["high"]
        frame["kc_upper"], frame["kc_lower"] = (
            200.0 - frame["kc_lower"], 200.0 - frame["kc_upper"],
        )

    last = frame.index[-1]
    close = float(frame.loc[last, "close"])
    body = body_atr * float(frame.loc[last, "atr"])
    sign = 1 if side == "LONG" else -1
    opened = close - sign * body
    frame.loc[last, ["open", "high", "low", "volume"]] = [
        opened, max(opened, close) + 0.05, min(opened, close) - 0.05, 535.9,
    ]
    return frame


def test_mock_fast_ma5_midline_traversals_are_blocked():
    frame = _base_frame()
    # Three low-amplitude closed-bar traversals within the 10-bar window.
    offsets = np.array([0.10, -0.10, 0.10, -0.10, 0.10, 0.20, 0.30, 0.40, 0.50, 0.60])
    frame.loc[14:, "ma5"] = frame.loc[14:, "kc_middle"].to_numpy() + offsets
    blocked, reason, details = evaluate_market_blacklist(frame, -1)
    assert blocked is True
    assert reason == "VETO_MA5_KC_MIDLINE_CHOP"
    assert details["ma5_midline_crossings"] >= 3
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
    frame.loc[14:, "ma5"] = frame.loc[14:, "kc_middle"].to_numpy() + offsets
    result = CentralEntryGate.verify_and_authorize(
        symbol="MOCK/USDT", side="LONG", price=float(frame.iloc[-1]["close"]),
        klines_or_snapshot=frame, source="MOCK_MA_CROSS",
    )
    assert result.passed is False
    assert result.reason == "VETO_MA5_KC_MIDLINE_CHOP"


def test_volume_contraction_macro_conflict_and_cooldown_are_vetoes():
    frame = _base_frame()
    frame.loc[frame.index[-1], "volume"] = 700.0
    blocked, reason, _ = evaluate_market_blacklist(frame, -1)
    assert blocked and reason == "VETO_VOLUME_CONTRACTION"

    frame = _base_frame()
    blocked, reason, _ = evaluate_market_blacklist(frame, -1, side="LONG", macro_trend="BEARISH")
    assert not blocked, reason
    frame.loc[frame.index[-1], "kc_middle"] = frame.iloc[-2]["kc_middle"] - 0.1
    blocked, reason, _ = evaluate_market_blacklist(frame, -1, side="LONG", macro_trend="BULLISH")
    assert blocked and reason == "VETO_MACRO_TREND_CONFLICT"

    current_bar = int(frame.iloc[-1]["timestamp"] // 60_000)
    blocked, reason, _ = evaluate_market_blacklist(
        frame, -1, last_closed_trade={"bar_index": current_bar - 2},
    )
    assert blocked and reason == "VETO_TRADE_COOLDOWN"


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
@pytest.mark.parametrize("authority", ["expansion", "meme_structural_entry"])
@pytest.mark.parametrize("body_atr", [0.3, 0.4, 0.512])
def test_confirmed_meme_structure_can_clear_volume_contraction(
    side, authority, body_atr,
):
    frame = _low_volume_directional_frame(side, body_atr)
    structural_authority = {authority: True}
    allowed = authority == "meme_structural_entry" or body_atr >= 0.4

    blocked, reason, checks = evaluate_market_blacklist(
        frame,
        side=side,
        meme_policy=True,
        **structural_authority,
    )

    assert checks["volume_ratio"] < 0.8
    assert checks["meme_structural_volume_override"] is allowed
    assert blocked is (not allowed)
    assert reason == ("PASS_MARKET_BLACKLIST" if allowed else "VETO_VOLUME_CONTRACTION")


@pytest.mark.parametrize(
    "meme_policy,expansion,meme_structural_entry",
    [(False, True, False), (True, False, False)],
)
def test_volume_contraction_override_requires_meme_structure(
    meme_policy, expansion, meme_structural_entry,
):
    frame = _low_volume_directional_frame("SHORT")

    blocked, reason, checks = evaluate_market_blacklist(
        frame,
        side="SHORT",
        meme_policy=meme_policy,
        expansion=expansion,
        meme_structural_entry=meme_structural_entry,
    )

    assert blocked and reason == "VETO_VOLUME_CONTRACTION"
    assert checks["meme_structural_volume_override"] is False


def test_central_entry_gate_accepts_revalidated_meme_method_with_low_volume(monkeypatch):
    import time
    from types import SimpleNamespace

    import core.gates.master_gate_funnel as master_gate_funnel
    import core.services.entry_methods as entry_methods
    from core.gates.central_entry_gate import CentralEntryGate

    frame = _low_volume_directional_frame("SHORT")
    frame.attrs.update(
        timeframe_ms=60_000,
        snapshot_ms=time.time() * 1000,
        entry_method_context={
            "symbol": "CAP/USDT",
            "entry_method_authority": "MEME_GATE_ENTRY",
        },
    )
    quote = float(frame.iloc[-1]["close"])
    monkeypatch.setattr(
        entry_methods, "revalidate_entry_method",
        lambda *_args, **_kwargs: (True, "ENTRY_METHOD_REVALIDATED"),
    )
    monkeypatch.setattr(
        master_gate_funnel, "evaluate_meme_entry_frame",
        lambda *_args, **_kwargs: SimpleNamespace(
            can_execute=True, reason="PASS_MEME_GATE", details={},
        ),
    )
    monkeypatch.setattr(
        master_gate_funnel, "market_snapshot_from_frame",
        lambda *_args, **_kwargs: SimpleNamespace(),
    )
    for name in (
        "is_expansion", "is_early_long", "is_squeeze_breakout_long",
        "is_top_pivot_short",
    ):
        monkeypatch.setattr(
            master_gate_funnel.MasterGateFunnel, name,
            lambda *_args, **_kwargs: False,
        )
    monkeypatch.setattr(
        master_gate_funnel.MasterGateFunnel, "priority_channel",
        lambda *_args, **_kwargs: None,
    )

    result = CentralEntryGate.verify_and_authorize(
        symbol="CAP/USDT",
        side="SHORT",
        price=quote,
        klines_or_snapshot=frame,
        source="UNIFIED_PIPELINE|ENTRY_METHOD|MEME_GATE_ENTRY",
    )

    assert result.passed, result.reason
    assert result.checks["market_blacklist"]["evidence"]["meme_structural_volume_override"]


def test_zero_trust_entry_gate_rules_a_to_h_and_check_entry_signal():
    # Valid trend base frame -> check_entry_signal returns OPEN_LONG
    valid_df = _base_frame()
    valid_df["ma3"] = valid_df["ma5"] + 0.15
    valid_df["close"] = valid_df["ma3"] + 0.10
    valid_df["open"] = valid_df["close"] - 0.25
    valid_df["high"] = valid_df["close"] + 0.05
    valid_df["low"] = valid_df["open"] - 0.05
    res_valid = check_entry_signal(valid_df, -1, side="LONG")
    assert res_valid["action"] == "OPEN_LONG"
    assert res_valid["can_execute"] is True

    # 0. Unclosed bar (is_kline_closed == False) -> 100% ACTION: PASS
    unclosed_df = valid_df.copy()
    unclosed_df.loc[unclosed_df.index[-1], "is_closed"] = False
    res_unclosed = check_entry_signal(unclosed_df, -1, side="LONG")
    assert res_unclosed["action"] == "PASS"
    assert res_unclosed["reason"] == "VETO_BAR_NOT_CLOSED"
    assert is_market_in_consolidation(unclosed_df, -1) is True

    # Rule A: Flat MA3 / KC Middle -> ACTION: PASS
    flat_df = valid_df.copy()
    flat_df.loc[flat_df.index[-10:], "ma3"] = 102.0
    res_a = check_entry_signal(flat_df, -1, side="LONG")
    assert res_a["action"] == "PASS"
    assert res_a["reason"] == "VETO_FLAT_MA5_OR_KC_MIDLINE"

    # Rule B: Wave Entanglement (MA3 / MA15 >= 2 crosses in last 8 bars) -> ACTION: PASS
    wave_df = valid_df.copy()
    wave_offsets = np.array([0.20, -0.20, 0.25, -0.25, 0.30, 0.35, 0.40, 0.50])
    wave_df.loc[wave_df.index[-8:], "ma3"] = (
        wave_df.loc[wave_df.index[-8:], "ma15"].to_numpy() + wave_offsets
    )
    res_b = check_entry_signal(wave_df, -1, side="LONG")
    assert res_b["action"] == "PASS"
    assert res_b["reason"] == "VETO_MA5_MA15_CHOP"
    assert res_b["details"]["ma3_ma15_recent_crossings"] >= 2

    # Rule C: KC Big Mouth (Bandwidth >= 2.0x average bandwidth) -> ACTION: PASS
    horn_df = valid_df.copy()
    horn_df.loc[horn_df.index[-1], "kc_upper"] += 6.0
    res_c = check_entry_signal(horn_df, -1, side="LONG")
    assert res_c["action"] == "PASS"
    assert res_c["reason"] == "VETO_KC_BIG_MOUTH_EXPANSION"
    assert res_c["details"]["kc_big_mouth_ratio"] >= 2.0

    # Rule D: Donchian Range Box + Low ADX (< 20) & Low ATR -> ACTION: PASS
    box_df = valid_df.copy()
    box_df.loc[box_df.index[-2], "high"] = float(box_df.iloc[-1]["close"]) + 1.0
    box_df.loc[box_df.index[-2], "low"] = float(box_df.iloc[-1]["close"]) - 1.0
    box_df.loc[box_df.index[-1], "adx"] = 14.0
    box_df.loc[box_df.index[-1], "atr"] = 0.10
    res_d = check_entry_signal(box_df, -1, side="LONG")
    assert res_d["action"] == "PASS"
    assert res_d["reason"] == "VETO_DONCHIAN_RANGE_LOW_TREND"

    # Rule E: Volume Contraction (< 0.8x 5-bar MA) -> ACTION: PASS
    vol_df = valid_df.copy()
    vol_df.loc[vol_df.index[-1], "volume"] = 600.0
    res_e = check_entry_signal(vol_df, -1, side="LONG")
    assert res_e["action"] == "PASS"
    assert res_e["reason"] == "VETO_VOLUME_CONTRACTION"

    # Rule F: Long Wick Spinning Top / Doji (total wick > 2.0x body) -> ACTION: PASS
    wick_df = valid_df.copy()
    last_idx = wick_df.index[-1]
    c_val = float(wick_df.loc[last_idx, "close"])
    wick_df.loc[last_idx, "open"] = c_val - 0.10
    wick_df.loc[last_idx, "high"] = c_val + 0.25
    wick_df.loc[last_idx, "low"] = c_val - 0.30
    res_f = check_entry_signal(wick_df, -1, side="LONG")
    assert res_f["action"] == "PASS"
    assert res_f["reason"] == "VETO_LONG_WICK_DOJI"

    # Rule G: Macro / HTF KC Trend Conflict -> ACTION: PASS
    htf_df = valid_df.copy()
    htf_df["macro_kc_middle_slope"] = -0.25
    res_g = check_entry_signal(htf_df, -1, side="LONG")
    assert res_g["action"] == "PASS"
    assert res_g["reason"] == "VETO_MACRO_TREND_CONFLICT"

    # Rule H: Trade Cooldown (< 3 bars since last exit) -> ACTION: PASS
    res_h = check_entry_signal(valid_df, -1, side="LONG", bars_since_last_exit=1, cooldown_bars=3)
    assert res_h["action"] == "PASS"
    assert res_h["reason"] == "VETO_TRADE_COOLDOWN"
    assert res_h["details"]["bars_since_exit"] == 1


def test_meme_dead_chop_adx_with_midline_and_ma_crossings_blocked():
    """回歸測試：1000PEPE/USDT ADX < 15 死魚盤整期間即便觸發 meme_structural_entry，中軌與均線反覆交錯仍須攔截。"""
    df = _base_frame()
    df["adx"] = 13.38
    # 1. 中軌穿刺 >= 3 次 + ADX < 15 -> 必須觸發 VETO_MA5_KC_MIDLINE_CHOP（含 breakout/meme 通道）
    mid_chop = df.copy()
    mid_offsets = np.array([0.05, -0.05, 0.05, -0.05, 0.05, -0.05, 0.05, 0.08, 0.10, 0.12])
    mid_chop.loc[mid_chop.index[-10:], "ma5"] = (
        mid_chop.loc[mid_chop.index[-10:], "kc_middle"].to_numpy() + mid_offsets
    )
    blocked_mid, reason_mid, _ = evaluate_market_blacklist(
        mid_chop, -1, side="LONG", meme_policy=True, is_breakout=True, meme_structural_entry=True,
    )
    assert blocked_mid is True
    assert reason_mid == "VETO_MA5_KC_MIDLINE_CHOP"

    # 2. 近 8 根均線交錯 >= 2 次且 ADX < 15（或 20 根累計 >= 5 次）-> 必須觸發 VETO_MA5_MA15_CHOP
    ma_chop = df.copy()
    ma_chop["ma3"] = ma_chop["ma5"]
    wave_offsets = np.array([0.12, -0.12, 0.12, -0.12, 0.12, 0.14, 0.16, 0.18])
    ma_chop.loc[ma_chop.index[-8:], "ma3"] = (
        ma_chop.loc[ma_chop.index[-8:], "ma15"].to_numpy() + wave_offsets
    )
    blocked_ma, reason_ma, _ = evaluate_market_blacklist(
        ma_chop, -1, side="LONG", meme_policy=True, is_breakout=True, meme_structural_entry=True,
    )
    assert blocked_ma is True
    assert reason_ma == "VETO_MA5_MA15_CHOP"

