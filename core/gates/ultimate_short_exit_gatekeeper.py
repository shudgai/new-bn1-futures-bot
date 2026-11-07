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
    "STAGE1_LADDER_STOP", "STAGE2_LADDER_STOP", "STAGE3_LADDER_STOP", "STAGE4_LADDER_STOP",
    "TRAILING_STOP", "BREAKEVEN_PROTECTION", "LADDER_STOP",
)

BOTTOM_REVERSAL_EXIT_REASONS = {
    "SHORT": "EXIT_SHORT_BOTTOM_GREEN_CANDLE_MA5",
    "LONG": "EXIT_LONG_TOP_RED_CANDLE_MA5",
}
EMERGENCY_CIRCUIT_BREAKER_REASONS = {
    "SHORT": "EXIT_SHORT_EMERGENCY_SURGE",
    "LONG": "EXIT_LONG_EMERGENCY_WATERFALL",
}

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
     *(r.upper() for r in BOTTOM_REVERSAL_EXIT_REASONS.values()),
     "EXIT_BY_STAGED_PROFIT_LOCK_TIER1",
     "EXIT_BY_STAGED_PROFIT_LOCK_TIER2",
     "EXIT_BY_SCHEME1_PROFIT_LOCK_MA3_MA15",
     "PROFIT_LOCK"}
)
WATERFALL_EXIT_TOKENS = (
    "EXIT_BY_EXTREME_WATERFALL", "WATERFALL_DROP", "LIVE_ADVERSE_WATERFALL",
)
OWN_DISASTER_REASONS = frozenset(
    r.upper() for r in (
        *DISASTER_LOSS_REASONS.values(),
        *DISASTER_RAIL_REASONS.values(),
        *EMERGENCY_CIRCUIT_BREAKER_REASONS.values(),
    )
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

        # 空單真谷底平倉鐵律（True Valley Exit）：
        # 1. 必須收出具有明確向上動能的陽線 (c3 - o3 >= 0.35 * atr2)
        # 2. 必須強勢收盤站上 MA5 (c3 > ma5_3) 且站上中間棒高點或實體中點
        # 3. 谷底探底顯著性（Prominence）與防回踩過敏：
        #    - 中間棒 Low 必須明確探底：(l1 - l2 >= 0.45 * atr2) 或實體下殺 (o2 - c2 >= 0.45 * atr2) 或衝出下軌深度 (kc_l2 - l2 >= 0.45 * atr2)。
        #    - 若中間棒僅為扁平十字線 (Doji) 或探底幅度微弱 (< 0.45 * atr2)，確認棒 c3 必須實質突破左棒起跌高點 (c3 > h1)，
        #      否則 100% 判定為「下跌途中的箱體震盪或回踩確認 (PULLBACK)」，絕非真谷底！
        is_prominent_valley = (
            (l1 - l2) >= 0.45 * atr2
            or (o2 - c2) >= 0.45 * atr2
            or (kc_l2 - l2) >= 0.45 * atr2
            or c3 > h1
        )
        is_strong_rebound_candle = (c3 - o3) >= 0.35 * atr2
        is_true_valley = (
            at_outer_rail
            and l2 < l1
            and l2 < l3
            and is_prominent_valley
            and c3 > o3
            and is_strong_rebound_candle
            and c3 > mid2
            and c3 > ma5_3
            and (ma5_3 > ma5_2 or c3 > h2)
            and profit_expanded
            and in_profit
        )
        return is_true_valley

    if side == "LONG":
        ma15_3 = _float(b3.get("ma15"))
        if ma15_3 is not None and c3 >= ma15_3 and ma5_3 >= ma15_3 and (c3 - entry) < 1.0 * atr2:
            return False
        peak_high = max(h1, h2)
        gain_atr = (peak_high - entry) / atr2
        gain_roe = (peak_high - entry) / entry * 5.0
        profit_expanded = gain_atr >= 0.5 or gain_roe >= 0.05
        at_outer_rail = (h2 >= kc_u2 or h1 >= kc_u1)
        in_profit = c3 > entry and quote > entry

        # 多單真峰頂平倉鐵律（True Peak Exit）：
        # 1. 若 1m 收盤價仍位於 MA15 上方、MA5 未死叉 MA15 且仍在 1.0 ATR 成本保護區內，100% 認定為主升浪回踩洗盤 (如 10:48 之 0.0050025)，嚴禁平多！
        # 2. 必須收出具有明確向下下殺動能的陰線 (o3 - c3 >= 0.35 * atr2) 且跌破 MA5、MA5 明確下彎
        # 3. 峰頂衝頂顯著性（Prominence）與防回踩過敏：
        #    - 中間棒 High 必須明確衝頂：(h2 - h1 >= 0.45 * atr2) 或實體拉升 (c2 - o2 >= 0.45 * atr2) 或衝出上軌深度 (h2 - kc_u2 >= 0.45 * atr2)。
        #    - 若中間棒僅為扁平十字線 (Doji) 或衝頂幅度微弱 (< 0.45 * atr2)，確認棒 c3 必須實質跌破左棒起漲低點 (c3 < l1)，
        #      否則 100% 判定為「上漲途中的箱體震盪或回踩確認 (PULLBACK)」，絕非真峰頂！
        is_prominent_peak = (
            (h2 - h1) >= 0.45 * atr2
            or (c2 - o2) >= 0.45 * atr2
            or (h2 - kc_u2) >= 0.45 * atr2
            or c3 < l1
        )
        is_strong_drop_candle = (o3 - c3) >= 0.35 * atr2
        is_true_peak = (
            at_outer_rail
            and h2 > h1
            and h2 > h3
            and is_prominent_peak
            and c3 < o3
            and is_strong_drop_candle
            and c3 < mid2
            and c3 < ma5_3
            and (ma5_3 < ma5_2 or c3 < l2)
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
                return hold("VETO_WATERFALL_LIVE_BODY_OR_STRUCTURE_NOT_CONFIRMED")
            waterfall_evidence.update({"symbol": symbol, "reason": reason})
            logger.warning(
                "[ULTIMATE_EXIT_ALLOW] symbol=%s side=%s code=EXIT_BY_EXTREME_WATERFALL "
                "body=%s atr=%s quote=%s kc_middle=%s",
                symbol, side, waterfall_evidence["body"], waterfall_evidence["atr"],
                waterfall_evidence["quote"], waterfall_evidence["kc_middle"],
            )
            return ExitVerdict(ALLOW, "EXIT_BY_EXTREME_WATERFALL", waterfall_evidence)

        if normalized in EMERGENCY_CIRCUIT_BREAKER_REASONS.values() or any(
            token in normalized for token in ("EMERGENCY_WATERFALL", "EMERGENCY_SURGE", "WATERFALL_CIRCUIT_BREAKER")
        ):
            is_emerg_wf, emerg_wf_reason, emerg_wf_ev = cls.check_waterfall_emergency_exit(
                symbol, position, metadata, price, frame=frame
            )
            if is_emerg_wf:
                logger.warning(
                    "[ULTIMATE_EXIT_ALLOW] symbol=%s side=%s code=%s (intra-bar emergency circuit breaker) evidence=%s",
                    symbol, side, emerg_wf_reason, emerg_wf_ev,
                )
                return ExitVerdict(ALLOW, emerg_wf_reason, emerg_wf_ev)
            return hold("VETO_EMERGENCY_CIRCUIT_BREAKER_NOT_CONFIRMED")

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
        loss_atr = 0.0
        if live_atr is not None and live_atr > 0:
            disaster_atr = meme_distance_atr(symbol, live_atr)
            loss_atr = -sign * (quote - entry) / disaster_atr
            evidence["loss_atr"] = round(loss_atr, 4)
            if loss_atr > DISASTER_LOSS_ATR:
                return allow(DISASTER_LOSS_REASONS[side])

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

        # 🎯 階梯鎖利與移動止盈優先放行 (Staged Profit Lock & Trailing Stop Exit)
        # 核心鐵律：已展開獲利且達到階梯鎖利/保本防守價，100% 豁免 MA15/KC中軌趨勢續抱一票否決，落袋保利！
        is_profit_lock_reason = any(token in normalized for token in PROFIT_LOCK_TOKENS)
        is_net_profit_favorable = (side == "LONG" and quote >= entry * 1.001) or (side == "SHORT" and quote <= entry * 0.999)
        if is_profit_lock_reason and is_net_profit_favorable:
            return allow(normalized)

        # 🎯 真峰谷優先放行 (True Valley / True Peak Pivot Exit - 優先於趨勢波段續抱審查)
        # 核心鐵律：一旦確認真谷底(或真峰頂)，立即在谷底/峰頂市價平倉獲利了結！
        # 絕不拖延、絕不坐過山車回到中軌或反側外軌，一票否決「健康回踩續抱 (VETO_HOLD)」，100% 豁免持倉滿 5 根限制！
        if normalized in OWN_STRUCTURE_REASONS and _outer_rail_true_pivot_release(
            symbol, side, closed, opened, entry, quote, is_bar_closed, current_time,
        ):
            return allow(TRUE_OUTER_PIVOT_EXIT_REASONS[side])

        # ⚡ 通道 A：真峰谷/V轉「最高優先級極速平倉與反手」(True Peak/Valley Override)
        # 核心鐵律：持倉防洗！開倉後不要隨便平倉，只有當波段實質展開獲利 (peak_gain >= 0.5 ATR 或 peak_roe >= 5%)，
        # 且當前確實處於正淨利獲利區 (空單 quote < entry，多單 quote > entry)，才放行谷底陽線/頂部陰線極速平倉！
        # 100% 豁免 MA15 / KC 中軌到達限制與持倉滿 5 根限制！
        raw_atr = fields["atr"]
        pos_trough = _float(position.get("trough_price") or position.get("lowest_price"))
        pos_peak = _float(position.get("highest_price") or position.get("peak_price"))
        try:
            open_min = _minute(opened)
            held_bars = closed[closed["timestamp"].apply(_minute) >= open_min]
            bars_low = float(held_bars["low"].min()) if len(held_bars) > 0 else fields["low"]
            bars_high = float(held_bars["high"].max()) if len(held_bars) > 0 else fields["high"]
        except Exception:
            bars_low = fields["low"]
            bars_high = fields["high"]
        trough_candidates = [v for v in (pos_trough, bars_low, fields["low"], close, quote) if v is not None and v > 0]
        peak_candidates = [v for v in (pos_peak, bars_high, fields["high"], close, quote) if v is not None and v > 0]
        trough_val = min(trough_candidates) if trough_candidates else entry
        peak_val = max(peak_candidates) if peak_candidates else entry

        buffer_line = kc_mid - sign * STRUCTURE_BUFFER_ATR * atr
        evidence["structure_line"] = buffer_line
        beyond_buffer = -sign * (close - buffer_line) > 0
        fresh_ma_cross = -sign * (fields["ma5_prev"] - fields["ma15_prev"]) <= 0 and -sign * (fields["ma5"] - fields["ma15"]) > 0
        full_structure_break_ready = (
            beyond_buffer
            and fresh_ma_cross
            and -sign * (close - fields["ma15"]) > 0
            and -sign * (quote - fields["ma15"]) > 0
            and abs(loss_atr) > COST_SHIELD_ATR
            and (bar_minute - _minute(opened)) >= MIN_BARS_HELD
        )

        # 1. 【真谷底 2 點成立 ➔ 100% 瞬間平空 (True Bottom Exit - 取消 Body >= 0.35 ATR 限制，100% 豁免 KC 中軌限制)】：
        #    - 審查條件（必須同時成立）：
        #            (1) 1m 收盤價實質站上 MA5 (close > ma5) 且站上 MA15 (close > ma15)
        #            (2) MA5 明確向上拐頭 (ma5 > ma5_prev) 且金叉 MA15 (ma5 > ma15)
        #            (3) 持有空單且已展開波段獲利，或已滿足真谷底極值
        #    🚨【空頭主降浪一票否決】：若 1m 收盤價仍位於 MA5 下方或 MA15 下方，或 MA5 未金叉 MA15，
        #       100% 判定為「空頭順勢壓制 / 正常反彈洗盤」，100% 傳回 False (HOLD) 阻斷提前平空！
        if side == "SHORT" and not full_structure_break_ready:
            gain_atr_short = (entry - trough_val) / raw_atr if raw_atr > 0 else 0.0
            gain_roe_short = (entry - trough_val) / entry * 5.0 if entry > 0 else 0.0
            profit_expanded_short = gain_atr_short >= 0.5 or gain_roe_short >= 0.05
            in_profit_short = (quote < entry and close < entry) or (gain_atr_short >= 1.5 and close < entry)
            has_bottom_extremum = (
                pos_trough is not None
                or trough_val < fields["kc_lower"]
            )
            # 必須實質站上 MA5 與 MA15，且 MA5 向上拐頭並金叉 MA15
            is_ma15_and_ma5_broken = (
                close > fields["ma5"]
                and (close > fields["ma15"] or fields["ma5"] > fields["ma15"])
                and fields["ma5"] > fields["ma5_prev"]
            )
            is_bottom_green_candle = (
                profit_expanded_short
                and in_profit_short
                and has_bottom_extremum
                and close > fields["open"]
                and is_ma15_and_ma5_broken
            )
            if is_bottom_green_candle and (
                normalized in OWN_STRUCTURE_REASONS
                or "BOTTOM" in normalized
                or "VALLEY" in normalized
                or "REVERSAL" in normalized
            ):
                return allow(BOTTOM_REVERSAL_EXIT_REASONS[side])

        # 2. 【真峰頂 2 點成立 ➔ 100% 瞬間平多 (True Top Exit - 取消 Body >= 0.35 ATR 限制，100% 豁免 KC 中軌限制)】：
        #    - 審查條件（必須同時成立）：
        #            (1) 1m 收盤價實質跌破 MA5 (close < ma5) 且跌破 MA15 (close < ma15)
        #            (2) MA5 明確向下拐頭 (ma5 < ma5_prev) 且死叉 MA15 (ma5 < ma15)
        #            (3) 持有多單且已展開波段獲利，或已滿足真峰頂極值
        #    🚨【多頭主升浪一票否決】：若 1m 收盤價仍位於 MA5 上方或 MA15 上方，或 MA5 未死叉 MA15，
        #       100% 判定為「多頭順勢支撐 / 正常回踩洗盤」，100% 傳回 False (HOLD) 阻斷提前平多！
        if side == "LONG" and not full_structure_break_ready:
            gain_atr_long = (peak_val - entry) / raw_atr if raw_atr > 0 else 0.0
            gain_roe_long = (peak_val - entry) / entry * 5.0 if entry > 0 else 0.0
            profit_expanded_long = gain_atr_long >= 0.5 or gain_roe_long >= 0.05
            in_profit_long = (quote > entry and close > entry) or (gain_atr_long >= 1.5 and close > entry)
            has_top_extremum = (
                pos_peak is not None
                or peak_val > fields["kc_upper"]
            )
            # 必須實質跌破 MA5 與 MA15，且 MA5 向下拐頭並死叉 MA15
            is_ma15_and_ma5_broken = (
                close < fields["ma5"]
                and (close < fields["ma15"] or fields["ma5"] < fields["ma15"])
                and fields["ma5"] < fields["ma5_prev"]
            )
            is_top_red_candle = (
                profit_expanded_long
                and in_profit_long
                and has_top_extremum
                and close < fields["open"]
                and is_ma15_and_ma5_broken
            )
            if is_top_red_candle and (
                normalized in OWN_STRUCTURE_REASONS
                or "TOP" in normalized
                or "PEAK" in normalized
                or "REVERSAL" in normalized
            ):
                return allow(BOTTOM_REVERSAL_EXIT_REASONS[side])

        # 🛡️ 通道 B：一般波段「未過 MA15 100% 死抱防過敏」(Trend-Holding Immunity)
        # 多頭主升浪 100% 強制死抱鐵律 (Symmetric Anti-Premature Exit Gate)
        # 🚨【核心硬鎖：持多單時未達「真峰頂 2 點」100% 一票否決平多 (Anti-Premature Long Exit)】：
        # 當持有一單多單且大趨勢標頭為「KC 向上 (只評估多單)」時：
        # 只要 1m 收盤價仍位於 MA5 上方 (close_price >= ma5_curr) 或 MA5 仍向上發散 (ma5_curr >= ma5_prev)，
        # 100% 認定為「多頭順勢拉升 / 正常洗盤」，Gate 100% 傳回 False (HOLD) 阻斷平多 API！
        if side == "LONG":
            is_kc_up_header = fields["kc_middle"] > fields["kc_middle_prev"] or str(fields.get("macro_trend", "")).upper() == "BULLISH"
            if is_kc_up_header:
                if close >= fields["ma5"]:
                    return hold("VETO_HOLD_LONG_ABOVE_MA5")
                if fields["ma5"] >= fields["ma5_prev"]:
                    return hold("VETO_HOLD_LONG_MA5_NOT_TURNING_DOWN")
            if close >= fields["ma15"] and fields["ma5"] >= fields["ma15"]:
                return hold("VETO_HOLD_LONG_ABOVE_MA15")
            if close > fields["open"]:
                return hold("VETO_HOLD_LONG_ON_GREEN_CANDLE")

        # 空頭主降浪 100% 強制死抱鐵律 (Symmetric Anti-Premature Exit Gate)
        # 🚨【核心硬鎖：持空單時未達「真谷底 2 點」100% 一票否決平空 (Anti-Premature Short Exit)】：
        # 當持有一單空單且大趨勢標頭為「KC 向下 (只評估空單)」時：
        # 只要 1m 收盤價仍位於 MA5 下方 (close_price <= ma5_curr) 或 MA5 仍向下發散 (ma5_curr <= ma5_prev)，
        # 100% 認定為「空頭順勢下殺 / 正常洗盤」，Gate 100% 傳回 False 阻斷平空 API！
        if side == "SHORT":
            is_kc_down_header = fields["kc_middle"] < fields["kc_middle_prev"] or str(fields.get("macro_trend", "")).upper() == "BEARISH"
            if is_kc_down_header:
                if close <= fields["ma5"]:
                    return hold("VETO_HOLD_SHORT_BELOW_MA5")
                if fields["ma5"] <= fields["ma5_prev"]:
                    return hold("VETO_HOLD_SHORT_MA5_NOT_TURNING_UP")
            if close <= fields["ma15"] and fields["ma5"] <= fields["ma15"]:
                return hold("VETO_HOLD_SHORT_BELOW_MA15")
            if close < fields["open"]:
                return hold("VETO_HOLD_SHORT_ON_RED_CANDLE")

        candle_body = abs(close - fields["open"])
        fresh_ma_cross = -sign * (fields["ma5_prev"] - fields["ma15_prev"]) <= 0 and -sign * (fields["ma5"] - fields["ma15"]) > 0
        ma_reversed = -sign * (fields["ma5"] - fields["ma15"]) > 0 and -sign * (fields["ma5"] - fields["ma5_prev"]) > 0
        wave_favorable_atr = (
            (entry - trough_val) / atr if (side == "SHORT" and atr > 0)
            else ((peak_val - entry) / atr if (side == "LONG" and atr > 0) else 0.0)
        )
        wave_favorable_roe = (
            (entry - trough_val) / entry * 5.0 if (side == "SHORT" and entry > 0)
            else ((peak_val - entry) / entry * 5.0 if (side == "LONG" and entry > 0) else 0.0)
        )
        in_profit_for_fast_exit = (side == "LONG" and close > entry) or (side == "SHORT" and close < entry)
        wave_expanded_for_fast_exit = (wave_favorable_atr >= 1.5 or wave_favorable_roe >= 0.05) and in_profit_for_fast_exit
        fast_reversal = (
            -sign * (close - fields["open"]) > 0
            and candle_body >= 0.35 * raw_atr
            and -sign * (close - fields["kc_middle"]) > 0
            and -sign * (close - fields["ma15"]) > 0
            and -sign * (quote - fields["kc_middle"]) > 0
            and -sign * (quote - fields["ma15"]) > 0
            and (fresh_ma_cross or (ma_reversed and candle_body >= 1.50 * raw_atr))
            and wave_expanded_for_fast_exit
        )

        bars_held = bar_minute - _minute(opened)
        evidence["bars_held"] = bars_held
        if bars_held < MIN_BARS_HELD:
            if fast_reversal and normalized in OWN_STRUCTURE_REASONS and loss_atr >= 0.5:
                return allow(FAST_TOP_EXIT_REASONS[side])
            return hold("VETO_HOLD_MIN_5_COMPLETED_BARS")

        if live_atr is not None and live_atr > 0:
            from core.services.exits.strict_true_pivot import PROFIT_PIVOT_REASONS

            close_loss_atr = -sign * (close - entry) / disaster_atr
            in_cost_shield = abs(loss_atr) <= COST_SHIELD_ATR or (
                abs(close_loss_atr) <= COST_SHIELD_ATR and loss_atr <= COST_SHIELD_ATR
            )
            if in_cost_shield and normalized not in PROFIT_PIVOT_REASONS and not any(
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

        # 🚨【核心硬鎖：順勢走勢 KC 沒翻向，中間 100% 一票否決中途割肉/提前平倉，死抱到峰谷】
        # 做多時：只要 KC 中軌維持向上 (kc_middle >= kc_middle_prev 或 macro_trend == BULLISH)，
        # 中間的任何 1m 回踩或陰線下殺，絕不允許被普通結構突破 (STRUCTURE_BREAK) 或偽頂部平倉提前洗出場！
        # 必須死抱主升浪，直到走到真峰頂 (True Peak) 或階梯鎖利才准平多！
        if side == "LONG" and is_kc_up_header and not in_profit_for_fast_exit:
            return hold("VETO_HOLD_LONG_UPWARD_KC_UNTIL_TRUE_PEAK")

        # 做空時：只要 KC 中軌維持向下 (kc_middle <= kc_middle_prev 或 macro_trend == BEARISH)，
        # 中間的任何 1m 反抽或陽線拉升，絕不允許被普通結構突破或偽谷底平倉提前洗出場！
        # 必須死抱主降浪，直到走到真谷底 (True Valley) 或階梯鎖利才准平空！
        if side == "SHORT" and is_kc_down_header and not in_profit_for_fast_exit:
            return hold("VETO_HOLD_SHORT_DOWNWARD_KC_UNTIL_TRUE_VALLEY")

        # 🚀 頂部/底部大實體 K 線貫穿極速平倉通道 (Fast Top / Bottom Exit Channel)
        # 解決龍蝦/PEPE峰頂滯後平多：脫離成本保護盾(Cost Shield)後，當收線時出現大實體逆向K線(陰線下殺 Body >= 0.35 ATR 貫穿 KC 中軌與 MA15 且 MA5 死叉 MA15)，
        # 100% 豁免後續 0.50 ATR 緩衝區與 fresh_cross 限制，直接在頂部/底部第一時間授權平倉！
        if fast_reversal and (
            not beyond_buffer
            or not fresh_ma_cross
            or normalized in FAST_TOP_EXIT_REASONS.values()
            or "FAST_TOP" in normalized
            or "FAST_BOTTOM" in normalized
        ) and normalized in OWN_STRUCTURE_REASONS:
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
          (1) 盤中最新報價 (Quote) 垂直貫穿跌破 KC 中軌與 MA15 深度達 >= 0.5 ATR
          (2) 或單根盤中未收線 K 棒下殺幅度 (Live Candle Drop) 超過 1.2 ATR 且跌破 KC 中軌
        做空時：
          (1) 盤中最新報價 (Quote) 垂直爆拉突破 KC 中軌與 MA15 深度達 >= 0.5 ATR
          (2) 或單根盤中未收線 K 棒拉升幅度 (Live Candle Pump) 超過 1.2 ATR 且突破 KC 中軌
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

            kc_mid = (
                _float(curr_candle.get("kc_middle"))
                or _float(getattr(frame, "attrs", {}).get("live_kc_middle"))
                or _float(prev_candle.get("kc_middle"))
            )
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

            if side == "LONG" and price < live_open:
                # 條件 1：即時價貫穿跌破 KC 中軌與 MA15 深度 >= 0.5 ATR
                penetrates_mid_ma15 = (price <= (kc_mid - 0.5 * dist_atr)) and (price <= (ma15 - 0.5 * dist_atr))
                # 條件 2：單根盤中 K 棒下殺幅度超過 1.2 ATR 且跌破中軌
                single_bar_dump = ((live_open - price) >= 1.2 * dist_atr) and (price < kc_mid)

                if penetrates_mid_ma15 or single_bar_dump:
                    reason = EMERGENCY_CIRCUIT_BREAKER_REASONS["LONG"]
                    evidence["trigger"] = "PENETRATES_MID_MA15" if penetrates_mid_ma15 else "SINGLE_BAR_DUMP"
                    return True, reason, evidence

            elif side == "SHORT" and price > live_open:
                # 條件 1：即時價貫穿突破 KC 中軌與 MA15 深度 >= 0.5 ATR
                penetrates_mid_ma15 = (price >= (kc_mid + 0.5 * dist_atr)) and (price >= (ma15 + 0.5 * dist_atr))
                # 條件 2：單根盤中 K 棒拉升幅度超過 1.2 ATR 且突破中軌
                single_bar_pump = ((price - live_open) >= 1.2 * dist_atr) and (price > kc_mid)

                if penetrates_mid_ma15 or single_bar_pump:
                    reason = EMERGENCY_CIRCUIT_BREAKER_REASONS["SHORT"]
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

    @classmethod
    def evaluate_two_point_pivot_flip(
        cls,
        position_side: Optional[str],
        close_price: float,
        ma5_curr: float,
        ma5_prev: float,
        *,
        is_bar_closed: bool = True,
    ) -> Tuple[bool, str, Optional[str]]:
        """
        ⚡ 真峰谷轉彎與 MA5/MA15 交叉 2 點秒平＋秒開/反手通道 (True Peak/Valley & MA Cross 2-Point Override):
        當 1m K 棒確定收線 (is_bar_closed == True) 時，100% 豁免大趨勢標頭與超賣/走平限制：
        1. 谷底轉彎 / 金叉成立 (close_price > ma5_curr 且 ma5_curr > ma5_prev):
           - 若持空單 (SHORT): 回傳 (True, "CLOSE_SHORT", "OPEN_LONG")
           - 若為空倉 (FLAT/NONE/""): 回傳 (True, "OPEN_LONG", "OPEN_LONG")
        2. 頂部轉彎 / 死叉成立 (close_price < ma5_curr 且 ma5_curr < ma5_prev):
           - 若持多單 (LONG): 回傳 (True, "CLOSE_LONG", "OPEN_SHORT")
           - 若為空倉 (FLAT/NONE/""): 回傳 (True, "OPEN_SHORT", "OPEN_SHORT")
        """
        if not is_bar_closed:
            return False, "HOLD", None
        side = str(position_side or "").upper()
        if side == "SHORT":
            if close_price > ma5_curr and ma5_curr > ma5_prev:
                return True, "CLOSE_SHORT", "OPEN_LONG"
        elif side == "LONG":
            if close_price < ma5_curr and ma5_curr < ma5_prev:
                return True, "CLOSE_LONG", "OPEN_SHORT"
        elif side in ("", "FLAT", "NONE"):
            if close_price > ma5_curr and ma5_curr > ma5_prev:
                return True, "OPEN_LONG", "OPEN_LONG"
            if close_price < ma5_curr and ma5_curr < ma5_prev:
                return True, "OPEN_SHORT", "OPEN_SHORT"
        return False, "HOLD", None


UltimateExitGatekeeper = UltimateShortExitGatekeeper
