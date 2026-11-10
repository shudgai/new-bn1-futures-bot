"""
真峰谷轉向開/平/翻倉策略（True Peak-Trough Flip Strategy）
========================================================
核心設計原則（2026-11-10 授權）：
  1. 震盪總開關（Chop Gate）：ADX < 15 或 KC 帶寬 < 0.012 時凍結一切交易。
  2. 峰頂轉折（True Peak）捕捉：在 KC 中軌上方，出現第一根確定性反轉 K 棒或
     動能拐點（MACD Hist 由正轉負 / RSI > 60 且向下）時授權開空。
  3. 谷底轉折（True Trough）捕捉：在 KC 中軌下方，出現第一根確定性反轉 K 棒或
     動能拐點（MACD Hist 由負轉正 / RSI < 40 且向上）時授權開多。
  4. 持倉呼吸防洗（Mid-Trend Hold）：已持倉時，若 KC 中軌方向未轉向，
     中間所有普通震盪 K 棒一律 VETO_HOLD，不提早平倉。
  5. 翻倉授權（Flip）：達到真峰谷後同步授權平舊倉並反向秒級反手。
  6. 此策略僅提供訊號判斷（signal dict），實際下單仍委託 execution_manager
     既有的 open_position / close_position 流程執行。

使用方法：
  from core.strategies.true_peak_trough_strategy import TruePeakTroughStrategy, PositionState
  strategy = TruePeakTroughStrategy(symbol="1000PEPE/USDT")
  result = strategy.evaluate(df, current_position=PositionState.NONE, live_price=0.012345)
  # result["action"] ∈ {"OPEN_LONG","OPEN_SHORT","FLIP_TO_LONG","FLIP_TO_SHORT","HOLD","FREEZE"}
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# ─────────────────────── 參數常數 ───────────────────────
# 震盪過濾門檻
CHOP_ADX_MIN = 15.0          # ADX 低於此值視為震盪盤整，凍結開倉
CHOP_BW_MIN = 0.012          # KC 帶寬（bandwidth）低於此值視為壓縮震盪

# 峰頂 / 谷底 RSI 判定門檻
RSI_PEAK_THRESHOLD = 60.0    # RSI 高位轉折門檻（> 此值且向下 → 峰頂訊號）
RSI_TROUGH_THRESHOLD = 40.0  # RSI 低位轉折門檻（< 此值且向上 → 谷底訊號）

# 吞噬 K 棒確認：反向實體需達前根實體的倍數
ENGULF_BODY_RATIO = 0.6

# MACD Hist 轉折確認：由正轉負 or 由負轉正
MACD_HIST_SIGN_CHANGE = True

# KC 帶寬計算欄位名稱（與 compute_technical_indicators 一致）
KC_BW_COL = "bandwidth"      # 若管道沒算，可自行用 kc_width / kc_middle 推算


# ─────────────────────── 資料結構 ───────────────────────
class PositionState(str, Enum):
    NONE = "NONE"       # 空手
    LONG = "LONG"       # 持有多單
    SHORT = "SHORT"     # 持有空單


@dataclass
class SignalResult:
    """策略評估輸出"""
    action: str               # OPEN_LONG | OPEN_SHORT | FLIP_TO_LONG | FLIP_TO_SHORT | HOLD | FREEZE
    reason: str               # 人可讀的決策原因
    rsi: Optional[float] = None
    macd_hist: Optional[float] = None
    adx: Optional[float] = None
    bandwidth: Optional[float] = None
    kc_slope: Optional[float] = None
    details: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "action": self.action,
            "reason": self.reason,
            "rsi": self.rsi,
            "macd_hist": self.macd_hist,
            "adx": self.adx,
            "bandwidth": self.bandwidth,
            "kc_slope": self.kc_slope,
            **self.details,
        }


# ─────────────────────── 主策略類別 ───────────────────────
class TruePeakTroughStrategy:
    """
    真峰谷轉折 + 防洗持倉策略

    架構流程：
      evaluate()
        └→ _chop_gate()           # 1. 震盪總開關
        └→ _extract_metrics()     # 2. 擷取最新 2 根 K 棒指標
        └→ _check_true_peak()     # 3. 峰頂轉折判定（開空/反手開空）
        └→ _check_true_trough()   # 4. 谷底轉折判定（開多/反手開多）
        └→ _mid_trend_hold()      # 5. 中途持倉呼吸防洗
        └→ HOLD                   # 6. 無訊號靜觀
    """

    def __init__(self, symbol: str = ""):
        self.symbol = symbol

    # ──────────────────── 公開主入口 ────────────────────
    def evaluate(
        self,
        df: pd.DataFrame,
        current_position: PositionState = PositionState.NONE,
        live_price: Optional[float] = None,
    ) -> dict:
        """
        評估當前 K 線數據，回傳交易訊號字典。

        :param df: 已由 compute_technical_indicators 豐富過的 K 線 DataFrame
                   必要欄位：open, high, low, close, kc_middle, kc_upper, kc_lower,
                            rsi, macd_hist, adx, bandwidth（或 kc_width）
        :param current_position: 目前持倉狀態（PositionState 枚舉）
        :param live_price: 最新盤中報價（選填，若無則用 df 最後一根 close）
        :return: SignalResult.to_dict()
        """
        if len(df) < 5:
            return SignalResult("HOLD", "INSUFFICIENT_DATA").to_dict()

        # 1. 擷取指標
        metrics = self._extract_metrics(df, live_price)
        if metrics is None:
            return SignalResult("HOLD", "INDICATOR_NAN").to_dict()

        adx = metrics["adx"]
        bandwidth = metrics["bandwidth"]
        rsi = metrics["rsi"]
        macd_hist = metrics["macd_hist"]
        prev_macd_hist = metrics["prev_macd_hist"]
        prev_rsi = metrics["prev_rsi"]
        kc_mid = metrics["kc_mid"]
        kc_slope = metrics["kc_slope"]
        curr_close = metrics["curr_close"]
        curr_open = metrics["curr_open"]
        curr_high = metrics["curr_high"]
        curr_low = metrics["curr_low"]
        prev_close = metrics["prev_close"]
        prev_open = metrics["prev_open"]
        prev_high = metrics["prev_high"]
        prev_low = metrics["prev_low"]
        curr_body = abs(curr_close - curr_open)
        prev_body = abs(prev_close - prev_open)

        # 2. 震盪總開關
        freeze_result = self._chop_gate(adx, bandwidth)
        if freeze_result:
            return freeze_result.to_dict()

        # ─── 3. 無持倉：尋找新開倉機會 ───
        if current_position == PositionState.NONE:
            # 嘗試峰頂開空
            peak_signal = self._check_true_peak(
                curr_close, curr_open, curr_high, curr_body,
                prev_close, prev_open, prev_high, prev_body,
                kc_mid, kc_slope, rsi, prev_rsi, macd_hist, prev_macd_hist,
                metrics,
            )
            if peak_signal:
                return SignalResult(
                    "OPEN_SHORT",
                    peak_signal,
                    rsi=rsi, macd_hist=macd_hist, adx=adx,
                    bandwidth=bandwidth, kc_slope=kc_slope,
                ).to_dict()

            # 嘗試谷底開多
            trough_signal = self._check_true_trough(
                curr_close, curr_open, curr_low, curr_body,
                prev_close, prev_open, prev_low, prev_body,
                kc_mid, kc_slope, rsi, prev_rsi, macd_hist, prev_macd_hist,
                metrics,
            )
            if trough_signal:
                return SignalResult(
                    "OPEN_LONG",
                    trough_signal,
                    rsi=rsi, macd_hist=macd_hist, adx=adx,
                    bandwidth=bandwidth, kc_slope=kc_slope,
                ).to_dict()

        # ─── 4. 持有多單：等真峰頂平倉 / 反手開空 ───
        elif current_position == PositionState.LONG:
            peak_signal = self._check_true_peak(
                curr_close, curr_open, curr_high, curr_body,
                prev_close, prev_open, prev_high, prev_body,
                kc_mid, kc_slope, rsi, prev_rsi, macd_hist, prev_macd_hist,
                metrics,
                allow_kc_down=True,
            )
            if peak_signal:
                # 順勢檢查：KC 中軌須向下才允許反手開空
                if kc_slope < 0:
                    return SignalResult(
                        "FLIP_TO_SHORT",
                        f"FLIP_LONG_TO_SHORT|{peak_signal}",
                        rsi=rsi, macd_hist=macd_hist, adx=adx,
                        bandwidth=bandwidth, kc_slope=kc_slope,
                    ).to_dict()
                else:
                    return SignalResult(
                        "HOLD",
                        f"VETO_FLIP_SHORT_ON_BULLISH_KC|peak_detected_but_kc_up (slope={kc_slope:.6f})",
                        rsi=rsi, kc_slope=kc_slope,
                    ).to_dict()

            # 多單持倉防洗：中途不平倉
            return self._mid_trend_hold("LONG", kc_slope, rsi, kc_mid, curr_close, metrics).to_dict()

        # ─── 5. 持有空單：等真谷底平倉 / 反手開多 ───
        elif current_position == PositionState.SHORT:
            trough_signal = self._check_true_trough(
                curr_close, curr_open, curr_low, curr_body,
                prev_close, prev_open, prev_low, prev_body,
                kc_mid, kc_slope, rsi, prev_rsi, macd_hist, prev_macd_hist,
                metrics,
                allow_kc_up=True,
            )
            if trough_signal:
                # 順勢檢查：KC 中軌須向上才允許反手開多
                if kc_slope > 0:
                    return SignalResult(
                        "FLIP_TO_LONG",
                        f"FLIP_SHORT_TO_LONG|{trough_signal}",
                        rsi=rsi, macd_hist=macd_hist, adx=adx,
                        bandwidth=bandwidth, kc_slope=kc_slope,
                    ).to_dict()
                else:
                    return SignalResult(
                        "HOLD",
                        f"VETO_FLIP_LONG_ON_BEARISH_KC|trough_detected_but_kc_down (slope={kc_slope:.6f})",
                        rsi=rsi, kc_slope=kc_slope,
                    ).to_dict()

            # 空單持倉防洗：中途不平倉
            return self._mid_trend_hold("SHORT", kc_slope, rsi, kc_mid, curr_close, metrics).to_dict()

        return SignalResult("HOLD", "NO_SIGNAL").to_dict()

    # ──────────────────── 私有方法 ────────────────────

    def _extract_metrics(self, df: pd.DataFrame, live_price: Optional[float]) -> Optional[dict]:
        """擷取最新兩根 K 棒的所有指標，任一關鍵欄位為 NaN 則回傳 None。"""
        required_cols = {"open", "high", "low", "close", "kc_middle", "rsi", "macd_hist", "adx"}
        missing = required_cols - set(df.columns)
        if missing:
            logger.warning("[TruePeakTrough] 缺少欄位: %s", missing)
            return None

        curr = df.iloc[-1]
        prev = df.iloc[-2]

        def _safe(val) -> Optional[float]:
            try:
                v = float(val)
                return v if np.isfinite(v) else None
            except (TypeError, ValueError):
                return None

        kc_mid_curr = _safe(curr.get("kc_middle"))
        kc_mid_prev = _safe(prev.get("kc_middle"))
        if kc_mid_curr is None or kc_mid_prev is None:
            return None

        # 帶寬：優先用管道算好的 bandwidth；若無則用 kc_width / kc_middle
        bw = _safe(curr.get(KC_BW_COL))
        if bw is None and "kc_width" in df.columns:
            kw = _safe(curr.get("kc_width"))
            bw = (kw / kc_mid_curr) if (kw is not None and kc_mid_curr != 0) else None

        adx = _safe(curr.get("adx"))
        rsi = _safe(curr.get("rsi"))
        prev_rsi = _safe(prev.get("rsi"))
        macd_hist = _safe(curr.get("macd_hist"))
        prev_macd_hist = _safe(prev.get("macd_hist"))

        curr_close = _safe(live_price) or _safe(curr.get("close"))
        if curr_close is None:
            return None

        return {
            "adx": adx,
            "bandwidth": bw,
            "rsi": rsi,
            "prev_rsi": prev_rsi,
            "macd_hist": macd_hist,
            "prev_macd_hist": prev_macd_hist,
            "kc_mid": kc_mid_curr,
            "kc_slope": kc_mid_curr - kc_mid_prev,
            "curr_close": curr_close,
            "curr_open": _safe(curr.get("open")) or curr_close,
            "curr_high": _safe(curr.get("high")) or curr_close,
            "curr_low": _safe(curr.get("low")) or curr_close,
            "prev_close": _safe(prev.get("close")) or kc_mid_prev,
            "prev_open": _safe(prev.get("open")) or kc_mid_prev,
            "prev_high": _safe(prev.get("high")) or kc_mid_prev,
            "prev_low": _safe(prev.get("low")) or kc_mid_prev,
        }

    def _chop_gate(self, adx: Optional[float], bandwidth: Optional[float]) -> Optional[SignalResult]:
        """
        震盪總開關：ADX 過低或帶寬過窄時凍結所有訊號。
        回傳 SignalResult("FREEZE", ...) 或 None（代表通過）。
        """
        if adx is not None and adx < CHOP_ADX_MIN:
            return SignalResult(
                "FREEZE",
                f"CHOP_GATE_ADX_LOW: adx={adx:.1f} < {CHOP_ADX_MIN}",
                adx=adx, bandwidth=bandwidth,
            )
        if bandwidth is not None and bandwidth < CHOP_BW_MIN:
            return SignalResult(
                "FREEZE",
                f"CHOP_GATE_BW_LOW: bandwidth={bandwidth:.4f} < {CHOP_BW_MIN}",
                adx=adx, bandwidth=bandwidth,
            )
        return None

    def _check_true_peak(
        self,
        c_c: float, c_o: float, c_h: float, c_body: float,
        p_c: float, p_o: float, p_h: float, p_body: float,
        kc_mid: float, kc_slope: float,
        rsi: Optional[float], prev_rsi: Optional[float],
        macd_hist: Optional[float], prev_macd_hist: Optional[float],
        metrics: dict,
        allow_kc_down: bool = False,
    ) -> Optional[str]:
        """
        峰頂轉折判定：在 KC 中軌上方出現第一根確定性頂部信號。
        回傳原因字串（非空 → 達成），或 None（未達成）。

        觸發條件（至少符合其一）：
          A. 吞噬反轉：前根陽線，當前收黑陰且收盤破前根最低價，實體 >= ENGULF_BODY_RATIO * prev_body
          B. MACD Hist 高位由正轉負（本根 <= 0 且前根 > 0）
          C. RSI > RSI_PEAK_THRESHOLD 且當前 RSI < 前一根 RSI（向下拐頭）

        位置條件（必須通過）：
          - 當前收盤 >= KC 中軌（位於中軌上方）
          - KC 中軌斜率向上或走平（若為翻倉反手，可允許 KC 中軌已下拐）
        """
        # 位置條件：必須在中軌上方
        if c_c < kc_mid:
            return None

        # KC 中軌斜率方向：開空時只有在上升段頂部才尋找開空；若為多單翻空(allow_kc_down=True)則中軌下行也允許
        if not allow_kc_down and kc_slope < -1e-9:
            return None

        reasons = []

        # A. 吞噬反轉（前根為陽線，當根為陰線且貫穿前根低點）
        prev_is_bull = p_c > p_o
        curr_is_bear = c_c < c_o
        if (
            prev_is_bull
            and curr_is_bear
            and c_c < p_c                           # 當前收盤低於前根收盤
            and p_body > 1e-9
            and c_body >= ENGULF_BODY_RATIO * p_body  # 吞噬實體夠大
        ):
            reasons.append("BEARISH_ENGULF")

        # B. MACD Hist 高位由正轉負
        if (
            macd_hist is not None and prev_macd_hist is not None
            and prev_macd_hist > 0 and macd_hist <= 0
        ):
            reasons.append(f"MACD_HIST_NEG_CROSS (prev={prev_macd_hist:.6f}→curr={macd_hist:.6f})")

        # C. RSI 高位向下拐頭
        if (
            rsi is not None and prev_rsi is not None
            and rsi > RSI_PEAK_THRESHOLD
            and rsi < prev_rsi
        ):
            reasons.append(f"RSI_HIGH_TURN_DOWN (rsi={rsi:.1f} < prev={prev_rsi:.1f})")

        if not reasons:
            return None

        return "TRUE_PEAK|" + "|".join(reasons)

    def _check_true_trough(
        self,
        c_c: float, c_o: float, c_l: float, c_body: float,
        p_c: float, p_o: float, p_l: float, p_body: float,
        kc_mid: float, kc_slope: float,
        rsi: Optional[float], prev_rsi: Optional[float],
        macd_hist: Optional[float], prev_macd_hist: Optional[float],
        metrics: dict,
        allow_kc_up: bool = False,
    ) -> Optional[str]:
        """
        谷底轉折判定：在 KC 中軌下方出現第一根確定性底部信號。
        回傳原因字串（非空 → 達成），或 None（未達成）。

        觸發條件（至少符合其一）：
          A. 吞噬反轉：前根陰線，當前收紅陽且收盤突破前根最高價，實體 >= ENGULF_BODY_RATIO * prev_body
          B. MACD Hist 低位由負轉正（本根 >= 0 且前根 < 0）
          C. RSI < RSI_TROUGH_THRESHOLD 且當前 RSI > 前一根 RSI（向上拐頭）

        位置條件（必須通過）：
          - 當前收盤 <= KC 中軌（位於中軌下方）
          - KC 中軌斜率向下或走平（若為翻倉反手，可允許 KC 中軌已上拐）
        """
        # 位置條件：必須在中軌下方
        if c_c > kc_mid:
            return None

        # KC 中軌斜率方向：開多時只有在下跌段谷底才尋找開多；若為空單翻多(allow_kc_up=True)則中軌上行也允許
        if not allow_kc_up and kc_slope > 1e-9:
            return None

        reasons = []

        # A. 吞噬反轉（前根為陰線，當根為陽線且貫穿前根高點）
        prev_is_bear = p_c < p_o
        curr_is_bull = c_c > c_o
        if (
            prev_is_bear
            and curr_is_bull
            and c_c > p_c                           # 當前收盤高於前根收盤
            and p_body > 1e-9
            and c_body >= ENGULF_BODY_RATIO * p_body  # 吞噬實體夠大
        ):
            reasons.append("BULLISH_ENGULF")

        # B. MACD Hist 低位由負轉正
        if (
            macd_hist is not None and prev_macd_hist is not None
            and prev_macd_hist < 0 and macd_hist >= 0
        ):
            reasons.append(f"MACD_HIST_POS_CROSS (prev={prev_macd_hist:.6f}→curr={macd_hist:.6f})")

        # C. RSI 低位向上拐頭
        if (
            rsi is not None and prev_rsi is not None
            and rsi < RSI_TROUGH_THRESHOLD
            and rsi > prev_rsi
        ):
            reasons.append(f"RSI_LOW_TURN_UP (rsi={rsi:.1f} > prev={prev_rsi:.1f})")

        if not reasons:
            return None

        return "TRUE_TROUGH|" + "|".join(reasons)

    def _mid_trend_hold(
        self,
        side: str,
        kc_slope: float,
        rsi: Optional[float],
        kc_mid: float,
        curr_close: float,
        metrics: dict,
    ) -> SignalResult:
        """
        中途持倉防洗邏輯：KC 中軌仍維持同向則一律 VETO_HOLD，不提早平倉。
        持倉方向與 KC 中軌已明確背離時，才允許 HOLD（後續由 exit gate 接管）。
        """
        if side == "LONG":
            # 多單：只要 KC 中軌仍向上或走平，且收盤在中軌以上，繼續抱
            if kc_slope >= 0 and curr_close >= kc_mid:
                return SignalResult(
                    "HOLD",
                    f"VETO_HOLD_LONG|KC_MID_BULLISH|close={curr_close:.6f}≥kc_mid={kc_mid:.6f}|slope={kc_slope:.6f}",
                    kc_slope=kc_slope, rsi=rsi,
                )
            # KC 中軌開始向下或跌破中軌 → 由 exit gate 接手判斷
            return SignalResult(
                "HOLD",
                f"LONG_MID_TREND_WEAKENING|close={curr_close:.6f}<kc_mid={kc_mid:.6f}|slope={kc_slope:.6f}",
                kc_slope=kc_slope, rsi=rsi,
            )
        else:  # SHORT
            # 空單：只要 KC 中軌仍向下或走平，且收盤在中軌以下，繼續抱
            if kc_slope <= 0 and curr_close <= kc_mid:
                return SignalResult(
                    "HOLD",
                    f"VETO_HOLD_SHORT|KC_MID_BEARISH|close={curr_close:.6f}≤kc_mid={kc_mid:.6f}|slope={kc_slope:.6f}",
                    kc_slope=kc_slope, rsi=rsi,
                )
            # KC 中軌開始向上或突破中軌 → 由 exit gate 接手判斷
            return SignalResult(
                "HOLD",
                f"SHORT_MID_TREND_WEAKENING|close={curr_close:.6f}>kc_mid={kc_mid:.6f}|slope={kc_slope:.6f}",
                kc_slope=kc_slope, rsi=rsi,
            )


# ─────────────────────── 便利函式 ───────────────────────
def evaluate_true_peak_trough_strategy(
    df: pd.DataFrame,
    current_position: PositionState = PositionState.NONE,
    live_price: Optional[float] = None,
    symbol: str = "",
) -> dict:
    """
    與 evaluate_true_peak_trough 等價之標準別名介面。
    """
    if isinstance(current_position, str):
        current_position = PositionState(current_position.upper())
    strategy = TruePeakTroughStrategy(symbol=symbol)
    return strategy.evaluate(df, current_position=current_position, live_price=live_price)


def evaluate_true_peak_trough(
    df: pd.DataFrame,
    current_position: PositionState = PositionState.NONE,
    live_price: Optional[float] = None,
    symbol: str = "",
) -> dict:
    """
    無狀態便利函式，直接呼叫 TruePeakTroughStrategy.evaluate()。
    適合在 execution_manager / unified_pipeline 中快速整合。

    :param df: 已豐富指標的 K 線 DataFrame
    :param current_position: 當前持倉狀態（PositionState 或字串 "NONE"/"LONG"/"SHORT"）
    :param live_price: 盤中最新報價（選填）
    :param symbol: 交易對名稱（供 log 追蹤）
    :return: 訊號字典（含 action, reason, rsi, adx, kc_slope 等欄位）
    """
    return evaluate_true_peak_trough_strategy(
        df, current_position=current_position, live_price=live_price, symbol=symbol
    )


def check_exit_conditions(
    df: pd.DataFrame,
    current_price: float,
    current_position: PositionState,
    entry_price: Optional[float] = None,
    is_bar_closed: bool = True,
    hard_sl_pct: float = 0.02,
    waterfall_atr_mult: float = 3.0,
    symbol: str = "",
) -> dict:
    """
    平倉雙軌過濾機制：
    1. 異常 K 線 / 大瀑布 ➔ 即時觸發強制平倉 (EMERGENCY_CLOSE / STOP_LOSS)
    2. 一般 K 線 ➔ 必須等 K 線收盤 (Completed Bar) 確定轉折才平倉

    :param df: K 線數據 DataFrame (若含 atr 欄位直接使用，否則以 14 週期計算)
    :param current_price: 當前最新報價 (Quote)
    :param current_position: 當前持倉狀態 (LONG / SHORT / NONE)
    :param entry_price: 開倉進場成本價
    :param is_bar_closed: 當前 K 線是否已收盤
    :param hard_sl_pct: 硬止損百分比 (預設 0.02 即 2%)
    :param waterfall_atr_mult: 大瀑布 ATR 倍數門檻 (預設 3.0 倍)
    :param symbol: 交易對名稱
    :return: 決策字典 (action, reason 等)
    """
    if isinstance(current_position, str):
        current_position = PositionState(current_position.upper())

    if current_position == PositionState.NONE:
        return {"action": "HOLD", "reason": "目前無持倉，無須平倉"}

    # 取得 ATR
    atr_val = None
    if "atr" in df.columns and len(df["atr"]) > 0:
        atr_val = df["atr"].iloc[-1]
    if atr_val is None or not np.isfinite(atr_val) or atr_val <= 0:
        # 簡易計算 14 週期 ATR
        high = df["high"]
        low = df["low"]
        close = df["close"]
        tr1 = high - low
        tr2 = (high - close.shift(1)).abs()
        tr3 = (low - close.shift(1)).abs()
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        atr_series = tr.rolling(window=14).mean()
        atr_val = float(atr_series.iloc[-1]) if pd.notna(atr_series.iloc[-1]) else (current_price * 0.01)

    # =========================================================
    # 1. 例外緊急狀況：大瀑布 / 異常 K 線 (盤中即時監控，無須等收盤)
    # =========================================================
    if entry_price is not None and entry_price > 0:
        # 情況 A：價格波動超過 waterfall_atr_mult 倍 ATR (大瀑布 / 暴跌暴漲異常 K 線)
        price_change = abs(current_price - entry_price)
        if price_change > (atr_val * waterfall_atr_mult):
            return {
                "action": "EMERGENCY_CLOSE",
                "reason": f"防範大瀑布/異常極端 K 線，價格偏差={price_change:.4f} > {waterfall_atr_mult}*ATR({atr_val:.4f})，觸發緊急風控平倉！",
                "atr": atr_val,
                "entry_price": entry_price,
                "current_price": current_price,
            }

        # 情況 B：硬止損 (Hard Stop Loss，如固定 2% 離場)
        if current_position == PositionState.LONG and current_price <= entry_price * (1.0 - hard_sl_pct):
            return {
                "action": "STOP_LOSS",
                "reason": f"觸及硬止損線 {hard_sl_pct*100:.1f}%，現價={current_price:.4f} <= 止損價={entry_price * (1.0 - hard_sl_pct):.4f}，立即平多",
                "entry_price": entry_price,
                "current_price": current_price,
            }
        elif current_position == PositionState.SHORT and current_price >= entry_price * (1.0 + hard_sl_pct):
            return {
                "action": "STOP_LOSS",
                "reason": f"觸及硬止損線 {hard_sl_pct*100:.1f}%，現價={current_price:.4f} >= 止損價={entry_price * (1.0 + hard_sl_pct):.4f}，立即平空",
                "entry_price": entry_price,
                "current_price": current_price,
            }

    # =========================================================
    # 2. 一般情況：必須等待 K 線完全走完 (收盤) 才評估真峰谷轉向
    # =========================================================
    if is_bar_closed:
        return evaluate_true_peak_trough_strategy(
            df, current_position=current_position, live_price=current_price, symbol=symbol
        )

    # K 線還在走且無異常大瀑布時，一律繼續持倉防洗
    return {
        "action": "HOLD",
        "reason": "K線尚未走完，忽略盤中微幅反向波動 (VETO_HOLD_UNCLOSED_BAR)",
        "current_price": current_price,
    }


