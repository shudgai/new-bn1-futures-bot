"""
中央開倉審查與授權閘門 (CentralEntryGate)
Path: core/gates/central_entry_gate.py

作為全系統所有開倉請求進入交易所下單前的「中央單一物理關卡」。
任何開倉路徑（MA 交叉、KC 突破、趨勢延續、雙週期共振、流水線直通等）
均必須通過此處的嚴格硬性條件審查，任一不符 100% 拒絕送單。

【七大硬性驗證標準】：
1. 收盤確認：必須由已收線 K 棒 (is_closed=True) 產生訊號，嚴禁盤中毛刺 intrabar 偷跑。
2. KC 中軌斜率：多單中軌向上 >= 0.1 ATR；空單中軌向下 >= 0.1 ATR，走平或不足一票否決。
3. MA 斜率與乖離：MA5 必須順向發散；執行價距外軌 <= 0.5 ATR，距 MA5 <= 1.0 ATR，拒絕追高追低。
4. 通道帶寬：(upper - lower) / middle >= 1%，過度狹窄死水區一票否決。
5. 通道擴張：當前帶寬相較近期最低帶寬擴張 >= 5%，拒絕收口擠壓期開倉。
6. 量能門檻：突破棒成交量 >= 過去 5 根均量 1.2 倍（有量能數據時強制執行）。
7. 實體突破與防針：實體 >= 0.35 ATR，同向實體收盤，反向影線 <= 實體 70%，防插針假突破。

【全鏈路 signal_id 追蹤機制】：
每次開倉均生成或綁定唯一的 signal_id，貫穿：
1. 訊號產生 (SIGNAL_GEN)
2. 閘門審查 (ENTRY_GATE)
3. 下單入口 (DISPATCH)
4. 交易所回報 (FILL)
5. 持倉同步 (SYNC)
"""

from __future__ import annotations

import logging
import math
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple, Union

import numpy as np
import pandas as pd

logger = logging.getLogger("CentralEntryGate")


@dataclass
class GateCheckResult:
    passed: bool
    reason: str
    signal_id: str
    details: Dict[str, Any] = field(default_factory=dict)
    checks: Dict[str, Dict[str, Any]] = field(default_factory=dict)

    def log_summary(self) -> None:
        status_symbol = "✅ [GATE_PASS]" if self.passed else "🛑 [GATE_VETO]"
        log_msg = f"{status_symbol} signal_id={self.signal_id} reason={self.reason}"
        if not self.passed:
            logger.warning(log_msg)
        else:
            logger.info(log_msg)


class CentralEntryGate:
    """中央開倉物理閘門：唯一開倉授權機構"""

    # 門檻常數
    KC_SLOPE_MIN_ATR: float = 0.10          # KC 中軌斜率門檻 >= 0.1 ATR
    MAX_BAND_EXTENSION_ATR: float = 0.50    # 距 KC 同側外軌最大乖離 <= 0.5 ATR
    MAX_MA5_EXTENSION_ATR: float = 1.00     # 距 MA5 最大乖離 <= 1.0 ATR
    MIN_KC_BANDWIDTH_PCT: float = 0.01      # 通道帶寬門檻 >= 1.0%
    MIN_KC_EXPANSION_RATIO: float = 0.05    # 通道擴張門檻 >= 5.0%
    MIN_VOLUME_MULTIPLIER: float = 1.20     # 量能門檻 >= 1.2 倍均量
    MIN_BODY_ATR: float = 0.35              # 實體最小動能 >= 0.35 ATR
    MAX_OPPOSITE_WICK_RATIO: float = 0.70   # 反向影線不得超過實體的 70%

    @classmethod
    def generate_signal_id(cls, symbol: str, side: str, source: str) -> str:
        clean_sym = symbol.replace("/", "").replace(":", "")
        ts_part = int(time.time() * 1000)
        rand_part = uuid.uuid4().hex[:6]
        return f"SIG_{clean_sym}_{side}_{source[:10]}_{ts_part}_{rand_part}"

    @classmethod
    def verify_and_authorize(
        cls,
        symbol: str,
        side: str,
        price: float,
        klines_or_snapshot: Union[pd.DataFrame, Dict[str, Any], Any],
        signal_id: Optional[str] = None,
        source: str = "UNKNOWN",
        enforce_volume: bool = True,
        is_breakout: bool = True,
    ) -> GateCheckResult:
        """
        對即將送出交易所的開倉訂單進行中央硬性核驗。
        任一條件不通過，立即回傳 passed=False 並附帶詳細拒絕原因。
        """
        side = str(side or "").upper()
        if side not in ("LONG", "SHORT"):
            return GateCheckResult(
                passed=False,
                reason="VETO_INVALID_SIDE",
                signal_id=signal_id or cls.generate_signal_id(symbol, "UNKNOWN", source),
                details={"error": f"Invalid side: {side}"},
            )

        sig_id = signal_id or cls.generate_signal_id(symbol, side, source)
        checks: Dict[str, Dict[str, Any]] = {}

        # 1. 解析並標準化行情數據
        parsed = cls._extract_market_metrics(klines_or_snapshot, price)
        if not parsed.get("valid"):
            err_reason = parsed.get("error", "VETO_INVALID_MARKET_DATA")
            return GateCheckResult(
                passed=False,
                reason=err_reason,
                signal_id=sig_id,
                details=parsed,
                checks={"data_validation": {"status": "FAIL", "reason": err_reason}},
            )

        atr = float(parsed["atr"])
        curr_price = float(price or parsed["current_price"])

        # -------------------------------------------------------------
        # 條件 1：收盤確認 (Closed Bar Finality)
        # -------------------------------------------------------------
        is_closed = parsed.get("is_closed")
        if is_closed is False:
            checks["closed_bar"] = {
                "status": "FAIL",
                "actual": is_closed,
                "expected": True,
                "reason": "K 線尚未收盤 (Intrabar 未收線偷跑)",
            }
            return GateCheckResult(
                passed=False,
                reason="VETO_INTRABAR_NOT_CLOSED",
                signal_id=sig_id,
                details=parsed,
                checks=checks,
            )
        checks["closed_bar"] = {"status": "PASS", "actual": True, "expected": True}

        # -------------------------------------------------------------
        # 條件 2：KC 中軌斜率 (KC Middle Slope >= 0.1 ATR)
        # -------------------------------------------------------------
        kc_mid_slope = float(parsed["kc_middle_slope"])
        required_slope = cls.KC_SLOPE_MIN_ATR * atr
        if side == "LONG":
            slope_pass = kc_mid_slope >= required_slope
        else:
            slope_pass = kc_mid_slope <= -required_slope

        checks["kc_middle_slope"] = {
            "status": "PASS" if slope_pass else "FAIL",
            "actual_slope": kc_mid_slope,
            "required_slope": required_slope if side == "LONG" else -required_slope,
            "atr": atr,
            "side": side,
        }
        if not slope_pass:
            return GateCheckResult(
                passed=False,
                reason="VETO_KC_SLOPE_INSUFFICIENT",
                signal_id=sig_id,
                details={"kc_mid_slope": kc_mid_slope, "required": required_slope, "side": side},
                checks=checks,
            )

        # -------------------------------------------------------------
        # 條件 3：MA 斜率與乖離 (MA Slope & Extension)
        # -------------------------------------------------------------
        ma5_curr = float(parsed["ma5_curr"])
        ma5_prev = float(parsed["ma5_prev"])
        ma5_delta = ma5_curr - ma5_prev

        if side == "LONG":
            ma5_pass = ma5_delta > 0
        else:
            ma5_pass = ma5_delta < 0

        checks["ma5_slope"] = {
            "status": "PASS" if ma5_pass else "FAIL",
            "ma5_curr": ma5_curr,
            "ma5_prev": ma5_prev,
            "ma5_delta": ma5_delta,
            "side": side,
        }
        if not ma5_pass:
            return GateCheckResult(
                passed=False,
                reason="VETO_MA5_SLOPE_OPPOSITE",
                signal_id=sig_id,
                details={"ma5_delta": ma5_delta, "side": side},
                checks=checks,
            )

        # 乖離率檢查：距同側外軌不得超過 0.5 ATR
        kc_upper = float(parsed["kc_upper"])
        kc_lower = float(parsed["kc_lower"])
        if side == "LONG":
            band_dist = curr_price - kc_upper
            ma5_dist = curr_price - ma5_curr
        else:
            band_dist = kc_lower - curr_price
            ma5_dist = ma5_curr - curr_price

        band_overext = band_dist > (cls.MAX_BAND_EXTENSION_ATR * atr)
        ma5_overext = ma5_dist > (cls.MAX_MA5_EXTENSION_ATR * atr)

        checks["extension"] = {
            "band_dist": band_dist,
            "max_band_dist": cls.MAX_BAND_EXTENSION_ATR * atr,
            "band_pass": not band_overext,
            "ma5_dist": ma5_dist,
            "max_ma5_dist": cls.MAX_MA5_EXTENSION_ATR * atr,
            "ma5_pass": not ma5_overext,
        }
        if band_overext:
            return GateCheckResult(
                passed=False,
                reason="VETO_KC_OUTER_OVEREXTENSION",
                signal_id=sig_id,
                details={"band_dist": band_dist, "limit": cls.MAX_BAND_EXTENSION_ATR * atr},
                checks=checks,
            )
        if ma5_overext:
            return GateCheckResult(
                passed=False,
                reason="VETO_MA5_OVEREXTENSION",
                signal_id=sig_id,
                details={"ma5_dist": ma5_dist, "limit": cls.MAX_MA5_EXTENSION_ATR * atr},
                checks=checks,
            )

        # -------------------------------------------------------------
        # 條件 4：通道帶寬 (Bandwidth >= 1%)
        # -------------------------------------------------------------
        kc_mid = float(parsed["kc_middle"])
        bandwidth = (kc_upper - kc_lower) / kc_mid if kc_mid > 0 else 0.0
        bw_pass = bandwidth >= cls.MIN_KC_BANDWIDTH_PCT

        checks["bandwidth"] = {
            "status": "PASS" if bw_pass else "FAIL",
            "bandwidth": bandwidth,
            "min_required": cls.MIN_KC_BANDWIDTH_PCT,
        }
        if not bw_pass:
            return GateCheckResult(
                passed=False,
                reason="VETO_KC_BANDWIDTH_TOO_NARROW",
                signal_id=sig_id,
                details={"bandwidth": bandwidth, "min_required": cls.MIN_KC_BANDWIDTH_PCT},
                checks=checks,
            )

        # -------------------------------------------------------------
        # 條件 5：通道擴張 (Bandwidth Expansion >= 5%)
        # -------------------------------------------------------------
        curr_width = kc_upper - kc_lower
        min_prev_width = float(parsed.get("min_prev_width", curr_width))
        if min_prev_width > 0:
            expansion_ratio = (curr_width - min_prev_width) / min_prev_width
        else:
            expansion_ratio = 0.0

        # 若有歷史寬度序列，驗證是否擴張 >= 5%
        exp_pass = expansion_ratio >= cls.MIN_KC_EXPANSION_RATIO or parsed.get("history_bars_count", 0) < 5
        checks["expansion"] = {
            "status": "PASS" if exp_pass else "FAIL",
            "curr_width": curr_width,
            "min_prev_width": min_prev_width,
            "expansion_ratio": expansion_ratio,
            "min_required": cls.MIN_KC_EXPANSION_RATIO,
        }
        if not exp_pass:
            return GateCheckResult(
                passed=False,
                reason="VETO_KC_NOT_EXPANDING",
                signal_id=sig_id,
                details={"expansion_ratio": expansion_ratio, "min_required": cls.MIN_KC_EXPANSION_RATIO},
                checks=checks,
            )

        # -------------------------------------------------------------
        # 條件 6：量能門檻 (Volume >= 1.2 倍均量)
        # -------------------------------------------------------------
        vol_curr = float(parsed.get("volume", 0.0))
        vol_avg = float(parsed.get("volume_avg", 0.0))
        if enforce_volume and vol_curr > 0 and vol_avg > 0:
            vol_ratio = vol_curr / vol_avg
            vol_pass = vol_ratio >= cls.MIN_VOLUME_MULTIPLIER
            checks["volume"] = {
                "status": "PASS" if vol_pass else "FAIL",
                "vol_curr": vol_curr,
                "vol_avg": vol_avg,
                "vol_ratio": vol_ratio,
                "min_required": cls.MIN_VOLUME_MULTIPLIER,
            }
            if not vol_pass:
                return GateCheckResult(
                    passed=False,
                    reason="VETO_VOLUME_INSUFFICIENT",
                    signal_id=sig_id,
                    details={"vol_ratio": vol_ratio, "min_required": cls.MIN_VOLUME_MULTIPLIER},
                    checks=checks,
                )
        else:
            checks["volume"] = {"status": "SKIPPED_OR_PASS", "vol_curr": vol_curr, "vol_avg": vol_avg}

        # -------------------------------------------------------------
        # 條件 7：實體突破與反向大陰線/插針防護
        # -------------------------------------------------------------
        c_open = float(parsed["open"])
        c_close = float(parsed["close"])
        c_high = float(parsed["high"])
        c_low = float(parsed["low"])
        c_body = abs(c_close - c_open)

        # 實體最小動能
        if c_body < cls.MIN_BODY_ATR * atr:
            checks["candle_body"] = {
                "status": "FAIL",
                "c_body": c_body,
                "min_required": cls.MIN_BODY_ATR * atr,
                "reason": "K 棒實體動能過小 (十字星/假突破)",
            }
            return GateCheckResult(
                passed=False,
                reason="VETO_BODY_TOO_SMALL",
                signal_id=sig_id,
                details={"c_body": c_body, "min_required": cls.MIN_BODY_ATR * atr},
                checks=checks,
            )

        # 做多：嚴禁陰線，上影線不得超過實體的 70%
        if side == "LONG":
            if c_close <= c_open:
                checks["candle_color"] = {"status": "FAIL", "reason": "做多當根 K 棒收陰"}
                return GateCheckResult(
                    passed=False,
                    reason="VETO_BEARISH_CANDLE_FOR_LONG",
                    signal_id=sig_id,
                    details={"close": c_close, "open": c_open},
                    checks=checks,
                )
            upper_wick = c_high - c_close
            if upper_wick > cls.MAX_OPPOSITE_WICK_RATIO * c_body:
                checks["candle_wick"] = {
                    "status": "FAIL",
                    "upper_wick": upper_wick,
                    "max_allowed": cls.MAX_OPPOSITE_WICK_RATIO * c_body,
                    "reason": "上影線過長，衝高回落天花板插針",
                }
                return GateCheckResult(
                    passed=False,
                    reason="VETO_UPPER_WICK_TOO_LONG",
                    signal_id=sig_id,
                    details={"upper_wick": upper_wick, "body": c_body},
                    checks=checks,
                )

        # 做空：嚴禁陽線，下影線不得超過實體的 70%
        if side == "SHORT":
            if c_close >= c_open:
                checks["candle_color"] = {"status": "FAIL", "reason": "做空當根 K 棒收陽"}
                return GateCheckResult(
                    passed=False,
                    reason="VETO_BULLISH_CANDLE_FOR_SHORT",
                    signal_id=sig_id,
                    details={"close": c_close, "open": c_open},
                    checks=checks,
                )
            lower_wick = c_close - c_low
            if lower_wick > cls.MAX_OPPOSITE_WICK_RATIO * c_body:
                checks["candle_wick"] = {
                    "status": "FAIL",
                    "lower_wick": lower_wick,
                    "max_allowed": cls.MAX_OPPOSITE_WICK_RATIO * c_body,
                    "reason": "下影線過長，探底回升地板插針",
                }
                return GateCheckResult(
                    passed=False,
                    reason="VETO_LOWER_WICK_TOO_LONG",
                    signal_id=sig_id,
                    details={"lower_wick": lower_wick, "body": c_body},
                    checks=checks,
                )

        checks["candle_shape"] = {"status": "PASS", "body": c_body, "side": side}

        # 全部硬性條件通過！
        result = GateCheckResult(
            passed=True,
            reason="PASSED",
            signal_id=sig_id,
            details={
                "symbol": symbol,
                "side": side,
                "price": curr_price,
                "atr": atr,
                "kc_mid_slope": kc_mid_slope,
                "bandwidth": bandwidth,
                "expansion_ratio": expansion_ratio,
            },
            checks=checks,
        )
        result.log_summary()
        return result

    @classmethod
    def _extract_market_metrics(
        cls,
        data: Union[pd.DataFrame, Dict[str, Any], Any],
        price: float,
    ) -> Dict[str, Any]:
        """從各種資料格式 (DataFrame, Dict, Snapshot) 安全提取標準化數值"""
        metrics: Dict[str, Any] = {"valid": False}
        try:
            if isinstance(data, pd.DataFrame):
                if data.empty or len(data) < 2:
                    return {"valid": False, "error": "VETO_DATAFRAME_TOO_SHORT"}

                # 判斷最後一根是否已收盤
                last_row = data.iloc[-1]
                prev_row = data.iloc[-2]
                is_closed = bool(last_row.get("is_closed", True))

                # 若最後一根尚未收盤，以最後一根未收盤標註，或取前一根已收盤
                if not is_closed and len(data) >= 3:
                    eval_row = data.iloc[-2]
                    prev_eval_row = data.iloc[-3]
                    # 標註未收盤供 Gate 阻斷
                    metrics["is_closed"] = False
                else:
                    eval_row = last_row
                    prev_eval_row = prev_row
                    metrics["is_closed"] = is_closed

                metrics["open"] = float(eval_row.get("open", price))
                metrics["high"] = float(eval_row.get("high", price))
                metrics["low"] = float(eval_row.get("low", price))
                metrics["close"] = float(eval_row.get("close", price))
                metrics["current_price"] = float(price or last_row.get("close", price))

                metrics["atr"] = float(eval_row.get("atr", max(0.001, price * 0.01)))
                metrics["kc_upper"] = float(eval_row.get("kc_upper", price * 1.01))
                metrics["kc_lower"] = float(eval_row.get("kc_lower", price * 0.99))
                metrics["kc_middle"] = float(eval_row.get("kc_middle", (metrics["kc_upper"] + metrics["kc_lower"]) / 2))

                # KC slope
                prev_kc_mid = float(prev_eval_row.get("kc_middle", metrics["kc_middle"]))
                metrics["kc_middle_slope"] = float(eval_row.get("kc_middle_slope", metrics["kc_middle"] - prev_kc_mid))

                # MA5
                metrics["ma5_curr"] = float(eval_row.get("ma5", metrics["close"]))
                metrics["ma5_prev"] = float(prev_eval_row.get("ma5", metrics["ma5_curr"]))

                # 歷史帶寬與擴張
                if "kc_upper" in data.columns and "kc_lower" in data.columns:
                    widths = (data["kc_upper"] - data["kc_lower"]).tail(8).values
                    metrics["min_prev_width"] = float(np.min(widths[:-1])) if len(widths) > 1 else float(widths[-1])
                else:
                    metrics["min_prev_width"] = metrics["kc_upper"] - metrics["kc_lower"]
                metrics["history_bars_count"] = len(data)

                # 量能
                if "volume" in data.columns:
                    metrics["volume"] = float(eval_row.get("volume", 0.0))
                    vols = data["volume"].tail(6).values
                    if len(vols) > 1:
                        metrics["volume_avg"] = float(np.mean(vols[:-1]))
                    else:
                        metrics["volume_avg"] = metrics["volume"]
                metrics["valid"] = True
                return metrics

            elif isinstance(data, dict):
                curr_k = data.get("curr_k") or {}
                prev_k = data.get("prev_k") or {}

                metrics["open"] = float(curr_k.get("open", data.get("open", price)))
                metrics["high"] = float(curr_k.get("high", data.get("high", price)))
                metrics["low"] = float(curr_k.get("low", data.get("low", price)))
                metrics["close"] = float(curr_k.get("close", data.get("close", price)))
                metrics["current_price"] = float(price or data.get("current_price", metrics["close"]))

                atr_val = float(data.get("atr", max(0.001, price * 0.01)))
                metrics["atr"] = atr_val
                metrics["kc_upper"] = float(data.get("kc_upper", price + 1.5 * atr_val))
                metrics["kc_lower"] = float(data.get("kc_lower", price - 1.5 * atr_val))
                metrics["kc_middle"] = float(data.get("kc_middle", (metrics["kc_upper"] + metrics["kc_lower"]) / 2))

                metrics["kc_middle_slope"] = float(data.get("kc_middle_slope", 0.0))
                metrics["ma5_curr"] = float(data.get("ma5_curr", curr_k.get("ma5", data.get("ma5", metrics["close"]))))
                metrics["ma5_prev"] = float(data.get("ma5_prev", prev_k.get("ma5", metrics["ma5_curr"])))

                curr_w = metrics["kc_upper"] - metrics["kc_lower"]
                metrics["min_prev_width"] = float(data.get("min_prev_width", curr_w * 0.95))
                metrics["history_bars_count"] = int(data.get("history_bars_count", 10))

                metrics["is_closed"] = bool(data.get("is_closed", curr_k.get("is_closed", True)))
                metrics["volume"] = float(curr_k.get("volume", data.get("volume", 0.0)))
                metrics["volume_avg"] = float(data.get("volume_avg", metrics["volume"]))
                metrics["valid"] = True
                return metrics

            # 物件屬性 snapshot
            else:
                metrics["current_price"] = float(getattr(data, "current_price", price))
                atr_val = float(getattr(data, "atr", max(0.001, price * 0.01)))
                metrics["atr"] = atr_val
                metrics["kc_upper"] = float(getattr(data, "kc_upper", price + 1.5 * atr_val))
                metrics["kc_lower"] = float(getattr(data, "kc_lower", price - 1.5 * atr_val))
                metrics["kc_middle"] = float(getattr(data, "kc_middle", (metrics["kc_upper"] + metrics["kc_lower"]) / 2))
                metrics["kc_middle_slope"] = float(getattr(data, "kc_middle_slope", 0.0))

                curr_k = getattr(data, "curr_k", {}) or {}
                prev_k = getattr(data, "prev_k", {}) or {}
                metrics["open"] = float(curr_k.get("open", metrics["current_price"]))
                metrics["high"] = float(curr_k.get("high", metrics["current_price"]))
                metrics["low"] = float(curr_k.get("low", metrics["current_price"]))
                metrics["close"] = float(curr_k.get("close", metrics["current_price"]))

                metrics["ma5_curr"] = float(getattr(data, "ma5", curr_k.get("ma5", metrics["close"])))
                metrics["ma5_prev"] = float(getattr(data, "prev_ma5", prev_k.get("ma5", metrics["ma5_curr"])))

                curr_w = metrics["kc_upper"] - metrics["kc_lower"]
                metrics["min_prev_width"] = float(getattr(data, "min_prev_width", curr_w * 0.95))
                metrics["history_bars_count"] = 10
                metrics["is_closed"] = bool(getattr(data, "is_closed", curr_k.get("is_closed", True)))
                metrics["volume"] = float(curr_k.get("volume", 0.0))
                metrics["volume_avg"] = float(getattr(data, "volume_avg", metrics["volume"]))
                metrics["valid"] = True
                return metrics

        except Exception as exc:
            return {"valid": False, "error": f"METRICS_EXTRACTION_ERROR: {type(exc).__name__}: {exc}"}
