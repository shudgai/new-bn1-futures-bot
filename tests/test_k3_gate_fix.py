import pytest
import pandas as pd
from core.services.kc_pending_entry import evaluate_kc_pending_entry

def build_test_frame_long(third_bar=None):
    data = [
        {"timestamp": 1791030900000.0, "open": 0.04966, "high": 0.0502, "low": 0.04946, "close": 0.0499,
         "ma3": 0.04985333, "ma5": 0.0497, "ma15": 0.04938133, "atr": 0.000503, "kc_upper": 0.049712, "kc_middle": 0.049209, "kc_lower": 0.048706, "is_closed": True},
        {"timestamp": 1791030960000.0, "open": 0.04991, "high": 0.05003, "low": 0.04975, "close": 0.04998,
         "ma3": 0.04984666, "ma5": 0.0498, "ma15": 0.04936666, "atr": 0.000504, "kc_upper": 0.049786, "kc_middle": 0.049282, "kc_lower": 0.048778, "is_closed": True},
    ]
    if third_bar is not None:
        data.append(third_bar)
    df = pd.DataFrame(data)
    df.attrs['timeframe_ms'] = 60000
    return df

def build_test_frame_short(third_bar=None):
    data = [
        {"timestamp": 1791030900000.0, "open": 0.05034, "high": 0.05054, "low": 0.0498, "close": 0.0501,
         "ma3": 0.04914667, "ma5": 0.0495, "ma15": 0.04961867, "atr": 0.000503, "kc_upper": 0.051294, "kc_middle": 0.050791, "kc_lower": 0.050288, "is_closed": True},
        {"timestamp": 1791030960000.0, "open": 0.05009, "high": 0.05025, "low": 0.04997, "close": 0.05002,
         "ma3": 0.04915334, "ma5": 0.0494, "ma15": 0.04963334, "atr": 0.000504, "kc_upper": 0.051222, "kc_middle": 0.050718, "kc_lower": 0.050214, "is_closed": True},
    ]
    if third_bar is not None:
        data.append(third_bar)
    df = pd.DataFrame(data)
    df.attrs['timeframe_ms'] = 60000
    return df

def build_k3_row(open_px, high_px, low_px, close_px, is_long=True):
    return dict(
        timestamp=1791031020000.0,
        open=open_px,
        high=high_px,
        low=low_px,
        close=close_px,
        ma3=0.04995 if is_long else 0.0492,
        ma5=0.0499 if is_long else 0.0493,
        ma15=0.04935 if is_long else 0.04965,
        atr=0.000504,
        kc_upper=0.049786 if is_long else 0.051222,
        kc_middle=0.049282 if is_long else 0.050718,
        kc_lower=0.048778 if is_long else 0.050214,
        is_closed=True
    )




















