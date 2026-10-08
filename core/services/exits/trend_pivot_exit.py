"""Channel Swing exits: quote-repriced MA5 flat or adverse movement closes exposure."""
import copy
import math
import time

import numpy as np

from core.services.candle_data import closed_entry_candles
from core.services.exits.peak_trailing_exit import RETIRED_KEYS, position_identity
from core.services.exits.staged_risk_service import staged_enabled

POLICY = "live_ma5_flat_or_adverse_v7"
PREVIOUS_POLICY = "closed_ma5_post_entry_turn_v4"
STATE_KEY = "trend_pivot_exit_state"
PIVOT_REASON = "EXIT_CLOSED_MA5_CONFIRMED_TURN"
FLAT_REASON = "EXIT_CLOSED_MA5_FLAT"
LIVE_REASON = "EXIT_LIVE_MA5_FLAT_OR_ADVERSE"
REASONS = (PIVOT_REASON, FLAT_REASON, LIVE_REASON)
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
    if (prior.get("policy") == PREVIOUS_POLICY and prior.get("identity") == identity
            and prior.get("pending") == PIVOT_REASON
            and isinstance(prior.get("evidence"), dict)
            and prior["evidence"].get("reason") == PIVOT_REASON):
        state.update(pending=PIVOT_REASON, evidence=copy.deepcopy(prior["evidence"]))
    for store in (position, meta):
        for key in DISABLED_KEYS:
            store.pop(key, None)
        store.update(sl=0., tp=0., stop_loss=0., atr_sl=0., atr_tp=0.)
        store[STATE_KEY] = copy.deepcopy(state)
    return position[STATE_KEY]


def _evaluate(position, frame, price, quote_ms):
    """Use observed live quotes; replay only completed bars after restart."""
    try:
        identity = position_identity(position)
        price, stamp = float(price), float(quote_ms)
        if not all(math.isfinite(v) and v > 0 for v in (price, stamp)):
            return None, "WAIT_TREND_PIVOT_QUOTE", {}
        if stamp < identity[1] * 1000:
            return None, "WAIT_TREND_PIVOT_QUOTE", {}
        state = position.get(STATE_KEY, {})
        if stamp < float(state.get("last_ms", 0)):
            return None, "WAIT_TREND_PIVOT_QUOTE", {}
        if (state.get("policy") == POLICY and state.get("identity") == identity
                and state.get("pending") in REASONS):
            return copy.deepcopy(state["evidence"]), None, {}
        if (frame is None or frame.empty or "is_closed" not in frame
                or frame.attrs.get("timeframe_ms", 60000) != 60000
                or not all(isinstance(v, (bool, np.bool_)) for v in frame.is_closed)):
            return None, "WAIT_TREND_PIVOT_DATA", {}
        closed = closed_entry_candles(frame)
        current_bar = math.floor(stamp / 60000) * 60000
        if closed.empty or float(closed.iloc[-1].timestamp) != current_bar - 60000:
            return None, "WAIT_TREND_PIVOT_FRESH_CLOSED", {}
        if len(frame) == len(closed) + 1:
            live = frame.iloc[-1]
            if float(live.timestamp) != current_bar or len(closed) < 5:
                return None, "WAIT_LIVE_MA5_DATA", {}
            values = closed.tail(5)[["timestamp", "open", "high", "low", "close", "ma5"]].astype(float)
            if (not np.isfinite(values.to_numpy()).all() or not values.gt(0).all().all()
                    or not values.timestamp.diff().dropna().eq(60000).all()
                    or not (values.low <= values[["open", "close"]].min(axis=1)).all()
                    or not (values.high >= values[["open", "close"]].max(axis=1)).all()):
                return None, "WAIT_LIVE_MA5_DATA", {}
            previous = float(values.iloc[-1].ma5)
            current = (float(values.close.iloc[-4:].sum()) + price) / 5.
            delta = (1 if identity[0] == "LONG" else -1) * (current - previous)
            tolerance = max(current, previous) * 1e-12
            if delta <= tolerance:
                return {
                    "reason": LIVE_REASON, "trigger_bar_ms": current_bar,
                    "quote_ms": stamp, "trigger_price": price,
                    "closed_bar_ms": float(values.iloc[-1].timestamp),
                    "ma5_previous": previous, "ma5_live": current,
                    "directional_change": delta, "flat_tolerance": tolerance,
                    "flat": abs(delta) <= tolerance,
                }, None, {}
        eligible = closed[closed.timestamp >= identity[1] * 1000]
        observation = copy.deepcopy(state.get("ma5_observation", {})) if (
            state.get("policy") == POLICY and state.get("identity") == identity
        ) else {}
        if observation:
            last_bar = float(observation["bar_ms"])
            last_ma5 = float(observation["ma5"])
            if not all(math.isfinite(v) and v > 0 for v in (last_bar, last_ma5)):
                return None, "WAIT_TREND_PIVOT_DATA", {}
            eligible = eligible[eligible.timestamp > last_bar]
            if not eligible.empty and float(eligible.iloc[0].timestamp) != last_bar + 60000:
                return None, "WAIT_MA5_HISTORY_GAP", {}
        if eligible.empty:
            return None, ("HOLD_WAIT_CLOSED_MA5_TURN" if observation else
                          "WAIT_POST_ENTRY_PIVOT"), {}
        recent = eligible[["timestamp", "open", "high", "low", "close", "ma5"]].astype(float)
        if (not np.isfinite(recent.to_numpy()).all() or not recent.gt(0).all().all()
                or not recent.timestamp.diff().dropna().eq(60000).all()
                or not (recent.low <= recent[["open", "close"]].min(axis=1)).all()
                or not (recent.high >= recent[["open", "close"]].max(axis=1)).all()
                or not (recent.high >= recent.low).all()):
            return None, "WAIT_TREND_PIVOT_DATA", {}
        sign = 1 if identity[0] == "LONG" else -1
        for _, row in recent.iterrows():
            ma5, bar = float(row.ma5), float(row.timestamp)
            if observation:
                previous = float(observation["ma5"])
                delta = sign * (ma5 - previous)
                tolerance = max(ma5, previous) * 1e-12
                if delta <= tolerance:
                    evidence = {
                        "reason": FLAT_REASON if abs(delta) <= tolerance else PIVOT_REASON,
                        "closed_bar_ms": bar, "pivot_bar_ms": observation["bar_ms"],
                        "ma5_pivot": previous, "ma5_right": ma5,
                        "directional_change": delta, "flat_tolerance": tolerance,
                    }
                    observation.update(bar_ms=bar, ma5=ma5)
                    return evidence, None, {"ma5_observation": observation}
            observation.update(bar_ms=bar, ma5=ma5)
        return None, "HOLD_WAIT_CLOSED_MA5_TURN", {"ma5_observation": observation}
    except (AttributeError, KeyError, TypeError, ValueError, IndexError, OverflowError):
        return None, "WAIT_TREND_PIVOT_DATA", {}


def evaluate(position, frame, price, quote_ms):
    evidence, problem, _ = _evaluate(position, frame, price, quote_ms)
    return evidence, problem


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
        evidence, problem, observation = _evaluate(position, frame, price, stamp)
        state.update(observation)
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
                                 "ma5_observation"))
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
