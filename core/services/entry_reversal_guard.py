"""Persisted pullback-and-resume gate after a live breakout rejection."""
import math

from core.config import (
    RAPID_PIVOT_IMMEDIATE_REVERSE_BODY_ATR,
    RAPID_PIVOT_IMMEDIATE_REVERSE_ENABLED,
)

NEXT_BAR_PULLBACK_ATR = 0.10
NEXT_BAR_RESUME_ATR = 0.05
BAR_MS = 60_000


def _finite_positive(value):
    try:
        value = float(value)
        return math.isfinite(value) and value > 0
    except (TypeError, ValueError, OverflowError):
        return False


def _save(account):
    save = getattr(account, "save_state", None)
    if callable(save):
        save()


def _new_resume_decision(symbol, side, state, live, quote):
    bar_id = float(live["timestamp"])
    sign = 1 if side == "LONG" else -1
    return {
        "action": "ENTER",
        "side": side,
        "type": f"KC_PULLBACK_RESUME_{side}",
        "reason": f"KC_PULLBACK_RESUME_{side}",
        "price": float(quote),
        "entry_atr": float(state["atr"]),
        "confirmation_bar_id": bar_id,
        "close_price": float(live["close"]),
        "intrabar": True,
        "entry_phase": "POST_IMPULSE_PULLBACK_RESUME",
        "breakout_bar_id": float(state["breakout_bar_id"]),
        "pair_confirmation_bar_id": float(state["pair_confirmation_bar_id"]),
        "third_bar_id": bar_id,
        "pending_signal_id": (
            f"{symbol}_PULLBACK_RESUME_{side}_"
            f"{int(state['impulse_bar_id'])}_{int(bar_id)}"
        ),
        "pending_second_bar_id": float(state["pair_confirmation_bar_id"]),
        "pending_wait_bars": 1,
        "pending_max_wait_bars": 1,
        "impulse_bar_id": float(state["impulse_bar_id"]),
        "kc_confirmation_edge": float(live["kc_upper" if sign > 0 else "kc_lower"]),
    }


def observe_entry_reversal_wait(account, symbol, live, closed, quote, candidate):
    """Observe an impulse retracement in order, then allow one-bar pullback recovery."""
    if (account is None or not symbol or not RAPID_PIVOT_IMMEDIATE_REVERSE_ENABLED
            or not RAPID_PIVOT_IMMEDIATE_REVERSE_BODY_ATR):
        return {}
    qualifications = getattr(account, "breakout_qualification", None)
    if not isinstance(qualifications, dict):
        return {}
    qualification = qualifications.setdefault(symbol, {})
    if not isinstance(qualification, dict):
        return {}
    states = qualification.setdefault("entry_reversal_waits", {})
    if not isinstance(states, dict):
        qualification["entry_reversal_waits"] = {}
        states = qualification["entry_reversal_waits"]

    try:
        bar_id = float(live["timestamp"])
        opened = float(live["open"])
        high = float(live["high"])
        low = float(live["low"])
        quote = float(quote)
        reference_atr = float(closed.iloc[-1]["atr"])
        if (not all(math.isfinite(value) and value > 0 for value in
                    (bar_id, opened, high, low, quote, reference_atr))
                or high < max(opened, quote) or low > min(opened, quote)
                or bool(live["is_closed"])):
            return {}
    except (AttributeError, KeyError, TypeError, ValueError, IndexError, OverflowError):
        return {}

    symbol_states = states
    changed = False
    ready = {}
    blocked = set()
    for side in ("LONG", "SHORT"):
        state = symbol_states.get(side)
        if state:
            impulse_bar = float(state.get("impulse_bar_id", 0.0))
            if bar_id == impulse_bar:
                blocked.add(side)
                continue
            if bar_id != impulse_bar + BAR_MS:
                symbol_states.pop(side, None)
                changed = True
                state = None
            elif state.get("phase") == "READY":
                ready[side] = state
                continue
            else:
                atr = float(state.get("atr", 0.0))
                pulled_back = (
                    opened - low >= NEXT_BAR_PULLBACK_ATR * atr
                    if side == "LONG"
                    else high - opened >= NEXT_BAR_PULLBACK_ATR * atr
                )
                resumed = (
                    quote - low >= NEXT_BAR_RESUME_ATR * atr
                    if side == "LONG"
                    else high - quote >= NEXT_BAR_RESUME_ATR * atr
                )
                if pulled_back and resumed:
                    state["phase"] = "READY"
                    ready[side] = state
                    changed = True
                else:
                    blocked.add(side)

    if (candidate and candidate.get("side") in ("LONG", "SHORT")
            and candidate.get("breakout_bar_id") is not None
            and candidate.get("pair_confirmation_bar_id") is not None
            and candidate.get("pending_signal_id")
            and candidate.get("entry_phase") == "KC_2BAR_CLOSED_CONFIRM"):
        side = candidate["side"]
        if side not in symbol_states:
            impulse_atr = RAPID_PIVOT_IMMEDIATE_REVERSE_BODY_ATR * reference_atr
            impulse_seen = (
                high - opened >= impulse_atr
                if side == "LONG"
                else opened - low >= impulse_atr
            )
            retraced = (
                high - quote >= impulse_atr
                if side == "LONG"
                else quote - low >= impulse_atr
            )
            if impulse_seen and retraced:
                symbol_states[side] = {
                    "phase": "WAIT_NEXT_BAR",
                    "impulse_bar_id": bar_id,
                    "atr": reference_atr,
                    "breakout_bar_id": float(candidate["breakout_bar_id"]),
                    "pair_confirmation_bar_id": float(candidate["pair_confirmation_bar_id"]),
                    "pending_signal_id": str(candidate["pending_signal_id"]),
                }
                blocked.add(side)
                changed = True

    if not symbol_states:
        qualification.pop("entry_reversal_waits", None)
        if not qualification:
            qualifications.pop(symbol, None)
    if changed:
        _save(account)

    result = {"blocked_sides": blocked}
    for side, state in ready.items():
        result[f"resume_{side.lower()}"] = _new_resume_decision(
            symbol, side, state, live, quote
        )
    return result
