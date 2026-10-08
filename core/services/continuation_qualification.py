"""Observed two-candle breakout provenance; evaluation never changes state."""
import copy
import math
import time

import numpy as np

from core.services.candle_data import closed_entry_candles
from core.services.kc_pending_entry import evaluate_kc_pending_entry

STATE_VERSION = 1


def observe_runtime(account, symbol, frame, quote):
    try:
        if (not valid_frame(frame)
                or float(frame.iloc[-1].timestamp) != math.floor(time.time()/60)*60000):
            account.log(f"ENTRY_PROVENANCE_SUSPENDED {symbol}: invalid or stale candle", "ERROR")
            return False
        observe(account, symbol, frame, quote)
        return True
    except (OSError, AttributeError, KeyError, TypeError, ValueError, IndexError, OverflowError) as exc:
        account.log(f"ENTRY_PROVENANCE_SUSPENDED {symbol}: {exc}", "ERROR")
        return False


def valid_frame(frame):
    try:
        if frame is None or frame.empty or frame.attrs.get("timeframe_ms", 60000) != 60000:
            return False
        if not all(isinstance(v, (bool, np.bool_)) for v in frame.is_closed):
            return False
        closed = closed_entry_candles(frame)
        if len(closed) < 2 or len(frame) != len(closed) + 1:
            return False
        # Indicator prefixes are unavailable, not missing candles inside the history.
        ready = frame[["atr", "kc_lower", "kc_middle", "kc_upper"]].notna().all(axis=1)
        indices = np.flatnonzero(ready.to_numpy())
        if not len(indices):
            return False
        data = frame.iloc[int(indices[0]):]
        values = data[["timestamp", "open", "high", "low", "close",
                       "atr", "kc_lower", "kc_middle", "kc_upper"]].astype(float)
        return (len(data) >= 3 and np.isfinite(values.to_numpy()).all()
                and values.gt(0).all().all()
                and values.timestamp.diff().dropna().eq(60000).all()
                and (values.low <= values[["open", "close"]].min(axis=1)).all()
                and (values.high >= values[["open", "close"]].max(axis=1)).all()
                and (values.high >= values.low).all()
                and (values.kc_lower < values.kc_middle).all()
                and (values.kc_middle < values.kc_upper).all())
    except (AttributeError, KeyError, TypeError, ValueError, IndexError, OverflowError):
        return False


def invalidation_reason(state, frame, quote):
    if not valid_frame(frame):
        return "WAIT_VALID_CONTINUATION_DATA"
    try:
        price = float(quote)
        if not math.isfinite(price) or price <= 0:
            return "WAIT_VALID_CONTINUATION_QUOTE"
        if (state.get("version") != STATE_VERSION or state.get("side") not in ("LONG", "SHORT")
                or not state.get("pair_id") or state.get("active") is not True):
            return "WAIT_GENERAL_TWO_BAR_QUALIFICATION"
        sign = 1 if state["side"] == "LONG" else -1
        edge_key = "kc_upper" if sign == 1 else "kc_lower"
        live = frame.iloc[-1]
        observed_bar = float(state["observed_bar"])
        pair_bar = float(state["pair_confirmation_bar_id"])
        if float(live.timestamp) < observed_bar:
            return "WAIT_CONTINUATION_OUT_OF_ORDER_DATA"
        closed = closed_entry_candles(frame)
        if observed_bar < float(closed.iloc[0].timestamp):
            return "CONTINUATION_CANCELLED_DATA_GAP"
        if sign * (price - float(live[edge_key])) <= 0:
            return "CONTINUATION_CANCELLED_RETURN_TO_KC"
        # Check every newly completed candle, including bars formed while held.
        history = closed[closed.timestamp >= min(observed_bar, pair_bar)]
        for _, row in history.iterrows():
            if float(row.timestamp) > pair_bar and sign * (float(row.close) - float(row[edge_key])) <= 0:
                return "CONTINUATION_CANCELLED_RETURN_TO_KC"
        middle = [float(v) for v in history.kc_middle]
        if any(sign * (b-a) < -max(abs(a), abs(b))*1e-12
               for a, b in zip(middle, middle[1:])):
            return "CONTINUATION_CANCELLED_KC_REVERSED"
        if len(closed) >= 2:
            a, b = [float(v) for v in closed.kc_middle.iloc[-2:]]
            if sign * (b-a) < -max(abs(a), abs(b))*1e-12:
                return "CONTINUATION_CANCELLED_KC_REVERSED"
        return None
    except (AttributeError, KeyError, TypeError, ValueError, IndexError, OverflowError):
        return "WAIT_VALID_CONTINUATION_STATE"


def qualification(account, symbol, side, frame, quote):
    state = getattr(account, "channel_continuation_qualifications", {}).get(symbol, {})
    if state.get("symbol") != symbol or state.get("side") != side:
        return None
    return state if invalidation_reason(state, frame, quote) is None else None


def observe(account, symbol, frame, quote):
    """Runtime observer persists before exposing any new entry qualification."""
    if not valid_frame(frame):
        return
    price = float(quote)
    if not math.isfinite(price) or price <= 0:
        return
    states = getattr(account, "channel_continuation_qualifications", None)
    if states is None:
        states = account.channel_continuation_qualifications = {}
    if not isinstance(states, dict) or not isinstance(states.get(symbol, {}), dict):
        account.log(f"CONTINUATION_INVALID_PERSISTED_STATE {symbol}", "ERROR")
        raise ValueError("Invalid continuation state")
    previous = states.get(symbol, {})
    state = copy.deepcopy(previous)
    stamp = float(frame.iloc[-1].timestamp)
    if stamp < float(state.get("observed_bar", 0)):
        return
    if state.get("active"):
        problem = invalidation_reason(state, frame, price)
        if problem:
            state.update(active=False, reason=problem)
    closed = closed_entry_candles(frame)
    pair = evaluate_kc_pending_entry(closed, price, symbol=symbol, live=frame.iloc[-1])
    if (symbol not in getattr(account, "positions", {}) and pair["action"] == "ENTER"
            and pair["pending_signal_id"] != state.get("pair_id")):
        state = dict(version=STATE_VERSION, symbol=symbol, active=True,
                     side=pair["side"], pair_id=pair["pending_signal_id"],
                     breakout_bar_id=pair["breakout_bar_id"],
                     pair_confirmation_bar_id=pair["pair_confirmation_bar_id"])
    if not state:
        return
    state["observed_bar"] = stamp
    if state == previous:
        return
    states[symbol] = state
    try:
        account.save_state(strict=True)
    except (OSError, TypeError, ValueError) as exc:
        states[symbol] = {**state, "active": False, "reason": "CONTINUATION_PERSISTENCE_FAILED"}
        account.log(f"CONTINUATION_PERSISTENCE_FAILED {symbol}: {exc}", "ERROR")
        raise
