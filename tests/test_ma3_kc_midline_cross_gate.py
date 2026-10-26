"""
Unit tests for unified MA3 × KC Midline Cross Entry Gate.

涵蓋：
1. 多單進場：
   - MA3 從中軌下方上穿，且斜率向上：通過。
   - MA3 在中軌上方但沒有新交叉：拒絕。
   - MA3 上穿但斜率不向上：拒絕。
   - 尚未收線：拒絕。
2. 空單進場：
   - MA3 從中軌上方下穿，且斜率向下：通過。
   - MA3 在中軌下方但沒有新交叉：拒絕。
   - MA3 下穿但斜率不向下：拒絕。
   - 尚未收線：拒絕。
3. 共通安全檢核：
   - 指標缺失、NaN、無限值、過期資料：拒絕。
   - 同一根 K 棒重複觸發：拒絕 (去重防刷單)。
   - 其他既有 Gate 拒絕時，即使交叉成立亦不得開倉。
   - 整合測試：strict_ma3_ma15_entry_gate 與 CentralEntryGate。
"""
import math
import pandas as pd
import pytest

from core.gates.ma3_kc_midline_cross_gate import evaluate_ma3_kc_midline_cross
from core.services.strict_ma3_ma15_entry_gate import evaluate_ma3_ma15_entry
from core.gates.central_entry_gate import CentralEntryGate


def make_frame(
    ma3_tuple=(99.0, 101.0),
    kc_mid_tuple=(100.0, 100.0),
    closes=(100.0, 101.5),
    opens=(99.5, 100.0),
    highs=(101.0, 102.0),
    lows=(99.0, 99.8),
    kc_upper=103.0,
    kc_lower=97.0,
    current_closed=True,
    is_stale=False,
    timeframe_ms=60_000,
):
    rows = []
    # 建立 40 根 K 棒以滿足完整歷史需求 (LOOKBACK + 1)
    base_time = 1_000_000
    for i in range(38):
        stamp = base_time + i * timeframe_ms
        mid = 98.0 + i * 0.05
        rows.append({
            "timestamp": stamp,
            "open": mid,
            "high": mid + 1.0,
            "low": mid - 1.0,
            "close": mid,
            "ma3": mid,
            "ma5": mid,
            "ma15": mid - 0.2,
            "kc_upper": mid + 3.0,
            "kc_middle": mid,
            "kc_lower": mid - 3.0,
            "atr": 1.0,
            "volume": 1000.0,
            "is_closed": True,
        })
    # 前一根 (index -2)
    mid_prev = kc_mid_tuple[0]
    rows.append({
        "timestamp": base_time + 38 * timeframe_ms,
        "open": opens[0],
        "high": highs[0],
        "low": lows[0],
        "close": closes[0],
        "ma3": ma3_tuple[0],
        "ma5": mid_prev,
        "ma15": mid_prev - 0.2,
        "kc_upper": mid_prev + 3.0,
        "kc_middle": mid_prev,
        "kc_lower": mid_prev - 3.0,
        "atr": 1.0,
        "volume": 1000.0,
        "is_closed": True,
    })
    # 當前已收線根 (index -1)
    last_stamp = base_time + 39 * timeframe_ms
    mid_now = kc_mid_tuple[1]
    rows.append({
        "timestamp": last_stamp,
        "open": opens[1],
        "high": highs[1],
        "low": lows[1],
        "close": closes[1],
        "ma3": ma3_tuple[1],
        "ma5": mid_now + 0.1,
        "ma15": mid_now - 0.2,
        "kc_upper": mid_now + 3.0,
        "kc_middle": mid_now,
        "kc_lower": mid_now - 3.0,
        "atr": 1.0,
        "volume": 1000.0,
        "is_closed": current_closed,
    })
    df = pd.DataFrame(rows)
    df.attrs["timeframe_ms"] = timeframe_ms
    if is_stale:
        # 標註快照時間過期 (超過 180 秒)
        df.attrs["snapshot_ms"] = last_stamp + timeframe_ms + 300_000
    else:
        df.attrs["snapshot_ms"] = last_stamp + timeframe_ms + 10_000
    return df


# ═════════════════════════════════════════════════════════════
# 一、多單進場測試 (Long Entry Tests)
# ═════════════════════════════════════════════════════════════

def test_ma3_kc_cross_long_success():
    """多單：MA3 從中軌下方(99.0 <= 100.0)上穿到中軌上方(101.0 > 100.0)，且 MA3 斜率向上(101.0 > 99.0)。"""
    df = make_frame(ma3_tuple=(99.0, 101.0), kc_mid_tuple=(100.0, 100.0))
    passed, reason, details = evaluate_ma3_kc_midline_cross(df, "LONG", symbol="BTC/USDT")
    assert passed is True
    assert reason == "MA3_KC_MIDLINE_CROSS_CONFIRMED"
    assert details["cross_result"] == "LONG_CROSS_CONFIRMED"
    assert details["ma3_prev"] == 99.0
    assert details["ma3_now"] == 101.0
    assert details["ma3_slope"] == 2.0


def test_ma3_kc_cross_long_rejected_when_already_above():
    """多單：前一根 MA3 已在中軌上方(101.0 > 100.0)，無新上穿，嚴禁重新開多。"""
    df = make_frame(ma3_tuple=(101.0, 102.0), kc_mid_tuple=(100.0, 100.0))
    passed, reason, _ = evaluate_ma3_kc_midline_cross(df, "LONG", symbol="BTC/USDT")
    assert passed is False
    assert reason == "NO_FRESH_CROSS_MA3_ALREADY_ABOVE_MIDLINE"


def test_ma3_kc_cross_long_rejected_when_slope_flat_or_down():
    """多單：MA3 上穿但斜率未向上 (ma3_now <= ma3_prev)，拒絕開多。"""
    # 前一根 100.0 <= 100.0，當前 100.0 不大於前一根 (走平)
    df = make_frame(ma3_tuple=(100.0, 100.0), kc_mid_tuple=(100.0, 99.0))
    passed, reason, _ = evaluate_ma3_kc_midline_cross(df, "LONG", symbol="BTC/USDT")
    assert passed is False
    assert reason == "MA3_SLOPE_NOT_UPWARD"


def test_ma3_kc_cross_long_rejected_when_unclosed():
    """多單：當前 K 棒尚未確認收線 (is_closed=False)，嚴禁提前偷跑進場。"""
    df = make_frame(ma3_tuple=(99.0, 101.0), kc_mid_tuple=(100.0, 100.0), current_closed=False)
    passed, reason, _ = evaluate_ma3_kc_midline_cross(df, "LONG", symbol="BTC/USDT")
    assert passed is False
    assert reason == "LATEST_CANDLE_NOT_CONFIRMED_CLOSED"


# ═════════════════════════════════════════════════════════════
# 二、空單進場測試 (Short Entry Tests)
# ═════════════════════════════════════════════════════════════

def test_ma3_kc_cross_short_success():
    """空單：MA3 從中軌上方(101.0 >= 100.0)下穿到中軌下方(99.0 < 100.0)，且 MA3 斜率向下(99.0 < 101.0)。"""
    df = make_frame(
        ma3_tuple=(101.0, 99.0),
        kc_mid_tuple=(100.0, 100.0),
        closes=(100.0, 98.5),
        opens=(100.5, 100.0),
        highs=(101.0, 100.2),
        lows=(99.5, 98.0),
    )
    passed, reason, details = evaluate_ma3_kc_midline_cross(df, "SHORT", symbol="BTC/USDT")
    assert passed is True
    assert reason == "MA3_KC_MIDLINE_CROSS_CONFIRMED"
    assert details["cross_result"] == "SHORT_CROSS_CONFIRMED"
    assert details["ma3_prev"] == 101.0
    assert details["ma3_now"] == 99.0
    assert details["ma3_slope"] == -2.0


def test_ma3_kc_cross_short_rejected_when_already_below():
    """空單：前一根 MA3 已在中軌下方(99.0 < 100.0)，無新下穿，嚴禁重新開空。"""
    df = make_frame(ma3_tuple=(99.0, 98.0), kc_mid_tuple=(100.0, 100.0))
    passed, reason, _ = evaluate_ma3_kc_midline_cross(df, "SHORT", symbol="BTC/USDT")
    assert passed is False
    assert reason == "NO_FRESH_CROSS_MA3_ALREADY_BELOW_MIDLINE"


def test_ma3_kc_cross_short_rejected_when_slope_flat_or_up():
    """空單：MA3 下穿但斜率未向下 (ma3_now >= ma3_prev)，拒絕開空。"""
    df = make_frame(ma3_tuple=(100.0, 100.0), kc_mid_tuple=(100.0, 101.0))
    passed, reason, _ = evaluate_ma3_kc_midline_cross(df, "SHORT", symbol="BTC/USDT")
    assert passed is False
    assert reason == "MA3_SLOPE_NOT_DOWNWARD"


def test_ma3_kc_cross_short_rejected_when_unclosed():
    """空單：當前 K 棒尚未確認收線 (is_closed=False)，嚴禁提前確認交叉。"""
    df = make_frame(ma3_tuple=(101.0, 99.0), kc_mid_tuple=(100.0, 100.0), current_closed=False)
    passed, reason, _ = evaluate_ma3_kc_midline_cross(df, "SHORT", symbol="BTC/USDT")
    assert passed is False
    assert reason == "LATEST_CANDLE_NOT_CONFIRMED_CLOSED"


# ═════════════════════════════════════════════════════════════
# 三、共通安全與風控檢核測試 (Safety & Robustness Tests)
# ═════════════════════════════════════════════════════════════

def test_ma3_kc_cross_rejected_on_nan_or_inf():
    """安全性：MA3 或 KC 中軌出現 NaN 或 inf，一律拒絕。"""
    df = make_frame(ma3_tuple=(float("nan"), 101.0), kc_mid_tuple=(100.0, 100.0))
    passed, reason, _ = evaluate_ma3_kc_midline_cross(df, "LONG", symbol="BTC/USDT")
    assert passed is False
    assert reason == "INVALID_OR_NON_FINITE_INDICATORS"

    df_inf = make_frame(ma3_tuple=(99.0, float("inf")), kc_mid_tuple=(100.0, 100.0))
    passed, reason, _ = evaluate_ma3_kc_midline_cross(df_inf, "LONG", symbol="BTC/USDT")
    assert passed is False
    assert reason == "INVALID_OR_NON_FINITE_INDICATORS"


def test_ma3_kc_cross_rejected_on_stale_data():
    """安全性：行情快照過期 (超過 180 秒)，拒絕進場。"""
    df = make_frame(ma3_tuple=(99.0, 101.0), kc_mid_tuple=(100.0, 100.0), is_stale=True)
    passed, reason, _ = evaluate_ma3_kc_midline_cross(df, "LONG", symbol="BTC/USDT")
    assert passed is False
    assert reason == "DATA_STALE_OR_EXPIRED"


def test_ma3_kc_cross_deduplication_prevents_duplicate_entry():
    """去重安全：同一根已收線 K 棒時間戳已成交過，不得重複進場。"""
    df = make_frame(ma3_tuple=(99.0, 101.0), kc_mid_tuple=(100.0, 100.0))
    bar_time = int(df.iloc[-1]["timestamp"])
    passed, reason, _ = evaluate_ma3_kc_midline_cross(
        df, "LONG", symbol="BTC/USDT", last_trade_bar_time=bar_time,
    )
    assert passed is False
    assert reason == "BAR_ALREADY_TRADED"


# ═════════════════════════════════════════════════════════════
# 四、授權整合測試 (Integration with Entry Gates)
# ═════════════════════════════════════════════════════════════

def test_strict_ma3_entry_gate_authorizes_long_when_cross_confirmed():
    """整合：strict_ma3_ma15_entry_gate 依據 MA3 x KC 中軌交叉授權開多。"""
    df = make_frame(
        ma3_tuple=(99.0, 101.0),
        kc_mid_tuple=(99.5, 100.0),
        closes=(99.8, 101.5),
        opens=(99.5, 100.2),
        highs=(100.0, 101.8),
        lows=(99.2, 100.0),
    )
    # 增加 KC 斜率向上
    df.iloc[-1, df.columns.get_loc("kc_middle")] = 100.5
    df.iloc[-2, df.columns.get_loc("kc_middle")] = 100.0

    decision = evaluate_ma3_ma15_entry(
        df, "LONG",
        current_position="IDLE",
        last_trade_bar_time=None,
        stop_loss_price=98.0,
        execution_price=101.5,
    )
    assert decision.action == "OPEN_LONG"
    assert decision.checklist["STRICT_MA3_MA15_CROSS"] is True


def test_strict_ma3_entry_gate_rejects_long_when_no_fresh_cross():
    """整合：前一根 MA3 已在軌上，strict_ma3_ma15_entry_gate 拒絕非新交叉開多。"""
    df = make_frame(
        ma3_tuple=(101.0, 102.0),
        kc_mid_tuple=(99.5, 100.0),
    )
    decision = evaluate_ma3_ma15_entry(
        df, "LONG",
        current_position="IDLE",
        last_trade_bar_time=None,
        stop_loss_price=98.0,
        execution_price=102.0,
    )
    assert decision.action == "HOLD / PASS"
    assert "NO_STRICT_MA3_MA15_CROSS" in decision.violations


def test_central_entry_gate_enforces_ma3_kc_cross_for_cross_mid():
    """整合：CentralEntryGate 對 CROSS_MID 訊號強制執行 MA3 x KC 中軌檢驗。"""
    df = make_frame(
        ma3_tuple=(101.0, 102.0),  # 已在軌上，無新交叉
        kc_mid_tuple=(100.0, 100.0),
        closes=(101.0, 102.0),
        opens=(100.5, 101.0),
        highs=(101.5, 102.5),
        lows=(100.0, 100.8),
        kc_upper=105.0,
        kc_lower=95.0,
    )
    df.attrs["macro_trend"] = "BULLISH"
    df["macro_trend"] = "BULLISH"

    result = CentralEntryGate.verify_and_authorize(
        symbol="BTC/USDT",
        side="LONG",
        price=102.0,
        klines_or_snapshot=df,
        source="UNIFIED_PIPELINE|ENTRY_LONG_MA3_MA15_CROSS_MID",
    )
    assert result.passed is False
    assert "VETO_NO_FRESH_CROSS_MA3_ALREADY_ABOVE_MIDLINE" in result.reason
