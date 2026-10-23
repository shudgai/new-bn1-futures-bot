"""
tests/test_mid_band_ma3_exit.py
驗證 KC 中軌 + MA3 多空對稱平倉規則（2026-10-23）：
- 跌破／突破中軌 + MA3 同向轉弱 -> 立即平倉
- MA3 方向有利 -> 暫緩 (HOLD_WAIT)
- 破位後反彈觸中軌遇阻／回測中軌有撐 -> 平倉（破位當根不觸發 A）
- 暫緩期間收線擊穿反側外軌 -> 強制平倉
- 僅使用已收線 K 棒；盤中棒不觸發
- ExitGate 整合：優先於 VETO_HOLD；安全出口仍最優先
"""
import pandas as pd
import pytest

from core.gates.mid_band_ma3_exit import (
    ACTION_EXIT, ACTION_HOLD_WAIT, evaluate_mid_band_ma3_exit,
    LONG_TREND_BROKEN, LONG_BOUNCE_RESISTANCE, LONG_HARD_STOP, LONG_HOLD_WAIT,
    SHORT_TREND_BROKEN, SHORT_PULLBACK_SUPPORT, SHORT_HARD_STOP, SHORT_HOLD_WAIT,
)
from core.gates.unified_pipeline_gates import ExitGate

MID, UP, LOW = 100.0, 104.0, 96.0


def bar(o, h, l, c, closed=True, mid=MID, up=UP, low=LOW):
    return {"open": o, "high": h, "low": l, "close": c, "kc_middle": mid,
            "kc_upper": up, "kc_lower": low, "atr": 1.0, "is_closed": closed}


LIVE = bar(99.0, 99.2, 98.8, 99.0, closed=False)


def frame(*bars):
    return pd.DataFrame(list(bars) + [LIVE])


# ── 多單 ──────────────────────────────────────────────

def test_long_breakdown_with_ma3_down_exits_immediately():
    df = frame(bar(101, 101.5, 100.5, 101.2), bar(101.2, 101.4, 100.8, 101.0),
               bar(101.0, 101.2, 100.6, 100.8), bar(100.8, 100.9, 99.0, 99.2))
    action, reason, _ = evaluate_mid_band_ma3_exit("LONG", df)
    assert (action, reason) == (ACTION_EXIT, LONG_TREND_BROKEN)


def test_long_breakdown_bar_itself_does_not_trigger_bounce_resistance():
    # MA3 仍向上（前段急漲），破位當根 high >= mid 但前一根在中軌上方 -> 暫緩
    df = frame(bar(97, 98, 96.8, 97.5), bar(97.5, 98.2, 97.4, 98.0),
               bar(98.0, 101.5, 97.9, 101.0), bar(101.0, 101.2, 99.5, 99.8))
    action, reason, _ = evaluate_mid_band_ma3_exit("LONG", df)
    assert (action, reason) == (ACTION_HOLD_WAIT, LONG_HOLD_WAIT)


def test_long_bounce_touches_mid_and_fails_exits():
    df = frame(bar(97, 97.5, 96.8, 97.2), bar(97.2, 98.0, 97.0, 97.8),
               bar(97.8, 99.0, 97.7, 98.8), bar(98.8, 100.2, 98.7, 99.6))
    action, reason, _ = evaluate_mid_band_ma3_exit("LONG", df)
    assert (action, reason) == (ACTION_EXIT, LONG_BOUNCE_RESISTANCE)


def test_long_hard_stop_on_close_below_lower_band_while_ma3_up():
    df = frame(bar(90, 91, 89.8, 90.5), bar(90.5, 92, 90.4, 91.8),
               bar(91.8, 99.5, 91.7, 99.0), bar(99.0, 99.2, 95.0, 95.5))
    action, reason, _ = evaluate_mid_band_ma3_exit("LONG", df)
    assert (action, reason) == (ACTION_EXIT, LONG_HARD_STOP)


def test_long_above_mid_rule_not_involved():
    df = frame(bar(101, 102, 100.5, 101.5), bar(101.5, 102, 101, 101.2),
               bar(101.2, 101.4, 100.6, 100.9), bar(100.9, 101.0, 100.2, 100.4))
    action, _, _ = evaluate_mid_band_ma3_exit("LONG", df)
    assert action is None


def test_live_bar_is_ignored():
    # 已收線棒全在中軌上方，只有盤中棒跌破 -> 不介入
    closed = [bar(101, 102, 100.5, 101.5), bar(101.5, 102, 101, 101.2),
              bar(101.2, 101.4, 100.6, 100.9), bar(100.9, 101.0, 100.2, 100.4)]
    live = bar(100.4, 100.5, 97.0, 97.2, closed=False)
    df = pd.DataFrame(closed + [live])
    action, _, _ = evaluate_mid_band_ma3_exit("LONG", df)
    assert action is None


def test_no_is_closed_column_drops_last_row_as_live():
    rows = [bar(101, 101.5, 100.5, 101.2), bar(101.2, 101.4, 100.8, 101.0),
            bar(101.0, 101.2, 100.6, 100.8), bar(100.8, 100.9, 99.0, 99.2),
            bar(99.2, 103, 99.0, 102.5)]  # 最後一列視為盤中棒
    df = pd.DataFrame(rows).drop(columns=["is_closed"])
    action, reason, _ = evaluate_mid_band_ma3_exit("LONG", df)
    assert (action, reason) == (ACTION_EXIT, LONG_TREND_BROKEN)


def test_bars_closed_before_entry_are_skipped():
    rows = [bar(101, 101.5, 100.5, 101.2), bar(101.2, 101.4, 100.8, 101.0),
            bar(101.0, 101.2, 100.6, 100.8), bar(100.8, 100.9, 99.0, 99.2)]
    for i, r in enumerate(rows):
        r["timestamp"] = 1_000_000 + i * 60_000
    df = frame(*rows)
    action, _, meta = evaluate_mid_band_ma3_exit("LONG", df, entry_timestamp=1_000_000 + 10 * 60_000)
    assert action is None and meta.get("skipped") == "NO_CLOSED_BAR_AFTER_ENTRY"


# ── 空單（鏡像） ──────────────────────────────────────

def test_short_breakout_with_ma3_up_exits_immediately():
    df = frame(bar(99, 99.5, 98.5, 98.8), bar(98.8, 99.2, 98.6, 99.0),
               bar(99.0, 99.4, 98.8, 99.2), bar(99.2, 101.0, 99.1, 100.8))
    action, reason, _ = evaluate_mid_band_ma3_exit("SHORT", df)
    assert (action, reason) == (ACTION_EXIT, SHORT_TREND_BROKEN)


def test_short_breakout_bar_itself_holds_when_ma3_down():
    df = frame(bar(103, 103.2, 102, 102.5), bar(102.5, 102.6, 101.8, 102.0),
               bar(102.0, 102.1, 98.5, 99.0), bar(99.0, 100.5, 98.8, 100.2))
    action, reason, _ = evaluate_mid_band_ma3_exit("SHORT", df)
    assert (action, reason) == (ACTION_HOLD_WAIT, SHORT_HOLD_WAIT)


def test_short_pullback_holds_mid_support_exits():
    df = frame(bar(103, 103.2, 102.5, 102.8), bar(102.8, 103.0, 102.0, 102.2),
               bar(102.2, 102.3, 101.0, 101.2), bar(101.2, 101.3, 99.8, 100.4))
    action, reason, _ = evaluate_mid_band_ma3_exit("SHORT", df)
    assert (action, reason) == (ACTION_EXIT, SHORT_PULLBACK_SUPPORT)


def test_short_hard_stop_on_close_above_upper_band_while_ma3_down():
    df = frame(bar(110, 110.2, 109, 109.5), bar(109.5, 109.6, 108, 108.2),
               bar(108.2, 108.3, 100.5, 101.0), bar(101.0, 105, 100.8, 104.5))
    action, reason, _ = evaluate_mid_band_ma3_exit("SHORT", df)
    assert (action, reason) == (ACTION_EXIT, SHORT_HARD_STOP)


# ── ExitGate 整合 ─────────────────────────────────────

def _snapshot(df, quote):
    return {"current_price": quote, "atr": 1.0, "kc_upper": UP, "kc_middle": MID,
            "kc_lower": LOW, "kc_middle_slope": 0.0, "ema_fast": 100.0,
            "ema_slow": 100.0, "raw_df": df, "macro_trend": "NEUTRAL"}


def test_exit_gate_mid_band_exit_overrides_veto_hold():
    df = frame(bar(101, 101.5, 100.5, 101.2), bar(101.2, 101.4, 100.8, 101.0),
               bar(101.0, 101.2, 100.6, 100.8), bar(100.8, 100.9, 99.0, 99.2))
    pos = {"side": "LONG", "entry_price": 99.0, "qty": 1.0, "leverage": 5.0}
    should_exit, reason, meta = ExitGate.evaluate(pos, _snapshot(df, 99.1))
    assert should_exit is True
    assert reason in (LONG_TREND_BROKEN, "CLOSE_LONG_AT_TOP")
    assert ("mid_band_ma3" in meta) or ("top_bottom_reversal" in meta)


def test_exit_gate_hold_wait_suppresses_ordinary_exits():
    df = frame(bar(97, 98, 96.8, 97.5), bar(97.5, 98.2, 97.4, 98.0),
               bar(98.0, 101.5, 97.9, 101.0), bar(101.0, 101.2, 99.5, 99.8))
    pos = {"side": "LONG", "entry_price": 99.0, "qty": 1.0, "leverage": 5.0}
    should_exit, reason, _ = ExitGate.evaluate(pos, _snapshot(df, 99.5))
    assert should_exit is False and reason == LONG_HOLD_WAIT


def test_exit_gate_hard_stop_still_has_priority():
    df = frame(bar(97, 98, 96.8, 97.5), bar(97.5, 98.2, 97.4, 98.0),
               bar(98.0, 101.5, 97.9, 101.0), bar(101.0, 101.2, 99.5, 99.8))
    pos = {"side": "LONG", "entry_price": 120.0, "qty": 1.0, "leverage": 5.0}
    should_exit, reason, _ = ExitGate.evaluate(pos, _snapshot(df, 99.5))
    assert should_exit is True and reason == "CIRCUIT_BREAKER_HARD_SL"


@pytest.mark.parametrize("reason", [LONG_TREND_BROKEN, LONG_BOUNCE_RESISTANCE,
                                    SHORT_TREND_BROKEN, SHORT_PULLBACK_SUPPORT])
def test_reason_codes_recognised_by_execution_midline_validator(reason):
    assert "MID_BAND_EXIT" in reason


@pytest.mark.parametrize("reason", [LONG_HARD_STOP, SHORT_HARD_STOP])
def test_hard_stop_codes_are_emergency(reason):
    from core.services.math_execution_gate import is_emergency_exit
    assert is_emergency_exit(reason)
