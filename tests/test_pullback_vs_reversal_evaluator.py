"""
tests/test_pullback_vs_reversal_evaluator.py
============================================
驗證健康回踩 (HEALTHY_PULLBACK) vs 真見頂/見底 (TOP/BOTTOM_REVERSAL) 核心模組
"""

import pandas as pd
import pytest
from core.services.exits.pullback_vs_reversal_evaluator import (
    PullbackVsReversalEvaluator,
    MarketRegimeState,
)


def _build_base_candles(n: int = 15, base_price: float = 100.0) -> pd.DataFrame:
    """生成基礎行情 K 線數據"""
    bars = []
    for i in range(n):
        p = base_price + i * 0.5
        bars.append({
            "timestamp": 1700000000 + i * 60,
            "open": p - 0.2,
            "high": p + 0.5,
            "low": p - 0.3,
            "close": p,
            "kc_middle": p - 0.8,
            "atr": 1.0,
            "is_closed": True,
        })
    return pd.DataFrame(bars)


def test_healthy_pullback_veto_hold():
    """場景 1：健康回踩（收盤穩守中軌 + 帶承接下影線 + 實體受控 <= 1.2 ATR）➔ 強制續抱"""
    df = _build_base_candles(15, 100.0)
    # 最後一根 K 棒小黑回調：Open 107.5, Close 107.0 (實體 0.5 ATR), Low 106.2 (下影線 0.8), KC 中軌 105.5
    df.loc[df.index[-1], "open"] = 107.5
    df.loc[df.index[-1], "high"] = 107.6
    df.loc[df.index[-1], "low"] = 106.2
    df.loc[df.index[-1], "close"] = 107.0
    df.loc[df.index[-2], "kc_middle"] = 105.0
    df.loc[df.index[-1], "kc_middle"] = 105.5
    df.loc[df.index[-1], "atr"] = 1.0

    evaluator = PullbackVsReversalEvaluator()
    res = evaluator.evaluate(
        side="LONG",
        df=df,
        entry_price=100.0,
        current_quote=107.0,
        peak_price=107.6,
        leverage=5.0,
    )
    assert res.state == MarketRegimeState.HEALTHY_PULLBACK
    assert res.should_exit is False
    assert "VETO_HOLD" in res.reason


def test_panic_marubozu_dump_top_reversal():
    """場景 2：恐慌光頭大陰線（實體 2.0 ATR > 1.8 ATR 且幾乎無下影線）➔ 立即平多"""
    df = _build_base_candles(15, 100.0)
    # 最後一根出現巨大光頭陰線：Open 108.0, Close 106.0, Low 105.9, ATR=1.0
    df.loc[df.index[-1], "open"] = 108.0
    df.loc[df.index[-1], "high"] = 108.1
    df.loc[df.index[-1], "low"] = 105.9
    df.loc[df.index[-1], "close"] = 106.0
    df.loc[df.index[-1], "kc_middle"] = 105.5
    df.loc[df.index[-1], "atr"] = 1.0

    evaluator = PullbackVsReversalEvaluator()
    res = evaluator.evaluate(
        side="LONG",
        df=df,
        entry_price=100.0,
        current_quote=106.0,
        peak_price=108.1,
        leverage=5.0,
    )
    assert res.state == MarketRegimeState.TOP_REVERSAL
    assert res.should_exit is True
    assert "PANIC_MARUBOZU_DUMP" in res.triggers


def test_kc_midline_breakdown_top_reversal():
    """場景 3：摜破 KC 中軌防守線 ➔ 立即平多"""
    df = _build_base_candles(15, 100.0)
    # 最後一根收盤跌破中軌：Close 104.5 < KC Mid 105.5
    df.loc[df.index[-1], "open"] = 106.0
    df.loc[df.index[-1], "high"] = 106.2
    df.loc[df.index[-1], "low"] = 104.3
    df.loc[df.index[-1], "close"] = 104.5
    df.loc[df.index[-1], "kc_middle"] = 105.5
    df.loc[df.index[-1], "atr"] = 1.0

    evaluator = PullbackVsReversalEvaluator()
    res = evaluator.evaluate(
        side="LONG",
        df=df,
        entry_price=100.0,
        current_quote=104.5,
        peak_price=107.0,
        leverage=5.0,
    )
    assert res.state == MarketRegimeState.TOP_REVERSAL
    assert res.should_exit is True
    assert "KC_MIDLINE_BREAKDOWN" in res.triggers


def test_swing_low_breakdown_top_reversal():
    """場景 4：微觀結構破位（跌破前 5 根最低點）➔ 立即平多"""
    df = _build_base_candles(15, 100.0)
    # 設定前 5 根的最低點為 105.0，當根直接跌破收在 104.8
    for i in range(-6, -1):
        df.loc[df.index[i], "low"] = 105.0
    df.loc[df.index[-1], "open"] = 105.8
    df.loc[df.index[-1], "close"] = 104.8
    df.loc[df.index[-1], "low"] = 104.7
    df.loc[df.index[-1], "kc_middle"] = 104.0
    df.loc[df.index[-1], "atr"] = 1.0

    evaluator = PullbackVsReversalEvaluator()
    res = evaluator.evaluate(
        side="LONG",
        df=df,
        entry_price=100.0,
        current_quote=104.8,
        peak_price=107.0,
        leverage=5.0,
    )
    assert res.state == MarketRegimeState.TOP_REVERSAL
    assert res.should_exit is True
    assert "SWING_LOW_BREAKDOWN" in res.triggers


def test_trailing_profit_lock_drawdown():
    """場景 5：浮盈達標 (15% ROE) 後自高點回吐達 25% ➔ 移動鎖利平倉"""
    df = _build_base_candles(15, 100.0)
    # Entry=100, Peak=105 (ROE = +25%), Quote=103.5 (利潤從 5.0 回吐到 3.5，回吐 30% > 20%)
    evaluator = PullbackVsReversalEvaluator()
    res = evaluator.evaluate(
        side="LONG",
        df=df,
        entry_price=100.0,
        current_quote=103.5,
        peak_price=105.0,
        leverage=5.0,
    )
    assert res.state == MarketRegimeState.TRAILING_PROFIT_LOCK
    assert res.should_exit is True
    assert "TRAILING_PROFIT_LOCK" in res.reason


def test_short_healthy_pullback_and_bottom_reversal():
    """場景 6：空單對稱性測試（健康反抽續抱 vs 真見底暴拉平空）"""
    df = _build_base_candles(15, 100.0)
    evaluator = PullbackVsReversalEvaluator()

    # A. 空單健康反抽：收盤壓在中軌下，帶長上影線
    df.loc[df.index[-1], "open"] = 94.0
    df.loc[df.index[-1], "close"] = 94.5
    df.loc[df.index[-1], "high"] = 95.5
    df.loc[df.index[-1], "low"] = 93.8
    df.loc[df.index[-1], "kc_middle"] = 96.0
    df.loc[df.index[-1], "atr"] = 1.0

    res_hold = evaluator.evaluate(
        side="SHORT",
        df=df,
        entry_price=100.0,
        current_quote=94.5,
        peak_price=93.5,
        leverage=5.0,
    )
    assert res_hold.state == MarketRegimeState.HEALTHY_PULLBACK
    assert res_hold.should_exit is False

    # B. 空單真見底暴拉：巨大光頭光腳陽線穿過 KC 中軌
    df.loc[df.index[-1], "open"] = 94.0
    df.loc[df.index[-1], "close"] = 96.5  # 貫穿中軌 96.0
    df.loc[df.index[-1], "high"] = 96.6
    df.loc[df.index[-1], "low"] = 93.9
    df.loc[df.index[-1], "kc_middle"] = 96.0
    df.loc[df.index[-1], "atr"] = 1.0

    res_exit = evaluator.evaluate(
        side="SHORT",
        df=df,
        entry_price=100.0,
        current_quote=96.5,
        peak_price=93.5,
        leverage=5.0,
    )
    assert res_exit.state == MarketRegimeState.BOTTOM_REVERSAL
    assert res_exit.should_exit is True
