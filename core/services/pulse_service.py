import pandas as pd
from typing import Dict, Any, Optional

def detect_btc_1m_pulse(
    btc_df_1m: pd.DataFrame, btc_live: float
) -> Optional[str]:
    if btc_df_1m is None or len(btc_df_1m) < 5:
        return None
    closes = btc_df_1m["close"].astype(float)
    opens = btc_df_1m["open"].astype(float)
    ret = (closes.iloc[-1] - opens.iloc[-5]) / opens.iloc[-5] * 100.0
    if ret >= 0.3:
        return "LONG"
    elif ret <= -0.3:
        return "SHORT"
    return "NEUTRAL"

def begin_btc_lead_shadow(btc_1m_turn: Optional[str], btc_df_1m: pd.DataFrame) -> None:
    pass

def record_btc_lead_shadow_candidate(
    symbol: str, frame: pd.DataFrame, price: float, is_takeover: bool = False
) -> Optional[dict]:
    return None

def btc_pulse_blocks_entry(side: str, btc_1m_pulse: Optional[str]) -> bool:
    if not btc_1m_pulse or btc_1m_pulse == "NEUTRAL":
        return False
    return side != btc_1m_pulse
