"""Pre-close-API absolute veto for meme (1000PEPE / 龍蝦) positions.

Any automated close request on a meme symbol must pass this gate; ``HOLD`` means
the request is discarded and no buy-back/sell-back order may be sent.
LONG mirrors SHORT exactly.
"""

from __future__ import annotations

import logging
import math
import time
from typing import Any, Mapping, NamedTuple, Optional

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
     *(r.upper() for r in KC_FLIP_EXIT_REASONS.values())}
)
LEGACY_VERIFIED_DISASTER_REASONS = frozenset({"EXIT_BY_EXTREME_WATERFALL"})
OWN_DISASTER_REASONS = frozenset(
    r.upper() for r in (*DISASTER_LOSS_REASONS.values(), *DISASTER_RAIL_REASONS.values())
)


def _cost_shield_distance_atr(
    symbol: str, position: Mapping[str, Any], price: Any, frame: Any,
) -> Optional[float]:
    """Signed favourable distance (ATR) of the quote from entry; None if unknown."""
    from core.gates.master_gate_funnel import meme_distance_atr

    side = str(position.get("side") or "").upper()
    quote, entry = _float(price), _float(position.get("entry_price"))
    if side not in ("LONG", "SHORT") or not quote or not entry or quote <= 0 or entry <= 0:
        return None
    try:
        closed = closed_entry_candles(frame) if frame is not None else None
    except Exception:
        return None
    if closed is None or len(closed) < 1:
        return None
    raw_atr = _float(closed.iloc[-1].get("atr"))
    if raw_atr is None or raw_atr <= 0:
        return None
    sign = 1 if side == "LONG" else -1
    return sign * (quote - entry) / meme_distance_atr(symbol, raw_atr)


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


class UltimateShortExitGatekeeper:
    """Highest-priority exit veto; also mirrors the identical rules for LONG."""

    @staticmethod
    def evaluate(
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

        if normalized in LEGACY_VERIFIED_DISASTER_REASONS:
            shield = _cost_shield_distance_atr(symbol, position, price, frame)
            if shield is not None and abs(shield) <= COST_SHIELD_ATR:
                evidence["cost_distance_atr"] = round(shield, 4)
                return hold(f"VETO_HOLD_{side}_COST_SHIELD")
            # Disaster class: only the existing math-verified live waterfall proof may authorize it.
            return ExitVerdict(DEFER, "VERIFIED_WATERFALL_MATH_PROOF_REQUIRED", {})

        def allow(code: str) -> ExitVerdict:
            if normalized not in OWN_STRUCTURE_REASONS | OWN_DISASTER_REASONS:
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
        if len(closed) < 2:
            return hold("VETO_EXIT_CLOSED_DATA_UNAVAILABLE")
        current, previous = closed.iloc[-1], closed.iloc[-2]
        fields = {}
        for key in ("timestamp", "open", "high", "low", "close", "ma5", "ma15",
                    "kc_upper", "kc_middle", "kc_lower"):
            fields[key] = _float(current.get(key))
        fields["ma5_prev"] = _float(previous.get("ma5"))
        fields["ma15_prev"] = _float(previous.get("ma15"))
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

        bars_held = bar_minute - _minute(opened)
        evidence["bars_held"] = bars_held
        if bars_held < MIN_BARS_HELD:
            return hold("VETO_HOLD_MIN_5_COMPLETED_BARS")

        if normalized in {
            "STRUCTURE_EXIT_PROBE",
            *(reason.upper() for reason in KC_MIDDLE_MA15_BREAK_REASONS.values()),
        }:
            opposite_body = -sign * (close - fields["open"]) > 0
            structure_broken = (
                -sign * (close - kc_mid) > 0
                and -sign * (close - fields["ma15"]) > 0
            )
            if not opposite_body:
                return hold(f"VETO_HOLD_{side}_OPPOSITE_BODY_REQUIRED")
            if not structure_broken:
                return hold(f"VETO_HOLD_{side}_KC_MIDDLE_MA15_NOT_BROKEN")
            return allow(KC_MIDDLE_MA15_BREAK_REASONS[side])

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
        if not -sign * (close - buffer_line) > 0:
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
    def structure_exit_candidate(cls, symbol, position, metadata, price, frame, *, now=None):
        """Exit producer for the main loop: reason string when the gate itself authorizes."""
        verdict = cls.evaluate(symbol, position, metadata, "STRUCTURE_EXIT_PROBE", price,
                               frame=frame, now=now)
        return verdict.code if verdict.allowed else None
