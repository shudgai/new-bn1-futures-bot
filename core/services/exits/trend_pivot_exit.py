"""Position-bound observed MA5 peaks; flat and minor pullbacks keep exposure."""
import copy
import math
import time

import numpy as np

from core.services.candle_data import closed_entry_candles
from core.services.exits.peak_trailing_exit import RETIRED_KEYS, position_identity
from core.services.exits.staged_risk_service import staged_enabled

POLICY = "kc_reverse_observed_ma5_peak_turn_010_atr_v9"
STATE_KEY = "trend_pivot_exit_state"
PIVOT_REASON = "EXIT_KC_REVERSE_CLOSED_MA5_PEAK_010_ATR"
LIVE_REASON = "EXIT_KC_REVERSE_LIVE_MA5_PEAK_010_ATR"
REASONS = (PIVOT_REASON, LIVE_REASON)
MA5_TURN_ATR = 0.10
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
    if (prior.get("policy") == "observed_ma5_peak_turn_010_atr_v8"
            and prior.get("identity") == identity):
        for key in ("reference_atr", "ma5_peak", "ma5_observation"):
            if key in prior:
                state[key] = copy.deepcopy(prior[key])
    # Older policies cannot authorize retries under the new MA5-only rule.
    if prior.get("identity") == identity and "last_ms" in prior:
        state["last_ms"] = prior["last_ms"]
    if "reference_atr" not in state:
        state["reference_atr"] = position.get("entry_atr", meta.get("entry_atr"))
    for store in (position, meta):
        for key in DISABLED_KEYS:
            store.pop(key, None)
        store.update(sl=0., tp=0., stop_loss=0., atr_sl=0., atr_tp=0.)
        store[STATE_KEY] = copy.deepcopy(state)
    return position[STATE_KEY]


def _observe_ma5(peak, current, previous, sign, atr, evidence):
    tolerance = max(current, previous) * 1e-12
    if not peak:
        peak.update(baseline=current, extreme=current, favorable=False)
    if sign * (current - float(peak["extreme"])) > tolerance:
        peak["extreme"] = current
    if sign * (current - float(peak["baseline"])) > tolerance:
        peak["favorable"] = True
    retreat = sign * (float(peak["extreme"]) - current)
    threshold = MA5_TURN_ATR * atr
    if (peak["favorable"] and sign * (current - previous) < -tolerance
            and (retreat >= threshold or math.isclose(retreat, threshold, rel_tol=1e-12))):
        return dict(evidence, ma5_pivot=peak["extreme"], ma5_current=current,
                    ma5_previous=previous, ma5_retreat=retreat,
                    fixed_entry_atr=atr, ma5_turn_atr=MA5_TURN_ATR,
                    ma5_retreat_threshold=threshold)
    return None


def _evaluate(position, frame, price, quote_ms):
    """Replay completed post-entry observations, never unobserved live quotes."""
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
        atr = float(state.get("reference_atr", position.get("entry_atr")))
        if not math.isfinite(atr) or atr <= 0:
            return None, "WAIT_MA5_FIXED_ENTRY_ATR", {}
        if (frame is None or frame.empty or "is_closed" not in frame
                or frame.attrs.get("timeframe_ms", 60000) != 60000
                or not all(isinstance(v, (bool, np.bool_)) for v in frame.is_closed)):
            return None, "WAIT_TREND_PIVOT_DATA", {}
        closed = closed_entry_candles(frame)
        current_bar = math.floor(stamp / 60000) * 60000
        if closed.empty or float(closed.iloc[-1].timestamp) != current_bar - 60000:
            return None, "WAIT_TREND_PIVOT_FRESH_CLOSED", {}
        live_values = None
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
            live_values = values
        elif len(frame) != len(closed):
            return None, "WAIT_LIVE_MA5_DATA", {}
        eligible = closed[closed.timestamp >= identity[1] * 1000]
        observation = copy.deepcopy(state.get("ma5_observation", {})) if (
            state.get("policy") == POLICY and state.get("identity") == identity
        ) else {}
        peak = copy.deepcopy(state.get("ma5_peak", {})) if (
            state.get("policy") == POLICY and state.get("identity") == identity
        ) else {}
        if peak and (not all(math.isfinite(float(peak[k])) and float(peak[k]) > 0
                             for k in ("baseline", "extreme"))
                     or not isinstance(peak.get("favorable"), bool)):
            return None, "WAIT_TREND_PIVOT_DATA", {}
        if observation:
            last_bar = float(observation["bar_ms"])
            last_ma5 = float(observation["ma5"])
            if not all(math.isfinite(v) and v > 0 for v in (last_bar, last_ma5)):
                return None, "WAIT_TREND_PIVOT_DATA", {}
            eligible = eligible[eligible.timestamp > last_bar]
            if not eligible.empty and float(eligible.iloc[0].timestamp) != last_bar + 60000:
                return None, "WAIT_MA5_HISTORY_GAP", {}
        recent = eligible[["timestamp", "open", "high", "low", "close", "ma5"]].astype(float)
        if (not np.isfinite(recent.to_numpy()).all() or not recent.gt(0).all().all()
                or not recent.timestamp.diff().dropna().eq(60000).all()
                or not (recent.low <= recent[["open", "close"]].min(axis=1)).all()
                or not (recent.high >= recent[["open", "close"]].max(axis=1)).all()
                or not (recent.high >= recent.low).all()):
            return None, "WAIT_TREND_PIVOT_DATA", {}
        sign = 1 if identity[0] == "LONG" else -1
        kc = [float(v) for v in closed.kc_middle.tail(2)]
        if (len(kc) != 2 or not all(math.isfinite(v) and v > 0 for v in kc)
                or float(closed.iloc[-1].timestamp)-float(closed.iloc[-2].timestamp) != 60000):
            return None, "WAIT_EXIT_KC_DIRECTION", {}
        kc_reversed = sign*(kc[1]-kc[0]) < -max(kc)*1e-12
        for _, row in recent.iterrows():
            ma5, bar = float(row.ma5), float(row.timestamp)
            previous = float(observation.get("ma5", ma5))
            evidence = _observe_ma5(peak, ma5, previous, sign, atr, {
                "reason": PIVOT_REASON, "closed_bar_ms": bar,
                "quote_ms": stamp, "trigger_price": price,
            })
            observation.update(bar_ms=bar, ma5=ma5)
            if evidence and kc_reversed:
                evidence.update(kc_previous=kc[0], kc_current=kc[1])
                return evidence, None, {"ma5_observation": observation, "ma5_peak": peak}
        patch = {"ma5_observation": observation, "ma5_peak": peak}
        if live_values is not None:
            previous = float(live_values.iloc[-1].ma5)
            current = (float(live_values.close.iloc[-4:].sum()) + price) / 5.
            evidence = _observe_ma5(peak, current, previous, sign, atr, {
                "reason": LIVE_REASON, "trigger_bar_ms": current_bar,
                "quote_ms": stamp, "trigger_price": price,
                "closed_bar_ms": float(live_values.iloc[-1].timestamp),
                "ma5_live": current,
            })
            patch["ma5_peak"] = peak
            if not kc_reversed:
                return None, "HOLD_KC_TREND_NOT_REVERSED", patch
            if evidence:
                evidence.update(kc_previous=kc[0], kc_current=kc[1])
            return evidence, None if evidence else "HOLD_WAIT_MA5_PEAK_TURN", patch
        return None, "HOLD_WAIT_MA5_PEAK_TURN", patch
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
                                 "ma5_observation", "ma5_peak", "reference_atr"))
        cleaned = any(key in store for store in before for key in DISABLED_KEYS)
        if evidence or meaningful_change or cleaned or any(before[0].get(k) != 0. for k in ("sl", "tp", "atr_sl")):
            account.save_state(strict=True)
        if not evidence or account.positions.get(symbol) is not position:
            return False
        account.log(f"TREND_PIVOT_EXIT symbol={symbol} reason={evidence['reason']} "
                    f"price={price} evidence={evidence}", "INFO")
        closed = await account.close_position(
            symbol, price, "Channel Swing " + evidence["reason"], is_manual=True)
        return bool(closed and symbol not in account.positions)
    except (OSError, KeyError, TypeError, ValueError, OverflowError) as exc:
        account.log(f"TREND_PIVOT_EXIT_INVALID symbol={symbol} error={exc}", "WARNING")
        return False
