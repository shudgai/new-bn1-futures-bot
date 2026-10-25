"""
core/services/exits/pullback_vs_reversal_evaluator.py
=====================================================
妖幣合約專用：健康回踩 (HEALTHY_PULLBACK) vs 真見頂/見底反轉 (TOP/BOTTOM_REVERSAL)
多維度量化過濾器與狀態機判定引擎

核心規則實作：
1. 健康回踩（HEALTHY_PULLBACK）：
   - 收盤穩在 KC 中軌之上，KC 整體方向維持向上。
   - 陰線實體長度 <= 1.2 ATR（常規呼吸換手）。
   - 回測中軌或 MA3 時出現明顯承接下影線（下影線 >= 0.8 實體）或回調低點始終高於前波結構低點（Higher Low）。
   - 裁決：VETO_HOLD（一票否決平倉，強制續抱）。

2. 真見頂崩跌（TOP_REVERSAL）：
   - 異常恐慌大陰線：實體 > 1.8 ATR 且幾乎光頭光腳（下影線 <= 25% 實體）。
   - 關鍵軌道破位：大實體陰線直接收盤摜破 KC 中軌（防守線失守）。
   - 微觀結構破位（Swing Breakdown）：收盤價直接跌破前 N 根 K 棒最低價。
   - 連續加速下殺：連續 2~3 根全實體無下影線的大陰線，伴隨實體急劇擴大。
   - 裁決：解除續抱，果斷平多 / 啟動轉空。

3. 動態防禦配套（Trailing Profit Lock）：
   - 浮盈達標（ROE >= 10%）時啟動。
   - 自波段峰值回吐 >= 20% 或收盤跌破前 3 根低點，強制市價離場鎖利。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple, Union
import pandas as pd
from core.services.candle_data import closed_entry_candles

logger = logging.getLogger("PullbackReversalEvaluator")


class PositionSide(Enum):
    LONG = "LONG"
    SHORT = "SHORT"


class MarketRegimeState(Enum):
    HEALTHY_PULLBACK     = "HEALTHY_PULLBACK"      # 健康回踩/反抽（強制續抱，一票否決平倉）
    TOP_REVERSAL         = "TOP_REVERSAL"          # 多單真見頂崩跌（果斷平多 / 啟動轉空）
    BOTTOM_REVERSAL      = "BOTTOM_REVERSAL"       # 空單真見底暴拉（果斷平空 / 啟動轉多）
    TRAILING_PROFIT_LOCK = "TRAILING_PROFIT_LOCK"  # 動態移動停利鎖利離場（落袋為安）
    STRONG_EXPANSION     = "STRONG_EXPANSION"      # 軌外狂飆暴走段（外軌鎖定死抱）
    NORMAL_HOLD          = "NORMAL_HOLD"           # 正常波段運行持倉


@dataclass
class RegimeEvaluationResult:
    state: MarketRegimeState
    should_exit: bool
    confidence: float                              # 訊號置信度 (0.0 ~ 1.0)
    reason: str
    triggers: List[str] = field(default_factory=list)
    metrics: Dict[str, Any] = field(default_factory=dict)


class PullbackVsReversalEvaluator:
    """健康回踩與真頂底反轉量化判定引擎"""

    def __init__(
        self,
        swing_lookback: int = 5,                  # 擺動結構回溯期 (前 N 根低/高點)
        normal_pullback_max_atr: float = 1.20,    # 健康回踩最大允許實體 (<= 1.2 ATR)
        panic_reversal_min_atr: float = 1.80,     # 異常恐慌大陰/大陽線最小實體 (>= 1.8 ATR)
        wick_absorption_ratio: float = 0.80,      # 承接/壓制影線比例 (影線 >= 0.8 實體)
        trailing_lock_trigger_roe: float = 0.10,  # 浮盈達 10% ROE 啟動移動鎖利
        max_profit_drawdown_pct: float = 0.20,    # 自波段峰值利潤回吐 20% 強制離場
    ):
        self.swing_lookback = swing_lookback
        self.normal_pullback_max_atr = normal_pullback_max_atr
        self.panic_reversal_min_atr = panic_reversal_min_atr
        self.wick_absorption_ratio = wick_absorption_ratio
        self.trailing_lock_trigger_roe = trailing_lock_trigger_roe
        self.max_profit_drawdown_pct = max_profit_drawdown_pct

    def evaluate(
        self,
        side: Union[str, PositionSide],
        df: pd.DataFrame,
        entry_price: float,
        current_quote: float,
        peak_price: float,
        leverage: float = 5.0,
        entry_timestamp: Optional[float] = None,
    ) -> RegimeEvaluationResult:
        """
        對當前持倉進行全維度量化診斷（健康回踩 vs 真反轉）
        前提：一律以已收線 K 棒（Completed Bars）為計算標準，防盤中毛刺
        """
        side_value = side.value if isinstance(side, PositionSide) else str(side).upper()
        if side_value not in (PositionSide.LONG.value, PositionSide.SHORT.value):
            return RegimeEvaluationResult(
                state=MarketRegimeState.NORMAL_HOLD,
                should_exit=False,
                confidence=0.0,
                reason="INVALID_POSITION_SIDE_HOLD",
            )
        pos_side = PositionSide(side_value)

        closed = closed_entry_candles(df)
        minimum_bars = max(self.swing_lookback + 2, 10)
        if len(closed) < minimum_bars:
            return RegimeEvaluationResult(
                state=MarketRegimeState.NORMAL_HOLD,
                should_exit=False,
                confidence=0.5,
                reason="INSUFFICIENT_DATA_HOLD",
            )

        required = {"open", "high", "low", "close", "kc_middle", "atr"}
        if not required.issubset(closed.columns):
            return RegimeEvaluationResult(
                state=MarketRegimeState.NORMAL_HOLD,
                should_exit=False,
                confidence=0.0,
                reason="INVALID_MARKET_DATA_HOLD",
            )

        # 後續所有形態、加速與 swing 計算都只看同一份已收線資料。
        eval_bar = closed.iloc[-1]
        prev_bar = closed.iloc[-2]

        c_open  = float(eval_bar["open"])
        c_high  = float(eval_bar["high"])
        c_low   = float(eval_bar["low"])
        c_close = float(eval_bar["close"])

        kc_mid  = float(eval_bar.get("kc_middle", c_close))
        prev_kc_mid = float(prev_bar.get("kc_middle", kc_mid))
        kc_slope = kc_mid - prev_kc_mid

        atr = float(eval_bar["atr"])

        # K 棒實體與影線量化
        real_body = abs(c_close - c_open)
        upper_wick = c_high - max(c_open, c_close)
        lower_wick = min(c_open, c_close) - c_low

        # 擺動結構臨界位 (不含當前棒的前 N 根最低/最高價)
        lookback_slice = closed.iloc[-(self.swing_lookback + 1):-1]
        swing_low = float(lookback_slice["low"].min())
        swing_high = float(lookback_slice["high"].max())

        numeric_values = (
            c_open, c_high, c_low, c_close, kc_mid, prev_kc_mid, atr,
            swing_low, swing_high, float(entry_price), float(current_quote),
            float(peak_price), float(leverage),
        )
        if (not all(pd.notna(value) and value != float("inf") and value != float("-inf")
                    for value in numeric_values)
                or min(c_open, c_high, c_low, c_close, kc_mid, prev_kc_mid,
                       atr, swing_low, swing_high, float(entry_price),
                       float(current_quote), float(peak_price), float(leverage)) <= 0
                or c_low > min(c_open, c_close)
                or c_high < max(c_open, c_close)
                or c_low > c_high):
            return RegimeEvaluationResult(
                state=MarketRegimeState.NORMAL_HOLD,
                should_exit=False,
                confidence=0.0,
                reason="INVALID_MARKET_DATA_HOLD",
            )

        entry_stamp = entry_timestamp
        if entry_stamp is None and isinstance(df, pd.DataFrame):
            entry_stamp = df.attrs.get("entry_timestamp") or None
        # Position metadata is optional for offline backtests; when supplied,
        # ignore the entry candle itself to avoid immediate post-fill churn.
        if isinstance(df, pd.DataFrame) and "timestamp" in closed and entry_stamp is not None:
            try:
                bar_stamp = float(closed["timestamp"].iloc[-1])
                entry_stamp_value = float(entry_stamp)
                if abs(bar_stamp) < 1e11:
                    bar_stamp *= 1000.0
                if abs(entry_stamp_value) < 1e11:
                    entry_stamp_value *= 1000.0
                if bar_stamp <= entry_stamp_value:
                    return RegimeEvaluationResult(
                        state=MarketRegimeState.NORMAL_HOLD,
                        should_exit=False,
                        confidence=0.0,
                        reason="ENTRY_BAR_PROTECTION_HOLD",
                    )
            except (TypeError, ValueError, OverflowError):
                return RegimeEvaluationResult(
                    state=MarketRegimeState.NORMAL_HOLD,
                    should_exit=False,
                    confidence=0.0,
                    reason="INVALID_ENTRY_TIMESTAMP_HOLD",
                )

        # 帳戶收益與波段峰值回撤量化
        if pos_side == PositionSide.LONG:
            unrealized_roe = ((current_quote - entry_price) / entry_price) * leverage
            peak_roe = ((peak_price - entry_price) / entry_price) * leverage
            profit_drawdown = (peak_price - current_quote) / (peak_price - entry_price) if peak_price > entry_price else 0.0
        else:
            unrealized_roe = ((entry_price - current_quote) / entry_price) * leverage
            peak_roe = ((entry_price - peak_price) / entry_price) * leverage
            profit_drawdown = (current_quote - peak_price) / (entry_price - peak_price) if peak_price < entry_price else 0.0

        metrics = {
            "real_body_atr": real_body / atr,
            "close": c_close,
            "kc_middle": kc_mid,
            "kc_slope": kc_slope,
            "swing_low": swing_low,
            "swing_high": swing_high,
            "unrealized_roe": unrealized_roe,
            "peak_roe": peak_roe,
            "profit_drawdown": profit_drawdown,
        }

        # =============================================================
        # 模組 A：結構破位與真反轉評估 (TOP / BOTTOM REVERSAL)
        # =============================================================
        if pos_side == PositionSide.LONG:
            reversal_triggers = []
            is_bear_candle = c_close < c_open

            # 1. 異常恐慌大陰線 (> 1.8 ATR 且幾乎光頭光腳)
            if is_bear_candle and (real_body >= self.panic_reversal_min_atr * atr):
                if lower_wick <= 0.25 * real_body:
                    reversal_triggers.append("PANIC_MARUBOZU_DUMP")

            # 2. 關鍵軌道摜破：實體陰線收盤跌破 KC 中軌且中軌下拐/走平
            if is_bear_candle and c_close < kc_mid and (kc_slope <= 0.000005 or (kc_mid - c_close) >= 0.25 * atr):
                reversal_triggers.append("KC_MIDLINE_BREAKDOWN")

            # 3. 微觀擺動結構破位 (Swing Low Breakdown)
            if c_close < swing_low:
                reversal_triggers.append("SWING_LOW_BREAKDOWN")

            # 4. 連續加速下殺 (連續 2 根無下影實體放大陰線)
            if len(closed) >= 3:
                b1, b2 = closed.iloc[-2], closed.iloc[-1]
                if (b1["close"] < b1["open"]) and (b2["close"] < b2["open"]):
                    b1_body = abs(b1["close"] - b1["open"])
                    b2_body = abs(b2["close"] - b2["open"])
                    if b2_body >= b1_body and (b2["close"] - b2["low"]) <= 0.2 * b2_body:
                        reversal_triggers.append("ACCELERATING_CONSECUTIVE_DUMP")

            # 若觸發見頂反轉條件 -> 立即平多離場
            if reversal_triggers:
                return RegimeEvaluationResult(
                    state=MarketRegimeState.TOP_REVERSAL,
                    should_exit=True,
                    confidence=min(0.70 + len(reversal_triggers) * 0.12, 0.98),
                    reason=f"TOP_REVERSAL: 觸發結構破壞與真見頂信號 [{'+'.join(reversal_triggers)}]，果斷平多！",
                    triggers=reversal_triggers,
                    metrics=metrics,
                )

        else:
            reversal_triggers = []
            is_bull_candle = c_close > c_open

            # 1. 異常恐慌大陽線 (> 1.8 ATR 且幾乎光頭光腳)
            if is_bull_candle and (real_body >= self.panic_reversal_min_atr * atr):
                if upper_wick <= 0.25 * real_body:
                    reversal_triggers.append("PANIC_MARUBOZU_PUMP")

            # 2. 關鍵軌道摜破：實體陽線收盤突破 KC 中軌且中軌上行/走平
            if is_bull_candle and c_close > kc_mid and (kc_slope >= -0.000005 or (c_close - kc_mid) >= 0.25 * atr):
                reversal_triggers.append("KC_MIDLINE_BREAKOUT")

            # 3. 微觀擺動結構破位 (Swing High Breakout)
            if c_close > swing_high:
                reversal_triggers.append("SWING_HIGH_BREAKOUT")

            # 4. 連續加速軋空拉升 (連續 2 根無上影實體放大陽線)
            if len(closed) >= 3:
                b1, b2 = closed.iloc[-2], closed.iloc[-1]
                if (b1["close"] > b1["open"]) and (b2["close"] > b2["open"]):
                    b1_body = abs(b1["close"] - b1["open"])
                    b2_body = abs(b2["close"] - b2["open"])
                    if b2_body >= b1_body and (b2["high"] - b2["close"]) <= 0.2 * b2_body:
                        reversal_triggers.append("ACCELERATING_CONSECUTIVE_PUMP")

            # 若觸發見底反轉條件 -> 立即平空離場
            if reversal_triggers:
                return RegimeEvaluationResult(
                    state=MarketRegimeState.BOTTOM_REVERSAL,
                    should_exit=True,
                    confidence=min(0.70 + len(reversal_triggers) * 0.12, 0.98),
                    reason=f"BOTTOM_REVERSAL: 觸發結構破壞與真見底信號 [{'+'.join(reversal_triggers)}]，果斷平空！",
                    triggers=reversal_triggers,
                    metrics=metrics,
                )

        # =============================================================
        # 模組 B：動態移動停利防禦 (Trailing Profit Lock)
        # =============================================================
        if peak_roe >= self.trailing_lock_trigger_roe:
            recent_3bar_slice = closed.iloc[-4:-1]
            if pos_side == PositionSide.LONG:
                recent_3_low = float(recent_3bar_slice["low"].min())
                if profit_drawdown >= self.max_profit_drawdown_pct or c_close < recent_3_low:
                    return RegimeEvaluationResult(
                        state=MarketRegimeState.TRAILING_PROFIT_LOCK,
                        should_exit=True,
                        confidence=0.92,
                        reason=f"TRAILING_PROFIT_LOCK: 峰值浮盈 {peak_roe:.2%} 已回吐 {profit_drawdown:.1%} 或破前3棒低點，獲利落袋！",
                        triggers=["PROFIT_DRAWDOWN_BREACH" if profit_drawdown >= self.max_profit_drawdown_pct else "BREAK_RECENT_3BAR_LOW"],
                        metrics=metrics,
                    )
            else:
                recent_3_high = float(recent_3bar_slice["high"].max())
                if profit_drawdown >= self.max_profit_drawdown_pct or c_close > recent_3_high:
                    return RegimeEvaluationResult(
                        state=MarketRegimeState.TRAILING_PROFIT_LOCK,
                        should_exit=True,
                        confidence=0.92,
                        reason=f"TRAILING_PROFIT_LOCK: 峰值浮盈 {peak_roe:.2%} 已回吐 {profit_drawdown:.1%} 或突破前3棒高點，空單鎖利離場！",
                        triggers=["PROFIT_DRAWDOWN_BREACH" if profit_drawdown >= self.max_profit_drawdown_pct else "BREAK_RECENT_3BAR_HIGH"],
                        metrics=metrics,
                    )

        # =============================================================
        # 模組 C：健康回踩與健康反抽評估 (HEALTHY PULLBACK)
        # =============================================================
        if pos_side == PositionSide.LONG:
            above_mid = c_close >= kc_mid and kc_slope > 0
            body_controlled = c_close < c_open and real_body <= self.normal_pullback_max_atr * atr
            absorption = (lower_wick >= self.wick_absorption_ratio * real_body) or (c_low > swing_low)

            if above_mid and body_controlled and absorption:
                return RegimeEvaluationResult(
                    state=MarketRegimeState.HEALTHY_PULLBACK,
                    should_exit=False,
                    confidence=0.88,
                    reason="VETO_HOLD: 處於健康回踩換手區（收盤穩守中軌、實體受控、買盤承接），一票否決平倉，強制續抱！",
                    triggers=["HEALTHY_BODY", "MIDLINE_SUPPORTED"],
                    metrics=metrics,
                )
        else:
            below_mid = c_close <= kc_mid and kc_slope < 0
            body_controlled = c_close > c_open and real_body <= self.normal_pullback_max_atr * atr
            absorption = (upper_wick >= self.wick_absorption_ratio * real_body) or (c_high < swing_high)

            if below_mid and body_controlled and absorption:
                return RegimeEvaluationResult(
                    state=MarketRegimeState.HEALTHY_PULLBACK,
                    should_exit=False,
                    confidence=0.88,
                    reason="VETO_HOLD: 處於健康反抽消化區（受壓於中軌、實體受控、空方壓制完好），一票否決平倉，強制續抱！",
                    triggers=["HEALTHY_BODY", "MIDLINE_RESISTED"],
                    metrics=metrics,
                )

        # 預設維持常態波段持倉
        return RegimeEvaluationResult(
            state=MarketRegimeState.NORMAL_HOLD,
            should_exit=False,
            confidence=0.60,
            reason="NORMAL_TREND_HOLD: 無結構破壞信號，維持常規持倉防守",
            metrics=metrics,
        )
