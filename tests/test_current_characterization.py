import pytest
import pandas as pd
from core.services.kc_pending_entry import evaluate_kc_pending_entry

def create_bar(ts, o, h, l, c, kcu, kcl, ma5=10, ma15=5, atr=1, closed=True):
    return {
        'timestamp': float(ts),
        'open': float(o),
        'high': float(h),
        'low': float(l),
        'close': float(c),
        'kc_upper': float(kcu),
        'kc_lower': float(kcl),
        'ma5': float(ma5),
        'ma15': float(ma15),
        'atr': float(atr),
        'is_closed': closed
    }



if __name__ == "__main__":
    pytest.main([__file__, "-v"])
