import json
import math
import subprocess
import time
from typing import Dict, Any, Optional
from core.services.exits.peak_trailing_exit import ABNORMAL_BODY_ATR, ABNORMAL_REASON, DOJI_TRIGGER, PEAK_REASON, HARD_REASON

try:
    from core import config
    # A fallback if it exists, though peak_trailing uses ABNORMAL_BODY_ATR
    WATERFALL_BODY_ATR = getattr(config, 'CHANNEL_WATERFALL_BODY_ATR', 1.5)
except Exception:
    WATERFALL_BODY_ATR = 1.5

try:
    _commit_hash = subprocess.check_output(['git', 'rev-parse', '--short', 'HEAD']).decode('utf-8').strip()
except Exception:
    _commit_hash = "unknown"

SCHEMA_VERSION = "1.0"

# Module-local cache for deduplication to avoid mutating position
_throttle_cache: Dict[str, int] = {}

def log_exit_telemetry(
    position: dict,
    price: float,
    stamp: float,
    snapshot: dict,
    atr: float,
    hard_stop_hit: bool,
    decision: Optional[dict],
    trend_status: str,
    final_decision_is_exit: bool,
    final_reason: str,
    hard_stop_price: float
):
    try:
        symbol = position.get('symbol', 'UNKNOWN')
        side = position.get('side', 'UNKNOWN')
        pos_id = position.get('id', 'UNKNOWN')

        bar = math.floor(stamp / 60000) * 60000
        live_ms = snapshot.get('live_bar_ms')
        closed_ms = snapshot.get('closed_bar_ms')
        opening = snapshot.get('live_open')
        prior_atr = snapshot.get('atr')

        # SYNC evaluation
        sync_status = "READY"
        sync_reason = ""
        if 'reason' in snapshot and snapshot['reason']:
            sync_status = "NOT_READY"
            sync_reason = snapshot['reason']
        elif live_ms != bar:
            sync_status = "NOT_READY"
            sync_reason = f"LIVE_BAR_MISMATCH (bar={bar}, live={live_ms})"
        elif closed_ms != bar - 60000:
            sync_status = "NOT_READY"
            sync_reason = f"CLOSED_BAR_MISMATCH (bar={bar}, closed={closed_ms})"
        elif not opening:
            sync_status = "NOT_READY"
            sync_reason = "NO_LIVE_OPEN"
        elif not prior_atr:
            sync_status = "NOT_READY"
            sync_reason = "NO_PRIOR_ATR"

        # Risk Exit evaluation
        abnormal_body_live = 0.0
        abnormal_threshold = 0.0
        waterfall_body_live = 0.0
        waterfall_threshold = 0.0
        
        sign = 1 if side == "LONG" else -1
        if sync_status == "READY":
            abnormal_body_live = sign * (float(opening) - price)
            abnormal_threshold = float(ABNORMAL_BODY_ATR) * float(prior_atr)
            waterfall_body_live = abnormal_body_live
            waterfall_threshold = float(WATERFALL_BODY_ATR) * float(prior_atr)

        abnormal_hit = (abnormal_body_live > 0 and abnormal_body_live >= abnormal_threshold)
        waterfall_hit = (waterfall_body_live > 0 and waterfall_body_live >= waterfall_threshold)

        # Trend / Profit evaluation
        peak_state = position.get('peak_trailing_state', {})
        armed = peak_state.get('armed', False)
        
        raw_decision_type = decision.get('type') if decision else None
        raw_trigger = decision.get('trigger') if decision else None

        # Exact Level evaluation
        exit_level = "UNKNOWN"
        if not final_decision_is_exit:
            exit_level = "NONE"
        else:
            if hard_stop_hit or final_reason == "HARD_STOP":
                exit_level = "LEVEL_1_HARD_RISK"
            elif final_reason == ABNORMAL_REASON or raw_trigger == 'WATERFALL_DROP':
                exit_level = "LEVEL_2_ADVERSE_EMERGENCY"
            elif final_reason in (PEAK_REASON, DOJI_TRIGGER, 'EXIT_INITIAL_ATR_HARD_STOP') or raw_trigger in ('EXIT_CATASTROPHIC_PROFIT_FLOOR',):
                # NOTE: 'EXIT_INITIAL_ATR_HARD_STOP' corresponds to HARD_REASON in peak_trailing_exit but acts like profit lock sometimes? Wait, HARD_REASON is LEVEL_1!
                if final_reason == HARD_REASON:
                    exit_level = "LEVEL_1_HARD_RISK"
                else:
                    exit_level = "LEVEL_3_PROFIT_PROTECTION"
            elif "DOJI" in final_reason or "PEAK" in final_reason or "MA3" in final_reason:
                exit_level = "LEVEL_3_PROFIT_PROTECTION"

        # Throttling logic
        # Log if: NOT_READY, approaching abnormal (>= 80%), actual candidate, actual exit
        abnormal_phase = "IDLE"
        if abnormal_body_live > 0:
            if abnormal_hit:
                abnormal_phase = "HIT"
            elif abnormal_body_live >= abnormal_threshold * 0.8:
                abnormal_phase = "APPROACHING"

        should_log = False
        if sync_status == "NOT_READY":
            should_log = True
        elif abnormal_phase != "IDLE":
            should_log = True
        elif final_decision_is_exit:
            should_log = True
        elif raw_decision_type is not None:
            should_log = True
            
        # Deduplication using module-level cache
        identity_key = f"{symbol}_{side}_{pos_id}_{bar}"
        current_hash = hash((sync_status, sync_reason, exit_level, final_decision_is_exit, armed, abnormal_phase, raw_decision_type, raw_trigger))
        
        # If it's not a loggable event AND state hasn't changed, ignore.
        # But even if it IS a loggable event (like NOT_READY), if state hasn't changed, ignore!
        # Exceptions: if it's an actual exit, we might want to log every time?
        # Actually, final_decision_is_exit means it will close the position, so it's fine to dedupe it too.
        # But just in case, we can force log if final_decision_is_exit and it wasn't logged this tick.
        if _throttle_cache.get(identity_key) == current_hash:
            if not final_decision_is_exit:
                return
        elif not should_log:
            # If state changed but it's not a significant event, we still don't log it?
            # Wait, the user said "只記錄有意義的 state transition". 
            # If should_log is False, we NEVER log it! But we should update the cache so we don't log it next time it becomes True? No, we just return.
            return
        
        _throttle_cache[identity_key] = current_hash

        record = {
            "deployment": {
                "production_strategy_baseline": "bcece9e",
                "running_commit": _commit_hash,
                "schema_version": SCHEMA_VERSION
            },
            "position": {
                "symbol": symbol,
                "side": side,
                "position_id": pos_id
            },
            "quote": {
                "quote_timestamp": stamp,
                "quote_price": price,
                "quote_bar_ms": bar,
                "live_bar_ms": live_ms,
                "closed_bar_ms": closed_ms,
                "live_open": opening,
                "prior_atr": prior_atr,
                "snapshot_sync": sync_status,
                "snapshot_sync_reason": sync_reason
            },
            "risk_exit": {
                "hard_stop_price": hard_stop_price,
                "hard_stop_hit": hard_stop_hit,
                "abnormal_body_live": abnormal_body_live,
                "abnormal_threshold": abnormal_threshold,
                "abnormal_hit": abnormal_hit,
                "waterfall_body_live": waterfall_body_live,
                "waterfall_threshold": waterfall_threshold,
                "waterfall_hit": waterfall_hit
            },
            "trend": {
                "KC_upper": snapshot.get('kc_upper'),
                "KC_middle": snapshot.get('kc_middle'),
                "KC_lower": snapshot.get('kc_lower'),
                "KC_middle_previous": snapshot.get('last_kc_middle'),
                "MA3": snapshot.get('ma5'),
                "MA5": None,
                "MA15": snapshot.get('ma15'),
                "peak_gain_atr": peak_state.get('peak_gain_atr'),
                "armed": armed,
                "profit_exit_candidate": raw_decision_type,
                "profit_exit_trigger": raw_trigger
            },
            "final_decision": {
                "exit_level": exit_level,
                "decision": "EXIT" if final_decision_is_exit else "HOLD",
                "raw_decision_type": raw_decision_type,
                "raw_trigger": raw_trigger,
                "reason": final_reason,
                "sub_reason": raw_trigger
            }
        }
        
        with open("logs/exit_telemetry.jsonl", "a") as f:
            f.write(json.dumps(record) + "\n")
            
    except Exception:
        pass
