"""Pre-close-API absolute veto for meme (1000PEPE / 龍蝦) positions.

Any automated close request on a meme symbol must pass this gate; ``HOLD`` means
the request is discarded and no buy-back/sell-back order may be sent.
LONG mirrors SHORT exactly.
"""

from __future__ import annotations

import logging
import math
import time
from typing import Any, Dict, Mapping, NamedTuple, Optional, Tuple

import pandas as pd

from core.services.candle_data import closed_entry_candles

logger = logging.getLogger("UltimateShortExitGatekeeper")

MIN_BARS_HELD = 5
STRUCTURE_BUFFER_ATR = 0.50
DISASTER_LOSS_ATR = 3.0
# Owner decision 2026-11-03: while the quote stays within this many ATR of the
# entry price (either side), structure/waterfall/rail exits hold the position.
# Profitable confirmed pivots keep their own structure-locked authority.
COST_SHIELD_ATR = 1.0
COST_SHIELD_EXEMPT_TOKENS = ("HARD_S", "CIRCUIT_BREAKER", "STOP_LOSS", "DISASTER_LOSS")
ACCOUNT_LEVEL_TOKENS = (
    "LIQUIDATION", "DATA_OUTAGE", "ACCOUNT_CIRCUIT_BREAKER", "ACCOUNT_HARD_STOP",
    "帳戶硬止損", "MARGIN_LOSS",
)
STRUCTURE_EXIT_REASONS = {
    "SHORT": "EXIT_SHORT_STRUCTURE_BREAK_050ATR",
    "LONG": "EXIT_LONG_STRUCTURE_BREAK_050ATR",
}
KC_MIDDLE_MA15_BREAK_REASONS = {
    "SHORT": "EXIT_SHORT_KC_MIDDLE_MA15_BREAK",
    "LONG": "EXIT_LONG_KC_MIDDLE_MA15_BREAK",
}
DISASTER_LOSS_REASONS = {
    "SHORT": "EXIT_SHORT_DISASTER_LOSS_3ATR",
    "LONG": "EXIT_LONG_DISASTER_LOSS_3ATR",
}
DISASTER_RAIL_REASONS = {
    "SHORT": "EXIT_SHORT_DISASTER_CLOSED_ABOVE_KC_UPPER",
    "LONG": "EXIT_LONG_DISASTER_CLOSED_BELOW_KC_LOWER",
}
# Owner decision 2026-11-03: inside the cost shield, a freshly completed bar whose
# KC middle turned against the position and closed across that middle releases the shield.
KC_FLIP_TURN_BARS = 3
KC_FLIP_EXIT_REASONS = {
    "SHORT": "EXIT_SHORT_KC_FLIP_CLOSED_ABOVE_MIDDLE",
    "LONG": "EXIT_LONG_KC_FLIP_CLOSED_BELOW_MIDDLE",
}
TRUE_OUTER_PIVOT_EXIT_REASONS = {
    "SHORT": "EXIT_SHORT_TRUE_VALLEY_BOTTOM",
    "LONG": "EXIT_LONG_TRUE_PEAK_TOP",
}
FAST_TOP_EXIT_REASONS = {
    "SHORT": "EXIT_SHORT_FAST_BOTTOM_BREAK_MA15_MID",
    "LONG": "EXIT_LONG_FAST_TOP_BREAK_MA15_MID",
}
PROFIT_LOCK_GATE_REASONS = {
    "SHORT": "EXIT_BY_STAGED_PROFIT_LOCK_TIER1",
    "LONG": "EXIT_BY_STAGED_PROFIT_LOCK_TIER1",
}
PROFIT_LOCK_TOKENS = (
    "STAGED_PROFIT_LOCK", "PROFIT_LOCK", "RATCHET_PROFIT_LOCK",
    "TRAILING_PROFIT_LOCK", "EXIT_BY_SCHEME1_PROFIT_LOCK",
)

ALLOW, HOLD, DEFER = "ALLOW", "HOLD", "DEFER"


class ExitVerdict(NamedTuple):
    verdict: str            # ALLOW / HOLD / DEFER (DEFER = not governed, use existing chain)
    code: str
    evidence: dict

    @property
    def allowed(self) -> bool:
        return self.verdict == ALLOW


def _float(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _minute(value: float) -> int:
    seconds = value / 1000.0 if value >= 1e11 else value
    return int(seconds // 60)


OWN_STRUCTURE_REASONS = frozenset(
    {"STRUCTURE_EXIT_PROBE", *(r.upper() for r in STRUCTURE_EXIT_REASONS.values()),
     *(r.upper() for r in KC_MIDDLE_MA15_BREAK_REASONS.values()),
     *(r.upper() for r in KC_FLIP_EXIT_REASONS.values()),
     *(r.upper() for r in TRUE_OUTER_PIVOT_EXIT_REASONS.values()),
     *(r.upper() for r in FAST_TOP_EXIT_REASONS.values()),
     "EXIT_BY_STAGED_PROFIT_LOCK_TIER1",
     "EXIT_BY_STAGED_PROFIT_LOCK_TIER2",
     "EXIT_BY_SCHEME1_PROFIT_LOCK_MA3_MA15",
     "PROFIT_LOCK"}
)
WATERFALL_EXIT_TOKENS = (
    "EXIT_BY_EXTREME_WATERFALL", "WATERFALL_DROP", "LIVE_ADVERSE_WATERFALL",
)
OWN_DISASTER_REASONS = frozenset(
    r.upper() for r in (*DISASTER_LOSS_REASONS.values(), *DISASTER_RAIL_REASONS.values())
)
ORDINARY_PULLBACK_EXIT_TOKENS = ("PULLBACK", "RETRACE", "RETRACEMENT")


def _kc_flip_turn_candidate(symbol: str, side: str, closed: Any, current_time: float) -> bool:
    """Track the preconditions that a valid KC turn is underway without requiring full release."""
    if closed is None or len(closed) < KC_FLIP_TURN_BARS + 1:
        return False
    current = closed.iloc[-1]
    previous = closed.iloc[-2]
    stamp = _float(current.get("timestamp"))
    atr = _float(previous.get("atr"))
    middle = _float(current.get("kc_middle"))
    if stamp is None or atr is None or middle is None or atr <= 0:
        return False
    bar_minute = _minute(stamp)
    if bar_minute != int(current_time // 60) - 1:
        return False
    mids = [_float(closed.iloc[i].get("kc_middle")) for i in range(-KC_FLIP_TURN_BARS - 1, 0)]
    if any(v is None for v in mids) or len(mids) < KC_FLIP_TURN_BARS:
        return False
    sign = 1 if side == "LONG" else -1
    return all(-sign * (later - earlier) > 0 for earlier, later in zip(mids, mids[1:]))


def _kc_flip_release(
    symbol: str, side: str, closed: Any, opened: float, is_bar_closed: Any,
    current_time: float,
) -> bool:
    """Confirmed turn on a fresh completed bar (Owner 2026-11-03, pullbacks keep holding):
    KC middle moved against the side on each of the last KC_FLIP_TURN_BARS completed bars,
    and the normal 0.50 ATR / fresh MA5-MA15 / opposite-body exit lock also passes."""
    if is_bar_closed is False or closed is None or len(closed) < KC_FLIP_TURN_BARS + 1:
        return False
    current = closed.iloc[-1]
    previous = closed.iloc[-2]
    stamp = _float(current.get("timestamp"))
    opened_bar = _float(current.get("open"))
    close = _float(current.get("close"))
    ma5, ma15 = _float(current.get("ma5")), _float(current.get("ma15"))
    ma5_prev, ma15_prev = _float(previous.get("ma5")), _float(previous.get("ma15"))
    atr = _float(previous.get("atr"))
    mids = [_float(closed.iloc[i].get("kc_middle")) for i in range(-KC_FLIP_TURN_BARS - 1, 0)]
    middle = _float(current.get("kc_middle"))
    values = [stamp, opened_bar, close, ma5, ma15, ma5_prev, ma15_prev, atr, middle, *mids]
    if any(v is None for v in values) or min(values) <= 0:
        return False
    bar_minute = _minute(stamp)
    if bar_minute != int(current_time // 60) - 1 or bar_minute - _minute(opened) < MIN_BARS_HELD:
        return False
    from core.gates.master_gate_funnel import meme_distance_atr

    sign = 1 if side == "LONG" else -1
    buffer_line = middle - sign * STRUCTURE_BUFFER_ATR * meme_distance_atr(symbol, atr)
    return (
        all(-sign * (later - earlier) > 0 for earlier, later in zip(mids, mids[1:]))
        and -sign * (close - buffer_line) > 0
        and -sign * (close - opened_bar) > 0
        and -sign * (ma5_prev - ma15_prev) <= 0
        and -sign * (ma5 - ma15) > 0
        and -sign * (ma5 - ma5_prev) > 0
    )


def _outer_rail_true_pivot_release(
    symbol: str, side: str, closed: Any, opened: float, entry: float, quote: float,
    is_bar_closed: Any, current_time: float,
) -> bool:
    """Confirm a 3-bar true valley/peak bottom (真谷底/真峰頂) in profit or reversal."""
    if is_bar_closed is False or closed is None or len(closed) < 3:
        return False
    b1, b2, b3 = closed.iloc[-3], closed.iloc[-2], closed.iloc[-1]
    stamp1 = _float(b1.get("timestamp"))
    stamp3 = _float(b3.get("timestamp"))
    if stamp1 is None or stamp3 is None:
        return False
    bar_minute = _minute(stamp3)
    opened_minute = _minute(opened)
    if bar_minute != int(current_time // 60) - 1:
        return False
    o1, h1, l1, c1 = (_float(b1.get(k)) for k in ("open", "high", "low", "close"))
    o2, h2, l2, c2 = (_float(b2.get(k)) for k in ("open", "high", "low", "close"))
    o3, h3, l3, c3 = (_float(b3.get(k)) for k in ("open", "high", "low", "close"))
    kc_u1, kc_l1 = _float(b1.get("kc_upper")), _float(b1.get("kc_lower"))
    kc_u2, kc_l2 = _float(b2.get("kc_upper")), _float(b2.get("kc_lower"))
    ma5_2, ma5_3 = _float(b2.get("ma5")), _float(b3.get("ma5"))
    atr2 = _float(b2.get("atr"))
    values = [
        o1, h1, l1, c1, o2, h2, l2, c2, o3, h3, l3, c3,
        kc_u1, kc_l1, kc_u2, kc_l2, ma5_2, ma5_3, atr2,
    ]
    if any(v is None for v in values) or min(values) <= 0:
        return False
    mid2 = (o2 + c2) / 2.0
    # True Pivot Check (AGENTS.md 核心授權規範):
    # 1. 持倉呼吸防洗鐵律：開倉後若尚未實質獲利展開（多單最高浮動漲幅 < 0.5 ATR 且 ROE < 5%；空單同理），
    #    價格在成本價附近的 1m 小陰小陽或均線盤中抖動回踩，100% 判定為「成本區間正常呼吸回踩（PULLBACK）」，一票否決平倉！
    # 2. 方案 A 真峰谷獲利了結（True Pivot Exit）：
    #    - 前提條件：開倉後已實質獲利展開（多單 peak_gain >= 0.5 ATR 或 peak_roe >= 5% 且出場仍保有淨利 c3 > entry 與 quote > entry；空單同理）。
    #    - 峰值位置：必須衝破或觸碰同側 KC 外軌（多單 h2 >= kc_u2 或 h1 >= kc_u1；空單 l2 <= kc_l2 或 l1 <= kc_l1），或實體遠離中軌確立波段頂/底部。
    #    - 確認棒反轉：多單三點頂部樞紐（h2 > h1 且 h2 > h3，第 3 棒收陰跌破中點且跌破 MA5）；空單三點底部樞紐（l2 < l1 且 l2 < l3，第 3 棒收陽突破中點且站上 MA5）。
    if side == "SHORT":
        valley_low = min(l1, l2)
        gain_atr = (entry - valley_low) / atr2
        gain_roe = (entry - valley_low) / entry * 5.0
        profit_expanded = gain_atr >= 0.5 or gain_roe >= 0.05
        at_outer_rail = (l2 <= kc_l2 or l1 <= kc_l1)
        in_profit = c3 < entry and quote < entry

        is_true_valley = (
            at_outer_rail
            and l2 < l1
            and l2 < l3
            and c3 > o3
            and c3 > mid2
            and c3 > ma5_3
            and ma5_3 > ma5_2
            and profit_expanded
            and in_profit
        )
        return is_true_valley

    if side == "LONG":
        peak_high = max(h1, h2)
        gain_atr = (peak_high - entry) / atr2
        gain_roe = (peak_high - entry) / entry * 5.0
        profit_expanded = gain_atr >= 0.5 or gain_roe >= 0.05
        at_outer_rail = (h2 >= kc_u2 or h1 >= kc_u1)
        in_profit = c3 > entry and quote > entry

        is_true_peak = (
            at_outer_rail
            and h2 > h1
            and h2 > h3
            and c3 < o3
            and c3 < mid2
            and c3 < ma5_3
            and ma5_3 < ma5_2
            and profit_expanded
            and in_profit
        )
        return is_true_peak
    return False


class UltimateShortExitGatekeeper:
    """Highest-priority exit veto; also mirrors the identical rules for LONG."""

    @classmethod
    def evaluate(
        cls,
        symbol: str,
        position: Mapping[str, Any],
        metadata: Mapping[str, Any] | None,
        reason: str,
        price: float,
        *,
        frame: Any = None,
        is_bar_closed: bool | None = None,
        now: float | None = None,
    ) -> ExitVerdict:
        from core.symbol_aliases import is_meme_breakout_symbol

        normalized = str(reason or "").strip().upper()
        if not is_meme_breakout_symbol(symbol):
            return ExitVerdict(DEFER, "NOT_MEME_SYMBOL", {})
        if str(reason or "").strip() == "手動平倉":
            return ExitVerdict(DEFER, "MANUAL_EXIT", {})
        if any(token in normalized for token in ACCOUNT_LEVEL_TOKENS):
            return ExitVerdict(DEFER, "ACCOUNT_LEVEL_EXIT", {})
        side = str(position.get("side") or "").upper()
        evidence: dict = {"symbol": symbol, "side": side, "reason": reason}

        def hold(code: str) -> ExitVerdict:
            logger.info("[VETO_HOLD_%s] ULTIMATE_EXIT_GATE symbol=%s code=%s reason=%s",
                        side, symbol, code, reason)
            if side == "SHORT":
                logger.info("[UI_DISPLAY: 續抱] symbol=%s reason=%s", symbol, code)
            return ExitVerdict(HOLD, code, evidence)

        if any(token in normalized for token in WATERFALL_EXIT_TOKENS):
            from core.services.exits.abnormal_candle_exit import live_adverse_waterfall_evidence

            waterfall_evidence = live_adverse_waterfall_evidence(
                position, frame, price,
            )
            if waterfall_evidence is None:
                # 🚨 檢查盤中極速瀑布緊急出場通道 check_waterfall_emergency_exit (100% 豁免 is_bar_closed)
                is_emerg, emerg_reason, emerg_ev = cls.check_waterfall_emergency_exit(
                    symbol, position, metadata, price, frame=frame
                )
                if is_emerg:
                    logger.warning(
                        "[ULTIMATE_EXIT_ALLOW] symbol=%s side=%s code=%s (via emergency waterfall) "
                        "evidence=%s", symbol, side, emerg_reason, emerg_ev,
                    )
                    return ExitVerdict(ALLOW, emerg_reason, emerg_ev)
                return hold("VETO_WATERFALL_LIVE_BODY_OR_STRUCTURE_NOT_CONFIRMED")
            waterfall_evidence.update({"symbol": symbol, "reason": reason})
            logger.warning(
                "[ULTIMATE_EXIT_ALLOW] symbol=%s side=%s code=EXIT_BY_EXTREME_WATERFALL "
                "body=%s atr=%s quote=%s kc_middle=%s",
                symbol, side, waterfall_evidence["body"], waterfall_evidence["atr"],
                waterfall_evidence["quote"], waterfall_evidence["kc_middle"],
            )
            return ExitVerdict(ALLOW, "EXIT_BY_EXTREME_WATERFALL", waterfall_evidence)

        if any(token in normalized for token in ORDINARY_PULLBACK_EXIT_TOKENS):
            return hold("VETO_HOLD_ORDINARY_PULLBACK_UNTIL_STRUCTURE_BREAK")

        def allow(code: str) -> ExitVerdict:
            if (
                normalized not in OWN_STRUCTURE_REASONS | OWN_DISASTER_REASONS
                and not any(token in normalized for token in PROFIT_LOCK_TOKENS)
            ):
                # Gate-only authority: foreign labels (e.g. numeric hard SL) keep their own policy.
                return ExitVerdict(DEFER, "VETO_PASSED_LEGACY_AUTHORITY_REQUIRED", evidence)
            evidence["exit_type"] = code
            logger.warning("[ULTIMATE_EXIT_ALLOW] symbol=%s side=%s code=%s reason=%s",
                           symbol, side, code, reason)
            return ExitVerdict(ALLOW, code, evidence)

        if side not in ("LONG", "SHORT"):
            return hold("VETO_EXIT_INVALID_SIDE")
        sign = 1 if side == "LONG" else -1
        quote = _float(price)
        entry = _float(position.get("entry_price"))
        opened = _float(position.get("open_timestamp"))
        if opened is None:
            opened = _float((metadata or {}).get("open_timestamp"))
        if not quote or quote <= 0 or not entry or entry <= 0 or not opened or opened <= 0:
            return hold("VETO_EXIT_INVALID_POSITION_DATA")

        try:
            closed = closed_entry_candles(frame) if frame is not None else None
        except Exception as exc:  # malformed frame must fail closed
            logger.error("ULTIMATE_EXIT_FRAME_ERROR symbol=%s error=%s", symbol, exc)
            closed = None
        if closed is None or len(closed) < 1:
            from core.services.exits.authorized_position_exit import strategy_exit_bar_block_reason
            entry_bar = strategy_exit_bar_block_reason(position, metadata or {})
            return hold(entry_bar or "VETO_EXIT_CLOSED_DATA_UNAVAILABLE")
        from core.gates.master_gate_funnel import meme_distance_atr

        # Disaster 1: adverse distance from entry beyond 3.0 ATR (live quote allowed).
        # ATR is the last completed bar before the live candle, so it works on the entry candle.
        live_atr = _float(closed.iloc[-1].get("atr"))
        if live_atr is not None and live_atr > 0:
            disaster_atr = meme_distance_atr(symbol, live_atr)
            loss_atr = -sign * (quote - entry) / disaster_atr
            evidence["loss_atr"] = round(loss_atr, 4)
            if loss_atr > DISASTER_LOSS_ATR:
                return allow(DISASTER_LOSS_REASONS[side])

        # 🚨 盤中極速大瀑布緊急避險出口 (Waterfall Emergency Exit - 100% 豁免 is_bar_closed 收線限制)
        # 當候選平倉原因為 WATERFALL 相關、或盤中報價觸發瀑布熔斷且不是普通探針結構探測時放行
        if any(token in normalized for token in WATERFALL_EXIT_TOKENS) or (
            normalized not in ("STRUCTURE_EXIT_PROBE",) and is_bar_closed is not False
        ):
            is_emerg_wf, emerg_wf_reason, emerg_wf_ev = cls.check_waterfall_emergency_exit(
                symbol, position, metadata, quote, frame=frame
            )
            if is_emerg_wf:
                logger.warning(
                    "[ULTIMATE_EXIT_ALLOW] symbol=%s side=%s code=%s (intra-bar emergency circuit breaker) evidence=%s",
                    symbol, side, emerg_wf_reason, emerg_wf_ev,
                )
                return ExitVerdict(ALLOW, emerg_wf_reason, emerg_wf_ev)
        if len(closed) < 2:
            return hold("VETO_EXIT_CLOSED_DATA_UNAVAILABLE")
        current, previous = closed.iloc[-1], closed.iloc[-2]
        fields = {}
        for key in ("timestamp", "open", "high", "low", "close", "ma5", "ma15",
                    "kc_upper", "kc_middle", "kc_lower"):
            fields[key] = _float(current.get(key))
        fields["ma5_prev"] = _float(previous.get("ma5"))
        fields["ma15_prev"] = _float(previous.get("ma15"))
        fields["kc_middle_prev"] = _float(previous.get("kc_middle"))
        # Same ATR convention as the entry snapshot: the previous completed bar.
        fields["atr"] = _float(previous.get("atr"))
        if any(v is None or v <= 0 for v in fields.values()):
            return hold("VETO_EXIT_INVALID_CLOSED_DATA")
        atr = meme_distance_atr(symbol, fields["atr"])
        evidence.update({k: fields[k] for k in ("close", "open", "ma5", "ma15", "kc_middle",
                                                 "kc_upper", "kc_lower", "atr")})
        evidence["distance_atr"] = atr

        closed_flag = current.get("is_closed")
        frame_finality = (
            getattr(frame, "attrs", {}).get("entry_finality_verified") is True
        )
        explicitly_closed = (
            closed_flag is True
            or (type(closed_flag).__name__ == "bool_" and bool(closed_flag))
            or frame_finality
        )
        if is_bar_closed is False or not (is_bar_closed is True or explicitly_closed):
            return hold("VETO_EXIT_BAR_NOT_CLOSED")
        current_time = time.time() if now is None else float(now)
        bar_minute = _minute(fields["timestamp"])
        if bar_minute != int(current_time // 60) - 1:
            # Only the freshly completed 1m bar can authorize a trend exit.
            return hold("VETO_EXIT_BAR_NOT_CLOSED")

        close, kc_mid = fields["close"], fields["kc_middle"]
        # Exits are only authorized on a fresh, opposite-signal bar. A position-direction
        # completed bar is a hard hold even when the quote is near the entry.
        if side == "SHORT" and close <= fields["open"]:
            turn_candidate = _kc_flip_turn_candidate(symbol, side, closed, current_time)
            if live_atr is not None and live_atr > 0 and abs(loss_atr) <= COST_SHIELD_ATR and turn_candidate:
                return hold(f"VETO_HOLD_{side}_COST_SHIELD")
            return hold(f"VETO_HOLD_{side}_OPPOSITE_BODY_REQUIRED")
        if side == "LONG" and close >= fields["open"]:
            turn_candidate = _kc_flip_turn_candidate(symbol, side, closed, current_time)
            if live_atr is not None and live_atr > 0 and abs(loss_atr) <= COST_SHIELD_ATR and turn_candidate:
                return hold(f"VETO_HOLD_{side}_COST_SHIELD")
            return hold(f"VETO_HOLD_{side}_OPPOSITE_BODY_REQUIRED")
        # Disaster 2: completed close beyond the opposite KC rail.
        if (side == "SHORT" and close > fields["kc_upper"]) or (
                side == "LONG" and close < fields["kc_lower"]):
            return allow(DISASTER_RAIL_REASONS[side])

        # 🛡️ 核心鐵律：多頭主升浪 100% 強制死抱鐵律 (Trend Wave Hold Gate)
        # 1. 【大趨勢「KC 向上」未跌破 MA15 100% 絕對禁止平多（最高鐵律）】：
        #    當 1m 收盤價仍位於 MA15 上方 (close >= ma15) 時，100% 認定為「多頭主升浪貼軌推升 / 洗盤」，【Gate 100% 傳回 False 阻斷平多】！強制死抱部位讓利潤奔跑！
        # 2. 【MA5 未實質死叉 100% 絕對禁止平多】：
        #    評估平多時，若 MA5 尚未實質向下死叉 MA15 (ma5 >= ma15)，100% 傳回 False 一票否決，嚴禁被單根上影線或回踩小紅 K 過敏騙出場！
        # 3. 【綠 K 陽線 100% 絕對禁止平多】：
        #    當前 1m K 棒收盤若為「實體綠 K / 陽線 (close > open)」，100% 傳回 False 一票否決！
        # 4. 【只有大實體紅 K 實質貫穿跌破 MA15 ＋ MA5 死叉才授權平多】：
        #    唯有同時滿足：(1) 實體紅 K (close < open 且 open - close >= 0.35 * atr)
        #                 (2) 收盤價實質跌破 MA15 (close < ma15) 且跌破 KC 中軌 (close < kc_middle)
        #                 (3) MA5 實質死叉 MA15 (ma5 < ma15)
        #    若不滿足，100% 傳回 False 阻斷平多！
        if side == "LONG":
            if close >= fields["ma15"]:
                return hold("VETO_HOLD_LONG_ABOVE_MA15")
            if fields["ma5"] >= fields["ma15"]:
                return hold("VETO_HOLD_LONG_MA5_ABOVE_MA15")
            if close > fields["open"]:
                return hold("VETO_HOLD_LONG_ON_GREEN_CANDLE")
            is_true_top_break = (
                close < fields["open"]
                and (fields["open"] - close) >= 0.35 * atr
                and close < fields["ma15"]
                and close < fields["kc_middle"]
                and fields["ma5"] < fields["ma15"]
            )
            if not is_true_top_break:
                return hold("VETO_HOLD_LONG_TREND_WAVE_ABOVE_MID")

        # 空頭主降浪 100% 強制死抱鐵律 (Symmetric Anti-Premature Exit Gate)
        if side == "SHORT":
            if close <= fields["ma15"]:
                return hold("VETO_HOLD_SHORT_BELOW_MA15")
            if fields["ma5"] <= fields["ma15"]:
                return hold("VETO_HOLD_SHORT_MA5_BELOW_MA15")
            if close < fields["open"]:
                return hold("VETO_HOLD_SHORT_ON_RED_CANDLE")
            is_true_bottom_break = (
                close > fields["open"]
                and (close - fields["open"]) >= 0.35 * atr
                and close > fields["ma15"]
                and close > fields["kc_middle"]
                and fields["ma5"] > fields["ma15"]
            )
            if not is_true_bottom_break:
                return hold("VETO_HOLD_SHORT_TREND_WAVE_BELOW_MID")

        # 🎯 真峰谷優先放行 (True Valley / True Peak Pivot Exit)
        # 使用者與策略核心鐵律：一旦確認真谷底(或真峰頂)，立即在谷底市價平倉獲利了結！
        # 絕不拖延、絕不走過頭到中軌或反側外軌，100% 豁免持倉滿 5 根 K 棒限制！
        if normalized in OWN_STRUCTURE_REASONS and _outer_rail_true_pivot_release(
            symbol, side, closed, opened, entry, quote, is_bar_closed, current_time,
        ):
            return allow(TRUE_OUTER_PIVOT_EXIT_REASONS[side])

        bars_held = bar_minute - _minute(opened)
        evidence["bars_held"] = bars_held
        if bars_held < MIN_BARS_HELD:
            return hold("VETO_HOLD_MIN_5_COMPLETED_BARS")

        if live_atr is not None and live_atr > 0:
            from core.services.exits.strict_true_pivot import PROFIT_PIVOT_REASONS

            if abs(loss_atr) <= COST_SHIELD_ATR and normalized not in PROFIT_PIVOT_REASONS and not any(
                token in normalized for token in COST_SHIELD_EXEMPT_TOKENS
            ):
                if normalized in OWN_STRUCTURE_REASONS and _kc_flip_release(
                    symbol, side, closed, opened, is_bar_closed, current_time,
                ):
                    return allow(KC_FLIP_EXIT_REASONS[side])
                return hold(f"VETO_HOLD_{side}_COST_SHIELD")

        buffer_line = kc_mid - sign * STRUCTURE_BUFFER_ATR * atr
        evidence["structure_line"] = buffer_line
        beyond_buffer = -sign * (close - buffer_line) > 0

        # 🚀 頂部/底部大實體 K 線貫穿極速平倉通道 (Fast Top / Bottom Exit Channel)
        # 解決龍蝦/PEPE峰頂滯後平多：脫離成本保護盾(Cost Shield)後，當收線時出現大實體逆向K線(陰線下殺 Body >= 0.35 ATR 貫穿 KC 中軌與 MA15 且 MA5 死叉 MA15)，
        # 100% 豁免後續 0.50 ATR 緩衝區，直接在頂部第一時間高位授權平倉！
        candle_body = abs(close - fields["open"])
        fresh_ma_cross = -sign * (fields["ma5_prev"] - fields["ma15_prev"]) <= 0 and -sign * (fields["ma5"] - fields["ma15"]) > 0
        ma_reversed = -sign * (fields["ma5"] - fields["ma15"]) > 0 and -sign * (fields["ma5"] - fields["ma5_prev"]) > 0
        fast_reversal = (
            -sign * (close - fields["open"]) > 0
            and candle_body >= 0.35 * atr
            and -sign * (close - fields["kc_middle"]) > 0
            and -sign * (close - fields["ma15"]) > 0
            and (fresh_ma_cross or (ma_reversed and not beyond_buffer))
        )
        if fast_reversal and (
            not beyond_buffer
            or normalized in FAST_TOP_EXIT_REASONS.values()
            or "FAST_TOP" in normalized
            or "FAST_BOTTOM" in normalized
        ):
            return allow(FAST_TOP_EXIT_REASONS[side])

        if not beyond_buffer:
            return hold(f"VETO_HOLD_{side}_WITHIN_050_ATR_BUFFER")
        ma5, ma15, ma5_prev = fields["ma5"], fields["ma15"], fields["ma5_prev"]
        # Fresh cross only: MA5 must have been on the position side of MA15 on the previous bar.
        fresh_cross = -sign * (ma5_prev - fields["ma15_prev"]) <= 0
        if not (fresh_cross and -sign * (ma5 - ma15) > 0 and -sign * (ma5 - ma5_prev) > 0):
            return hold(f"VETO_HOLD_{side}_MA5_MA15_NOT_CROSSED")
        if not -sign * (close - fields["open"]) > 0:
            return hold(f"VETO_HOLD_{side}_OPPOSITE_BODY_REQUIRED")
        return allow(STRUCTURE_EXIT_REASONS[side])

    @classmethod
    def can_execute_exit(cls, symbol, position, metadata, reason, price, *,
                         frame=None, is_bar_closed=None, now=None) -> bool:
        """True only for an authoritative ALLOW; HOLD and DEFER both return False."""
        return cls.evaluate(symbol, position, metadata, reason, price, frame=frame,
                            is_bar_closed=is_bar_closed, now=now).allowed

    @classmethod
    def check_waterfall_emergency_exit(
        cls,
        symbol: str,
        position: Mapping[str, Any],
        metadata: Mapping[str, Any] | None,
        price: float,
        *,
        frame: Any = None,
    ) -> Tuple[bool, str, Dict[str, Any]]:
        """
        🚨 盤中極速大瀑布緊急避險出口 (100% 豁免 is_bar_closed，毫秒級防崩盤熔斷)
        做多時：
          (1) 盤中最新報價 (Quote) 垂直貫穿跌破 KC 中軌與 MA15 深度達 > 0.5 ATR
          (2) 單根盤中未收線 K 棒下殺幅度 (Live Candle Drop) 超過 1.2 ATR
        做空時：
          (1) 盤中最新報價 (Quote) 垂直爆拉突破 KC 中軌與 MA15 深度達 > 0.5 ATR
          (2) 單根盤中未收線 K 棒拉升幅度 (Live Candle Pump) 超過 1.2 ATR
        """
        evidence: Dict[str, Any] = {"symbol": symbol, "quote": price}
        if frame is None or not isinstance(frame, pd.DataFrame) or len(frame) < 2:
            return False, "INSUFFICIENT_FRAME", evidence

        side = str((position or {}).get("side") or "").upper()
        if side not in ("LONG", "SHORT"):
            return False, "INVALID_SIDE", evidence

        try:
            curr_candle = frame.iloc[-1]
            prev_candle = frame.iloc[-2]
            live_open = _float(curr_candle.get("open")) or price
            atr_val = _float(prev_candle.get("atr")) or _float(curr_candle.get("atr")) or 0.0
            from core.gates.master_gate_funnel import meme_distance_atr
            dist_atr = meme_distance_atr(symbol, atr_val) if atr_val > 0 else 0.0
            if dist_atr <= 0:
                return False, "INVALID_ATR", evidence

            kc_mid = _float(curr_candle.get("kc_middle")) or _float(prev_candle.get("kc_middle"))
            ma15 = _float(curr_candle.get("ma15")) or _float(prev_candle.get("ma15"))
            if kc_mid is None or ma15 is None:
                return False, "INVALID_STRUCTURE_INDICATORS", evidence

            evidence.update({
                "side": side,
                "live_open": live_open,
                "dist_atr": dist_atr,
                "kc_middle": kc_mid,
                "ma15": ma15,
            })

            if side == "LONG":
                # 條件 1：即時價貫穿跌破 KC 中軌與 MA15 深度 > 0.5 ATR
                penetrates_mid_ma15 = (price < (kc_mid - 0.5 * dist_atr)) and (price < (ma15 - 0.5 * dist_atr))
                # 條件 2：單根盤中 K 棒下殺幅度超過 1.2 ATR
                single_bar_dump = (live_open - price) >= 1.2 * dist_atr

                if penetrates_mid_ma15 or single_bar_dump:
                    reason = "EXIT_BY_EXTREME_WATERFALL"
                    evidence["trigger"] = "PENETRATES_MID_MA15" if penetrates_mid_ma15 else "SINGLE_BAR_DUMP"
                    return True, reason, evidence

            elif side == "SHORT":
                # 條件 1：即時價貫穿突破 KC 中軌與 MA15 深度 > 0.5 ATR
                penetrates_mid_ma15 = (price > (kc_mid + 0.5 * dist_atr)) and (price > (ma15 + 0.5 * dist_atr))
                # 條件 2：單根盤中 K 棒拉升幅度超過 1.2 ATR
                single_bar_pump = (price - live_open) >= 1.2 * dist_atr

                if penetrates_mid_ma15 or single_bar_pump:
                    reason = "EXIT_BY_EXTREME_WATERFALL"
                    evidence["trigger"] = "PENETRATES_MID_MA15" if penetrates_mid_ma15 else "SINGLE_BAR_PUMP"
                    return True, reason, evidence

            return False, "NO_WATERFALL_CRITERIA_MET", evidence

        except Exception as exc:
            logger.error("WATERFALL_EMERGENCY_EXIT_CHECK_ERROR symbol=%s error=%s", symbol, exc)
            return False, f"ERROR_{exc}", evidence

    @classmethod
    def structure_exit_candidate(cls, symbol, position, metadata, price, frame, *, now=None):
        """Exit producer for the main loop: reason string when the gate itself authorizes."""
        verdict = cls.evaluate(symbol, position, metadata, "STRUCTURE_EXIT_PROBE", price,
                               frame=frame, now=now)
        return verdict.code if verdict.allowed else None
