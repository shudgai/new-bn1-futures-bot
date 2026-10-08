"""Channel Swing exits: two completed MA5 legs confirm a post-entry turn."""
import copy
import math
import time

import numpy as np

from core.services.candle_data import closed_entry_candles
from core.services.exits.peak_trailing_exit import RETIRED_KEYS, position_identity
from core.services.exits.staged_risk_service import staged_enabled

POLICY = "closed_ma5_post_entry_turn_v4"
STATE_KEY = "trend_pivot_exit_state"
PIVOT_REASON = "EXIT_CLOSED_MA5_CONFIRMED_TURN"
REASONS = (PIVOT_REASON,)
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
    # Older policies cannot authorize retries under the new MA5-only rule.
    if prior.get("identity") == identity and "last_ms" in prior:
        state["last_ms"] = prior["last_ms"]
    for store in (position, meta):
        for key in DISABLED_KEYS:
            store.pop(key, None)
        store.update(sl=0., tp=0., stop_loss=0., atr_sl=0., atr_tp=0.)
        store[STATE_KEY] = copy.deepcopy(state)
    return position[STATE_KEY]


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
                and state.get("pending") in REASONS):
            return copy.deepcopy(state["evidence"]), None
        if (frame is None or frame.empty or "is_closed" not in frame
                or frame.attrs.get("timeframe_ms", 60000) != 60000
                or not all(isinstance(v, (bool, np.bool_)) for v in frame.is_closed)):
            return None, "WAIT_TREND_PIVOT_DATA"
        closed = closed_entry_candles(frame)
        current_bar = math.floor(stamp / 60000) * 60000
        if closed.empty or float(closed.iloc[-1].timestamp) != current_bar - 60000:
            return None, "WAIT_TREND_PIVOT_FRESH_CLOSED"
        recent = closed.tail(3)[["timestamp", "open", "high", "low", "close", "ma5"]].astype(float)
        if (len(recent) != 3 or not np.isfinite(recent.to_numpy()).all() or not recent.gt(0).all().all()
                or not recent.timestamp.diff().dropna().eq(60000).all()
                or not (recent.low <= recent[["open", "close"]].min(axis=1)).all()
                or not (recent.high >= recent[["open", "close"]].max(axis=1)).all()
                or not (recent.high > recent.low).all()):
            return None, "WAIT_TREND_PIVOT_DATA"
        sign = 1 if identity[0] == "LONG" else -1
        left, pivot, right = (recent.iloc[i] for i in (0, 1, 2))
        if float(left.timestamp) < identity[1] * 1000:
            return None, "WAIT_POST_ENTRY_PIVOT"
        tolerance = max(float(row.ma5) for row in (left, pivot, right)) * 1e-12
        forward = sign * (float(pivot.ma5) - float(left.ma5))
        reverse = sign * (float(right.ma5) - float(pivot.ma5))
        if forward <= tolerance or reverse >= -tolerance:
            return None, "HOLD_WAIT_CLOSED_MA5_TURN"
        return {
            "reason": PIVOT_REASON, "closed_bar_ms": float(right.timestamp),
            "pivot_bar_ms": float(pivot.timestamp),
            "ma5_left": float(left.ma5), "ma5_pivot": float(pivot.ma5),
            "ma5_right": float(right.ma5),
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
                                ("policy", "identity", "pending", "evidence", "diagnostic"))
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
