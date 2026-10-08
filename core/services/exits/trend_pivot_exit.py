"""Channel Swing exits: post-entry doji pressure or reversed CK and closed pivot."""
import copy
import math
import time

import numpy as np

from core.services.candle_data import closed_entry_candles
from core.services.exits.peak_trailing_exit import (
    RETIRED_KEYS, position_identity, doji_reversal_evidence,
)
from core.services.exits.staged_risk_service import staged_enabled

POLICY = "closed_ck_reverse_pivot_or_mature_doji_06_v2"
PREVIOUS_POLICY = "closed_ck_reverse_pivot_or_doji_06_v1"
DOJI_ARM_GAIN_ATR = 3.0
STATE_KEY = "trend_pivot_exit_state"
PIVOT_REASON = "EXIT_CK_REVERSED_CONFIRMED_PIVOT"
DOJI_REASON = "EXIT_POST_ENTRY_DOJI_ADVERSE_06_ATR"
REASONS = (PIVOT_REASON, DOJI_REASON)
DISABLED_KEYS = RETIRED_KEYS + ("peak_trailing_state", "channel_hard_stop_pending")


def enabled(position, meta=None):
    return (str(position.get("entry_mode") or (meta or {}).get("entry_mode") or "").upper()
            == "CHANNEL_SWING" and not staged_enabled(position, meta))


def migrate(position, meta):
    identity = position_identity(position)
    prior = position.get(STATE_KEY) or meta.get(STATE_KEY) or {}
    state = copy.deepcopy(prior) if (
        prior.get("policy") == POLICY and prior.get("identity") == identity
    ) else {"policy": POLICY, "identity": identity}
    if prior.get("policy") == PREVIOUS_POLICY and prior.get("identity") == identity:
        # A prior unqualified doji ticket must not survive the new maturity gate.
        if prior.get("pending") == PIVOT_REASON:
            state.update(pending=PIVOT_REASON, evidence=copy.deepcopy(prior["evidence"]))
        if "last_ms" in prior:
            state["last_ms"] = prior["last_ms"]
    if "fixed_entry_atr" not in state:
        atr = float(position.get("entry_atr") or meta.get("entry_atr") or 0.)
        if math.isfinite(atr) and atr > 0:
            state["fixed_entry_atr"] = atr
    for store in (position, meta):
        for key in DISABLED_KEYS:
            store.pop(key, None)
        store.update(sl=0., tp=0., stop_loss=0., atr_sl=0., atr_tp=0.)
        store[STATE_KEY] = copy.deepcopy(state)
    return position[STATE_KEY]


def observe_maturity(state, identity, price, stamp):
    if stamp < identity[1] * 1000 or stamp < float(state.get("last_ms", 0)):
        return
    atr = float(state.get("fixed_entry_atr", 0.))
    if not math.isfinite(atr) or atr <= 0:
        return
    sign = 1 if identity[0] == "LONG" else -1
    gain = max(0., sign * (price - identity[2]))
    state["max_favorable_move"] = max(float(state.get("max_favorable_move", 0.)), gain)
    threshold = DOJI_ARM_GAIN_ATR * atr
    if (state["max_favorable_move"] >= threshold
            or math.isclose(state["max_favorable_move"], threshold, rel_tol=1e-12)):
        state["doji_maturity_armed"] = True


def doji_mature(state, identity):
    if state.get("policy") != POLICY or state.get("identity") != identity:
        return False
    atr = float(state.get("fixed_entry_atr", 0.))
    gain = float(state.get("max_favorable_move", 0.))
    if not all(math.isfinite(v) for v in (atr, gain)) or atr <= 0:
        return False
    threshold = DOJI_ARM_GAIN_ATR * atr
    return (state.get("doji_maturity_armed") is True
            and (gain >= threshold or math.isclose(gain, threshold, rel_tol=1e-12)))


def evaluate(position, frame, price, quote_ms):
    """No future wicks, pre-entry pivots or unobserved live-trigger reconstruction."""
    try:
        identity = position_identity(position)
        price, stamp = float(price), float(quote_ms)
        if not all(math.isfinite(v) and v > 0 for v in (price, stamp)):
            return None, "WAIT_TREND_PIVOT_QUOTE"
        if stamp < identity[1] * 1000:
            return None, "WAIT_TREND_PIVOT_QUOTE"
        state = position.get(STATE_KEY, {})
        if stamp < float(state.get("last_ms", 0)):
            return None, "WAIT_TREND_PIVOT_QUOTE"
        if (state.get("policy") == POLICY and state.get("identity") == identity
                and state.get("pending") in REASONS
                and (state["pending"] != DOJI_REASON or doji_mature(state, identity))):
            return copy.deepcopy(state["evidence"]), None
        if (frame is None or frame.empty or "is_closed" not in frame
                or frame.attrs.get("timeframe_ms", 60000) != 60000
                or not all(isinstance(v, (bool, np.bool_)) for v in frame.is_closed)):
            return None, "WAIT_TREND_PIVOT_DATA"
        closed = closed_entry_candles(frame)
        current_bar = math.floor(stamp / 60000) * 60000
        if closed.empty or float(closed.iloc[-1].timestamp) != current_bar - 60000:
            return None, "WAIT_TREND_PIVOT_FRESH_CLOSED"
        recent = closed.tail(3)[["timestamp", "open", "high", "low", "close"]].astype(float)
        if (not np.isfinite(recent.to_numpy()).all() or not recent.gt(0).all().all()
                or not recent.timestamp.diff().dropna().eq(60000).all()
                or not (recent.low <= recent[["open", "close"]].min(axis=1)).all()
                or not (recent.high >= recent[["open", "close"]].max(axis=1)).all()
                or not (recent.high > recent.low).all()):
            return None, "WAIT_TREND_PIVOT_DATA"
        sign = 1 if identity[0] == "LONG" else -1
        if len(frame) == len(closed) + 1 and doji_mature(state, identity):
            last, live = closed.iloc[-1], frame.iloc[-1]
            if float(last.timestamp) >= identity[1] * 1000 and float(live.timestamp) == current_bar:
                snapshot = {
                    "quote_ms": stamp, "closed_bar_ms": float(last.timestamp),
                    "live_bar_ms": float(live.timestamp), "atr": float(last.atr),
                    **{"last_" + key: float(last[key]) for key in ("open", "high", "low", "close")},
                    **{"live_" + key: float(live[key]) for key in ("open", "high", "low")},
                }
                evidence = doji_reversal_evidence(
                    snapshot, price, sign, identity[2], identity[1] * 1000, 0.,
                    adverse_body_atr=0.6)
                if evidence:
                    return {"reason": DOJI_REASON, **evidence,
                            "fixed_entry_atr": state["fixed_entry_atr"],
                            "max_favorable_move": state["max_favorable_move"],
                            "doji_arm_gain_atr": DOJI_ARM_GAIN_ATR}, None
        values = closed.tail(3)[["timestamp", "open", "high", "low", "close", "kc_middle"]].astype(float)
        if (len(values) != 3 or not np.isfinite(values.to_numpy()).all()
                or not values.gt(0).all().all()
                or not values.timestamp.diff().dropna().eq(60000).all()
                or not (values.low <= values[["open", "close"]].min(axis=1)).all()
                or not (values.high >= values[["open", "close"]].max(axis=1)).all()):
            return None, "WAIT_TREND_PIVOT_DATA"
        left, pivot, right = (values.iloc[i] for i in (0, 1, 2))
        if float(left.timestamp) < identity[1] * 1000:
            return None, "WAIT_POST_ENTRY_PIVOT"
        tolerance = max(float(pivot.kc_middle), float(right.kc_middle)) * 1e-12
        reversed_ck = sign * (float(right.kc_middle) - float(pivot.kc_middle)) < -tolerance
        extremum = (pivot.high > max(left.high, right.high) if sign == 1
                    else pivot.low < min(left.low, right.low))
        if not reversed_ck or not extremum:
            return None, "HOLD_WAIT_CK_REVERSE_AND_PIVOT"
        return {
            "reason": PIVOT_REASON, "closed_bar_ms": float(right.timestamp),
            "pivot_bar_ms": float(pivot.timestamp),
            "pivot_price": float(pivot.high if sign == 1 else pivot.low),
            "ck_previous": float(pivot.kc_middle), "ck_latest": float(right.kc_middle),
        }, None
    except (AttributeError, KeyError, TypeError, ValueError, IndexError, OverflowError):
        return None, "WAIT_TREND_PIVOT_DATA"


def close_allowed(position, meta, reason, is_manual):
    if not enabled(position, meta):
        return True
    if is_manual and reason == "手動平倉":
        return True
    state = position.get(STATE_KEY) or meta.get(STATE_KEY) or {}
    try:
        return (state.get("pending") in REASONS
                and reason == "Channel Swing " + state["pending"]
                and state.get("policy") == POLICY
                and state.get("identity") == position_identity(position)
                and (state["pending"] != DOJI_REASON
                     or doji_mature(state, position_identity(position)))
                and isinstance(state.get("evidence"), dict)
                and state["evidence"].get("reason") == state["pending"])
    except (KeyError, TypeError, ValueError, OverflowError):
        return False


async def enforce(account, symbol, price, frame=None, quote_ms=None):
    position = account.positions.get(symbol)
    if not position:
        return False
    meta = account.position_meta.setdefault(symbol, {})
    if not enabled(position, meta):
        return False
    try:
        stamp = time.time() * 1000 if quote_ms is None else float(quote_ms)
        price = float(price)
        if (not math.isfinite(stamp) or not 0 <= time.time() * 1000 - stamp <= 5000
                or not math.isfinite(price) or price <= 0):
            return False
        before = copy.deepcopy((position, meta))
        state = migrate(position, meta)
        observe_maturity(state, position_identity(position), price, stamp)
        evidence, problem = evaluate(position, frame, price, stamp)
        if evidence:
            state.update(pending=evidence["reason"], evidence=evidence)
        state["last_ms"] = max(stamp, float(state.get("last_ms", 0)))
        if state.get("diagnostic") != problem:
            state["diagnostic"] = problem
            if problem:
                account.log(f"TREND_PIVOT_EXIT symbol={symbol} reason={problem}", "INFO")
        meta[STATE_KEY] = copy.deepcopy(state)
        old_state = before[0].get(STATE_KEY, {})
        meaningful_change = any(old_state.get(k) != state.get(k) for k in
                                ("policy", "identity", "pending", "evidence", "diagnostic",
                                 "fixed_entry_atr", "max_favorable_move", "doji_maturity_armed"))
        cleaned = any(key in store for store in before for key in DISABLED_KEYS)
        if meaningful_change or cleaned or any(before[0].get(k) != 0. for k in ("sl", "tp", "atr_sl")):
            account.save_state()
        if not evidence or account.positions.get(symbol) is not position:
            return False
        account.log(f"TREND_PIVOT_EXIT symbol={symbol} reason={evidence['reason']} "
                    f"price={price} evidence={evidence}", "INFO")
        closed = await account.close_position(
            symbol, price, "Channel Swing " + evidence["reason"], is_manual=True)
        return bool(closed and symbol not in account.positions)
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        account.log(f"TREND_PIVOT_EXIT_INVALID symbol={symbol} error={exc}", "WARNING")
        return False
