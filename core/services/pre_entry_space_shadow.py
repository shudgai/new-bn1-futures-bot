import json
import logging
from datetime import datetime
import math

from core.config import TAKER_FEE_RATE, SLIPPAGE_PCT

def record_pre_entry_space_shadow(
    symbol: str, 
    side: str, 
    timestamp_ms: float, 
    expected_entry: float, 
    entry_atr: float, 
    initial_stop: float, 
    frame, 
    signal_id: str, 
    candidate_bar_id: float,
    entry_mode: str,
    entry_phase: str
) -> None:
    """
    Shadow telemetry function for pre-entry space gate.
    100% fail-open, no blocking logic.
    """
    try:
        if entry_mode != 'CHANNEL_SWING':
            return
            
        if entry_phase in ('CONTINUATION_REENTRY', 'OUTSIDE_CONTINUATION', 'PROFIT_REENTRY'):
            return

        from core.services.entry_room_service import entry_room
        
        # We reuse the target discovery logic of entry_room
        res = entry_room(frame, expected_entry, side, TAKER_FEE_RATE, SLIPPAGE_PCT, 0.0)
        
        target_status = "NO_VALID_TARGET"
        structural_target = None
        target_source = None
        space_raw = None
        space_atr = None
        net_reward_space = None
        net_risk = None
        net_space_rr = None
        
        if res.get("target") is not None:
            target_status = "TARGET_FOUND"
            structural_target = res["target"]
            target_source = "entry_room"
            
            if side == "LONG":
                space_raw = structural_target - expected_entry
                risk_raw = expected_entry - initial_stop
            else:
                space_raw = expected_entry - structural_target
                risk_raw = initial_stop - expected_entry
                
            if math.isfinite(entry_atr) and entry_atr > 0:
                space_atr = space_raw / entry_atr
            else:
                space_atr = None
                
            # Cost calc
            entry_fee_cost = expected_entry * TAKER_FEE_RATE
            exit_fee_cost = structural_target * TAKER_FEE_RATE
            slip_cost = expected_entry * SLIPPAGE_PCT + structural_target * SLIPPAGE_PCT
            
            net_reward_space = space_raw - entry_fee_cost - exit_fee_cost - slip_cost
            net_risk = risk_raw + entry_fee_cost + exit_fee_cost + slip_cost
            
            if net_risk > 0:
                net_space_rr = net_reward_space / net_risk
            else:
                net_space_rr = None
                
        # Hypotheses
        hypotheses = {}
        if space_atr is not None:
            for t in [0.25, 0.50, 0.75, 1.00, 1.50, 2.00]:
                hypotheses[f"hypothetical_block_{t:.2f}"] = space_atr < t

        record = {
            "timestamp": timestamp_ms,
            "datetime": datetime.utcfromtimestamp(timestamp_ms/1000).isoformat() if timestamp_ms else None,
            "symbol": symbol,
            "side": side,
            "entry_mode": entry_mode,
            "entry_phase": entry_phase,
            "signal_id": signal_id,
            "candidate_bar_id": candidate_bar_id,
            "expected_entry": expected_entry,
            "entry_atr": entry_atr,
            "initial_stop": initial_stop,
            "target_status": target_status,
            "structural_target": structural_target,
            "target_source": target_source,
            "space_raw": space_raw,
            "space_atr": space_atr,
            "raw_risk": risk_raw if target_status == "TARGET_FOUND" else None,
            "net_reward_space": net_reward_space,
            "net_risk": net_risk,
            "net_space_rr": net_space_rr,
            "hypotheses": hypotheses,
            "shadow_result": "ALLOW_NO_OBSTACLE" if target_status == "NO_VALID_TARGET" else "ALLOW_HYPOTHETICAL",
            "metric_status": "INVALID_ATR" if target_status == "TARGET_FOUND" and space_atr is None else "OK"
        }
        
        with open("logs/pre_entry_space_shadow.jsonl", "a") as f:
            f.write(json.dumps(record) + "\n")
            
    except Exception as e:
        # 100% fail-open, do not log loudly in production
        pass
