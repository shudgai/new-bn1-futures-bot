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
        curr_ma5 = metrics["curr_ma5"]
        curr_bb_upper = metrics["curr_bb_upper"]
        curr_bb_lower = metrics["curr_bb_lower"]
        curr_body = abs(curr_close - curr_open)
        prev_body = abs(prev_close - prev_open)

        # 2. 震盪總開關
        freeze_result = self._chop_gate(adx, bandwidth)
        if freeze_result:
            return freeze_result.to_dict()

        # ─── 3. 無持倉：尋找新開倉機會 ───
        if current_position == PositionState.NONE:
            # 1. 首選：首波真峰谷轉折開倉
            peak_signal = self._check_true_peak(
                curr_close, curr_open, curr_high, curr_body,
                prev_close, prev_open, prev_high, prev_body,
                kc_mid, kc_slope, rsi, prev_rsi, macd_hist, prev_macd_hist,
                metrics,
            )
            if peak_signal:
                # 若有布林下軌防護，避免貼軌地板放空 (c0 > bb_lower)
                if curr_bb_lower is None or curr_close > curr_bb_lower:
                    return SignalResult(
                        "OPEN_SHORT",
                        f"【第一順位】捕捉到真峰頂，第一時間開空|{peak_signal}",
                        rsi=rsi, macd_hist=macd_hist, adx=adx,
                        bandwidth=bandwidth, kc_slope=kc_slope,
                    ).to_dict()

            trough_signal = self._check_true_trough(
                curr_close, curr_open, curr_low, curr_body,
                prev_close, prev_open, prev_low, prev_body,
                kc_mid, kc_slope, rsi, prev_rsi, macd_hist, prev_macd_hist,
                metrics,
            )
            if trough_signal:
                # 若有布林上軌防護，避免貼軌天花板追多 (c0 < bb_upper)
                if curr_bb_upper is None or curr_close < curr_bb_upper:
                    return SignalResult(
                        "OPEN_LONG",
                        f"【第一順位】捕捉到真谷底，第一時間開多|{trough_signal}",
                        rsi=rsi, macd_hist=macd_hist, adx=adx,
                        bandwidth=bandwidth, kc_slope=kc_slope,
                    ).to_dict()

            # 2. 次選：錯過峰谷後的「補救開倉 (Secondary Entry)」
            # 🔴 補救開空：中軌向下 (主趨勢偏空) + 價格反彈回踩 MA5 且收陰線受阻
            is_trend_down = kc_slope < 0
            is_pullback_short = (curr_close < curr_open) and (curr_ma5 is not None and curr_close <= curr_ma5)
            bb_lower_ok = (curr_bb_lower is None or curr_close > curr_bb_lower)

            if is_trend_down and is_pullback_short and bb_lower_ok:
                return SignalResult(
                    "OPEN_SHORT",
                    f"【補救開倉】錯過峰頂，於拉回回踩 MA5 受阻時補救開空 (close={curr_close:.4f}<=ma5={curr_ma5:.4f})",
                    rsi=rsi, macd_hist=macd_hist, adx=adx,
                    bandwidth=bandwidth, kc_slope=kc_slope,
                ).to_dict()

            # 🟢 補救開多：中軌向上 (主趨勢偏多) + 價格回踩 MA5 且收陽線受支撐
            is_trend_up = kc_slope > 0
            is_pullback_long = (curr_close > curr_open) and (curr_ma5 is not None and curr_close >= curr_ma5)
            bb_upper_ok = (curr_bb_upper is None or curr_close < curr_bb_upper)

            if is_trend_up and is_pullback_long and bb_upper_ok:
                return SignalResult(
                    "OPEN_LONG",
                    f"【補救開倉】錯過谷底，於拉回回踩 MA5 止跌時補救開多 (close={curr_close:.4f}>=ma5={curr_ma5:.4f})",
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
        curr_ma5 = _safe(curr.get("ma5"))
        curr_bb_upper = _safe(curr.get("bb_upper"))
        curr_bb_lower = _safe(curr.get("bb_lower"))

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
            "curr_ma5": curr_ma5,
            "curr_bb_upper": curr_bb_upper,
            "curr_bb_lower": curr_bb_lower,
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
        震盪總開關：針對妖幣/小幣進行動態微調。
        回傳 SignalResult("FREEZE", ...) 或 None（代表通過）。
        """
        is_meme_coin = self.symbol in ["龙虾/USDT", "1000PEPE/USDT", "1000RATS/USDT"] or any(
            k in self.symbol.upper() for k in ("LOBSTER", "PEPE", "RATS", "CAP")
        )
        bw_threshold = 0.004 if is_meme_coin else 0.015
        adx_threshold = 20.0

        curr_adx = adx if adx is not None else 25.0
        curr_bw = bandwidth if bandwidth is not None else 0.05
        is_strong_trend = curr_adx >= 35.0

        if is_meme_coin:
            is_choppy = (curr_bw < bw_threshold) and not is_strong_trend
        else:
            is_choppy = (curr_adx < adx_threshold) or (curr_bw < bw_threshold)

        if is_choppy:
            return SignalResult(
                "FREEZE",
                f"震盪盤保護中 (ADX={curr_adx:.1f}, BW={curr_bw:.4f}) - 凍結交易",
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


def evaluate_dual_strategy(
    df: pd.DataFrame, current_position: PositionState = PositionState.NONE
) -> dict:
    """多空雙向 - 真峰谷起點開倉與波段鎖定策略 (Dual True Peak/Trough Strategy)

    核心鐵律：
      1. 震盪盤防護 (ADX < 20 或 帶幅過窄) ➔ 凍結所有交易。
      2. 波段發動第一時間 ➔ 在谷底起漲點開多，在峰頂起跌點開空。
      3. 強勢 K 線保護鎖 ➔ 大綠 K 暴漲中絕不平多；大紅 K 下砸中絕不平空！
      4. 峰谷三點確理解鎖 ➔ 只有出現實體反轉跌破/突破前高低 (三點確立) 或 中軌破位，才允許平倉/翻倉。
      5. 波段走向保護 ➔ 中軌方向未改變前，中間回踩/反彈一律無視 (HOLD)。
    """
    if df is None or len(df) < 3:
        return {"action": "HOLD", "position": current_position.value if isinstance(current_position, PositionState) else str(current_position), "reason": "數據不足，維持現狀"}

    if isinstance(current_position, str):
        try:
            current_position = PositionState(current_position.upper())
        except ValueError:
            current_position = PositionState.NONE

    # ---------------------------------------------------------
    # Step A: 數據與指標提取 (從 completed K 線獲取)
    # ---------------------------------------------------------
    c0 = float(df["close"].iloc[-1])
    o0 = float(df["open"].iloc[-1])
    h0 = float(df["high"].iloc[-1])
    l0 = float(df["low"].iloc[-1])

    c1 = float(df["close"].iloc[-2])
    o1 = float(df["open"].iloc[-2])
    h1 = float(df["high"].iloc[-2])
    l1 = float(df["low"].iloc[-2])

    kc_col = "kc_mid" if "kc_mid" in df.columns else ("kc_middle" if "kc_middle" in df.columns else "close")
    kc_mid = float(df[kc_col].iloc[-1])
    kc_mid_prev = float(df[kc_col].iloc[-2])

    bb_upper = float(df["bb_upper"].iloc[-1]) if "bb_upper" in df.columns else (kc_mid * 1.02)
    bb_lower = float(df["bb_lower"].iloc[-1]) if "bb_lower" in df.columns else (kc_mid * 0.98)

    curr_adx = float(df["adx"].iloc[-1]) if "adx" in df.columns else 25.0
    curr_bw = float(df["bandwidth"].iloc[-1]) if "bandwidth" in df.columns else 0.01

    rsi0 = float(df["rsi"].iloc[-1]) if "rsi" in df.columns else 50.0
    rsi1 = float(df["rsi"].iloc[-2]) if "rsi" in df.columns and len(df) >= 2 else rsi0

    # K 線形態與中軌趨勢判定
    is_green_bar = c0 > o0  # 實體陽線 (大綠 K)
    is_red_bar = c0 < o0  # 實體陰線 (大紅 K)
    is_kc_mid_up = kc_mid > kc_mid_prev  # 中軌向上
    is_kc_mid_down = kc_mid < kc_mid_prev  # 中軌向下

    # 針對 MEME 幣 / 妖幣 (如 龍蝦/PEPE) 的彈性震盪帶幅門檻 (0.4%)
    bw_threshold = 0.004

    # ---------------------------------------------------------
    # 關卡 1：震盪盤防禦 (帶幅極窄且 ADX 無波段動態時，凍結交易)
    # ---------------------------------------------------------
    is_choppy = (curr_bw < bw_threshold) and (curr_adx < 20)
    if is_choppy:
        return {
            "action": "HOLD",
            "position": current_position.value,
            "reason": f"震盪盤保護中 (ADX={curr_adx:.1f}, BW={curr_bw:.4f}) - 凍結交易",
        }

    # ---------------------------------------------------------
    # 關卡 2：真峰谷 (True Peak / Trough) 轉向條件判定
    # ---------------------------------------------------------
    # 🟢 真谷底起漲點 (低位區 + 實體陽線突破前高 / RSI 低位上彎)
    # 低位區判定：當前或前根在 KC 中軌下方
    is_trough_area = (c0 <= kc_mid) or (l1 <= kc_mid)
    is_true_trough = is_trough_area and (
        (is_green_bar and c0 > h1) or (rsi1 < 40 and rsi0 > rsi1)
    )

    # 🔴 真峰頂起跌點 (高位區 + 實體陰線跌破前低 / RSI 高位下彎)
    # 高位區判定：當前或前根衝高在 KC 中軌上方
    is_peak_area = (c0 >= kc_mid) or (h1 >= kc_mid)
    is_true_peak = is_peak_area and (
        (is_red_bar and c0 < l1) or (rsi1 > 60 and rsi0 < rsi1)
    )

    # ---------------------------------------------------------
    # 關卡 3：多空對稱狀態機 (State Machine)
    # ---------------------------------------------------------

    # 🟢 情況 1：持有多單中 (LONG)
    if current_position == PositionState.LONG:
        # 1. 陽線保護鎖：大綠 K 暴漲中，100% 強行 HOLD，絕不平多！
        if is_green_bar:
            return {
                "action": "HOLD",
                "reason": "🚀 [多單] 強勢大綠 K 暴漲中，鎖定 HOLD 讓利潤奔跑！",
            }

        # 2. 只有在「峰頂三點確立 (陰線吞噬前低)」或「中軌轉向跌破」時才平多/翻空
        if is_true_peak or ((not is_kc_mid_up) and c0 < kc_mid):
            return {
                "action": "FLIP_TO_SHORT",
                "reason": "🎯 [多單] 到達真峰頂三點確立或跌破中軌，平多並瞬間翻空",
            }

        # 3. 中軌持續向上，中間小拉回/小紅 K 一律無視，繼續持多！
        return {
            "action": "HOLD",
            "reason": "[多單] KC中軌持續向上，波段未變，無視短線拉回繼續持多",
        }

    # 🔴 情況 2：持有空單中 (SHORT)
    elif current_position == PositionState.SHORT:
        # 1. 陰線保護鎖：大紅 K 急跌中，100% 強行 HOLD，絕不平空！
        if is_red_bar:
            return {
                "action": "HOLD",
                "reason": "📉 [空單] 強勢大紅 K 下砸中，鎖定 HOLD 讓空頭利潤奔跑！",
            }

        # 2. 只有在「谷底三點確立 (陽線突破前高)」或「中軌轉向突破」時才平空/翻多
        if is_true_trough or ((not is_kc_mid_down) and c0 > kc_mid):
            return {
                "action": "FLIP_TO_LONG",
                "reason": "🎯 [空單] 到達真谷底三點確立或突破中軌，平空並瞬間翻多",
            }

        # 3. 中軌持續向下，中間小反彈/小綠 K 一律無視，繼續持空！
        return {
            "action": "HOLD",
            "reason": "[空單] KC中軌持續向下，波段未變，無視短線反彈繼續持空",
        }

    # ⚪ 情況 3：完全無持倉 (NONE) —— 尋求波段起點進場或強勢補救買回
    elif current_position == PositionState.NONE:
        # 首選 1：波段發動第一時間 (谷底起漲開多)
        if is_true_trough and c0 < bb_upper:
            return {
                "action": "OPEN_LONG",
                "reason": "🚀 [無持倉] 第一時間捕捉到谷底波段起漲點，開多！",
            }

        # 首選 2：錯過谷底起點後的「強勢大綠 K 陽線補救開多 (Secondary Entry / 買回)」
        # 只要：實體大綠 K (c0 > o0) 且 收盤站上 MA5 (c0 >= ma5) 且 KC 中軌向上，且價格未貼頂天花板 (c0 < bb_upper)
        ma5_curr = float(df["ma5"].iloc[-1]) if "ma5" in df.columns else kc_mid
        is_strong_green_breakout = is_green_bar and (c0 >= ma5_curr) and is_kc_mid_up
        if is_strong_green_breakout and (c0 < bb_upper):
            return {
                "action": "OPEN_LONG",
                "reason": "🚀 [無持倉] 錯過谷底，出現強勢大綠 K 陽線突破 MA5 且中軌向上，發動補救開多買回！",
            }

        # 🛑 【防追空鐵律】：嚴禁在 KC 中軌下方超賣區追空！只允許在高位衝頂轉折時開空
        can_open_short_zone = (c0 > kc_mid) and (c0 > bb_lower)
        if is_true_peak and can_open_short_zone:
            return {
                "action": "OPEN_SHORT",
                "reason": "📉 [無持倉] 第一時間捕捉到峰頂波段起跌點，開空！",
            }
        elif is_true_peak and not can_open_short_zone:
            return {
                "action": "HOLD",
                "reason": "🛑 [防追空保護] 價格已處於中軌下方低位/超賣區，絕對禁止在谷底開空！",
            }

        # 次選 1：錯過首波後的順勢補救開倉 (回踩 MA5 止跌/受阻)
        is_ma5_retest_long = is_green_bar and (c0 >= ma5_curr)
        is_ma5_retest_short = is_red_bar and (c0 <= ma5_curr)

        if (c0 > kc_mid) and is_ma5_retest_long and (c0 < bb_upper):
            return {
                "action": "OPEN_LONG",
                "reason": "➕ [無持倉] 錯過谷底，拉回回踩 MA5 止跌，補救開多",
            }
        # 補救開空同樣嚴格要求身處中軌上方高位受阻 (c0 > kc_mid)
        if (c0 > kc_mid) and is_ma5_retest_short and (c0 > bb_lower) and is_kc_mid_down:
            return {
                "action": "OPEN_SHORT",
                "reason": "➕ [無持倉] 錯過峰頂，反彈回踩 MA5 受阻，補救開空",
            }

    return {"action": "HOLD", "reason": "等待波段確切發動"}


def evaluate_high_precision_strategy(
    df: pd.DataFrame, current_position: PositionState = PositionState.NONE
) -> dict:
    """高精準度多空波段與峰谷轉折策略引擎 (四大實戰強化模組整合)

    1. 震盪盤保護：帶幅收斂 + ADX 低迷 ➔ 凍結交易
    2. 強勢 K 線保護：綠 K 暴漲中絕不平多，紅 K 急跌中絕不平空
    3. 真峰谷三點吞噬：只在低位強陽突破開多/平空，高位強陰跌破開空/平多
    4. K 線走完才發出指令：嚴格確保使用已收盤 K 線數據
    """
    if df is None or len(df) < 2:
        return {"action": "HOLD", "reason": "數據不足"}

    # 確保只使用已收盤 K 線 (Bar Completion)
    if "is_closed" in df.columns:
        closed_df = df[df["is_closed"] == True]
        if len(closed_df) >= 2:
            df = closed_df

    if isinstance(current_position, str):
        try:
            current_position = PositionState(current_position.upper())
        except ValueError:
            current_position = PositionState.NONE

    # 提取已收盤 K 線數據 (index -1 為當前剛收盤 K 線, -2 為上一根)
    c0 = float(df["close"].iloc[-1])
    o0 = float(df["open"].iloc[-1])
    h0 = float(df["high"].iloc[-1])
    l0 = float(df["low"].iloc[-1])

    c1 = float(df["close"].iloc[-2])
    o1 = float(df["open"].iloc[-2])
    h1 = float(df["high"].iloc[-2])
    l1 = float(df["low"].iloc[-2])

    kc_col = "kc_mid" if "kc_mid" in df.columns else ("kc_middle" if "kc_middle" in df.columns else "close")
    kc_mid = float(df[kc_col].iloc[-1])
    kc_mid_prev = float(df[kc_col].iloc[-2])

    bb_upper = float(df["bb_upper"].iloc[-1]) if "bb_upper" in df.columns else (
        float(df["kc_upper"].iloc[-1]) if "kc_upper" in df.columns else (kc_mid * 1.02)
    )
    bb_lower = float(df["bb_lower"].iloc[-1]) if "bb_lower" in df.columns else (
        float(df["kc_lower"].iloc[-1]) if "kc_lower" in df.columns else (kc_mid * 0.98)
    )

    adx = float(df["adx"].iloc[-1]) if "adx" in df.columns else 25.0
    bw = float(df["bandwidth"].iloc[-1]) if "bandwidth" in df.columns else (
        float(df["kc_width"].iloc[-1]) if "kc_width" in df.columns else 0.01
    )
    rsi0 = float(df["rsi"].iloc[-1]) if "rsi" in df.columns else 50.0
    rsi1 = float(df["rsi"].iloc[-2]) if "rsi" in df.columns and len(df) >= 2 else rsi0

    ma5_val = float(df["ma5"].iloc[-1]) if "ma5" in df.columns else kc_mid

    # K 線形態與趨勢方向判定
    is_green_bar = c0 > o0  # 當前為實體陽線 (大綠 K)
    is_red_bar = c0 < o0  # 當前為實體陰線 (大紅 K)
    is_kc_mid_up = kc_mid > kc_mid_prev  # 中軌持續向上
    is_kc_mid_down = kc_mid < kc_mid_prev  # 中軌持續向下

    # =========================================================
    # 關卡 1：震盪盤與防追高殺低門檻
    # =========================================================
    # 針對高波動小幣/妖幣放寬至 0.4%
    if (bw < 0.004) and (adx < 20):
        return {"action": "HOLD", "reason": "盤整區間帶幅收斂 - 凍結交易"}

    # =========================================================
    # 關卡 2：精準真峰谷反轉型態判定 (三點吞噬)
    # =========================================================
    # 🟢 真谷底起漲：價格在中軌下方/低位 + 實體大綠 K 突破前高 (或 RSI 低位上彎)
    is_true_trough = (c0 <= kc_mid) and (
        (is_green_bar and c0 > h1) or (rsi1 < 40 and rsi0 > rsi1)
    )

    # 🔴 真峰頂起跌：價格在中軌上方/高位 + 實體大紅 K 跌破前低 (或 RSI 高位下彎)
    is_true_peak = (c0 >= kc_mid) and (
        (is_red_bar and c0 < l1) or (rsi1 > 60 and rsi0 < rsi1)
    )

    # =========================================================
    # 關卡 3：持倉狀態機精準決策
    # =========================================================

    # 🟢 1. 目前持有多單 (LONG)
    if current_position == PositionState.LONG:
        # 【保護鎖】大綠 K 暴漲中 ➔ 強行抱緊，絕不平多！
        if is_green_bar:
            return {
                "action": "HOLD",
                "reason": "🚀 強勢大綠 K 暴漲中，鎖定 HOLD 吃滿主升段！",
            }

        # 【平多/翻空】頂部三點吞噬確立 或 實體跌破中軌
        if is_true_peak or ((not is_kc_mid_up) and c0 < kc_mid):
            return {
                "action": "FLIP_TO_SHORT",
                "reason": "🎯 頂部出現陰線吞噬 (峰頂確立)，高位精準平多並翻空",
            }

        return {
            "action": "HOLD",
            "reason": "中軌向上，波段未變，無視微幅拉回繼續持多",
        }

    # 🔴 2. 目前持有空單 (SHORT)
    elif current_position == PositionState.SHORT:
        # 【保護鎖】大紅 K 下砸中 ➔ 強行抱緊，絕不平空！
        if is_red_bar:
            return {
                "action": "HOLD",
                "reason": "📉 強勢大紅 K 急跌中，鎖定 HOLD 吃滿主跌段！",
            }

        # 【平空/翻多】底部三點吞噬確立 或 實體突破中軌
        if is_true_trough or ((not is_kc_mid_down) and c0 > kc_mid):
            return {
                "action": "FLIP_TO_LONG",
                "reason": "🎯 底部出現陽線吞噬 (谷底確立)，低位精準平空並翻多",
            }

        return {
            "action": "HOLD",
            "reason": "中軌向下，波段未變，無視微幅反彈繼續持空",
        }

    # ⚪ 3. 無持倉 (NONE) ➔ 尋求精準起點進場
    elif current_position == PositionState.NONE:
        # 首選：波段起點 (谷底第一時間開多 / 峰頂第一時間開空)
        if is_true_trough and c0 < bb_upper:
            return {
                "action": "OPEN_LONG",
                "reason": "🚀 谷底三點吞噬確立，第一時間開多！",
            }
        if is_true_peak and c0 > bb_lower:
            return {
                "action": "OPEN_SHORT",
                "reason": "📉 峰頂三點吞噬確立，第一時間開空！",
            }

        # 次選：錯過谷底後的順勢補救開多 (回踩 MA5 止跌)
        if (
            (c0 > kc_mid)
            and is_green_bar
            and (c0 >= ma5_val)
            and (c0 < bb_upper)
        ):
            return {
                "action": "OPEN_LONG",
                "reason": "➕ 錯過谷底，強勢大綠 K 回踩 MA5 止跌，補救開多！",
            }

    return {"action": "HOLD", "reason": "無明確高勝率訊號"}


def evaluate_precise_short_entry(
    df: pd.DataFrame, current_position: PositionState = PositionState.NONE
) -> dict:
    """專治「空在最低點」的開空邏輯：

    1. 峰頂第一時間開空 (高位 + 陰線吞噬/RSI下彎)
    2. 高位回踩 MA5 補救開空
    3. 鐵律：價格已處於中軌下方超賣區 (或觸及下軌)，100% 攔截禁止開空！
    """
    if df is None or len(df) < 2:
        return {"action": "HOLD", "reason": "數據不足"}

    if isinstance(current_position, str):
        try:
            current_position = PositionState(current_position.upper())
        except ValueError:
            current_position = PositionState.NONE

    c0 = float(df["close"].iloc[-1])
    o0 = float(df["open"].iloc[-1])
    h0 = float(df["high"].iloc[-1])
    l0 = float(df["low"].iloc[-1])
    l1 = float(df["low"].iloc[-2])

    kc_col = "kc_mid" if "kc_mid" in df.columns else ("kc_middle" if "kc_middle" in df.columns else "close")
    kc_mid = float(df[kc_col].iloc[-1])
    bb_upper = float(df["bb_upper"].iloc[-1]) if "bb_upper" in df.columns else (kc_mid * 1.02)
    bb_lower = float(df["bb_lower"].iloc[-1]) if "bb_lower" in df.columns else (kc_mid * 0.98)

    rsi0 = float(df["rsi"].iloc[-1]) if "rsi" in df.columns else 50.0
    rsi1 = float(df["rsi"].iloc[-2]) if "rsi" in df.columns and len(df) >= 2 else rsi0

    is_red_bar = c0 < o0  # 當前收陰線 (紅 K)

    # 🔴 只有在無持倉 (NONE) 時才尋求開空
    if current_position == PositionState.NONE:

        # 🛑 【防追空鐵律】：價格已經跌至 KC 中軌下方 或 觸及布林下軌 ➔ 嚴禁開空！
        if c0 <= kc_mid or c0 <= bb_lower:
            return {
                "action": "HOLD",
                "reason": "🛑 [防追空保護] 價格已處於低位/超賣區，絕對禁止在谷底開空！",
            }

        # 🎯 【開空模式 A：峰頂第一時間精準開空】
        # 位置：價格處於 KC 中軌上方高位 (高於中軌或接近上軌)
        # 條件：當前為實體陰線 且 跌破前一根低點 (c0 < l1)，或 RSI 自 >60 高位彎頭向下
        is_high_zone = c0 > kc_mid
        is_peak_engulf = is_red_bar and (c0 < l1)
        is_rsi_peak_down = (rsi1 > 60) and (rsi0 < rsi1)

        if is_high_zone and (is_peak_engulf or is_rsi_peak_down):
            return {
                "action": "OPEN_SHORT",
                "reason": "📉 [高位精準開空] 捕捉到峰頂第一時間轉折，於最高區發動開空！",
            }

        # 🎯 【開空模式 B：高位反彈回踩 MA5 受阻補救開空】
        # 中軌向下波段中，價格反彈觸及 MA5 均線但收盤收陰線受阻
        ma5_curr = float(df["ma5"].iloc[-1]) if "ma5" in df.columns else kc_mid
        is_ma5_retest_short = (
            is_red_bar
            and (c0 <= ma5_curr)
            and (c0 > kc_mid)
            and (c0 < bb_upper)
        )
        if is_ma5_retest_short:
            return {
                "action": "OPEN_SHORT",
                "reason": "➕ [高位補救開空] 錯過最高點，於反彈回踩 MA5 受阻時補救開空",
            }

    return {"action": "HOLD", "reason": "未達精準開空條件"}


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
        # Disaster exits require an abnormal adverse candle, not simply a
        # large loss from entry. A normal pullback across a price threshold is
        # not a hard-stop signal.
        live = df.iloc[-1]
        opening = float(live.get("open", current_price))
        close = float(live.get("close", current_price))
        middle = float(live.get("kc_middle", live.get("kc_mid", 0.0)) or 0.0)
        adverse_body = (
            max(opening - current_price, opening - close)
            if current_position == PositionState.LONG
            else max(current_price - opening, close - opening)
        )
        crossed_middle = middle > 0 and (
            min(current_price, close) < middle
            if current_position == PositionState.LONG
            else max(current_price, close) > middle
        )
        if adverse_body >= max(1.5, waterfall_atr_mult) * atr_val and crossed_middle:
            return {
                "action": "EMERGENCY_CLOSE",
                "reason": f"大瀑布/異常反向 K 實體={adverse_body:.4f}，貫穿 KC 中軌且達 {max(1.5, waterfall_atr_mult):.1f} ATR，緊急平倉",
                "atr": atr_val,
                "entry_price": entry_price,
                "current_price": current_price,
            }

        # Fixed percentage/entry-price hard stops are intentionally disabled.

    # =========================================================
    # 2. 一般情況：必須等待 K 線完全走完 (收盤) 才評估真峰谷轉向
    # =========================================================
    if is_bar_closed:
        # 優先執行三層主動鎖盈平倉機制 (evaluate_exit_logic)
        if entry_price is not None and entry_price > 0:
            exit_eval = evaluate_exit_logic(
                df=df, current_position=current_position, entry_price=entry_price
            )
            if exit_eval.get("action") in ("CLOSE_LONG", "CLOSE_SHORT"):
                return exit_eval

        return evaluate_true_peak_trough_strategy(
            df, current_position=current_position, live_price=current_price, symbol=symbol
        )

    # K 線還在走且無異常大瀑布時，一律繼續持倉防洗
    return {
        "action": "HOLD",
        "reason": "K線尚未走完，忽略盤中微幅反向波動 (VETO_HOLD_UNCLOSED_BAR)",
        "current_price": current_price,
    }


# =============================================================================
# 策略主引擎 (清理後的簡潔結構 / 輕量純粹版本 evaluate_strategy)
# =============================================================================
def evaluate_strategy(
    df: pd.DataFrame, current_position: PositionState = PositionState.NONE, symbol: str = ""
) -> dict:
    """
    真峰谷轉折與翻倉策略 (清理後之輕量高階純粹函式)：
      1. 關卡 1：震盪盤防禦 (針對妖幣/小幣進行動態微調)
      2. 捕捉真峰谷第一時間 ➔ 發動開倉或瞬間翻倉 (Flip Position)
      3. 一般 K 線 ➔ 必須走完 (收盤) 才評估平倉；大瀑布 / 暴跌 ➔ 盤中即時緊急風控
      4. 錯過第一時間 ➔ 回踩 MA5 止跌/受阻時進行補救開倉
    """
    if isinstance(current_position, str):
        current_position = PositionState(current_position.upper())

    if len(df) < 5:
        return {"action": "HOLD", "reason": "數據長度不足"}

    # --- Step A: 取得指標數據 (相容 kc_mid / kc_middle, bandwidth / kc_width) ---
    c0 = float(df["close"].iloc[-1])
    o0 = float(df["open"].iloc[-1])
    h0 = float(df["high"].iloc[-1])
    l0 = float(df["low"].iloc[-1])

    c1 = float(df["close"].iloc[-2])
    l1 = float(df["low"].iloc[-2])
    h1 = float(df["high"].iloc[-2])

    kc_mid = float(df["kc_mid"].iloc[-1]) if "kc_mid" in df.columns else float(df["kc_middle"].iloc[-1])

    if "bb_upper" in df.columns and "bb_lower" in df.columns:
        bb_upper = float(df["bb_upper"].iloc[-1])
        bb_lower = float(df["bb_lower"].iloc[-1])
    elif "kc_upper" in df.columns and "kc_lower" in df.columns:
        bb_upper = float(df["kc_upper"].iloc[-1])
        bb_lower = float(df["kc_lower"].iloc[-1])
    else:
        bb_upper = float(df["bb_upper"].iloc[-1]) if "bb_upper" in df.columns else (kc_mid * 1.02)
        bb_lower = float(df["bb_lower"].iloc[-1]) if "bb_lower" in df.columns else (kc_mid * 0.98)

    curr_adx = float(df["adx"].iloc[-1]) if "adx" in df.columns else 25.0
    if "bandwidth" in df.columns:
        curr_bw = float(df["bandwidth"].iloc[-1])
    elif "kc_width" in df.columns:
        curr_bw = float(df["kc_width"].iloc[-1]) / (kc_mid if kc_mid != 0 else 1.0)
    else:
        curr_bw = (bb_upper - bb_lower) / (kc_mid if kc_mid != 0 else 1.0)

    rsi0 = float(df["rsi"].iloc[-1]) if "rsi" in df.columns else 50.0
    rsi1 = float(df["rsi"].iloc[-2]) if "rsi" in df.columns else 50.0

    ma5_val = float(df["ma5"].iloc[-1]) if "ma5" in df.columns else c0

    # --- Step B: 關卡 1 - 震盪盤防禦 (針對妖幣/小幣進行動態微調) ---
    is_meme_coin = symbol in ["龙虾/USDT", "1000PEPE/USDT", "1000RATS/USDT"] or any(
        k in symbol.upper() for k in ("LOBSTER", "PEPE", "RATS", "CAP")
    )
    bw_threshold = 0.004 if is_meme_coin else 0.015  # 妖幣放寬至 0.4%
    adx_threshold = 20.0

    # 判定條件：若 ADX 非常強 (ADX >= 35)，代表爆發力強，允許帶寬適度放寬
    is_strong_trend = curr_adx >= 35.0

    # 震盪凍結判定
    if is_meme_coin:
        # 妖幣邏輯：帶寬極低 ( < 0.004 ) 且 無強大動能 ( ADX < 20 ) 時才凍結
        is_choppy = (curr_bw < bw_threshold) and not is_strong_trend
    else:
        # 一般幣種邏輯
        is_choppy = (curr_adx < adx_threshold) or (curr_bw < bw_threshold)

    if is_choppy:
        return {
            "action": "HOLD",
            "reason": f"震盪盤保護中 (ADX={curr_adx:.1f}, BW={curr_bw:.4f}) - 凍結交易",
        }

    # --- Step C: 關卡 2 - 真峰谷訊號判定與 MA5 斜率角度 (斷頭 / 真谷判定) ---
    # MA5 角度計算：arctan( (ma5_curr - ma5_prev) / close_prev * 1000 )
    ma5_prev = float(df["ma5"].iloc[-2]) if ("ma5" in df.columns and len(df) >= 2) else ma5_val
    ma5_diff = ma5_val - ma5_prev
    ma5_angle = float(np.degrees(np.arctan((ma5_diff / (c1 if c1 > 0 else 1.0)) * 1000.0)))

    # 斷頭式急跌 (<= -75度) 與 真谷式急拉 (>= +75度)
    v_shape_down = ma5_angle <= -75.0
    v_shape_up = ma5_angle >= 75.0

    # 🔴 真峰頂：高位區 + 陰線實體跌破前低 (或 RSI 下彎 或 斷頭式急砸)
    is_true_peak = ((c0 >= kc_mid) and (
        (c0 < o0 and c0 < l1) or (rsi1 > 60 and rsi0 < rsi1)
    )) or v_shape_down

    # 🟢 真谷底：低位區 + 陽線實體突破前高 (或 RSI 上彎 或 真谷式急拉)
    is_true_trough = ((c0 <= kc_mid) and (
        (c0 > o0 and c0 > h1) or (rsi1 < 40 and rsi0 > rsi1)
    )) or v_shape_up

    # --- Step D: 關卡 3 - 狀態機決策 (清乾淨的 if/elif 分流) ---

    # 情況 1：持有多單中
    if current_position == PositionState.LONG:
        if v_shape_down:
            return {
                "action": "FLIP_TO_SHORT",
                "reason": f"⚡ 遇斷頭式急砸 (MA5角度={ma5_angle:.1f}° <= -75°)，秒級平多並瞬間開空！",
            }
        if is_true_peak:
            return {
                "action": "FLIP_TO_SHORT",
                "reason": "到達真峰頂，平多並瞬間開空",
            }
        return {"action": "HOLD", "reason": "未達真峰頂，無視盤中回檔繼續持多"}

    # 情況 2：持有空單中
    elif current_position == PositionState.SHORT:
        if v_shape_up:
            return {
                "action": "FLIP_TO_LONG",
                "reason": f"⚡ 遇真谷式急拉 (MA5角度={ma5_angle:.1f}° >= +75°)，秒級平空並瞬間開多！",
            }
        if is_true_trough:
            return {
                "action": "FLIP_TO_LONG",
                "reason": "到達真谷底，平空並瞬間開多",
            }
        return {"action": "HOLD", "reason": "未達真谷底，無視盤中反彈繼續持空"}

    # 情況 3：完全無持倉 (尋求首波或補救進場)
    elif current_position == PositionState.NONE:
        # 特殊極速觸發：斷頭式直接開空 / 真谷式直接開多
        if v_shape_down:
            return {
                "action": "OPEN_SHORT",
                "reason": f"⚡ 遇斷頭式急砸 (MA5角度={ma5_angle:.1f}° <= -75°)，直接市價開空！",
            }
        if v_shape_up:
            return {
                "action": "OPEN_LONG",
                "reason": f"⚡ 遇真谷式急拉 (MA5角度={ma5_angle:.1f}° >= +75°)，直接市價開多！",
            }

        # 首選：真峰谷第一時間
        if is_true_peak and c0 > bb_lower:
            return {"action": "OPEN_SHORT", "reason": "第一時間真峰頂開空"}
        if is_true_trough and c0 < bb_upper:
            return {"action": "OPEN_LONG", "reason": "第一時間真谷底開多"}

        # 次選：補救開倉 (回踩 MA5 止跌/受阻)
        is_ma5_retest_long = (c0 > o0) and (c0 >= ma5_val)
        is_ma5_retest_short = (c0 < o0) and (c0 <= ma5_val)

        if (c0 > kc_mid) and is_ma5_retest_long and (c0 < bb_upper):
            return {
                "action": "OPEN_LONG",
                "reason": "錯過谷底，回踩 MA5 止跌補救開多",
            }
        if (c0 < kc_mid) and is_ma5_retest_short and (c0 > bb_lower):
            return {
                "action": "OPEN_SHORT",
                "reason": "錯過峰頂，反彈 MA5 受阻補救開空",
            }

# =============================================================================
# 三層主動鎖盈平倉機制 (evaluate_exit_logic)
# 專治平倉滯後：1. 真峰谷第一時間轉折平倉 (主動鎖盈)
#               2. 追蹤止盈 (Trailing Stop / 獲利回吐平倉)
#               3. 失守 MA5 均線平倉防禦
# =============================================================================
def evaluate_exit_logic(
    df: pd.DataFrame, current_position: PositionState, entry_price: float
) -> dict:
    """
    專治平倉滯後的三層離場/平倉機制：
    1. 真峰谷第一時間轉折平倉 (主動鎖盈)
    2. 追蹤止盈 (Trailing Stop / 獲利回吐平倉)
    3. 跌破 MA5 均線及時平倉
    """
    if isinstance(current_position, str):
        current_position = PositionState(current_position.upper())

    if current_position == PositionState.NONE:
        return {"action": "HOLD", "reason": "無持倉"}

    if len(df) < 5:
        return {"action": "HOLD", "reason": "數據長度不足"}

    c0 = float(df["close"].iloc[-1])
    o0 = float(df["open"].iloc[-1])
    h0 = float(df["high"].iloc[-1])
    l0 = float(df["low"].iloc[-1])
    l1 = float(df["low"].iloc[-2])
    h1 = float(df["high"].iloc[-2])

    kc_mid = float(df["kc_mid"].iloc[-1]) if "kc_mid" in df.columns else float(df["kc_middle"].iloc[-1])
    rsi0 = float(df["rsi"].iloc[-1]) if "rsi" in df.columns else 50.0
    rsi1 = float(df["rsi"].iloc[-2]) if "rsi" in df.columns else 50.0
    ma5_val = float(df["ma5"].iloc[-1]) if "ma5" in df.columns else c0

    # 進場後近期最高/最低點 (取最近 10 根)
    high_series = df["high"].iloc[-10:] if len(df) >= 10 else df["high"]
    low_series = df["low"].iloc[-10:] if len(df) >= 10 else df["low"]
    max_price_since_entry = float(high_series.max())
    min_price_since_entry = float(low_series.min())

    # =========================================================
    # 🟢 持有「多單」時的平倉/鎖盈條件 (針對 1000PEPE / 龙虾 修正)
    # =========================================================
    if current_position == PositionState.LONG:
        # 【條件 A：真峰頂第一時間主動平多】(防止跌到底部才平)
        # 價格處於 KC 中軌上方高位 + 出現陰線實體吞噬前低 (或 RSI 自高位 >65 下彎)
        is_high_zone = c0 >= kc_mid
        is_bearish_engulf = (c0 < o0) and (c0 < l1)
        is_rsi_peak_down = (rsi1 > 65.0) and (rsi0 < rsi1)

        if is_high_zone and (is_bearish_engulf or is_rsi_peak_down):
            return {
                "action": "CLOSE_LONG",
                "reason": "觸發真峰頂轉向信號，高位第一時間獲利平多",
            }

        # 【條件 B：動態追蹤止盈 / 獲利回吐保護】(防止利潤全吐光)
        # 若曾衝高獲利 > 0.6%，但從最高點拉回超過 0.3% 時，立刻平倉鎖定利潤
        if entry_price > 0:
            max_profit_pct = (max_price_since_entry - entry_price) / entry_price
            current_drawback = (
                (max_price_since_entry - c0) / max_price_since_entry
                if max_price_since_entry > 0
                else 0.0
            )

            if max_profit_pct > 0.006 and current_drawback > 0.003:
                return {
                    "action": "CLOSE_LONG",
                    "reason": "觸發追蹤止盈，利潤自最高點回吐 0.3% 強制平多鎖盈",
                }

        # 【條件 C：失守 MA5 均線平多】(第二道退路，絕不可等跌穿 KC 下軌)
        if c0 < ma5_val and c0 < o0:
            return {
                "action": "CLOSE_LONG",
                "reason": "收盤跌破 MA5 均線，及時平多保本",
            }

    # =========================================================
    # 🔴 持有「空單」時的平倉/鎖盈條件
    # =========================================================
    elif current_position == PositionState.SHORT:
        is_low_zone = c0 <= kc_mid
        is_bullish_engulf = (c0 > o0) and (c0 > h1)
        is_rsi_trough_up = (rsi1 < 35.0) and (rsi0 > rsi1)

        if is_low_zone and (is_bullish_engulf or is_rsi_trough_up):
            return {
                "action": "CLOSE_SHORT",
                "reason": "觸發真谷底轉向信號，低位第一時間平空/買回",
            }

        # 空單追蹤止盈：跌幅獲利超過 0.6%，自最低點反彈超過 0.3%
        if entry_price > 0:
            max_profit_pct = (entry_price - min_price_since_entry) / entry_price
            current_rebound = (
                (c0 - min_price_since_entry) / min_price_since_entry
                if min_price_since_entry > 0
                else 0.0
            )
            if max_profit_pct > 0.006 and current_rebound > 0.003:
                return {
                    "action": "CLOSE_SHORT",
                    "reason": "觸發空單追蹤止盈，自最低點反彈 0.3% 強制平空鎖盈",
                }

        # 失守 MA5：收盤突破 MA5 且收陽線
        if c0 > ma5_val and c0 > o0:
            return {
                "action": "CLOSE_SHORT",
                "reason": "收盤突破 MA5 均線，及時平空保本",
            }

    return {"action": "HOLD", "reason": "未達平倉條件，繼續持倉"}


# =============================================================================
# 波段方向鎖定平倉邏輯 (evaluate_exit_with_trend_lock)
# 核心原則：只要 KC 中軌生命線方向沒變，中間微幅拉回小陰小陽一律不平倉，抱緊讓利潤奔跑！
# =============================================================================
def evaluate_exit_with_trend_lock(
    df: pd.DataFrame, current_position: PositionState, entry_price: float
) -> dict:
    """
    專注於「波段走向沒變就不平倉」的平倉/翻倉邏輯：
    1. 以 KC 中軌 (MA20) 方向作為波段生命線
    2. 中間微幅 K 線反向、未跌破中軌前，一律 HOLD
    3. 只有波段趨勢真正反轉 (實體破中軌或大瀑布極端風控) 才離場
    """
    if isinstance(current_position, str):
        current_position = PositionState(current_position.upper())

    if current_position == PositionState.NONE:
        return {"action": "HOLD", "reason": "無持倉"}

    if len(df) < 5:
        return {"action": "HOLD", "reason": "數據長度不足"}

    c0 = float(df["close"].iloc[-1])
    o0 = float(df["open"].iloc[-1])

    # 1. 計算波段生命線 (KC 中軌 MA20) 與其斜率/方向
    kc_mid = float(df["kc_mid"].iloc[-1]) if "kc_mid" in df.columns else float(df["kc_middle"].iloc[-1])
    kc_mid_prev = float(df["kc_mid"].iloc[-2]) if "kc_mid" in df.columns else float(df["kc_middle"].iloc[-2])

    # 波段方向判定 (中軌當前 > 前一根 = 波段向上)
    is_main_trend_up = kc_mid > kc_mid_prev
    is_main_trend_down = kc_mid < kc_mid_prev

    # 2. 判斷極端大瀑布 / 風控防禦 (防止黑天鵝)
    atr = float(df["atr"].iloc[-1]) if ("atr" in df.columns and len(df["atr"]) > 0 and pd.notna(df["atr"].iloc[-1])) else (c0 * 0.02)
    # Emergency close only for a large adverse candle that cuts through the
    # KC middle. Distance from entry alone is not a disaster signal.
    adverse_body = (
        max(o0 - c0, 0.0)
        if current_position == PositionState.LONG
        else max(c0 - o0, 0.0)
    )
    if (
        atr > 0 and adverse_body >= 1.5 * atr
        and ((current_position == PositionState.LONG and o0 >= kc_mid and c0 < kc_mid)
             or (current_position == PositionState.SHORT and o0 <= kc_mid and c0 > kc_mid))
    ):
        return {
            "action": "EMERGENCY_CLOSE",
            "reason": f"大瀑布/異常反向 K 實體={adverse_body:.4f} 達 1.5 ATR 並貫穿 KC 中軌，緊急平倉",
        }

    # =========================================================
    # 🟢 持有「多單」時的離場邏輯
    # =========================================================
    if current_position == PositionState.LONG:
        # 判斷波段走向是否真的改變：
        # 條件 1: KC 中軌轉向向下
        # 條件 2: 價格收盤價實體跌破 KC 中軌 (代表主升段結束)
        is_trend_broken = is_main_trend_down or (c0 < kc_mid)

        if is_trend_broken:
            # 確認真峰頂或破位，才執行平多 / 翻空
            return {
                "action": "FLIP_TO_SHORT",
                "reason": "波段走向已改變 (跌破 KC 中軌或中軌下行)，平多並翻空",
            }
        else:
            # 🔴 【核心修正】：只要 KC 中軌依然向上且未跌破中軌，中間任何拉回小陰線一律不平倉！
            return {
                "action": "HOLD",
                "reason": "多頭波段走向未變 (中軌支撐有效)，無視短線拉回，繼續持多",
            }

    # =========================================================
    # 🔴 持有「空單」時的離場邏輯
    # =========================================================
    elif current_position == PositionState.SHORT:
        # 判斷波段走向是否真的改變：
        # 條件 1: KC 中軌轉向向上
        # 條件 2: 價格收盤價實體突破 KC 中軌 (代表主跌段結束)
        is_trend_broken = is_main_trend_up or (c0 > kc_mid)

        if is_trend_broken:
            return {
                "action": "FLIP_TO_LONG",
                "reason": "波段走向已改變 (突破 KC 中軌或中軌上行)，平空並翻多",
            }
        else:
            # 🔴 【核心修正】：只要 KC 中軌依然向下且未突破中軌，中間任何反彈小陽線一律不平倉！
            return {
                "action": "HOLD",
                "reason": "空頭波段走向未變 (中軌壓力有效)，無視短線反彈，繼續持空",
            }

    return {"action": "HOLD", "reason": "無動作"}


# =============================================================================
# 波段開始第一時間開倉與翻倉邏輯 (check_wave_start_and_open)
# 核心原則：
#   1. 判定盤整結束、波段發動 (ADX >= 20 且上升 / 帶寬張開 >= 0.005)
#   2. 捕捉波段起漲 (谷底第一根大陽/突破) 或 波段起跌 (峰頂第一根大陰/跌破)
#   3. 波段走向未變前保持持倉 (HOLD 讓利潤奔跑)
# =============================================================================
def check_wave_start_and_open(
    df: pd.DataFrame, current_position: PositionState = PositionState.NONE
) -> dict:
    """
    專門捕捉「波段開始第一時間」發動開倉/翻倉的邏輯：
    1. 判定盤整結束、波段發動 (ADX 向上爆發 / 帶寬張開)
    2. 捕捉波段起漲 (谷底第一根大陽/突破) 或 波段起跌 (峰頂第一根大陰/跌破)
    3. 波段走向未變前保持持倉 (HOLD 讓利潤奔跑)
    """
    if isinstance(current_position, str):
        current_position = PositionState(current_position.upper())

    if len(df) < 5:
        return {"action": "HOLD", "reason": "數據長度不足"}

    c0 = float(df["close"].iloc[-1])
    o0 = float(df["open"].iloc[-1])
    h0 = float(df["high"].iloc[-1])
    l0 = float(df["low"].iloc[-1])
    h1 = float(df["high"].iloc[-2])
    l1 = float(df["low"].iloc[-2])

    kc_mid = float(df["kc_mid"].iloc[-1]) if "kc_mid" in df.columns else float(df["kc_middle"].iloc[-1])

    if "bb_upper" in df.columns and "bb_lower" in df.columns:
        bb_upper = float(df["bb_upper"].iloc[-1])
        bb_lower = float(df["bb_lower"].iloc[-1])
    elif "kc_upper" in df.columns and "kc_lower" in df.columns:
        bb_upper = float(df["kc_upper"].iloc[-1])
        bb_lower = float(df["kc_lower"].iloc[-1])
    else:
        bb_upper = float(df["bb_upper"].iloc[-1]) if "bb_upper" in df.columns else (kc_mid * 1.02)
        bb_lower = float(df["bb_lower"].iloc[-1]) if "bb_lower" in df.columns else (kc_mid * 0.98)

    adx0 = float(df["adx"].iloc[-1]) if "adx" in df.columns else 25.0
    adx1 = float(df["adx"].iloc[-2]) if "adx" in df.columns else 25.0

    if "bandwidth" in df.columns:
        bw0 = float(df["bandwidth"].iloc[-1])
    elif "kc_width" in df.columns:
        bw0 = float(df["kc_width"].iloc[-1]) / (kc_mid if kc_mid != 0 else 1.0)
    else:
        bw0 = (bb_upper - bb_lower) / (kc_mid if kc_mid != 0 else 1.0)

    rsi0 = float(df["rsi"].iloc[-1]) if "rsi" in df.columns else 50.0
    rsi1 = float(df["rsi"].iloc[-2]) if "rsi" in df.columns else 50.0

    # =========================================================
    # Step 1: 判定是否為「波段剛開始發動」的特徵
    # =========================================================
    # 條件: ADX 大於 20 且呈上升趨勢 (或帶寬由收斂轉張開 >= 0.005)
    is_wave_starting = (adx0 >= 20.0 and adx0 > adx1) or (bw0 >= 0.005)

    # 若市場仍死水微幅橫盤，繼續凍結
    if not is_wave_starting:
        return {"action": "HOLD", "reason": "波段尚未發動 (ADX/帶寬未放大)，持續等待"}

    # =========================================================
    # Step 2: 捕捉波段起跌與起漲的第一時間點位
    # =========================================================
    # 🔴 多頭波段發動 / 真谷底起漲點 (開多 / 翻多)
    is_trough_reversal = (c0 <= kc_mid) and (
        (c0 > o0 and c0 > h1) or (rsi1 < 40.0 and rsi0 > rsi1)
    )

    # 🟢 空頭波段發動 / 真峰頂起跌點 (開空 / 翻空)
    is_peak_reversal = (c0 >= kc_mid) and (
        (c0 < o0 and c0 < l1) or (rsi1 > 60.0 and rsi0 < rsi1)
    )

    # =========================================================
    # Step 3: 狀態機發動交易 (無持倉開倉 / 有持倉到達波段終點翻倉)
    # =========================================================
    # 情況 A：無持倉 ➔ 捕捉波段起點進場
    if current_position == PositionState.NONE:
        if is_trough_reversal and c0 < bb_upper:
            return {
                "action": "OPEN_LONG",
                "reason": "🚀 多頭波段開始！第一時間於谷底/起漲點開多",
            }
        elif is_peak_reversal and c0 > bb_lower:
            return {
                "action": "OPEN_SHORT",
                "reason": "📉 空頭波段開始！第一時間於峰頂/起跌點開空",
            }

    # 情況 B：持有多單 ➔ 只有波段走向反轉到真峰頂才平多翻空
    elif current_position == PositionState.LONG:
        if is_peak_reversal:
            return {
                "action": "FLIP_TO_SHORT",
                "reason": "🔄 多頭波段結束，空頭波段開始！平多並瞬間翻空",
            }
        else:
            return {"action": "HOLD", "reason": "多頭波段走向未變，繼續持多讓利潤奔跑"}

    # 情況 C：持有空單 ➔ 只有波段走向反轉到真谷底才平空翻多
    elif current_position == PositionState.SHORT:
        if is_trough_reversal:
            return {
                "action": "FLIP_TO_LONG",
                "reason": "🔄 空頭波段結束，多頭波段開始！平空並瞬間翻多",
            }
        else:
            return {"action": "HOLD", "reason": "空頭波段走向未變，繼續持空讓利潤奔跑"}

    return {"action": "HOLD", "reason": "等待波段確立發動"}


def evaluate_long_position_holding(
    df: pd.DataFrame, current_position: PositionState = PositionState.LONG
) -> dict:
    """多單波段持倉保護與峰頂第 3 點平多邏輯"""
    from core.services.smart_exit_evaluator import evaluate_long_position_holding as _eval_long_holding
    return _eval_long_holding(df, current_position)


def evaluate_long_exit_protection(
    df: pd.DataFrame, current_position: PositionState = PositionState.LONG
) -> dict:
    """多單離場保護別名，指向強化版 evaluate_long_position_holding"""
    from core.services.smart_exit_evaluator import evaluate_long_exit_protection as _eval_long
    return _eval_long(df, current_position)


def evaluate_short_position_holding(
    df: pd.DataFrame, current_position: PositionState = PositionState.SHORT
) -> dict:
    """空單波段持倉保護與谷底第 3 點平空邏輯"""
    from core.services.smart_exit_evaluator import evaluate_short_position_holding as _eval_short_holding
    return _eval_short_holding(df, current_position)


def evaluate_short_exit_protection(
    df: pd.DataFrame, current_position: PositionState = PositionState.SHORT
) -> dict:
    """空單離場保護：

    只要當前 K 線走完是紅 K (陰線急跌)，100% 強行 HOLD，絕不平空！
    只有在出現綠 K (陽線) 且谷底三點確立時，才允許平空/翻多。
    """
    from core.services.smart_exit_evaluator import evaluate_short_exit_protection as _eval_short
    return _eval_short(df, current_position)







