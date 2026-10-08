"""Persistent SMALL/multi-bridge provenance, distinct from independent WAIT."""
import copy
import math

from core.services.candle_data import closed_entry_candles


def observe(account, symbol, frame):
    from core.services.entry_contract import is_entry_doji
    closed = closed_entry_candles(frame)
    if len(closed) < 2:
        return
    states = getattr(account, "channel_small_bridge_states", None)
    if states is None:
        states = account.channel_small_bridge_states = {}
    if not isinstance(states, dict):
        account.log(f"SMALL_BRIDGE_INVALID_STATE {symbol}", "ERROR")
        raise ValueError("Invalid small-bridge state")
    previous = states.get(symbol, {})
    if not isinstance(previous, dict):
        account.log(f"SMALL_BRIDGE_INVALID_STATE {symbol}", "ERROR")
        raise ValueError("Invalid small-bridge symbol state")
    state = copy.deepcopy(previous)
    bar = float(frame.iloc[-1].timestamp)
    if bar < float(state.get("live_bar", 0)):
        return
    if symbol in getattr(account, "positions", {}):
        state = {"cursor": float(closed.iloc[-1].timestamp), "live_bar": bar,
                 "active": False, "reason": "WAIT_EXISTING_POSITION"}
    else:
        cursor = float(state.get("cursor", 0))
        for index in range(1, len(closed)):
            row = closed.iloc[index]
            stamp = float(row.timestamp)
            if stamp <= cursor:
                continue
            if cursor and stamp != cursor + 60000:
                state = {"active": False, "reason": "SMALL_BRIDGE_DATA_GAP"}
            atr = float(closed.iloc[index-1].atr)
            body = float(row.close) - float(row.open)
            limit = .25 * atr
            small = (math.isfinite(atr) and atr > 0 and not is_entry_doji(row)
                     and (abs(body) <= limit or math.isclose(abs(body), limit, rel_tol=1e-12)))
            side = "LONG" if body > 0 else "SHORT"
            if not small:
                state.update(active=False, reason="SMALL_BRIDGE_INTERRUPTED")
            elif state.get("active") and side != state["side"]:
                state["bridge_bars"] = state.get("bridge_bars", []) + [stamp]
                state.update(bridge_count=state.get("bridge_count", 0)+1,
                             bridge_bar=stamp, bridge_reference_atr=atr,
                             bridge_body_atr=abs(body)/atr)
            else:
                state.update(active=True, side=side, setup_bar=stamp,
                             setup_reference_atr=atr, setup_body_atr=abs(body)/atr,
                             bridge_count=0, bridge_bars=[])
            cursor = stamp
            state["cursor"] = cursor
        if bar != state.get("live_bar"):
            state.update(live_bar=bar, trigger_atr=float(closed.iloc[-1].atr))
    if state == previous:
        return
    states[symbol] = state
    try:
        account.save_state(strict=True)
    except (OSError, TypeError, ValueError) as exc:
        states[symbol] = {**state, "active": False, "reason": "SMALL_BRIDGE_PERSISTENCE_FAILED"}
        account.log(f"SMALL_BRIDGE_PERSISTENCE_FAILED {symbol}: {exc}", "ERROR")
        raise


def pattern(account, symbol, frame, quote):
    state = getattr(account, "channel_small_bridge_states", {}).get(symbol, {})
    if not isinstance(state, dict) or not state.get("active") or not state.get("bridge_count"):
        return None
    closed = closed_entry_candles(frame)
    live = frame.iloc[-1]
    if (float(live.timestamp) != state.get("live_bar")
            or float(closed.iloc[-1].timestamp) != state.get("cursor")):
        return None
    atr = float(state["trigger_atr"])
    if not math.isfinite(atr) or atr <= 0:
        return None
    side = state["side"]
    sign = 1 if side == "LONG" else -1
    body = sign * (float(quote)-float(live.open))
    edge = float(live["kc_upper" if sign == 1 else "kc_lower"])
    if sign*(float(quote)-edge) <= 0 or (
        body < .5*atr and not math.isclose(body, .5*atr, rel_tol=1e-12)
    ):
        return None
    return dict(side=side, small_first_bar_id=state["setup_bar"],
                small_second_bar_id=state["bridge_bar"],
                small_first_reference_atr=state["setup_reference_atr"],
                small_second_reference_atr=state["bridge_reference_atr"],
                small_first_body_atr=state["setup_body_atr"],
                small_second_body_atr=state["bridge_body_atr"],
                live_body_reference_atr=atr, live_body_atr=body/atr,
                small_body_max_atr=.25, live_body_min_atr=.5,
                small_bridge_count=state["bridge_count"],
                small_bridge_bar_ids=state["bridge_bars"])
