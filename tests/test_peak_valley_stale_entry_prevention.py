"""
tests/test_peak_valley_stale_entry_prevention.py
================================================
驗證「嚴禁峰頂買多、谷底賣空」核心門禁：
1. 連續在同側外軌收線超過 2 根（consecutive_outside > 2），一律觸發 LATE_CHASE_STALE_CONTINUATION 拒絕開倉，徹底杜絕在峰頂追多！
2. 連續在下軌外收線超過 2 根，一律觸發 LATE_CHASE_STALE_CONTINUATION 拒絕開空，徹底杜絕在谷底追空！
3. 第一根新鮮破軌（前根收盤在軌內）放行主升浪起點 (BREAKOUT_MAIN_WAVE_START)。
4. 延續開倉乖離超過 0.5 ATR 或離 MA5 超過 1.0 ATR 一票否決 (LATE_CHASE_BAND_EXTENSION / LATE_CHASE_MA5_DISTANCE)。
"""
import pytest
import pandas as pd
import time
from core.gates.unified_pipeline_gates import EntryGate
from core.services.data_pipeline import DataPipeline


def _build_candles(n=10, start=100.0, step=1.0, side="UP"):
    bars = []
    price = start
    for i in range(n):
        if side == "UP":
            o = price
            c = price + step
            h = c + (step * 0.1)
            l = o - (step * 0.1)
            price = c
        else:
            o = price
            c = price - step
            h = o + (step * 0.1)
            l = c - (step * 0.1)
            price = c
        bars.append({
            "timestamp": int(time.time() * 1000) - (n - i) * 60_000,
            "open": o,
            "high": h,
            "low": l,
            "close": c,
            "volume": 100.0,
            "is_closed": True,
        })
    return pd.DataFrame(bars)


def test_entry_gate_veto_stale_breakout_at_peak():
    """
    商業場景重現：
    龍蝦在連續大漲 4~5 根後到達頂部峰頂，雖然當前依然是紅 K 且在軌外，
    但軌外收線已達 4 根，破軌新鮮度已過。
    【預期】：EntryGate 必須觸發 LATE_CHASE_STALE_CONTINUATION 一票否決，嚴禁在峰頂追買多單！
    """
    df = _build_candles(n=35, start=0.02000, step=0.00010, side="UP")
    # 計算 snapshot，將最新價設在上軌上方 0.00002 (0.25 ATR，乖離合格)，孤立驗證新鮮度
    snap = DataPipeline.calculate_snapshot("龙虾/USDT", df, current_price=0.02342)
    assert snap is not None
    snap.kc_upper = 0.02340  # 乖離 0.00002 < 0.5 * 0.00008
    snap.atr = 0.00008
    snap.curr_k["ma5"] = 0.02341
    snap.curr_k["close"] = 0.02342

    # 模擬延續開倉訊號
    should_entry, reason, meta = EntryGate.evaluate("CONTINUATION_OUTSIDE_KC_LONG", snap)
    assert should_entry is False, f"峰頂連續收線超過 2 根必須一票否決追多！actual reason={reason}"
    assert reason == "LATE_CHASE_STALE_CONTINUATION"
    assert meta.get("consecutive_outside", 0) > 2


def test_entry_gate_veto_stale_breakdown_at_valley():
    """
    商業場景重現：
    龍蝦在連續暴跌 4~5 根後到達底部谷底，雖然當前依然是黑 K 且跌破下軌，
    但下軌外收線已達 4 根，超跌動能隨時 V 轉。
    【預期】：EntryGate 必須觸發 LATE_CHASE_STALE_CONTINUATION 一票否決，嚴禁在谷底追賣空單！
    """
    df = _build_candles(n=35, start=0.02500, step=0.00010, side="DOWN")
    snap = DataPipeline.calculate_snapshot("龙虾/USDT", df, current_price=0.02158)
    assert snap is not None
    snap.kc_lower = 0.02160  # 乖離 0.00002 < 0.5 * 0.00008
    snap.atr = 0.00008
    snap.curr_k["ma5"] = 0.02159
    snap.curr_k["close"] = 0.02158

    should_entry, reason, meta = EntryGate.evaluate("CONTINUATION_OUTSIDE_KC_SHORT", snap)
    assert should_entry is False, f"谷底連續收線超過 2 根必須一票否決追空！actual reason={reason}"
    assert reason == "LATE_CHASE_STALE_CONTINUATION"
    assert meta.get("consecutive_outside", 0) > 2


def test_entry_gate_allows_fresh_first_breakout():
    """
    首根破軌新鮮發動點：
    前一根收盤仍在 KC 上軌之內，當根紅陽 K 剛衝破 KC 上軌，
    【預期】：放行 BREAKOUT_MAIN_WAVE_START！
    """
    df = _build_candles(n=35, start=100.0, step=0.2, side="UP")
    snap = DataPipeline.calculate_snapshot("LOBSTER/USDT", df, current_price=107.5)
    assert snap is not None
    snap.kc_upper = 107.0
    snap.atr = 1.0
    snap.prev_k = {"open": 106.2, "high": 106.9, "low": 106.1, "close": 106.8, "ma5": 106.5}  # 在軌內 (106.8 <= 107.0)
    snap.curr_k = {"open": 106.8, "high": 107.6, "low": 106.7, "close": 107.5, "ma5": 107.1}  # 剛衝破 (107.5 > 107.0)
    snap.kc_middle_slope = 0.01

    should_entry, reason, meta = EntryGate.evaluate("BREAKOUT_LONG", snap)
    assert should_entry is True, f"首根剛破軌必須及時放行開多！actual={reason}"
    assert reason == "BREAKOUT_MAIN_WAVE_START"


def test_entry_gate_veto_extension_exceeding_half_atr_on_continuation():
    """
    延續開倉防追高：
    延續開倉時，最新價距同側外軌超過 0.5 ATR，一律 LATE_CHASE_BAND_EXTENSION 拒絕。
    """
    snap = {
        "current_price": 102.0,
        "atr": 1.0,
        "kc_upper": 101.0,
        "kc_lower": 99.0,
        "kc_middle": 100.0,
        "kc_middle_slope": 0.02,
        "ema_fast": 100.5,
        "ema_slow": 99.5,
        "curr_k": {"open": 101.2, "close": 102.0, "high": 102.2, "low": 101.0, "ma5": 101.8},
        "prev_k": {"open": 100.5, "close": 101.2, "high": 101.3, "low": 100.4, "ma5": 101.0},
    }
    # 乖離 = 102.0 - 101.0 = 1.0 ATR > 0.5 ATR
    should_entry, reason, meta = EntryGate.evaluate("CONTINUATION_OUTSIDE_KC_LONG", snap)
    assert should_entry is False
    assert reason == "LATE_CHASE_BAND_EXTENSION"
