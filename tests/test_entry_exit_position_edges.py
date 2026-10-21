"""開倉位置（防追高追低）與平倉位置（真峰谷 / 緊急避險）邊際補完測試。

框架：pytest（專案既有 pytest.ini）。純數學 Gate 走 Unit Test；
HoldingProtectionExitGate.validate_exit 走 Feature Test（真實 DataFrame 資料流）。
"""

import copy
import math
from concurrent.futures import ThreadPoolExecutor

import pandas as pd
import pytest

from core.gates.breakout_continuation_entry_gate import (
    CONFIRMED_2BAR_BREAKOUT,
    CONTINUATION_OUTSIDE_KC,
    BreakoutContinuationEntryGate as Gate,
)
from core.gates.holding_protection_gate import (
    HoldingProtectionExitGate,
    has_confirmed_true_pivot,
)

UPPER, LOWER, ATR = 101.0, 99.0, 1.0


# ─────────────────────────── helpers ───────────────────────────
def _bars(closes, atr=ATR):
    rows = [{
        "open": c - 0.1, "high": c + 0.2, "low": c - 0.2, "close": c, "atr": atr,
    } for c in closes]
    return rows, [UPPER] * len(rows), [LOWER] * len(rows)


def _cand(entry_type, action, price, ma5):
    return {
        "type": entry_type, "action": action, "price": price,
        "ma5_current": ma5, "kc_upper": UPPER, "kc_lower": LOWER,
    }


def _block(cand, rows, up, lo, **kw):
    return Gate.late_chase_block_reason(cand, rows, up, lo, **kw)


# ═══════════════ A. 開倉位置：late_chase_block_reason（Unit） ═══════════════
class TestEntryPositionBoundaries:
    # 商業：剛好 0.5 ATR 是授權上限，不得被誤擋（漏掉合法行情 = 少賺）
    @pytest.mark.parametrize("action,price,ma5", [
        ("OPEN_LONG", UPPER + 0.5, UPPER),
        ("OPEN_SHORT", LOWER - 0.5, LOWER),
    ])
    def test_exactly_half_atr_extension_allowed(self, action, price, ma5):
        rows, up, lo = _bars([100.0, 100.0, price])
        assert _block(_cand(CONFIRMED_2BAR_BREAKOUT, action, price, ma5), rows, up, lo) is None

    # 商業：超過 0.5 ATR 一點點也要擋（追高追低 = 買在天花板、空在地板）
    @pytest.mark.parametrize("action,price,ma5", [
        ("OPEN_LONG", UPPER + 0.51, UPPER),
        ("OPEN_SHORT", LOWER - 0.51, LOWER),
    ])
    def test_just_over_half_atr_blocked(self, action, price, ma5):
        rows, up, lo = _bars([100.0, 100.0, price])
        reason = _block(_cand(CONFIRMED_2BAR_BREAKOUT, action, price, ma5), rows, up, lo)
        assert reason.startswith("LATE_CHASE_BAND_EXTENSION")

    # 商業：MA5 乖離剛好 1 ATR 放行、超過即擋；空方對稱
    def test_ma5_distance_boundary_short(self):
        rows, up, lo = _bars([100.0, 100.0, 98.8])
        assert _block(_cand(CONFIRMED_2BAR_BREAKOUT, "OPEN_SHORT", 98.8, 99.8), rows, up, lo) is None
        reason = _block(_cand(CONFIRMED_2BAR_BREAKOUT, "OPEN_SHORT", 98.8, 99.81), rows, up, lo)
        assert reason.startswith("LATE_CHASE_MA5_DISTANCE")

    # 商業：連續軌外收線剛好 2 根放行，第 3 根起屬延續過期（空方對稱，原測試只有多方）
    def test_short_continuation_freshness_two_vs_three_bars(self):
        rows, up, lo = _bars([100.0, 98.9, 98.8])
        assert _block(_cand(CONTINUATION_OUTSIDE_KC, "OPEN_SHORT", 98.8, 99.0), rows, up, lo) is None
        rows, up, lo = _bars([100.0, 98.9, 98.85, 98.8])
        reason = _block(_cand(CONTINUATION_OUTSIDE_KC, "OPEN_SHORT", 98.8, 99.0), rows, up, lo)
        assert reason.startswith("LATE_CHASE_STALE_CONTINUATION bars_outside=3")

    # 商業：最新報價已殺離下軌 > 0.5 ATR，最終送單前必須攔下（空方對稱）
    def test_short_runaway_tick_blocked_at_final_revalidation(self):
        rows, up, lo = _bars([100.0, 98.9, 98.8])
        cand = _cand(CONFIRMED_2BAR_BREAKOUT, "OPEN_SHORT", 98.8, 99.0)
        assert _block(cand, rows, up, lo) is None
        assert _block(cand, rows, up, lo, tick_price=98.3).startswith("LATE_CHASE_BAND_EXTENSION")

    # 商業：報價回落不可「洗白」已過度延伸的訊號收盤（取較極端者）
    def test_retreating_tick_cannot_whitewash_extended_close(self):
        rows, up, lo = _bars([100.0, 100.0, 101.7])
        cand = _cand(CONFIRMED_2BAR_BREAKOUT, "OPEN_LONG", 101.7, 101.2)
        assert _block(cand, rows, up, lo, tick_price=101.1).startswith("LATE_CHASE_BAND_EXTENSION")

    # 技術：非開倉動作（平倉 / None / 亂碼）不得被當成開倉放行
    @pytest.mark.parametrize("action", ["CLOSE_LONG", None, "", "open_long"])
    def test_invalid_action_rejected(self, action):
        rows, up, lo = _bars([100.0, 101.2])
        reason = _block(_cand(CONTINUATION_OUTSIDE_KC, action, 101.2, 101.0), rows, up, lo)
        assert reason == "LATE_CHASE_INVALID_ACTION"

    # 技術：NaN / Inf / None / 0 / 負值 → Fail-Closed（壞資料不准下單）
    @pytest.mark.parametrize("field,value", [
        ("price", float("nan")), ("price", float("inf")), ("price", None),
        ("price", 0.0), ("price", -1.0), ("ma5_current", None),
        ("ma5_current", "abc"), ("kc_upper", None),
    ])
    def test_corrupted_candidate_fails_closed(self, field, value):
        rows, up, lo = _bars([100.0, 101.2])
        cand = _cand(CONTINUATION_OUTSIDE_KC, "OPEN_LONG", 101.2, 101.0)
        cand[field] = value
        assert _block(cand, rows, up, lo) == "LATE_CHASE_DATA_INVALID"

    # 技術：最終重驗拿到無效報價（斷線回傳 NaN/0/負數/字串）必須 Fail-Closed，不能默默忽略
    @pytest.mark.parametrize("tick", [float("nan"), 0.0, -101.0, "timeout", float("inf")])
    def test_invalid_live_tick_fails_closed(self, tick):
        rows, up, lo = _bars([100.0, 101.2])
        cand = _cand(CONTINUATION_OUTSIDE_KC, "OPEN_LONG", 101.2, 101.0)
        assert _block(cand, rows, up, lo, tick_price=tick) == "LATE_CHASE_DATA_INVALID"

    # 技術：ATR 為 0 / 負數且無法以高低差回推 → Fail-Closed（避免除以零或門檻失效）
    def test_zero_atr_and_flat_range_fail_closed(self):
        rows = [{"open": 101.2, "high": 101.2, "low": 101.2, "close": 101.2, "atr": 0.0}] * 2
        cand = _cand(CONTINUATION_OUTSIDE_KC, "OPEN_LONG", 101.2, 101.0)
        assert _block(cand, rows, [UPPER] * 2, [LOWER] * 2) == "LATE_CHASE_DATA_INVALID"

    # 技術：空 K 線 / KC 序列長度不一致 → Fail-Closed，不得拋 IndexError 中斷掃描
    def test_empty_and_mismatched_series_fail_closed(self):
        cand = _cand(CONTINUATION_OUTSIDE_KC, "OPEN_LONG", 101.2, 101.0)
        assert _block(cand, [], [], []) == "LATE_CHASE_DATA_INVALID"
        rows, _, _ = _bars([100.0, 101.1, 101.2])
        assert _block(cand, rows, [UPPER], [LOWER]) == "LATE_CHASE_DATA_INVALID"

    # 技術：軌外連續計數途中 KC 值損毀 → Fail-Closed
    def test_corrupted_rail_inside_streak_fails_closed(self):
        rows, up, lo = _bars([101.1, 101.2])
        up[0] = float("nan")
        cand = _cand(CONTINUATION_OUTSIDE_KC, "OPEN_LONG", 101.2, 101.0)
        assert _block(cand, rows, up, lo) == "LATE_CHASE_DATA_INVALID"

    # 商業：PEPE 類極小單價（1e-6）與 BTC 級極大單價，ATR 比例門檻一樣成立（精度）
    @pytest.mark.parametrize("scale", [1e-8, 1e4])
    def test_scale_invariance_tiny_and_huge_prices(self, scale):
        def scaled(v):
            return v * scale
        rows = [{"open": scaled(c - 0.1), "high": scaled(c + 0.2), "low": scaled(c - 0.2),
                 "close": scaled(c), "atr": scaled(1.0)} for c in (100.0, 100.0, 101.3)]
        up, lo = [scaled(UPPER)] * 3, [scaled(LOWER)] * 3
        ok = {"type": CONFIRMED_2BAR_BREAKOUT, "action": "OPEN_LONG", "price": scaled(101.3),
              "ma5_current": scaled(101.0), "kc_upper": scaled(UPPER), "kc_lower": scaled(LOWER)}
        assert _block(ok, rows, up, lo) is None
        bad = dict(ok, price=scaled(101.7))
        assert _block(bad, rows, up, lo).startswith("LATE_CHASE_BAND_EXTENSION")

    # 併發 / 重複提交：同一訊號多執行緒重驗結果一致，且不得竄改輸入（掃描與送單共用同一份資料）
    def test_concurrent_revalidation_is_pure_and_deterministic(self):
        rows, up, lo = _bars([100.0, 100.0, 101.7])
        cand = _cand(CONFIRMED_2BAR_BREAKOUT, "OPEN_LONG", 101.7, 101.2)
        snapshot = copy.deepcopy((cand, rows, up, lo))
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: _block(cand, rows, up, lo), range(64)))
        assert len(set(results)) == 1 and results[0].startswith("LATE_CHASE_BAND_EXTENSION")
        assert (cand, rows, up, lo) == snapshot


# ═══════════════ B. 開倉位置：evaluate_pure_math 掃描漏斗（Integration） ═══════════════
def _scan_inputs(side, last_close, prev_bar, extra_outside=()):
    """組裝收線延續情境：上一根在軌內（非兩根確認），最後一根軌外同色。"""
    long = side == "LONG"
    rows = [{"open": 100.0, "high": 100.3, "low": 99.7, "close": 100.0, "atr": ATR}] * 2
    rows = rows + [prev_bar] + list(extra_outside)
    last_open = last_close - 0.7 if long else last_close + 0.7
    rows.append({
        "open": last_open, "high": max(last_open, last_close) + 0.05,
        "low": min(last_open, last_close) - 0.05, "close": last_close, "atr": ATR,
    })
    n = len(rows)
    ma5_last = last_close - 0.5 if long else last_close + 0.5
    ma5_prev = ma5_last - 0.2 if long else ma5_last + 0.2
    return dict(
        closed_klines=rows,
        kc_upper=[UPPER] * n, kc_lower=[LOWER] * n, kc_basis=[100.0] * n,
        ma5_series=[100.0] * (n - 2) + [ma5_prev, ma5_last],
        ma15_series=[100.0] * n,
    )


class TestScanFunnel:
    LONG_PREV = {"open": 100.5, "high": 100.6, "low": 100.1, "close": 100.2, "atr": ATR}
    SHORT_PREV = {"open": 99.5, "high": 99.9, "low": 99.4, "close": 99.8, "atr": ATR}

    # 商業：剛破軌、貼近外軌的延續訊號必須能出來（避免 Gate 把正常行情全部封殺）
    @pytest.mark.parametrize("side,close,prev", [
        ("LONG", 101.3, LONG_PREV), ("SHORT", 98.7, SHORT_PREV),
    ])
    def test_fresh_continuation_passes_scan(self, side, close, prev):
        result = Gate.evaluate_pure_math(**_scan_inputs(side, close, prev))
        assert result is not None
        assert result["type"] == CONTINUATION_OUTSIDE_KC
        assert result["action"] == ("OPEN_LONG" if side == "LONG" else "OPEN_SHORT")

    # 商業：同樣型態但已衝離外軌 0.8 ATR，掃描端就要擋（不可留給下游補救）
    @pytest.mark.parametrize("side,close,prev", [
        ("LONG", 101.8, LONG_PREV), ("SHORT", 98.2, SHORT_PREV),
    ])
    def test_extended_continuation_blocked_in_scan(self, side, close, prev):
        assert Gate.evaluate_pure_math(**_scan_inputs(side, close, prev)) is None

    # 商業：軌外已連續第 3 根才補上車 → 掃描端拒絕（防止尾段追單）
    def test_stale_long_continuation_blocked_in_scan(self):
        prev = {"open": 101.05, "high": 101.25, "low": 101.0, "close": 101.2, "atr": ATR}
        extra = [{"open": 101.1, "high": 101.3, "low": 101.05, "close": 101.25, "atr": ATR}]
        assert Gate.evaluate_pure_math(**_scan_inputs("LONG", 101.3, prev, extra)) is None


# ═══════════════ C. 平倉位置：真峰谷判定（Unit） ═══════════════
def _frame(rows):
    df = pd.DataFrame(rows)
    df.attrs["timeframe_ms"] = 60_000
    return df


def _bar(i, o, h, l, c, closed=True, mid=100.0, up=102.0, lo=98.0, ma5=None, ma15=None):
    return dict(timestamp=i * 60_000, open=o, high=h, low=l, close=c, kc_middle=mid,
                kc_upper=up, kc_lower=lo, ma5=c if ma5 is None else ma5,
                ma15=mid if ma15 is None else ma15, ema13=mid, atr=1.0,
                is_closed=closed, volume=100)


class TestTruePivot:
    # 商業（回歸）：分形頂（平頂）+ 收線跌破前棒實體中點 = 規格授權的真峰頂。
    # 舊碼引用未定義變數被 except 吞掉，此路徑永遠失效 → 該走不走、利潤回吐。
    def test_fractal_equal_high_peak_confirmed(self):
        df = _frame([_bar(1, 100, 100.5, 99.8, 100.3), _bar(2, 100.2, 101, 100.1, 100.9),
                     _bar(3, 100.8, 101, 100.2, 100.4)])
        ok, kind, _ = has_confirmed_true_pivot({"side": "LONG"}, df, 100.4)
        assert ok and kind == "TRUE_FRACTAL_PEAK_CONFIRMED"

    def test_fractal_equal_low_valley_confirmed(self):
        df = _frame([_bar(1, 100, 100.2, 99.5, 99.7), _bar(2, 99.8, 99.9, 99.0, 99.1),
                     _bar(3, 99.2, 99.8, 99.0, 99.6)])
        ok, kind, _ = has_confirmed_true_pivot({"side": "SHORT"}, df, 99.6)
        assert ok and kind == "TRUE_FRACTAL_VALLEY_CONFIRMED"

    # 商業：平頂但收線未跌破前棒實體中點 → 不是真峰頂，續抱
    def test_fractal_without_midpoint_break_not_confirmed(self):
        df = _frame([_bar(1, 100, 100.5, 99.8, 100.3), _bar(2, 100.2, 101, 100.1, 100.9),
                     _bar(3, 100.8, 101, 100.6, 100.8)])
        assert has_confirmed_true_pivot({"side": "LONG"}, df, 100.8)[0] is False

    # 商業：未收線的盤中 K 棒不得充當確認棒（插針洗盤不平倉）
    def test_live_unclosed_bar_cannot_confirm_peak(self):
        df = _frame([_bar(1, 100, 100.5, 99.8, 100.3), _bar(2, 100.3, 101, 100.2, 100.9),
                     _bar(3, 100.9, 100.95, 100.1, 100.2, closed=False)])
        assert has_confirmed_true_pivot({"side": "LONG"}, df, 100.2)[0] is False

    # 商業：開倉前就存在的歷史峰頂不得拿來平倉（數據錯置 = 剛進場就被洗出）
    def test_pre_entry_pivot_ignored(self):
        df = _frame([_bar(1, 100, 100.5, 99.8, 100.3), _bar(2, 100.3, 101, 100.2, 100.9),
                     _bar(3, 100.8, 100.9, 100.4, 100.6), _bar(4, 100.6, 100.95, 100.5, 100.9)])
        position = {"side": "LONG", "open_timestamp": 3 * 60_000}
        assert has_confirmed_true_pivot(position, df, 100.9)[0] is False

    # 技術：空 frame / 不足 3 根 / 非法方向 / 缺欄位 → 不確認、不拋例外
    @pytest.mark.parametrize("position,frame", [
        ({"side": "LONG"}, None),
        ({"side": "LONG"}, _frame([_bar(1, 100, 101, 99, 100)] * 2)),
        ({"side": "BOTH"}, _frame([_bar(i, 100, 101, 99, 100) for i in range(3)])),
        (None, _frame([_bar(i, 100, 101, 99, 100) for i in range(3)])),
        ({"side": "LONG"}, _frame([{"timestamp": i, "close": 1.0, "is_closed": True} for i in range(3)])),
    ])
    def test_degenerate_inputs_never_confirm(self, position, frame):
        assert has_confirmed_true_pivot(position, frame, 100.0)[0] is False


# ═══════════════ D. 平倉位置：validate_exit 授權白名單（Feature） ═══════════════
def _losing_long_below_lower():
    rows = [_bar(i, 100 - 0.3 * i, 100.2 - 0.3 * i, 99.6 - 0.3 * i, 99.7 - 0.3 * i,
                 ma5=100 - 0.3 * i, ma15=100) for i in range(1, 8)]
    rows.append(_bar(8, 97.6, 97.7, 96.8, 96.9, ma5=97.8, ma15=99.5))
    position = {"side": "LONG", "symbol": "LOBSTERUSDT", "entry_price": 100.0,
                "open_timestamp": 60_000}
    return position, _frame(rows)


def _losing_short_above_upper():
    rows = [_bar(i, 100 + 0.3 * i, 100.4 + 0.3 * i, 99.8 + 0.3 * i, 100.3 + 0.3 * i,
                 ma5=100 + 0.3 * i, ma15=100) for i in range(1, 8)]
    rows.append(_bar(8, 102.4, 103.2, 102.3, 103.1, ma5=102.2, ma15=100.5))
    position = {"side": "SHORT", "symbol": "LOBSTERUSDT", "entry_price": 100.0,
                "open_timestamp": 60_000}
    return position, _frame(rows)


class TestExitAuthorization:
    # 錢丟了風險（回歸）：多單虧損且已跌破 KC 下軌，反側擊穿緊急出場曾被盤整否決卡死
    def test_long_opposite_band_breach_always_escapes(self):
        position, df = _losing_long_below_lower()
        ok, code, _ = HoldingProtectionExitGate.validate_exit(
            position, df, 96.9, "OPPOSITE_KC_BAND_BREACH")
        assert ok and code == "OPPOSITE_KC_BAND_BREACH"

    def test_short_opposite_band_breach_always_escapes(self):
        position, df = _losing_short_above_upper()
        ok, code, _ = HoldingProtectionExitGate.validate_exit(
            position, df, 103.1, "OPPOSITE_KC_BAND_BREACH")
        assert ok and code == "OPPOSITE_KC_BAND_BREACH"

    # 詐欺型態：標籤寫「反側擊穿」但價格其實沒擊穿 → 標籤不構成授權
    def test_mislabelled_opposite_breach_not_authorized(self):
        position, df = _losing_long_below_lower()
        ok, code, _ = HoldingProtectionExitGate.validate_exit(
            position, df, 98.5, "OPPOSITE_KC_BAND_BREACH")
        assert code != "OPPOSITE_KC_BAND_BREACH"

    # 技術：報價為 NaN 時不得以反側擊穿放行
    def test_nan_quote_breach_not_authorized(self):
        position, df = _losing_long_below_lower()
        _, code, _ = HoldingProtectionExitGate.validate_exit(
            position, df, float("nan"), "OPPOSITE_KC_BAND_BREACH")
        assert code != "OPPOSITE_KC_BAND_BREACH"

    # 商業：帳戶硬止損永遠最高優先，無真峰谷也必須放行
    def test_hard_stop_always_authorized(self):
        position, df = _losing_long_below_lower()
        ok, code, _ = HoldingProtectionExitGate.validate_exit(position, df, 96.9, "HARD_STOP_LOSS")
        assert ok and code == "EXIT_BY_CIRCUIT_BREAKER_HARD_SL"

    # 商業：普通回調、未見真峰谷 → 一律否決（不被洗下車）
    def test_ordinary_pullback_without_pivot_vetoed(self):
        position, df = _losing_long_below_lower()
        ok, code, _ = HoldingProtectionExitGate.validate_exit(position, df, 96.9, "EXIT_BY_PULLBACK")
        assert ok is False and "VETO" in code

    # 商業（2026-10-21 使用者決議）：動能衰竭在真峰谷確認後平倉，無真峰谷則一票否決
    @pytest.mark.parametrize("reason", ["MOMENTUM_EXHAUSTION", "MA5_FLAT_REVERSAL"])
    def test_momentum_exhaustion_without_pivot_vetoed(self, reason):
        position, df = _losing_long_below_lower()
        ok, code, _ = HoldingProtectionExitGate.validate_exit(position, df, 96.9, reason)
        assert ok is False and code == "VETO_HOLDING_UNTIL_TRUE_PIVOT"

    @pytest.mark.parametrize("reason", ["MOMENTUM_EXHAUSTION", "MA5_FLAT_REVERSAL"])
    def test_momentum_exhaustion_with_confirmed_pivot_authorized(self, reason):
        # 建立已收線確認真峰頂：中間 high 102.0 > 兩側，且第 3 棒已收線
        rows = [
            _bar(1, 100.0, 100.5, 99.8, 100.3),
            _bar(2, 100.3, 102.0, 100.2, 101.8),
            _bar(3, 101.8, 101.5, 100.8, 101.0),
        ]
        position = {"side": "LONG", "symbol": "LOBSTERUSDT", "entry_price": 100.0,
                    "open_timestamp": 60}
        ok, code, details = HoldingProtectionExitGate.validate_exit(position, _frame(rows), 101.0, reason)
        assert ok is True and code == reason
        assert details.get("true_pivot_type") == "TRUE_PEAK_PIVOT_CONFIRMED"


# ═══════════════ E. 鎖利真正達標驗證（2026-10-21 使用者決議 B） ═══════════════
def test_ratchet_without_reached_floor_vetoed():
    # 浮盈僅 0.01% 但未達到 ROE 階梯門檻時，不再被隨意放行，必須否決
    rows = [_bar(i, 100 + 0.2 * i, 100.3 + 0.2 * i, 99.9 + 0.2 * i, 100.2 + 0.2 * i,
                 mid=100 + 0.1 * i, up=102 + 0.1 * i, lo=98 + 0.1 * i) for i in range(1, 9)]
    position = {"side": "LONG", "symbol": "LOBSTERUSDT", "entry_price": 101.79,
                "open_timestamp": 2 * 60_000}
    ok, code, _ = HoldingProtectionExitGate.validate_exit(
        position, _frame(rows), 101.8, "EXIT_BY_RATCHET_PROFIT_LOCK")
    assert ok is False and code == "VETO_PROFIT_LOCK_NOT_REACHED"

def test_ratchet_with_genuinely_reached_floor_authorized():
    # 上游帶有完整淨 ROE 階梯門檻（例如 floor=10%, current=9.5% 觸及止盈回吐線）時放行
    rows = [_bar(i, 100 + 0.2 * i, 100.3 + 0.2 * i, 99.9 + 0.2 * i, 100.2 + 0.2 * i,
                 mid=100 + 0.1 * i, up=102 + 0.1 * i, lo=98 + 0.1 * i) for i in range(1, 9)]
    position = {"side": "LONG", "symbol": "LOBSTERUSDT", "entry_price": 100.0,
                "open_timestamp": 2 * 60_000}
    details = {
        "floor_net_roe_pct": 10.0,
        "current_net_roe_pct": 9.5,
        "lock_type": "NET_ROE_RATCHET_LADDER",
    }
    ok, code, out_details = HoldingProtectionExitGate.validate_exit(
        position, _frame(rows), 101.5, "EXIT_BY_RATCHET_PROFIT_LOCK", details=details)
    assert ok is True and code == "EXIT_BY_RATCHET_PROFIT_LOCK"
    assert out_details.get("floor_net_roe_pct") == 10.0

