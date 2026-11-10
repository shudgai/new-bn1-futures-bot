"""
單元測試：TruePeakTroughStrategy（真峰谷轉向策略）

覆蓋場景：
  1. 震盪盤整（ADX 低）→ FREEZE
  2. 震盪盤整（帶寬過窄）→ FREEZE
  3. 無持倉 + 中軌上方吞噬陰線 → OPEN_SHORT
  4. 無持倉 + 中軌下方吞噬陽線 → OPEN_LONG
  5. 無持倉 + MACD Hist 由正轉負（峰頂）→ OPEN_SHORT
  6. 無持倉 + MACD Hist 由負轉正（谷底）→ OPEN_LONG
  7. 無持倉 + RSI 高位向下拐 → OPEN_SHORT
  8. 無持倉 + RSI 低位向上拐 → OPEN_LONG
  9. 持有多單 + KC 中軌仍向上 → VETO_HOLD_LONG（防洗）
  10. 持有空單 + KC 中軌仍向下 → VETO_HOLD_SHORT（防洗）
  11. 持有多單 + 真峰頂出現 + KC 向下 → FLIP_TO_SHORT
  12. 持有空單 + 真谷底出現 + KC 向上 → FLIP_TO_LONG
  13. 持有多單 + 真峰頂但 KC 仍向上 → VETO（不反手）
  14. 持有空單 + 真谷底但 KC 仍向下 → VETO（不反手）
  15. 中軌下方吞噬陰線（位置錯誤）→ 不觸發 OPEN_SHORT
"""

import pytest
import numpy as np
import pandas as pd

from core.strategies.true_peak_trough_strategy import (
    TruePeakTroughStrategy,
    PositionState,
    evaluate_strategy,
    evaluate_true_peak_trough,
    evaluate_true_peak_trough_strategy,
    check_exit_conditions,
    evaluate_exit_logic,
    evaluate_exit_with_trend_lock,
    check_wave_start_and_open,
    CHOP_ADX_MIN,
    CHOP_BW_MIN,
    RSI_PEAK_THRESHOLD,
    RSI_TROUGH_THRESHOLD,
    ENGULF_BODY_RATIO,
)


# ─────────────────────── 測試輔助函式 ───────────────────────

def _base_row(
    close: float,
    open_: float,
    high: float,
    low: float,
    kc_mid: float,
    rsi: float = 50.0,
    macd_hist: float = 0.001,
    adx: float = 25.0,
    bandwidth: float = 0.05,
) -> dict:
    """產生一根 K 棒的所有必要欄位。"""
    return {
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": 1000.0,
        "kc_middle": kc_mid,
        "kc_upper": kc_mid * 1.015,
        "kc_lower": kc_mid * 0.985,
        "kc_width": kc_mid * 0.030,
        "rsi": rsi,
        "macd_hist": macd_hist,
        "adx": adx,
        "bandwidth": bandwidth,
        "atr": kc_mid * 0.01,
        "ma5": kc_mid,
        "ma15": kc_mid,
    }


def _make_df(rows: list[dict]) -> pd.DataFrame:
    """將 K 棒列表轉為 DataFrame。"""
    return pd.DataFrame(rows)


def _build_bullish_market(n: int = 6, kc_mid: float = 100.0, slope: float = 0.1) -> list[dict]:
    """建立 n 根上升中的 K 棒（KC 中軌逐根上升）。"""
    rows = []
    for i in range(n):
        mid = kc_mid + i * slope
        rows.append(_base_row(
            close=mid + 0.5, open_=mid + 0.1, high=mid + 0.8, low=mid - 0.2,
            kc_mid=mid, rsi=55.0, macd_hist=0.002, adx=28.0, bandwidth=0.06,
        ))
    return rows


def _build_bearish_market(n: int = 6, kc_mid: float = 100.0, slope: float = -0.1) -> list[dict]:
    """建立 n 根下跌中的 K 棒（KC 中軌逐根下降）。"""
    rows = []
    for i in range(n):
        mid = kc_mid + i * slope
        rows.append(_base_row(
            close=mid - 0.5, open_=mid - 0.1, high=mid + 0.2, low=mid - 0.8,
            kc_mid=mid, rsi=45.0, macd_hist=-0.002, adx=28.0, bandwidth=0.06,
        ))
    return rows


strategy = TruePeakTroughStrategy(symbol="TEST/USDT")


# ─────────────────────── 測試案例 ───────────────────────

class TestChopGate:
    """震盪總開關測試"""

    def test_freeze_on_low_adx(self):
        """ADX < CHOP_ADX_MIN → FREEZE"""
        rows = _build_bullish_market(6)
        # 覆蓋 ADX 為過低值
        for r in rows:
            r["adx"] = CHOP_ADX_MIN - 1.0
        df = _make_df(rows)
        result = strategy.evaluate(df, PositionState.NONE)
        assert result["action"] == "FREEZE", f"應 FREEZE，實際: {result}"

    def test_freeze_on_low_bandwidth(self):
        """bandwidth < CHOP_BW_MIN → FREEZE"""
        rows = _build_bullish_market(6)
        for r in rows:
            r["bandwidth"] = CHOP_BW_MIN - 0.001
        df = _make_df(rows)
        result = strategy.evaluate(df, PositionState.NONE)
        assert result["action"] == "FREEZE", f"應 FREEZE，實際: {result}"

    def test_pass_when_adx_high_enough(self):
        """ADX >= CHOP_ADX_MIN 且帶寬足夠 → 不 FREEZE"""
        rows = _build_bullish_market(6)
        df = _make_df(rows)
        result = strategy.evaluate(df, PositionState.NONE)
        assert result["action"] != "FREEZE", f"不應 FREEZE，實際: {result}"


class TestOpenShortSignals:
    """無持倉時峰頂開空信號測試"""

    def test_bearish_engulf_at_peak_opens_short(self):
        """
        中軌上方，前根陽線 + 當根吞噬陰線 → OPEN_SHORT
        KC 中軌走平（slope=0 表示走平但不下行，符合峰頂條件）
        """
        rows = _build_bullish_market(5, kc_mid=100.0, slope=0.1)
        kc_mid_prev = 100.4   # 第 5 根（prev）
        kc_mid_curr = 100.4   # 第 6 根（curr），走平 → slope = 0

        # prev：陽線，收在中軌上方
        rows.append(_base_row(
            close=101.0, open_=100.5, high=101.5, low=100.3,
            kc_mid=kc_mid_prev, rsi=65.0, macd_hist=0.003, adx=28.0, bandwidth=0.06,
        ))
        # curr：吞噬陰線（收盤低於 prev.close，實體 >= ENGULF_BODY_RATIO * prev.body）
        # prev_body = |101.0 - 100.5| = 0.5，curr_body 需 >= 0.6 * 0.5 = 0.3
        rows.append(_base_row(
            close=100.4, open_=101.0, high=101.1, low=100.2,  # body=0.6 ≥ 0.3
            kc_mid=kc_mid_curr, rsi=63.0, macd_hist=0.002, adx=28.0, bandwidth=0.06,
        ))
        df = _make_df(rows)
        result = strategy.evaluate(df, PositionState.NONE)
        assert result["action"] == "OPEN_SHORT", f"應 OPEN_SHORT，實際: {result}"
        assert "BEARISH_ENGULF" in result["reason"]

    def test_macd_hist_neg_cross_opens_short(self):
        """
        中軌上方，MACD Hist 由正轉負 → OPEN_SHORT
        KC 中軌走平
        """
        rows = _build_bullish_market(5, kc_mid=100.0, slope=0.1)
        kc_mid_v = 100.4  # 走平
        # prev：MACD Hist 正值
        rows.append(_base_row(
            close=100.8, open_=100.5, high=101.0, low=100.3,
            kc_mid=kc_mid_v, rsi=62.0, macd_hist=0.005, adx=28.0, bandwidth=0.06,
        ))
        # curr：MACD Hist 轉負
        rows.append(_base_row(
            close=100.6, open_=100.9, high=101.0, low=100.4,  # 小陰線，收在中軌上方
            kc_mid=kc_mid_v, rsi=59.0, macd_hist=-0.001, adx=28.0, bandwidth=0.06,
        ))
        df = _make_df(rows)
        result = strategy.evaluate(df, PositionState.NONE)
        assert result["action"] == "OPEN_SHORT", f"應 OPEN_SHORT，實際: {result}"
        assert "MACD_HIST_NEG_CROSS" in result["reason"]

    def test_rsi_high_turn_down_opens_short(self):
        """
        中軌上方，RSI > RSI_PEAK_THRESHOLD 且當根 RSI < 前根 → OPEN_SHORT
        KC 中軌走平
        """
        rows = _build_bullish_market(5, kc_mid=100.0, slope=0.1)
        kc_mid_v = 100.4
        rows.append(_base_row(
            close=100.9, open_=100.6, high=101.2, low=100.4,
            kc_mid=kc_mid_v, rsi=RSI_PEAK_THRESHOLD + 5.0, macd_hist=0.002, adx=28.0, bandwidth=0.06,
        ))
        rows.append(_base_row(
            close=100.7, open_=100.9, high=101.0, low=100.5,
            kc_mid=kc_mid_v, rsi=RSI_PEAK_THRESHOLD + 2.0,  # RSI 下降
            macd_hist=0.001, adx=28.0, bandwidth=0.06,
        ))
        df = _make_df(rows)
        result = strategy.evaluate(df, PositionState.NONE)
        assert result["action"] == "OPEN_SHORT", f"應 OPEN_SHORT，實際: {result}"
        assert "RSI_HIGH_TURN_DOWN" in result["reason"]

    def test_no_short_below_kc_mid(self):
        """
        中軌下方出現吞噬陰線 → 位置錯誤，不應 OPEN_SHORT
        """
        rows = _build_bearish_market(5, kc_mid=100.0, slope=-0.1)
        kc_mid_v = 99.5  # 下跌 → 收盤已在中軌以下
        # prev：陽線但在中軌下方
        rows.append(_base_row(
            close=99.0, open_=98.5, high=99.3, low=98.2,
            kc_mid=kc_mid_v, rsi=42.0, macd_hist=-0.002, adx=28.0, bandwidth=0.06,
        ))
        # curr：吞噬陰線（但在中軌下方）
        rows.append(_base_row(
            close=98.4, open_=99.0, high=99.1, low=98.1,
            kc_mid=kc_mid_v, rsi=40.0, macd_hist=-0.003, adx=28.0, bandwidth=0.06,
        ))
        df = _make_df(rows)
        result = strategy.evaluate(df, PositionState.NONE)
        assert result["action"] != "OPEN_SHORT", f"不應 OPEN_SHORT，實際: {result}"


class TestOpenLongSignals:
    """無持倉時谷底開多信號測試"""

    def test_bullish_engulf_at_trough_opens_long(self):
        """
        中軌下方，前根陰線 + 當根吞噬陽線 → OPEN_LONG
        KC 中軌走平（slope=0）
        """
        rows = _build_bearish_market(5, kc_mid=100.0, slope=-0.1)
        kc_mid_v = 99.5  # 走平
        # prev：陰線，在中軌下方
        rows.append(_base_row(
            close=98.8, open_=99.3, high=99.5, low=98.5,  # body=0.5
            kc_mid=kc_mid_v, rsi=35.0, macd_hist=-0.004, adx=28.0, bandwidth=0.06,
        ))
        # curr：吞噬陽線，body >= ENGULF_BODY_RATIO * 0.5 = 0.3
        rows.append(_base_row(
            close=99.4, open_=98.8, high=99.5, low=98.6,  # body=0.6 ≥ 0.3
            kc_mid=kc_mid_v, rsi=37.0, macd_hist=-0.002, adx=28.0, bandwidth=0.06,
        ))
        df = _make_df(rows)
        result = strategy.evaluate(df, PositionState.NONE)
        assert result["action"] == "OPEN_LONG", f"應 OPEN_LONG，實際: {result}"
        assert "BULLISH_ENGULF" in result["reason"]

    def test_macd_hist_pos_cross_opens_long(self):
        """
        中軌下方，MACD Hist 由負轉正 → OPEN_LONG
        KC 中軌走平
        """
        rows = _build_bearish_market(5, kc_mid=100.0, slope=-0.1)
        kc_mid_v = 99.5
        rows.append(_base_row(
            close=99.1, open_=99.3, high=99.5, low=98.8,
            kc_mid=kc_mid_v, rsi=38.0, macd_hist=-0.004, adx=28.0, bandwidth=0.06,
        ))
        rows.append(_base_row(
            close=99.3, open_=99.1, high=99.5, low=98.9,  # 小陽線，收在中軌下方
            kc_mid=kc_mid_v, rsi=40.0, macd_hist=0.001, adx=28.0, bandwidth=0.06,
        ))
        df = _make_df(rows)
        result = strategy.evaluate(df, PositionState.NONE)
        assert result["action"] == "OPEN_LONG", f"應 OPEN_LONG，實際: {result}"
        assert "MACD_HIST_POS_CROSS" in result["reason"]

    def test_rsi_low_turn_up_opens_long(self):
        """
        中軌下方，RSI < RSI_TROUGH_THRESHOLD 且當根 RSI > 前根 → OPEN_LONG
        KC 中軌走平
        """
        rows = _build_bearish_market(5, kc_mid=100.0, slope=-0.1)
        kc_mid_v = 99.5
        rows.append(_base_row(
            close=99.0, open_=99.2, high=99.4, low=98.7,
            kc_mid=kc_mid_v, rsi=RSI_TROUGH_THRESHOLD - 5.0, macd_hist=-0.003, adx=28.0, bandwidth=0.06,
        ))
        rows.append(_base_row(
            close=99.2, open_=99.0, high=99.4, low=98.8,
            kc_mid=kc_mid_v, rsi=RSI_TROUGH_THRESHOLD - 2.0,  # RSI 上升
            macd_hist=-0.002, adx=28.0, bandwidth=0.06,
        ))
        df = _make_df(rows)
        result = strategy.evaluate(df, PositionState.NONE)
        assert result["action"] == "OPEN_LONG", f"應 OPEN_LONG，實際: {result}"
        assert "RSI_LOW_TURN_UP" in result["reason"]


class TestMidTrendHold:
    """持倉防洗（中途不平倉）測試"""

    def test_long_hold_on_bullish_kc(self):
        """
        持有多單，KC 中軌向上，收盤在中軌以上 → VETO_HOLD_LONG
        """
        rows = _build_bullish_market(6, kc_mid=100.0, slope=0.1)
        df = _make_df(rows)
        result = strategy.evaluate(df, PositionState.LONG)
        assert result["action"] == "HOLD", f"應 HOLD，實際: {result}"
        assert "VETO_HOLD_LONG" in result["reason"]

    def test_short_hold_on_bearish_kc(self):
        """
        持有空單，KC 中軌向下，收盤在中軌以下 → VETO_HOLD_SHORT
        """
        rows = _build_bearish_market(6, kc_mid=100.0, slope=-0.1)
        df = _make_df(rows)
        result = strategy.evaluate(df, PositionState.SHORT)
        assert result["action"] == "HOLD", f"應 HOLD，實際: {result}"
        assert "VETO_HOLD_SHORT" in result["reason"]


class TestFlipSignals:
    """翻倉信號測試"""

    def test_flip_long_to_short_on_true_peak_with_kc_down(self):
        """
        持有多單 + 真峰頂出現（吞噬陰線）+ KC 中軌向下 → FLIP_TO_SHORT
        """
        # 先建立一段上升行情
        rows = _build_bullish_market(5, kc_mid=100.0, slope=0.1)
        kc_mid_prev = 100.4
        kc_mid_curr = 100.3  # KC 中軌開始向下（slope < 0）
        # prev：陽線在中軌上方
        rows.append(_base_row(
            close=101.0, open_=100.5, high=101.5, low=100.3,
            kc_mid=kc_mid_prev, rsi=68.0, macd_hist=0.003, adx=28.0, bandwidth=0.06,
        ))
        # curr：吞噬陰線 + KC 開始下行
        rows.append(_base_row(
            close=100.3, open_=101.0, high=101.2, low=100.1,  # body=0.7 ≥ ENGULF_BODY_RATIO*0.5=0.3
            kc_mid=kc_mid_curr, rsi=62.0, macd_hist=0.001, adx=28.0, bandwidth=0.06,
        ))
        df = _make_df(rows)
        result = strategy.evaluate(df, PositionState.LONG)
        assert result["action"] == "FLIP_TO_SHORT", f"應 FLIP_TO_SHORT，實際: {result}"
        assert "FLIP_LONG_TO_SHORT" in result["reason"]

    def test_flip_short_to_long_on_true_trough_with_kc_up(self):
        """
        持有空單 + 真谷底出現（吞噬陽線）+ KC 中軌向上 → FLIP_TO_LONG
        """
        rows = _build_bearish_market(5, kc_mid=100.0, slope=-0.1)
        kc_mid_prev = 99.5
        kc_mid_curr = 99.6   # KC 中軌開始向上（slope > 0）
        rows.append(_base_row(
            close=98.8, open_=99.3, high=99.5, low=98.5,  # 陰線 body=0.5
            kc_mid=kc_mid_prev, rsi=32.0, macd_hist=-0.004, adx=28.0, bandwidth=0.06,
        ))
        # curr：吞噬陽線 + KC 開始上行
        rows.append(_base_row(
            close=99.4, open_=98.8, high=99.6, low=98.6,  # body=0.6 ≥ 0.3
            kc_mid=kc_mid_curr, rsi=36.0, macd_hist=-0.001, adx=28.0, bandwidth=0.06,
        ))
        df = _make_df(rows)
        result = strategy.evaluate(df, PositionState.SHORT)
        assert result["action"] == "FLIP_TO_LONG", f"應 FLIP_TO_LONG，實際: {result}"
        assert "FLIP_SHORT_TO_LONG" in result["reason"]

    def test_no_flip_to_short_when_kc_still_up(self):
        """
        持有多單 + 真峰頂訊號出現 + 但 KC 中軌仍向上 → VETO（不反手開空）
        """
        rows = _build_bullish_market(5, kc_mid=100.0, slope=0.1)
        kc_mid_prev = 100.4
        kc_mid_curr = 100.5  # KC 中軌仍向上！
        rows.append(_base_row(
            close=101.0, open_=100.5, high=101.5, low=100.3,
            kc_mid=kc_mid_prev, rsi=68.0, macd_hist=0.003, adx=28.0, bandwidth=0.06,
        ))
        rows.append(_base_row(
            close=100.5, open_=101.0, high=101.2, low=100.3,  # 吞噬陰線
            kc_mid=kc_mid_curr, rsi=62.0, macd_hist=0.001, adx=28.0, bandwidth=0.06,
        ))
        df = _make_df(rows)
        result = strategy.evaluate(df, PositionState.LONG)
        # KC 仍向上，不應反手開空，應 VETO
        assert result["action"] != "FLIP_TO_SHORT", f"不應 FLIP_TO_SHORT，實際: {result}"

    def test_no_flip_to_long_when_kc_still_down(self):
        """
        持有空單 + 真谷底訊號出現 + 但 KC 中軌仍向下 → VETO（不反手開多）
        """
        rows = _build_bearish_market(5, kc_mid=100.0, slope=-0.1)
        kc_mid_prev = 99.5
        kc_mid_curr = 99.4   # KC 中軌仍向下！
        rows.append(_base_row(
            close=98.8, open_=99.3, high=99.5, low=98.5,
            kc_mid=kc_mid_prev, rsi=32.0, macd_hist=-0.004, adx=28.0, bandwidth=0.06,
        ))
        rows.append(_base_row(
            close=99.3, open_=98.8, high=99.5, low=98.6,  # 吞噬陽線
            kc_mid=kc_mid_curr, rsi=36.0, macd_hist=-0.001, adx=28.0, bandwidth=0.06,
        ))
        df = _make_df(rows)
        result = strategy.evaluate(df, PositionState.SHORT)
        assert result["action"] != "FLIP_TO_LONG", f"不應 FLIP_TO_LONG，實際: {result}"


class TestConvenienceFunction:
    """便利函式測試"""

    def test_evaluate_true_peak_trough_string_state(self):
        """以字串形式傳入 position 狀態應正常工作"""
        rows = _build_bullish_market(6)
        df = _make_df(rows)
        result = evaluate_true_peak_trough(df, current_position="NONE", symbol="TEST/USDT")
        assert isinstance(result, dict)
        assert "action" in result

    def test_evaluate_true_peak_trough_enum_state(self):
        """以 PositionState 枚舉傳入應正常工作"""
        rows = _build_bullish_market(6)
        df = _make_df(rows)
        result = evaluate_true_peak_trough(df, current_position=PositionState.NONE)
        assert isinstance(result, dict)
        assert "action" in result


class TestCheckExitConditions:
    """平倉雙軌過濾機制 (check_exit_conditions) 測試"""

    def test_emergency_close_on_waterfall_atr(self):
        """價格偏離超過 3 倍 ATR ➔ EMERGENCY_CLOSE (大瀑布緊急出場)"""
        rows = _build_bullish_market(6)
        df = _make_df(rows)
        entry_price = 100.0
        # ATR 為 1.0 (kc_mid * 0.01 = 1.0)，若跌破 4.0 點 (> 3 ATR) 觸發緊急出場
        current_price = 95.0  # 偏差 5.0 > 3.0 ATR
        res = check_exit_conditions(
            df=df,
            current_price=current_price,
            current_position=PositionState.LONG,
            entry_price=entry_price,
            is_bar_closed=False,  # 盤中即可觸發
        )
        assert res["action"] == "EMERGENCY_CLOSE"
        assert "大瀑布" in res["reason"] or "EMERGENCY" in res["reason"]

    def test_hard_stop_loss_long(self):
        """多單跌幅 >= 2% 觸發硬止損 ➔ STOP_LOSS"""
        rows = _build_bullish_market(6)
        df = _make_df(rows)
        entry_price = 100.0
        current_price = 97.9  # 虧損 2.1% (但未超過 3 ATR，假設 ATR 較大如 2.0，3 ATR=6.0)
        # 設定 df 中的 atr 為 2.0
        for r in rows:
            r["atr"] = 2.0
        df = _make_df(rows)
        res = check_exit_conditions(
            df=df,
            current_price=current_price,
            current_position=PositionState.LONG,
            entry_price=entry_price,
            is_bar_closed=False,
        )
        assert res["action"] == "STOP_LOSS"
        assert "硬止損" in res["reason"]

    def test_hard_stop_loss_short(self):
        """空單漲幅 >= 2% 觸發硬止損 ➔ STOP_LOSS"""
        rows = _build_bearish_market(6)
        for r in rows:
            r["atr"] = 2.0
        df = _make_df(rows)
        entry_price = 100.0
        current_price = 102.1  # 虧損 2.1%
        res = check_exit_conditions(
            df=df,
            current_price=current_price,
            current_position=PositionState.SHORT,
            entry_price=entry_price,
            is_bar_closed=False,
        )
        assert res["action"] == "STOP_LOSS"
        assert "硬止損" in res["reason"]

    def test_hold_when_bar_unclosed_without_emergency(self):
        """未收盤且無異常瀑布/硬止損 ➔ HOLD (防洗)"""
        rows = _build_bullish_market(6)
        df = _make_df(rows)
        entry_price = 100.0
        current_price = 99.5  # 輕微回踩 0.5%
        res = check_exit_conditions(
            df=df,
            current_price=current_price,
            current_position=PositionState.LONG,
            entry_price=entry_price,
            is_bar_closed=False,
        )
        assert res["action"] == "HOLD"
        assert "K線尚未走完" in res["reason"]

    def test_evaluate_flip_when_bar_is_closed(self):
        """K 線已收盤 ➔ 轉由 evaluate_true_peak_trough_strategy 評估"""
        rows = _build_bullish_market(6)
        df = _make_df(rows)
        entry_price = 100.0
        current_price = 100.5
        res = check_exit_conditions(
            df=df,
            current_price=current_price,
            current_position=PositionState.LONG,
            entry_price=entry_price,
            is_bar_closed=True,
        )
        # 多單且 KC 向上 ➔ 應為 HOLD (VETO_HOLD_LONG)
        assert res["action"] == "HOLD"
        assert "VETO_HOLD_LONG" in res["reason"]


class TestSecondaryPullbackEntry:
    """錯過首波真峰谷後的「補救開倉 (Secondary Entry)」測試"""

    def test_secondary_pullback_short_entry(self):
        """
        中軌向下 (偏空) + 反彈回踩 MA5 收陰受阻 (close < open 且 close <= ma5) ➔ 補救開空
        """
        rows = _build_bearish_market(6, kc_mid=100.0, slope=-0.1)
        # 最新一根回踩 MA5 且收陰：kc_mid=99.4, ma5=99.2, open=99.3, close=99.1 <= ma5
        rows[-1]["close"] = 99.1
        rows[-1]["open"] = 99.3
        rows[-1]["ma5"] = 99.2
        rows[-1]["bb_lower"] = 98.0
        df = _make_df(rows)
        res = strategy.evaluate(df, PositionState.NONE)
        assert res["action"] == "OPEN_SHORT"
        assert "【補救開倉】" in res["reason"]

    def test_secondary_pullback_long_entry(self):
        """
        中軌向上 (偏多) + 回踩 MA5 止跌收陽 (close > open 且 close >= ma5) ➔ 補救開多
        """
        rows = _build_bullish_market(6, kc_mid=100.0, slope=0.1)
        # 最新一根回踩 MA5 且收陽：kc_mid=100.6, ma5=100.8, open=100.7, close=100.9 >= ma5
        rows[-1]["close"] = 100.9
        rows[-1]["open"] = 100.7
        rows[-1]["ma5"] = 100.8
        rows[-1]["bb_upper"] = 102.0
        df = _make_df(rows)
        res = strategy.evaluate(df, PositionState.NONE)
        assert res["action"] == "OPEN_LONG"
        assert "【補救開倉】" in res["reason"]


class TestCleanEvaluateStrategy:
    """清理後簡潔結構 evaluate_strategy 測試"""

    def test_chop_freeze(self):
        """一般幣種 ADX < 20 或帶幅 < 0.015 ➔ 凍結交易"""
        rows = _build_bullish_market(6)
        for r in rows:
            r["adx"] = 18.0
        df = _make_df(rows)
        res = evaluate_strategy(df, PositionState.NONE, symbol="BTC/USDT")
        assert res["action"] == "HOLD"
        assert "震盪盤保護中" in res["reason"]

    def test_meme_coin_relax_threshold(self):
        """妖幣帶寬 0.0045 且 ADX 25 ➔ 不凍結；若帶寬 < 0.004 且 ADX < 35 ➔ 凍結"""
        rows = _build_bullish_market(6)
        for r in rows:
            r["bandwidth"] = 0.003
            r["adx"] = 25.0
        df = _make_df(rows)
        res_freeze = evaluate_strategy(df, PositionState.NONE, symbol="1000PEPE/USDT")
        assert res_freeze["action"] == "HOLD"
        assert "震盪盤保護中" in res_freeze["reason"]

        # ADX >= 35 強動能 ➔ 妖幣豁免帶寬過窄
        for r in rows:
            r["bandwidth"] = 0.003
            r["adx"] = 38.0
        df2 = _make_df(rows)
        res_pass = evaluate_strategy(df2, PositionState.NONE, symbol="1000PEPE/USDT")
        assert "震盪盤保護中" not in res_pass["reason"]

    def test_long_flip_to_short_at_peak(self):
        """持有多單，到達真峰頂 (陰線跌破前低) ➔ FLIP_TO_SHORT"""
        rows = _build_bullish_market(6)
        # 前一根陽線
        rows[-2]["close"] = 101.5
        rows[-2]["open"] = 100.5
        rows[-2]["low"] = 100.4
        # 當前一根陰線實體跌破前低
        rows[-1]["close"] = 100.2
        rows[-1]["open"] = 101.4
        rows[-1]["kc_mid"] = 100.0  # c0 >= kc_mid (高位)
        df = _make_df(rows)
        res = evaluate_strategy(df, PositionState.LONG)
        assert res["action"] == "FLIP_TO_SHORT"
        assert "到達真峰頂" in res["reason"]

    def test_short_flip_to_long_at_trough(self):
        """持有空單，到達真谷底 (陽線突破前高) ➔ FLIP_TO_LONG"""
        rows = _build_bearish_market(6)
        rows[-2]["close"] = 98.5
        rows[-2]["open"] = 99.5
        rows[-2]["high"] = 99.6
        # 當前一根陽線實體突破前高
        rows[-1]["close"] = 99.8
        rows[-1]["open"] = 98.6
        rows[-1]["kc_mid"] = 100.0  # c0 <= kc_mid (低位)
        df = _make_df(rows)
        res = evaluate_strategy(df, PositionState.SHORT)
        assert res["action"] == "FLIP_TO_LONG"
        assert "到達真谷底" in res["reason"]

    def test_none_position_ma5_retest_long(self):
        """無持倉，回踩 MA5 止跌補救開多"""
        rows = _build_bullish_market(6)
        rows[-1]["kc_mid"] = 100.0
        rows[-1]["ma5"] = 100.8
        rows[-1]["open"] = 100.7
        rows[-1]["close"] = 101.0  # c0 > o0, c0 >= ma5, c0 > kc_mid
        rows[-1]["bb_upper"] = 103.0  # c0 < bb_upper
        # 避開真谷底條件 (c0 > kc_mid 本身就不是低位)
        df = _make_df(rows)
        res = evaluate_strategy(df, PositionState.NONE)
        assert res["action"] == "OPEN_LONG"
        assert "回踩 MA5 止跌補救開多" in res["reason"]

    def test_none_position_ma5_retest_short(self):
        """無持倉，反彈 MA5 受阻補救開空"""
        rows = _build_bearish_market(6)
        rows[-1]["kc_mid"] = 100.0
        rows[-1]["ma5"] = 99.2
        rows[-1]["open"] = 99.3
        rows[-1]["close"] = 99.0  # c0 < o0, c0 <= ma5, c0 < kc_mid
        rows[-1]["bb_lower"] = 97.0  # c0 > bb_lower
        df = _make_df(rows)
        res = evaluate_strategy(df, PositionState.NONE)
        assert res["action"] == "OPEN_SHORT"
        assert "反彈 MA5 受阻補救開空" in res["reason"]


class TestEvaluateExitLogic:
    """專治平倉滯後的三層離場/平倉機制 (evaluate_exit_logic) 測試"""

    def test_peak_engulfing_exit_long(self):
        """【條件 A】真峰頂第一時間吞噬平多：高位 + 陰線實體跌破前低 ➔ CLOSE_LONG"""
        rows = _build_bullish_market(6)
        rows[-2]["low"] = 101.0
        rows[-1]["open"] = 101.5
        rows[-1]["close"] = 100.8  # c0 < o0 且 c0 < l1
        rows[-1]["kc_mid"] = 100.0  # c0 >= kc_mid (高位)
        df = _make_df(rows)
        res = evaluate_exit_logic(df, PositionState.LONG, entry_price=100.0)
        assert res["action"] == "CLOSE_LONG"
        assert "真峰頂" in res["reason"]

    def test_trailing_stop_profit_lock_exit_long(self):
        """【條件 B】動態追蹤止盈平多：最高浮盈 > 0.6% 且自高點回吐 > 0.3% ➔ CLOSE_LONG"""
        rows = _build_bullish_market(10)
        entry_price = 100.0
        # 曾經衝高到 101.0 (+1.0% > 0.6%)
        rows[4]["high"] = 101.0
        # 目前回吐到 100.5 (自 101.0 回吐 0.5/101 = 0.495% > 0.3%)
        # 但未跌破 MA5 也未吞噬，僅觸發追蹤止盈
        rows[-1]["open"] = 100.4
        rows[-1]["close"] = 100.5  # 陽線 (排除條件 A)
        rows[-1]["ma5"] = 100.4    # close > ma5 (排除條件 C)
        rows[-1]["high"] = 100.6
        rows[-1]["kc_mid"] = 100.0
        df = _make_df(rows)
        res = evaluate_exit_logic(df, PositionState.LONG, entry_price=entry_price)
        assert res["action"] == "CLOSE_LONG"
        assert "追蹤止盈" in res["reason"]

    def test_broken_ma5_exit_long(self):
        """【條件 C】跌破 MA5 均線平多及時保本：close < ma5 且收黑陰線 ➔ CLOSE_LONG"""
        rows = _build_bullish_market(6)
        rows[-2]["low"] = 99.0
        # 排除追蹤止盈觸發 (entry_price 設為 101.0，最高點 101.3，最高浮盈 0.29% < 0.6%)
        entry_price = 101.0
        rows[-1]["open"] = 101.0
        rows[-1]["close"] = 100.8  # 陰線
        rows[-1]["ma5"] = 100.9    # close < ma5
        rows[-1]["kc_mid"] = 99.5
        df = _make_df(rows)
        res = evaluate_exit_logic(df, PositionState.LONG, entry_price=entry_price)
        assert res["action"] == "CLOSE_LONG"
        assert "跌破 MA5" in res["reason"]

    def test_trough_engulfing_exit_short(self):
        """空單真谷底第一時間平空：低位 + 陽線突破前高 ➔ CLOSE_SHORT"""
        rows = _build_bearish_market(6)
        rows[-2]["high"] = 99.0
        rows[-1]["open"] = 98.5
        rows[-1]["close"] = 99.2  # c0 > o0 且 c0 > h1
        rows[-1]["kc_mid"] = 100.0  # c0 <= kc_mid (低位)
        df = _make_df(rows)
        res = evaluate_exit_logic(df, PositionState.SHORT, entry_price=100.0)
        assert res["action"] == "CLOSE_SHORT"
        assert "真谷底" in res["reason"]


class TestEvaluateExitWithTrendLock:
    """波段方向鎖定平倉邏輯 (evaluate_exit_with_trend_lock) 測試"""

    def test_hold_long_during_minor_pullback_when_kc_up(self):
        """【走向未變抱緊】多單持倉，KC中軌持續向上，收陰小回踩不跌破中軌 ➔ 強制 HOLD"""
        rows = _build_bullish_market(6, kc_mid=100.0, slope=0.1)
        # 最新一根小陰線回踩，但 KC 中軌仍向上 (slope > 0) 且 c0 > kc_mid
        rows[-1]["open"] = 101.2
        rows[-1]["close"] = 100.8  # 小陰線
        rows[-1]["kc_mid"] = 100.5 # c0 > kc_mid
        df = _make_df(rows)
        res = evaluate_exit_with_trend_lock(df, PositionState.LONG, entry_price=100.0)
        assert res["action"] == "HOLD"
        assert "多頭波段走向未變" in res["reason"]

    def test_flip_short_when_long_breaks_kc_mid(self):
        """【走向改變平多翻空】多單持倉，價格實體跌破 KC 中軌 ➔ FLIP_TO_SHORT"""
        rows = _build_bullish_market(6, kc_mid=100.0, slope=0.1)
        # 最新一根貫穿跌破 KC 中軌
        rows[-1]["open"] = 100.8
        rows[-1]["close"] = 100.2  # 實體破中軌
        rows[-1]["kc_mid"] = 100.5 # c0 < kc_mid
        df = _make_df(rows)
        res = evaluate_exit_with_trend_lock(df, PositionState.LONG, entry_price=100.0)
        assert res["action"] == "FLIP_TO_SHORT"
        assert "波段走向已改變" in res["reason"]

    def test_hold_short_during_minor_rebound_when_kc_down(self):
        """【走向未變抱緊】空單持倉，KC中軌持續向下，收陽小反彈不突破中軌 ➔ 強制 HOLD"""
        rows = _build_bearish_market(6, kc_mid=100.0, slope=-0.1)
        # 最新一根小陽線反彈，但 KC 中軌仍向下且 c0 < kc_mid
        rows[-1]["open"] = 99.0
        rows[-1]["close"] = 99.3   # 小陽線
        rows[-1]["kc_mid"] = 99.5  # c0 < kc_mid
        df = _make_df(rows)
        res = evaluate_exit_with_trend_lock(df, PositionState.SHORT, entry_price=100.0)
        assert res["action"] == "HOLD"
        assert "空頭波段走向未變" in res["reason"]

    def test_flip_long_when_short_breaks_kc_mid(self):
        """【走向改變平空翻多】空單持倉，價格實體突破 KC 中軌 ➔ FLIP_TO_LONG"""
        rows = _build_bearish_market(6, kc_mid=100.0, slope=-0.1)
        # 最新一根突破 KC 中軌
        rows[-1]["open"] = 99.2
        rows[-1]["close"] = 99.8   # 突破中軌
        rows[-1]["kc_mid"] = 99.5  # c0 > kc_mid
        df = _make_df(rows)
        res = evaluate_exit_with_trend_lock(df, PositionState.SHORT, entry_price=100.0)
        assert res["action"] == "FLIP_TO_LONG"
        assert "波段走向已改變" in res["reason"]

    def test_emergency_close_on_2atr_waterfall(self):
        """極端風控防禦：虧損超過 2 倍 ATR ➔ EMERGENCY_CLOSE"""
        rows = _build_bullish_market(6)
        for r in rows:
            r["atr"] = 1.0  # 2 ATR = 2.0
        entry_price = 100.0
        rows[-1]["close"] = 97.8 # 虧損 2.2 > 2.0 ATR
        df = _make_df(rows)
        res = evaluate_exit_with_trend_lock(df, PositionState.LONG, entry_price=entry_price)
        assert res["action"] == "EMERGENCY_CLOSE"
        assert "極端風控" in res["reason"]


class TestCheckWaveStartAndOpen:
    """波段開始第一時間開倉與翻倉 (check_wave_start_and_open) 測試"""

    def test_wave_hold_when_adx_low_and_bandwidth_small(self):
        """ADX < 20 且帶寬 < 0.005 ➔ 波段尚未發動，HOLD"""
        rows = _build_bullish_market(6)
        for r in rows:
            r["adx"] = 15.0
            r["bandwidth"] = 0.003
        df = _make_df(rows)
        res = check_wave_start_and_open(df, PositionState.NONE)
        assert res["action"] == "HOLD"
        assert "波段尚未發動" in res["reason"]

    def test_wave_start_open_long_at_trough(self):
        """帶寬 >= 0.005，低位谷底大陽吞噬突破前高 ➔ 🚀 多頭波段開始！第一時間開多"""
        rows = _build_bearish_market(6)
        for r in rows:
            r["bandwidth"] = 0.006
        # 前一根陰線
        rows[-2]["high"] = 99.5
        rows[-2]["close"] = 98.8
        # 當前一根大陽線實體突破前高
        rows[-1]["open"] = 98.7
        rows[-1]["close"] = 99.7  # c0 > o0 且 c0 > h1
        rows[-1]["kc_mid"] = 100.0 # c0 <= kc_mid (低位)
        rows[-1]["bb_upper"] = 102.0
        df = _make_df(rows)
        res = check_wave_start_and_open(df, PositionState.NONE)
        assert res["action"] == "OPEN_LONG"
        assert "多頭波段開始" in res["reason"]

    def test_wave_start_open_short_at_peak(self):
        """帶寬 >= 0.005，高位峰頂大陰吞噬跌破前低 ➔ 📉 空頭波段開始！第一時間開空"""
        rows = _build_bullish_market(6)
        for r in rows:
            r["bandwidth"] = 0.006
        rows[-2]["low"] = 100.5
        rows[-2]["close"] = 101.2
        rows[-1]["open"] = 101.3
        rows[-1]["close"] = 100.3  # c0 < o0 且 c0 < l1
        rows[-1]["kc_mid"] = 100.0 # c0 >= kc_mid (高位)
        rows[-1]["bb_lower"] = 98.0
        df = _make_df(rows)
        res = check_wave_start_and_open(df, PositionState.NONE)
        assert res["action"] == "OPEN_SHORT"
        assert "空頭波段開始" in res["reason"]

    def test_hold_long_during_wave_continuation(self):
        """持有多單，未到真峰頂前 ➔ 繼續持多讓利潤奔跑"""
        rows = _build_bullish_market(6)
        for r in rows:
            r["bandwidth"] = 0.006
        rows[-1]["open"] = 100.8
        rows[-1]["close"] = 101.2  # 順向陽線
        rows[-1]["kc_mid"] = 100.0
        df = _make_df(rows)
        res = check_wave_start_and_open(df, PositionState.LONG)
        assert res["action"] == "HOLD"
        assert "多頭波段走向未變" in res["reason"]

    def test_flip_to_short_when_long_reaches_peak(self):
        """持有多單，波段到頂出現峰頂反轉 ➔ 🔄 平多並瞬間翻空"""
        rows = _build_bullish_market(6)
        for r in rows:
            r["bandwidth"] = 0.006
        rows[-2]["low"] = 100.5
        rows[-1]["open"] = 101.3
        rows[-1]["close"] = 100.3  # 陰線實體破前低
        rows[-1]["kc_mid"] = 100.0
        df = _make_df(rows)
        res = check_wave_start_and_open(df, PositionState.LONG)
        assert res["action"] == "FLIP_TO_SHORT"
        assert "平多並瞬間翻空" in res["reason"]







