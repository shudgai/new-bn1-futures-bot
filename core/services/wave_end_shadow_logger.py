import json
import logging
import math
import os
import time
from typing import Dict, List, Any
import copy

logger = logging.getLogger("WaveEndShadow")

_LOG_PATH = "logs/wave_end_shadow.jsonl"
_DEDUPE_PATH = "logs/wave_end_dedupe.json"
_state: Dict[str, Any] = {}
_log_initialized = False

def _ensure_log_dir() -> None:
    os.makedirs("logs", exist_ok=True)

def _append_event(event: Dict) -> None:
    try:
        _ensure_log_dir()
        event.setdefault("logged_at_ms", int(time.time() * 1000))
        with open(_LOG_PATH, "a") as f:
            f.write(json.dumps(event) + "\n")
    except Exception:
        pass

def _save_dedupe() -> None:
    try:
        _ensure_log_dir()
        out: Dict[str, Dict[str, List[int]]] = {}
        for sym, st in _state.items():
            out[sym] = {
                "l": sorted(list(st.get("consumed_l", set()))),
                "s": sorted(list(st.get("consumed_s", set()))),
            }
        with open(_DEDUPE_PATH, "w") as f:
            json.dump(out, f)
    except Exception:
        pass

def _initialize_from_log() -> None:
    global _log_initialized
    if _log_initialized:
        return
    _log_initialized = True

    try:
        if os.path.exists(_DEDUPE_PATH):
            with open(_DEDUPE_PATH) as f:
                raw = json.load(f)
            for sym, data in raw.items():
                _state[sym] = {
                    "consumed_l": set(data.get("l", [])),
                    "consumed_s": set(data.get("s", [])),
                }
    except Exception:
        pass

def check_wave_end_candidates(closed_frame, active_positions: List[Dict]) -> None:
    try:
        if not active_positions:
            return
            
        from core.services.reversal_shadow_logger import detect_reversal_candidates_pure
        _initialize_from_log()
        
        n = len(closed_frame)
        if n < 1: return
        t = n - 1  # confirmation bar
        c = closed_frame.iloc[t]
        c1 = closed_frame.iloc[t - 1] if t > 0 else c
        ts = int(c.get("timestamp", 0) or 0)
        
        kc_u = float(c.get("kc_upper", 0) or 0)
        kc_m = float(c.get("kc_middle", 0) or 0)
        kc_l = float(c.get("kc_lower", 0) or 0)
        kc_u1 = float(c1.get("kc_upper", 0) or 0)
        kc_l1 = float(c1.get("kc_lower", 0) or 0)
        
        kc_width_now = kc_u - kc_l
        kc_width_previous = kc_u1 - kc_l1
        kc_width_delta = kc_width_now - kc_width_previous

        for raw_pos in active_positions:
            # Deepcopy to ensure READ-ONLY
            pos = copy.deepcopy(raw_pos)
            symbol = pos.get('symbol', 'UNKNOWN')
            side = pos.get('side')
            pos_id = pos.get('id', 'UNKNOWN')
            
            entry_ms = 0
            if "entry_time" in pos:
                entry_ms = pos["entry_time"]
            elif "entry_datetime" in pos:
                try:
                    from datetime import datetime, timezone
                    dt = datetime.fromisoformat(pos["entry_datetime"])
                    entry_ms = int(dt.timestamp() * 1000)
                except:
                    pass
            
            sym_st = _state.setdefault(symbol, {})
            # We track our OWN consumed pivots for Wave End separately from Entry.
            # Long positions look for SHORT reversals, and vice versa.
            consumed_l = sym_st.setdefault("consumed_l", set())
            consumed_s = sym_st.setdefault("consumed_s", set())
            
            # 1. Use the shared pure detector with our own consumed state
            candidates = detect_reversal_candidates_pure(closed_frame, consumed_l, consumed_s)
            
            # 2. Filter candidates matching our opposite side
            for cand in candidates:
                # We need opposite reversal. So if LONG pos -> we want SHORT reversal (peak).
                if side == "LONG" and cand["side"] == "SHORT":
                    # Position lifecycle constraint: pivot must be after entry
                    if entry_ms > 0 and cand["pivot_ts_ms"] < entry_ms:
                        continue
                        
                    # Found!
                    consumed_s.add(cand["pivot_ts_ms"])
                    _save_dedupe()
                    
                    event = {
                        "event_type": "WAVE_END_OBSERVATION",
                        "symbol": symbol,
                        "side": side,
                        "position_id": pos_id,
                        "timestamp": ts,
                        "price": cand["entry_price"],
                        "kc_upper": kc_u,
                        "kc_middle": kc_m,
                        "kc_lower": kc_l,
                        "kc_width": kc_width_now,
                        "kc_width_previous": kc_width_previous,
                        "kc_width_delta": kc_width_delta,
                        "opposite_reversal_detected": True,
                        "pivot_timestamp": cand["pivot_ts_ms"],
                        "confirmation_timestamp": ts,
                        "bars_after_pivot": t - _argmin_pos_shim(closed_frame, cand["pivot_ts_ms"]), # Just use timestamp difference as proxy or exact bars?
                        "candidate_reason": "REVERSAL_SHORT_DETECTED_AGAINST_LONG"
                    }
                    _append_event(event)
                    
                elif side == "SHORT" and cand["side"] == "LONG":
                    if entry_ms > 0 and cand["pivot_ts_ms"] < entry_ms:
                        continue
                        
                    consumed_l.add(cand["pivot_ts_ms"])
                    _save_dedupe()
                    
                    event = {
                        "event_type": "WAVE_END_OBSERVATION",
                        "symbol": symbol,
                        "side": side,
                        "position_id": pos_id,
                        "timestamp": ts,
                        "price": cand["entry_price"],
                        "kc_upper": kc_u,
                        "kc_middle": kc_m,
                        "kc_lower": kc_l,
                        "kc_width": kc_width_now,
                        "kc_width_previous": kc_width_previous,
                        "kc_width_delta": kc_width_delta,
                        "opposite_reversal_detected": True,
                        "pivot_timestamp": cand["pivot_ts_ms"],
                        "confirmation_timestamp": ts,
                        "bars_after_pivot": -1, # We can omit or calculate it if needed
                        "candidate_reason": "REVERSAL_LONG_DETECTED_AGAINST_SHORT"
                    }
                    _append_event(event)

    except Exception as e:
        logger.error(f"WaveEndShadow Error: {e}")

def _argmin_pos_shim(closed, ts):
    # Just a helper to find the bar index by timestamp, since bars_after_pivot is purely informational
    for i in range(len(closed)-1, -1, -1):
        if closed.iloc[i].get("timestamp") == ts:
            return i
    return 0
