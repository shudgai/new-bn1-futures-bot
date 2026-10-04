import json
import logging
import math
import os
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional

logger = logging.getLogger("EntryVetoShadow")

_LOG_PATH = "logs/entry_veto_shadow.jsonl"

def _ensure_log_dir() -> None:
    os.makedirs("logs", exist_ok=True)

def _append_event(event: Dict) -> None:
    try:
        _ensure_log_dir()
        event.setdefault("logged_at_ms", int(time.time() * 1000))
        with open(_LOG_PATH, "a") as f:
            f.write(json.dumps(event) + "\n")
    except Exception:
        pass  # Fail open

def log_entry_veto_shadow(frame, price, side, decision, symbol):
    """
    Called exactly at the entry decision tick for SECOND_BAR_OUTSIDE candidates.
    100% NONBLOCKING, FAIL_OPEN.
    """
    try:
        from core.services.strategies.unified_entry_strategy import confirmed
        closed = confirmed(frame)
        if closed is None or len(closed) < 2:
            return

        live = frame.iloc[-1]
        prior_closed = closed.iloc[-1]
        
        current_open = float(live.get('open', 0))
        current_price = float(price)
        current_high = max(float(live.get('high', current_price)), current_price)
        current_low = min(float(live.get('low', current_price)), current_price)
        c_range = current_high - current_low

        prior_atr = float(prior_closed.get('atr', 0))
        closed_ma5 = float(prior_closed.get('ma5', 0))
        closed_kc_middle = float(prior_closed.get('kc_middle', 0))

        if side == 'LONG':
            adverse_body = max(0.0, current_open - current_price)
            current_direction = 'BEARISH' if current_price < current_open else 'BULLISH'
            price_vs_ma5 = current_price - closed_ma5
            price_vs_kc = current_price - closed_kc_middle
            s1 = current_price < closed_ma5
            s2 = current_price < closed_kc_middle
        else:
            adverse_body = max(0.0, current_price - current_open)
            current_direction = 'BULLISH' if current_price > current_open else 'BEARISH'
            price_vs_ma5 = closed_ma5 - current_price
            price_vs_kc = closed_kc_middle - current_price
            s1 = current_price > closed_ma5
            s2 = current_price > closed_kc_middle

        s3 = s1 and s2
        s4 = s1 or s2

        adverse_body_atr = adverse_body / prior_atr if prior_atr > 0 else 0.0
        adverse_body_range_ratio = adverse_body / c_range if c_range > 0 else 0.0

        event = {
            "timestamp": int(live.get('timestamp', 0)),
            "symbol": symbol,
            "side": side,
            "signal_code": decision.get('type'),
            "entry_phase": decision.get('reason'),
            "confirmation_candle_time": int(prior_closed.get('timestamp', 0)),
            
            "current_open": current_open,
            "current_price": current_price,
            "current_high": current_high,
            "current_low": current_low,
            
            "prior_closed_atr": prior_atr,
            
            "adverse_body": adverse_body,
            "adverse_body_atr": adverse_body_atr,
            "adverse_body_range_ratio": adverse_body_range_ratio,
            
            "closed_ma5": closed_ma5,
            "closed_kc_middle": closed_kc_middle,
            
            "price_vs_closed_ma5": price_vs_ma5,
            "price_vs_closed_kc_middle": price_vs_kc,
            
            "current_direction": current_direction,
            
            "candidate_filter_results": {
                "ANY_COLOR_BLOCK": adverse_body > 0,
                "ATR_0_10_BLOCK": adverse_body_atr >= 0.10,
                "ATR_0_25_BLOCK": adverse_body_atr >= 0.25,
                "ATR_0_50_BLOCK": adverse_body_atr >= 0.50,
                "ATR_0_75_BLOCK": adverse_body_atr >= 0.75,
                "ATR_1_00_BLOCK": adverse_body_atr >= 1.00,
                "ATR_1_25_BLOCK": adverse_body_atr >= 1.25,
                
                "BODY_RANGE_50_BLOCK": adverse_body_range_ratio >= 0.50,
                
                "S1_BLOCK": s1,
                "S2_BLOCK": s2,
                "S3_BLOCK": s3,
                "S4_BLOCK": s4,

                "COMPOSITE_0_10_BLOCK": (adverse_body_atr >= 0.10) and s3,
                "COMPOSITE_0_25_BLOCK": (adverse_body_atr >= 0.25) and s3,
                "COMPOSITE_0_50_BLOCK": (adverse_body_atr >= 0.50) and s3,
            }
        }
        
        _append_event(event)

    except Exception as e:
        logger.error(f"EntryVetoShadow error: {e}")
