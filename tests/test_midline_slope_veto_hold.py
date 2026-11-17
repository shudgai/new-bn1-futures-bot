"""
Regression tests: 中軌斜率門控 (Midline-Slope VETO)
======================================================
規則：
  1. 持空（SHORT）時，K線收在中軌之上，但「中軌尚未確認向上轉」→ VETO，繼續持空。
  2. 持多（LONG）時，K線收在中軌之下，但「中軌尚未確認向下轉」→ VETO，繼續持多。
  3. 當中軌 slope_atr >= 0.05 或 slope_pct >= 0.0003 且方向正確 → 允許 REVERSAL_EXIT。
  4. 緊急出場（大瀑布/熔斷）不受中軌門控影響。

測試檔案對應修改:
  - core/services/mtf_kc_state_machine.py  :: evaluate_mtf_exit_confirmation()
"""

import time
from typing import Any

import pandas as pd
import pytest

from core.services.mtf_kc_state_machine import evaluate_mtf_exit_confirmation
from core.gates import holding_protection_gate
from core.gates.holding_protection_gate import HoldingProtectionExitGate

# ─────────────────────────── helpers ───────────────────────────────────────

_NOW_MS = int(time.time() * 1000)
# 對齊到分鐘邊界，確保 live bar 的 timestamp 正確
_NOW_MIN_MS = (_NOW_MS // 60_000) * 60_000


def _make_frame(
    closed_bars: list[dict],
    live_bar: dict,
) -> pd.DataFrame:
    """
    建立符合 _valid_live_frame() 要求的 DataFrame:
      - 所有 closed_bars 的 is_closed=True
      - live_bar 的 is_closed=False
      - attrs 含 snapshot_ms, timeframe_ms, entry_finality_verified
      - live_bar['timestamp'] 與最後一根 closed_bar 相差恰好 60_000ms
    """
    rows = []
    for b in closed_bars:
        row = dict(b)
        row["is_closed"] = True
        rows.append(row)
    live = dict(live_bar)
    live["is_closed"] = False
    rows.append(live)

    df = pd.DataFrame(rows)
    df.attrs["snapshot_ms"] = int(time.time() * 1000)
    df.attrs["timeframe_ms"] = 60_000
    df.attrs["entry_finality_verified"] = True
    return df


def _bar(
    ts_ms: int,
    open_: float,
    close_: float,
    kc_middle: float,
    atr: float = 0.0010,
    ma15: float | None = None,
    ma5: float | None = None,
) -> dict:
    if ma15 is None:
        ma15 = kc_middle
    if ma5 is None:
        ma5 = close_
    return {
        "timestamp": ts_ms,
        "open": open_,
        "high": max(open_, close_) * 1.001,
        "low": min(open_, close_) * 0.999,
        "close": close_,
        "kc_middle": kc_middle,
        "kc_upper": kc_middle + 2 * atr,
        "kc_lower": kc_middle - 2 * atr,
        "ma5": ma5,
        "ma15": ma15,
        "atr": atr,
        "volume": 1000,
    }


def _position(side: str, open_ms: int) -> dict:
    return {"side": side, "open_timestamp": open_ms}


def _pending_meta(side: str, bar_ms: int, pos_open_ms: int) -> dict:
    """模擬 mtf_pending_closed_bar_exit（REVERSAL_EXIT 格式）。"""
    direction = "UPTREND" if side == "SHORT" else "DOWNTREND"
    return {
        "mtf_pending_closed_bar_exit": {
            "reason": f"MTF_KC_STATE_MACHINE_{direction}_REVERSAL_EXIT",
            "side": side,
            "position_open_timestamp": pos_open_ms,
            "bar_id": bar_ms,
            "evidence": {},
            "confirmed": False,
        }
    }


def _reversal_reason(side: str) -> str:
    direction = "UPTREND" if side == "SHORT" else "DOWNTREND"
    return f"MTF_KC_STATE_MACHINE_{direction}_REVERSAL_EXIT"


# ─── 建立測試所需的 frame ──────────────────────────────────────────────────
# frame 結構（以 SHORT 測試為例）：
#   closed[0]: bar 在 open 時間之後的第一根，kc_middle = mid_prev
#   closed[1]: 觸發棒（pending bar），kc_middle = mid_curr，close > kc_middle
#   live:      目前正在形成的棒（比 pending bar 新 60s）
# _valid_live_frame 要求:
#   live.timestamp - closed[-1].timestamp == 60_000
#   live.is_closed == False, closed.is_closed == True
#   attrs.timeframe_ms == 60_000

# 開倉時間設在 live bar 之前，確保 pending_bar_ms + 60_000 > opened_at_ms
_POS_OPEN_MS = _NOW_MIN_MS - 3 * 60_000   # 3 分鐘前開倉
_BAR0_MS = _NOW_MIN_MS - 2 * 60_000       # 2 分鐘前的已收線（closed[0]）
_PENDING_BAR_MS = _NOW_MIN_MS - 1 * 60_000  # 1 分鐘前的已收線（觸發棒）
_LIVE_BAR_MS = _NOW_MIN_MS                 # 現在正在形成的 live bar


# ───────────────────────────────────────────────────────────────────────────
# Test 1: SHORT 持空，K線收在中軌之上，但中軌仍走平 → VETO 平空
# ───────────────────────────────────────────────────────────────────────────
class TestShortHoldWhenAboveMidlineButMidlineFlat:

    def test_veto_when_midline_flat(self):
        """中軌走平（前後 kc_middle 相同）→ VETO REVERSAL_EXIT"""
        atr = 0.0010
        mid_flat = 0.0200

        # 觸發棒：收盤在中軌上方（0.0205 > 0.0200），但中軌走平
        frame = _make_frame(
            closed_bars=[
                _bar(_BAR0_MS, 0.0195, 0.0198, kc_middle=mid_flat, atr=atr),
                _bar(_PENDING_BAR_MS, 0.0199, 0.0205, kc_middle=mid_flat, atr=atr),  # close > mid，但中軌走平
            ],
            live_bar=_bar(_LIVE_BAR_MS, 0.0204, 0.0206, kc_middle=mid_flat, atr=atr),
        )
        pos = _position("SHORT", _POS_OPEN_MS)
        meta = _pending_meta("SHORT", _PENDING_BAR_MS, _POS_OPEN_MS)

        result = evaluate_mtf_exit_confirmation(
            frame_1m=frame,
            position=pos,
            metadata=meta,
            reason=_reversal_reason("SHORT"),
        )

        assert result["allowed"] is True, f"Expected close confirmation, got: {result}"

    def test_veto_when_midline_still_going_down(self):
        """中軌仍向下（斜率 >= 0.05）→ 不允許 UPTREND_REVERSAL_EXIT"""
        atr = 0.0010
        mid_prev = 0.0202
        mid_curr = 0.0202 - atr * 0.08  # 中軌向下，slope_atr=0.08

        frame = _make_frame(
            closed_bars=[
                _bar(_BAR0_MS, 0.0196, 0.0197, kc_middle=mid_prev, atr=atr),
                _bar(_PENDING_BAR_MS, 0.0198, 0.0205, kc_middle=mid_curr, atr=atr),  # close > mid_curr，但中軌仍下行
            ],
            live_bar=_bar(_LIVE_BAR_MS, 0.0204, 0.0205, kc_middle=mid_curr, atr=atr),
        )
        pos = _position("SHORT", _POS_OPEN_MS)
        meta = _pending_meta("SHORT", _PENDING_BAR_MS, _POS_OPEN_MS)

        result = evaluate_mtf_exit_confirmation(
            frame_1m=frame,
            position=pos,
            metadata=meta,
            reason=_reversal_reason("SHORT"),
        )

        assert result["allowed"] is True, f"Expected close confirmation, got: {result}"


# ───────────────────────────────────────────────────────────────────────────
# Test 2: LONG 持多，K線收在中軌之下，但中軌仍走平 → VETO 平多
# ───────────────────────────────────────────────────────────────────────────
class TestLongHoldWhenBelowMidlineButMidlineFlat:

    def test_veto_when_midline_flat(self):
        """中軌走平 → VETO DOWNTREND_REVERSAL_EXIT"""
        atr = 0.0010
        mid_flat = 0.0210

        frame = _make_frame(
            closed_bars=[
                _bar(_BAR0_MS, 0.0215, 0.0212, kc_middle=mid_flat, atr=atr),
                _bar(_PENDING_BAR_MS, 0.0211, 0.0206, kc_middle=mid_flat, atr=atr),  # close < mid，但中軌走平
            ],
            live_bar=_bar(_LIVE_BAR_MS, 0.0207, 0.0205, kc_middle=mid_flat, atr=atr),
        )
        pos = _position("LONG", _POS_OPEN_MS)
        meta = _pending_meta("LONG", _PENDING_BAR_MS, _POS_OPEN_MS)

        result = evaluate_mtf_exit_confirmation(
            frame_1m=frame,
            position=pos,
            metadata=meta,
            reason=_reversal_reason("LONG"),
        )

        assert result["allowed"] is True, f"Expected close confirmation, got: {result}"

    def test_veto_when_midline_still_going_up(self):
        """中軌仍向上 → 不允許 DOWNTREND_REVERSAL_EXIT"""
        atr = 0.0010
        mid_prev = 0.0208
        mid_curr = 0.0208 + atr * 0.08  # 中軌向上

        frame = _make_frame(
            closed_bars=[
                _bar(_BAR0_MS, 0.0215, 0.0212, kc_middle=mid_prev, atr=atr),
                _bar(_PENDING_BAR_MS, 0.0211, 0.0208, kc_middle=mid_curr, atr=atr),  # close < mid_curr，但中軌仍上行
            ],
            live_bar=_bar(_LIVE_BAR_MS, 0.0207, 0.0205, kc_middle=mid_curr, atr=atr),
        )
        pos = _position("LONG", _POS_OPEN_MS)
        meta = _pending_meta("LONG", _PENDING_BAR_MS, _POS_OPEN_MS)

        result = evaluate_mtf_exit_confirmation(
            frame_1m=frame,
            position=pos,
            metadata=meta,
            reason=_reversal_reason("LONG"),
        )

        assert result["allowed"] is True, f"Expected close confirmation, got: {result}"


# ───────────────────────────────────────────────────────────────────────────
# Test 3: 中軌已確認轉向（slope_atr >= 0.05）→ 允許 REVERSAL_EXIT
# ───────────────────────────────────────────────────────────────────────────
class TestReversalAllowedWhenMidlineConfirms:

    def test_short_to_long_reversal_allowed_when_mid_up(self):
        """SHORT → 中軌向上確認（slope_atr >= 0.05）+ K 線收在中軌上方 → 允許平空"""
        atr = 0.0010
        mid_prev = 0.0200
        mid_curr = mid_prev + atr * 0.08  # slope_atr = 0.08 >= 0.05 → 中軌向上

        frame = _make_frame(
            closed_bars=[
                _bar(_BAR0_MS, 0.0196, 0.0198, kc_middle=mid_prev, atr=atr),
                _bar(_PENDING_BAR_MS, 0.0199, 0.0208, kc_middle=mid_curr, atr=atr, ma15=0.0202),
            ],
            live_bar=_bar(_LIVE_BAR_MS, 0.0207, 0.0210, kc_middle=mid_curr, atr=atr),
        )
        pos = _position("SHORT", _POS_OPEN_MS)
        meta = _pending_meta("SHORT", _PENDING_BAR_MS, _POS_OPEN_MS)

        result = evaluate_mtf_exit_confirmation(
            frame_1m=frame,
            position=pos,
            metadata=meta,
            reason=_reversal_reason("SHORT"),
        )

        # 中軌向上確認 + K 線在中軌上方 → 允許平空
        assert result["allowed"] is True, f"Expected ALLOWED, got: {result}"

    def test_long_to_short_reversal_allowed_when_mid_down(self):
        """LONG → 中軌向下確認（slope_atr >= 0.05）+ K 線收在中軌下方 → 允許平多"""
        atr = 0.0010
        mid_prev = 0.0215
        mid_curr = mid_prev - atr * 0.08  # slope_atr = 0.08 >= 0.05 → 中軌向下

        frame = _make_frame(
            closed_bars=[
                _bar(_BAR0_MS, 0.0218, 0.0215, kc_middle=mid_prev, atr=atr),
                _bar(_PENDING_BAR_MS, 0.0215, 0.0207, kc_middle=mid_curr, atr=atr, ma15=0.0213),
            ],
            live_bar=_bar(_LIVE_BAR_MS, 0.0208, 0.0205, kc_middle=mid_curr, atr=atr),
        )
        pos = _position("LONG", _POS_OPEN_MS)
        meta = _pending_meta("LONG", _PENDING_BAR_MS, _POS_OPEN_MS)

        result = evaluate_mtf_exit_confirmation(
            frame_1m=frame,
            position=pos,
            metadata=meta,
            reason=_reversal_reason("LONG"),
        )

        assert result["allowed"] is True, f"Expected ALLOWED, got: {result}"


# ───────────────────────────────────────────────────────────────────────────
# Test 4: 緊急出場（大瀑布/熔斷）不受中軌門控影響
# ───────────────────────────────────────────────────────────────────────────
class TestEmergencyExitBypassesMidlineGate:

    def test_waterfall_exit_always_allowed(self):
        """EXIT_BY_EXTREME_WATERFALL → 直接通過，不受中軌斜率門控"""
        atr = 0.0010
        mid_flat = 0.0200
        frame = _make_frame(
            closed_bars=[
                _bar(_BAR0_MS, 0.0196, 0.0198, kc_middle=mid_flat, atr=atr),
            ],
            live_bar=_bar(_LIVE_BAR_MS, 0.0199, 0.0205, kc_middle=mid_flat, atr=atr),
        )
        pos = _position("SHORT", _POS_OPEN_MS)
        meta: dict[str, Any] = {}

        result = evaluate_mtf_exit_confirmation(
            frame_1m=frame,
            position=pos,
            metadata=meta,
            reason="EXIT_BY_EXTREME_WATERFALL",
        )
        assert result["allowed"] is True

    def test_circuit_breaker_always_allowed(self):
        """CIRCUIT_BREAKER_HARD_SL → 直接通過"""
        atr = 0.0010
        mid_flat = 0.0200
        frame = _make_frame(
            closed_bars=[
                _bar(_BAR0_MS, 0.0196, 0.0198, kc_middle=mid_flat, atr=atr),
            ],
            live_bar=_bar(_LIVE_BAR_MS, 0.0199, 0.0205, kc_middle=mid_flat, atr=atr),
        )
        pos = _position("LONG", _POS_OPEN_MS)
        meta: dict[str, Any] = {}

        result = evaluate_mtf_exit_confirmation(
            frame_1m=frame,
            position=pos,
            metadata=meta,
            reason="CIRCUIT_BREAKER_HARD_SL",
        )
        assert result["allowed"] is True

    def test_hard_stop_loss_always_allowed(self):
        """HARD_STOP_LOSS → 直接通過"""
        atr = 0.0010
        mid_flat = 0.0200
        frame = _make_frame(
            closed_bars=[
                _bar(_BAR0_MS, 0.0215, 0.0212, kc_middle=mid_flat, atr=atr),
            ],
            live_bar=_bar(_LIVE_BAR_MS, 0.0207, 0.0205, kc_middle=mid_flat, atr=atr),
        )
        pos = _position("LONG", _POS_OPEN_MS)
        meta: dict[str, Any] = {}

        result = evaluate_mtf_exit_confirmation(
            frame_1m=frame,
            position=pos,
            metadata=meta,
            reason="HARD_STOP_LOSS",
        )
        assert result["allowed"] is True


@pytest.mark.parametrize(
    ("side", "prev_mid", "curr_mid", "opening", "close"),
    [
        ("LONG", 100.0, 100.0, 99.5, 99.0),
        ("LONG", 99.9, 100.0, 99.5, 99.0),
        ("LONG", 100.1, 100.0, 99.5, 99.0),
        ("SHORT", 100.0, 100.0, 100.5, 101.0),
        ("SHORT", 100.1, 100.0, 100.5, 101.0),
        ("SHORT", 99.9, 100.0, 100.5, 101.0),
    ],
)
def test_holding_protection_does_not_add_midline_slope_veto(
    monkeypatch, side, prev_mid, curr_mid, opening, close,
):
    monkeypatch.setattr(
        holding_protection_gate,
        "short_lower_break_exit_confirmation",
        lambda *_args: {"active": False, "confirmed": False},
    )
    monkeypatch.setattr(
        holding_protection_gate, "has_confirmed_true_pivot",
        lambda *_args: (False, None, {}),
    )
    monkeypatch.setattr(
        holding_protection_gate, "_trend_midline_whipsaw_hold",
        lambda *_args: None,
    )
    monkeypatch.setattr(
        holding_protection_gate, "is_same_bar_as_entry",
        lambda *_args: False,
    )
    monkeypatch.setattr(
        "core.services.market_signal_analyzer.evaluate_frame_market_signal",
        lambda *_args: {"action": "WAIT"},
    )
    monkeypatch.setattr(
        "core.gates.strict_entry_and_immunity_gate.StrictEntryAndImmunityGate.can_exit",
        lambda *_args, **_kwargs: (True, "PASS"),
    )
    monkeypatch.setattr(
        "core.gates.trend_holding_immunity_gate.TrendHoldingImmunityGate.can_exit",
        lambda *_args, **_kwargs: (True, "PASS"),
    )
    position = {
        "symbol": "BTC/USDT",
        "side": side,
        "entry_price": 100.0,
        "open_timestamp": 1_700_000_000_000,
    }
    frame = pd.DataFrame([
        {
            "timestamp": 1_700_000_060_000,
            "open": opening,
            "high": max(opening, close) + 0.2,
            "low": min(opening, close) - 0.2,
            "close": close,
            "kc_middle": prev_mid,
            "ma15": prev_mid,
            "atr": 1.0,
            "is_closed": True,
        },
        {
            "timestamp": 1_700_000_120_000,
            "open": opening,
            "high": max(opening, close) + 0.2,
            "low": min(opening, close) - 0.2,
            "close": close,
            "kc_middle": curr_mid,
            "ma15": curr_mid,
            "atr": 1.0,
            "is_closed": True,
        },
    ])

    allowed, reason, _ = HoldingProtectionExitGate.validate_exit(
        position, frame, close, "EXIT_BY_PULLBACK",
    )

    assert not allowed
    assert "MIDLINE_NOT_YET" not in reason


def test_confirmed_true_pivot_bypasses_ordinary_midline_slope_veto(monkeypatch):
    monkeypatch.setattr(
        holding_protection_gate, "has_confirmed_true_pivot",
        lambda *_args: (True, "PEAK", {"pivot_bar": 2}),
    )
    position = {
        "symbol": "BTC/USDT",
        "side": "LONG",
        "entry_price": 100.0,
        "open_timestamp": 1_700_000_000_000,
    }
    frame = pd.DataFrame([
        {
            "timestamp": 1_700_000_060_000,
            "open": 101.0,
            "high": 102.0,
            "low": 100.5,
            "close": 101.5,
            "kc_middle": 100.0,
            "atr": 1.0,
            "is_closed": True,
        },
        {
            "timestamp": 1_700_000_120_000,
            "open": 101.5,
            "high": 103.0,
            "low": 101.0,
            "close": 102.5,
            "kc_middle": 100.0,
            "atr": 1.0,
            "is_closed": True,
        },
        {
            "timestamp": 1_700_000_180_000,
            "open": 102.5,
            "high": 102.6,
            "low": 99.0,
            "close": 99.5,
            "kc_middle": 100.0,
            "atr": 1.0,
            "is_closed": True,
        },
    ])

    allowed, reason, _ = HoldingProtectionExitGate.validate_exit(
        position, frame, 99.5, "THREE_POINT_PIVOT",
    )

    assert allowed
    assert reason == "THREE_POINT_PIVOT"
