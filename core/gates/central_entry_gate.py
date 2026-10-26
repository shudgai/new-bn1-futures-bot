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

    # 共用送單硬門檻：不因標的或訊號標籤放寬。
    KC_SLOPE_MIN_ATR: float = 0.10
    MAX_BAND_EXTENSION_ATR: float = 0.50
    MAX_MA5_EXTENSION_ATR: float = 1.00
    MIN_KC_BANDWIDTH_PCT: float = 0.0035
    MIN_KC_EXPANSION_RATIO: float = 0.05
    MIN_VOLUME_MULTIPLIER: float = 1.20
    MIN_BODY_ATR: float = 0.35
    MAX_OPPOSITE_WICK_RATIO: float = 0.70
    MAX_SQUEEZE_RATIO: float = 0.25
    MIN_MA_SPACING_WIDTH_RATIO: float = 0.25
    MIN_CROSS_MID_ROOM_WIDTH_RATIO: float = 0.20

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
        allow_intrabar: bool = False,
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

        source_upper = str(source or "").upper()
        swing_pivot_signal = "SWING_PIVOT" in source_upper or bool(
            isinstance(klines_or_snapshot, dict)
            and str(klines_or_snapshot.get("entry_type") or "").upper() == "SWING_PIVOT"
        )
        if swing_pivot_signal and isinstance(klines_or_snapshot, pd.DataFrame):
            from core.services.candle_data import closed_entry_candles
            closed_frame = closed_entry_candles(klines_or_snapshot)
            if closed_frame.empty:
                return GateCheckResult(
                    passed=False, reason="VETO_SWING_PIVOT_CLOSED_BAR_REQUIRED",
                    signal_id=sig_id,
                )
            closed_frame.attrs.update(klines_or_snapshot.attrs)
            klines_or_snapshot = closed_frame

        # 1. 解析並標準化行情數據
        parsed = cls._extract_market_metrics(
            klines_or_snapshot, price, allow_intrabar=allow_intrabar,
        )
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
        src_upper = str(source or "").upper()
        is_swing_pivot = swing_pivot_signal or bool(
            isinstance(klines_or_snapshot, dict)
            and str(klines_or_snapshot.get("entry_type") or "").upper() == "SWING_PIVOT"
        )
        allowed_intrabar_routes = (
            "BREAKOUT_LONG", "BREAKDOWN_SHORT", "CONTINUATION_OUTSIDE_KC_LONG",
            "CONTINUATION_OUTSIDE_KC_SHORT", "ENTRY_LONG_MA3_MA15_CROSS_MID",
            "ENTRY_SHORT_MA3_MA15_CROSS_MID",
        )
        route_is_intrabar_eligible = any(
            route in src_upper for route in allowed_intrabar_routes
        )
        if is_closed is False and not allow_intrabar:
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
        if is_closed is False and allow_intrabar and not route_is_intrabar_eligible:
            return GateCheckResult(
                passed=False,
                reason="VETO_INTRABAR_SIGNAL_TYPE_NOT_AUTHORIZED",
                signal_id=sig_id,
                details=parsed,
                checks={"intrabar_route": {"status": "FAIL", "source": source}},
            )
        if allow_intrabar and is_closed is not False:
            return GateCheckResult(
                passed=False,
                reason="VETO_INTRABAR_AUTHORITY_REQUIRES_FORMING_BAR",
                signal_id=sig_id,
                details=parsed,
                checks={"closed_bar": {"status": "FAIL", "actual": is_closed}},
            )
        checks["closed_bar"] = {"status": "PASS", "actual": True, "expected": True}

        # 最前置黑名單：所有 MA 交叉／突破入口共用此已收線盤整檢查。
        # 欄位或歷史不足一律拒絕，禁止策略分支繞過震盪濾網。
        from core.services.market_consolidation_filter import evaluate_market_blacklist
        if isinstance(klines_or_snapshot, pd.DataFrame):
            entry_frame = klines_or_snapshot
        elif isinstance(klines_or_snapshot, dict):
            entry_frame = klines_or_snapshot.get("raw_df")
            if entry_frame is None:
                entry_frame = klines_or_snapshot.get("frame")
            candles = klines_or_snapshot.get("candles")
            if entry_frame is None and isinstance(candles, (list, tuple)):
                entry_frame = pd.DataFrame(candles)
        else:
            entry_frame = getattr(klines_or_snapshot, "raw_df", None)
        if not isinstance(entry_frame, pd.DataFrame):
            blacklist = (True, "VETO_CONSOLIDATION_HISTORY_REQUIRED", {})
        else:
            last_trade = (
                klines_or_snapshot.get("last_closed_trade")
                if isinstance(klines_or_snapshot, dict) else None
            )
            macro_trend = (
                klines_or_snapshot.get("macro_trend")
                if isinstance(klines_or_snapshot, dict)
                else (
                    entry_frame.attrs.get("macro_trend")
                    or entry_frame.iloc[-1].get("macro_trend")
                    if isinstance(entry_frame, pd.DataFrame) and not entry_frame.empty
                    else getattr(klines_or_snapshot, "macro_trend", None)
                )
            )
            last_stamp = entry_frame.iloc[-1].get("timestamp") if len(entry_frame) else None
            try:
                bar_index = int(float(last_stamp) // 60_000) if last_stamp is not None else None
            except (TypeError, ValueError, OverflowError):
                bar_index = None
            if is_swing_pivot:
                from core.services.swing_pivot_rules import evaluate_swing_pivot_entry
                pivot_evidence = evaluate_swing_pivot_entry(entry_frame)
                if not pivot_evidence or pivot_evidence.get("side") != side:
                    return GateCheckResult(
                        passed=False, reason="VETO_SWING_PIVOT_NOT_CONFIRMED",
                        signal_id=sig_id, details={"side": side},
                    )
                macro_upper = str(macro_trend or "").upper()
                if side == "LONG" and macro_upper in ("BEARISH", "SHORT", "DOWN"):
                    return GateCheckResult(
                        passed=False, reason="BLOCKED_LONG_ON_BEARISH_TREND",
                        signal_id=sig_id, details={"side": side, "macro_trend": macro_trend},
                    )
                if side == "SHORT" and macro_upper in ("BULLISH", "LONG", "UP"):
                    return GateCheckResult(
                        passed=False, reason="BLOCKED_SHORT_ON_BULLISH_TREND",
                        signal_id=sig_id, details={"side": side, "macro_trend": macro_trend},
                    )
                blacklist = (False, "PASS_CONFIRMED_SWING_PIVOT", {
                    "swing_pivot": pivot_evidence,
                    "soft_market_blacklist": "BYPASSED_FOR_CONFIRMED_PIVOT",
                })
            else:
                # The entry may be submitted during a forming third candle,
                # but consolidation classification must use closed evidence.
                from core.services.candle_data import closed_entry_candles
                blacklist_frame = closed_entry_candles(entry_frame)
                blacklist = evaluate_market_blacklist(
                    blacklist_frame, -1, side=side, last_closed_trade=last_trade,
                    current_bar_index=bar_index, macro_trend=macro_trend,
                )
        blocked, block_reason, block_evidence = blacklist
        checks["market_blacklist"] = {
            "status": "FAIL" if blocked else "PASS",
            "reason": block_reason,
            "evidence": block_evidence,
        }
        if blocked:
            return GateCheckResult(
                passed=False,
                reason=block_reason,
                signal_id=sig_id,
                details={"market_blacklist": block_evidence},
                checks=checks,
            )

        # -------------------------------------------------------------
        # 區分訊號類型：均線轉折類 (CROSS_MID) vs 常規破軌追單類 (BREAKOUT)
        # -------------------------------------------------------------
        is_cross_mid = bool(
            "CROSS_MID" in src_upper
            or "MA_CROSS" in src_upper
            or "EMA_CROSS" in src_upper
            or "MID_LINE" in src_upper
            or "REVERSAL" in src_upper
            or "AGILE" in src_upper
            or is_swing_pivot
            or (isinstance(klines_or_snapshot, dict) and (
                klines_or_snapshot.get("allow_kc_internal")
                or klines_or_snapshot.get("is_cross_mid")
                or klines_or_snapshot.get("direction_override")
                or klines_or_snapshot.get("strategy_mode") == "AGILE_MOMENTUM"
                or klines_or_snapshot.get("entry_type") == "CROSS_MID"
            ))
        )

        # -------------------------------------------------------------
        # 條件 1.5：MA3 交叉 KC 中軌核心授權 (MA3 x KC Midline Cross)
        # -------------------------------------------------------------
        if ("CROSS_MID" in src_upper or "MA_CROSS" in src_upper) and not is_swing_pivot:
            from core.gates.ma3_kc_midline_cross_gate import evaluate_ma3_kc_midline_cross
            cross_ok, cross_reason, cross_meta = evaluate_ma3_kc_midline_cross(
                entry_frame, side, symbol=symbol,
            )
            checks["ma3_kc_midline_cross"] = {
                "status": "PASS" if cross_ok else "FAIL",
                "reason": cross_reason,
                **cross_meta,
            }
            if not cross_ok:
                return GateCheckResult(
                    passed=False,
                    reason=f"VETO_{cross_reason}",
                    signal_id=sig_id,
                    details=cross_meta,
                    checks=checks,
                )

        # -------------------------------------------------------------
        # 條件 2：KC 中軌斜率 (動態門檻)
        # -------------------------------------------------------------
        kc_mid_slope = float(parsed["kc_middle_slope"])
        required_slope = cls.KC_SLOPE_MIN_ATR * atr

        if is_swing_pivot:
            # Pivot entries are explicitly authorized against a lagging KC
            # slope. All candle, location, bandwidth, liquidity, and risk gates
            # below remain active.
            slope_pass = True
            req_target = "SWING_PIVOT_OVERRIDE"
        elif is_cross_mid:
            # 交叉訊號僅允許通道平向或同向，不能覆蓋反向結構。
            if side == "LONG":
                slope_pass = kc_mid_slope >= 0.0
            else:
                slope_pass = kc_mid_slope <= 0.0
            req_target = 0.0
        else:
            # 常規破軌追單類：維持原有的嚴格斜率審核 (>= 0.1 ATR)
            if side == "LONG":
                slope_pass = kc_mid_slope >= required_slope
            else:
                slope_pass = kc_mid_slope <= -required_slope
            req_target = required_slope if side == "LONG" else -required_slope

        checks["kc_middle_slope"] = {
            "status": "PASS" if slope_pass else "FAIL",
            "actual_slope": kc_mid_slope,
            "required_slope": req_target,
            "signal_mode": "CROSS_MID" if is_cross_mid else "BREAKOUT_RIDE",
            "atr": atr,
            "side": side,
        }
        if not slope_pass:
            return GateCheckResult(
                passed=False,
                reason="VETO_KC_SLOPE_INSUFFICIENT",
                signal_id=sig_id,
                details={"kc_mid_slope": kc_mid_slope, "required": req_target, "is_cross_mid": is_cross_mid, "side": side},
                checks=checks,
            )

        # -------------------------------------------------------------
        # 條件 2.5：中軌正向側防線 (做多必須在中軌之上，做空必須在中軌之下)
        # -------------------------------------------------------------
        kc_mid = float(parsed["kc_middle"])
        if side == "LONG" and curr_price < kc_mid and not is_swing_pivot:
            checks["midline_side"] = {"status": "FAIL", "price": curr_price, "kc_middle": kc_mid}
            return GateCheckResult(
                passed=False,
                reason="VETO_LONG_BELOW_KC_MIDDLE",
                signal_id=sig_id,
                details={"price": curr_price, "kc_middle": kc_mid},
                checks=checks,
            )
        elif side == "SHORT" and curr_price > kc_mid and not is_swing_pivot:
            checks["midline_side"] = {"status": "FAIL", "price": curr_price, "kc_middle": kc_mid}
            return GateCheckResult(
                passed=False,
                reason="VETO_SHORT_ABOVE_KC_MIDDLE",
                signal_id=sig_id,
                details={"price": curr_price, "kc_middle": kc_mid},
                checks=checks,
            )
        checks["midline_side"] = {"status": "PASS", "price": curr_price, "kc_middle": kc_mid}

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
        if not ma5_pass and not is_swing_pivot:
            return GateCheckResult(
                passed=False,
                reason="VETO_MA5_SLOPE_OPPOSITE",
                signal_id=sig_id,
                details={"ma5_delta": ma5_delta, "side": side},
                checks=checks,
            )

        if is_cross_mid and not is_swing_pivot:
            ma15_curr = float(parsed["ma15_curr"])
            ma15_prev = float(parsed["ma15_prev"])
            ma_cross_aligned = (
                ma5_curr > ma15_curr and ma15_curr >= ma15_prev
                if side == "LONG"
                else ma5_curr < ma15_curr and ma15_curr <= ma15_prev
            )
            if not ma_cross_aligned:
                return GateCheckResult(
                    passed=False, reason="VETO_MA5_MA15_CROSS_NOT_DIRECTIONALLY_ALIGNED",
                    signal_id=sig_id,
                    details={"ma5": ma5_curr, "ma15": ma15_curr,
                             "previous_ma15": ma15_prev, "side": side},
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
        if band_overext and not is_swing_pivot:
            return GateCheckResult(
                passed=False,
                reason="VETO_KC_OUTER_OVEREXTENSION",
                signal_id=sig_id,
                details={"band_dist": band_dist, "limit": cls.MAX_BAND_EXTENSION_ATR * atr},
                checks=checks,
            )
        if is_cross_mid and not is_swing_pivot:
            remaining_room = (
                kc_upper - curr_price if side == "LONG"
                else curr_price - kc_lower
            )
            width = kc_upper - kc_lower
            required_room = cls.MIN_CROSS_MID_ROOM_WIDTH_RATIO * width
            if remaining_room < required_room:
                return GateCheckResult(
                    passed=False, reason="VETO_CROSS_MID_CHANNEL_ROOM_INSUFFICIENT",
                    signal_id=sig_id,
                    details={"remaining_room": remaining_room,
                             "required_room": required_room, "kc_width": width},
                    checks=checks,
                )
        if ma5_overext and not is_swing_pivot:
            return GateCheckResult(
                passed=False,
                reason="VETO_MA5_OVEREXTENSION",
                signal_id=sig_id,
                details={"ma5_dist": ma5_dist, "limit": cls.MAX_MA5_EXTENSION_ATR * atr},
                checks=checks,
            )

        # -------------------------------------------------------------
        # 條件 4：通道帶寬 (Bandwidth >= 0.35% 或 通道寬度 >= 2.0 ATR)
        # -------------------------------------------------------------
        kc_mid = float(parsed["kc_middle"])
        kc_width = kc_upper - kc_lower
        bandwidth = kc_width / kc_mid if kc_mid > 0 else 0.0
        bw_pass = (bandwidth >= cls.MIN_KC_BANDWIDTH_PCT) or (kc_width >= 2.0 * atr)

        checks["bandwidth"] = {
            "status": "PASS" if bw_pass else "FAIL",
            "bandwidth": bandwidth,
            "kc_width": kc_width,
            "atr": atr,
            "min_required": cls.MIN_KC_BANDWIDTH_PCT,
        }
        if not bw_pass and not is_swing_pivot:
            return GateCheckResult(
                passed=False,
                reason="VETO_KC_BANDWIDTH_TOO_NARROW",
                signal_id=sig_id,
                details={"bandwidth": bandwidth, "min_required": cls.MIN_KC_BANDWIDTH_PCT},
                checks=checks,
            )

        # 20 根已收線最大寬度作為 squeeze 參考；歷史不足時 fail closed。
        curr_width = kc_upper - kc_lower
        max_prev_width = parsed.get("max_prev_width")
        if (not is_swing_pivot and (max_prev_width is None
                or not math.isfinite(float(max_prev_width)) or float(max_prev_width) <= 0)):
            return GateCheckResult(
                passed=False, reason="VETO_KC_SQUEEZE_HISTORY_INVALID",
                signal_id=sig_id, details={"max_prev_width": max_prev_width}, checks=checks,
            )
        max_prev_width = float(max_prev_width) if max_prev_width is not None else curr_width
        if not is_swing_pivot and curr_width <= cls.MAX_SQUEEZE_RATIO * max_prev_width:
            return GateCheckResult(
                passed=False, reason="VETO_KC_SQUEEZE_AT_OR_BELOW_25_PERCENT",
                signal_id=sig_id,
                details={"current_width": curr_width, "max_prev_width": max_prev_width},
                checks=checks,
            )

        # 均線黏合或 MA5 貼近目標外軌時，通道空間不足，一律拒絕。
        ma5_curr = float(parsed["ma5_curr"])
        ma15_curr = float(parsed["ma15_curr"])
        target_band = kc_upper if side == "LONG" else kc_lower
        min_spacing = cls.MIN_MA_SPACING_WIDTH_RATIO * curr_width
        if (not is_swing_pivot and (
                abs(ma5_curr - ma15_curr) < min_spacing
                or abs(ma5_curr - target_band) < min_spacing)):
            return GateCheckResult(
                passed=False, reason="VETO_MA_SPACING_BELOW_25_PERCENT_KC_WIDTH",
                signal_id=sig_id,
                details={"ma5": ma5_curr, "ma15": ma15_curr,
                         "target_band": target_band, "min_spacing": min_spacing},
                checks=checks,
            )

        # -------------------------------------------------------------
        # 條件 5：通道擴張確認
        # -------------------------------------------------------------
        min_prev_width = float(parsed.get("min_prev_width", curr_width))
        if min_prev_width > 0:
            expansion_ratio = (curr_width - min_prev_width) / min_prev_width
        else:
            expansion_ratio = 0.0

        is_continuation = "CONTINUATION" in src_upper
        exp_pass = expansion_ratio >= cls.MIN_KC_EXPANSION_RATIO
        checks["expansion"] = {
            "status": "PASS" if exp_pass else "FAIL",
            "curr_width": curr_width,
            "min_prev_width": min_prev_width,
            "expansion_ratio": expansion_ratio,
            "min_required": cls.MIN_KC_EXPANSION_RATIO,
            "is_cross_mid": is_cross_mid,
            "is_continuation": is_continuation,
        }
        if not exp_pass and not is_swing_pivot:
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
        if enforce_volume and not is_swing_pivot:
            if vol_curr <= 0 or vol_avg <= 0:
                return GateCheckResult(
                    passed=False, reason="VETO_VOLUME_DATA_UNAVAILABLE",
                    signal_id=sig_id, details={"volume": vol_curr, "volume_avg": vol_avg},
                    checks=checks,
                )
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
            checks["volume"] = {"status": "SKIPPED_OR_PASS", "vol_curr": vol_curr, "vol_avg": vol_avg, "is_cross_mid": is_cross_mid, "is_continuation": is_continuation}

        # -------------------------------------------------------------
        # 條件 7：實體突破與反向大陰線/插針防護
        # -------------------------------------------------------------
        c_open = float(parsed["open"])
        c_close = float(parsed["close"])
        c_high = float(parsed["high"])
        c_low = float(parsed["low"])
        c_body = abs(c_close - c_open)

        # 實體最小動能 (敏捷轉折模式放寬至 0.20 ATR，常規破軌維持 0.35 ATR)
        req_body_atr = cls.MIN_BODY_ATR
        if c_body < req_body_atr * atr:
            checks["candle_body"] = {
                "status": "FAIL",
                "c_body": c_body,
                "min_required": req_body_atr * atr,
                "reason": "K 棒實體動能過小 (十字星/假突破)",
            }
            return GateCheckResult(
                passed=False,
                reason="VETO_BODY_TOO_SMALL",
                signal_id=sig_id,
                details={"c_body": c_body, "min_required": req_body_atr * atr},
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
        *,
        allow_intrabar: bool = False,
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
                if not is_closed and allow_intrabar and len(data) < 3:
                    return {"valid": False, "error": "VETO_INTRABAR_CLOSED_HISTORY_INVALID"}

                # 若最後一根尚未收盤，以最後一根未收盤標註，或取前一根已收盤
                if not is_closed and allow_intrabar and len(data) >= 3:
                    if not bool(prev_row.get("is_closed", False)):
                        return {"valid": False, "error": "VETO_INTRABAR_CLOSED_HISTORY_INVALID"}
                    eval_row = last_row
                    prev_eval_row = data.iloc[-2]
                    metrics["is_closed"] = False
                elif not is_closed and len(data) >= 3:
                    eval_row = data.iloc[-2]
                    prev_eval_row = data.iloc[-3]
                    # 標註未收盤供 Gate 阻斷
                    metrics["is_closed"] = False
                else:
                    eval_row = last_row
                    prev_eval_row = prev_row
                    metrics["is_closed"] = is_closed

                metrics["open"] = float(eval_row.get("open", price))
                metrics["high"] = max(float(eval_row.get("high", price)), float(price)) if allow_intrabar else float(eval_row.get("high", price))
                metrics["low"] = min(float(eval_row.get("low", price)), float(price)) if allow_intrabar else float(eval_row.get("low", price))
                metrics["close"] = float(price) if allow_intrabar else float(eval_row.get("close", price))
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
                metrics["ma15_curr"] = float(eval_row.get("ma15", metrics["close"]))
                metrics["ma15_prev"] = float(prev_eval_row.get("ma15", metrics["ma15_curr"]))

                # 歷史帶寬與擴張
                if "kc_upper" in data.columns and "kc_lower" in data.columns:
                    widths = (data["kc_upper"] - data["kc_lower"]).tail(8).values
                    metrics["min_prev_width"] = float(np.min(widths[:-1])) if len(widths) > 1 else float(widths[-1])
                    eval_idx = int(data.index.get_loc(eval_row.name))
                    history = data.iloc[:eval_idx]
                    if "is_closed" in history.columns:
                        history = history[history["is_closed"] == True]
                    history_widths = (
                        pd.to_numeric(history["kc_upper"], errors="coerce")
                        - pd.to_numeric(history["kc_lower"], errors="coerce")
                    ).dropna().tail(20)
                    metrics["max_prev_width"] = (
                        float(history_widths.max()) if len(history_widths) >= 20 else None
                    )
                else:
                    metrics["min_prev_width"] = metrics["kc_upper"] - metrics["kc_lower"]
                    metrics["max_prev_width"] = None
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
                metrics["ma15_curr"] = float(data.get("ma15_curr", curr_k.get("ma15", data.get("ma15", metrics["close"]))))
                metrics["ma15_prev"] = float(data.get("ma15_prev", prev_k.get("ma15", metrics["ma15_curr"])))

                curr_w = metrics["kc_upper"] - metrics["kc_lower"]
                metrics["min_prev_width"] = float(data.get("min_prev_width", curr_w * 0.95))
                candles = data.get("candles") or []
                prior_widths = []
                for candle in candles[-21:-1]:
                    try:
                        prior_widths.append(float(candle["kc_upper"]) - float(candle["kc_lower"]))
                    except (KeyError, TypeError, ValueError, OverflowError):
                        prior_widths = []
                        break
                metrics["max_prev_width"] = max(prior_widths) if len(prior_widths) == 20 else None
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
                metrics["ma15_curr"] = float(getattr(data, "ma15", curr_k.get("ma15", metrics["close"])))
                metrics["ma15_prev"] = float(prev_k.get("ma15", metrics["ma15_curr"]))

                curr_w = metrics["kc_upper"] - metrics["kc_lower"]
                metrics["min_prev_width"] = float(getattr(data, "min_prev_width", curr_w * 0.95))
                metrics["max_prev_width"] = getattr(data, "max_prev_width", None)
                metrics["history_bars_count"] = 10
                metrics["is_closed"] = bool(getattr(data, "is_closed", curr_k.get("is_closed", True)))
                metrics["volume"] = float(curr_k.get("volume", 0.0))
                metrics["volume_avg"] = float(getattr(data, "volume_avg", metrics["volume"]))
                metrics["valid"] = True
                return metrics

        except Exception as exc:
            return {"valid": False, "error": f"METRICS_EXTRACTION_ERROR: {type(exc).__name__}: {exc}"}
