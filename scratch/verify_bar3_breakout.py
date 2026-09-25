import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import pandas as pd
import numpy as np
from core.services.strategies.unified_entry_strategy import evaluate_entry_signal

def create_mock_df(k0_c, k1_c, k2_o, k2_c, k2_h, k2_l, k3_o, k3_c, k3_h, k3_l, vol_ma20=1000, k2_vol=1000, k2_atr=1.0, is_short=False):
    # base mid
    mid = 100
    upper = 101  # 調低 upper 讓破軌不用跑太遠，避免觸發 2.5 ATR 乖離
    lower = 99

    rows = []
    for i in range(26):
        rows.append({
            'timestamp': 1000000 + i * 60000,
            'open': mid, 'high': mid, 'low': mid, 'close': mid,
            'volume': vol_ma20,
            'kc_upper': upper, 'kc_lower': lower, 'kc_middle': mid,
            'atr': k2_atr, 'ma3': mid
        })
    
    rows.append({
        'timestamp': 1000000 + 26 * 60000,
        'open': k0_c, 'high': k0_c, 'low': k0_c, 'close': k0_c,
        'volume': vol_ma20,
        'kc_upper': upper, 'kc_lower': lower, 'kc_middle': mid,
        'atr': k2_atr, 'ma3': mid
    })
    
    rows.append({
        'timestamp': 1000000 + 27 * 60000,
        'open': k1_c, 'high': k1_c, 'low': k1_c, 'close': k1_c,
        'volume': vol_ma20,
        'kc_upper': upper, 'kc_lower': lower, 'kc_middle': mid,
        'atr': k2_atr, 'ma3': mid
    })

    rows.append({
        'timestamp': 1000000 + 28 * 60000,
        'open': k2_o, 'high': k2_h, 'low': k2_l, 'close': k2_c,
        'volume': k2_vol,
        'kc_upper': upper, 'kc_lower': lower, 'kc_middle': mid,
        'atr': k2_atr, 'ma3': mid
    })

    rows.append({
        'timestamp': 1000000 + 29 * 60000 + 10000,
        'open': k3_o, 'high': k3_h, 'low': k3_l, 'close': k3_c,
        'volume': k2_vol,
        'kc_upper': upper, 'kc_lower': lower, 'kc_middle': mid,
        'atr': k2_atr, 'ma3': mid
    })
    
    return pd.DataFrame(rows)

print("--- 測試 1（老趨勢地板追空測試） ---")
# lower 99, k0_c 98 (已破), k1_c 97
df1 = create_mock_df(k0_c=98, k1_c=97, k2_o=97, k2_c=95, k2_h=97, k2_l=95, k3_o=95, k3_c=94, k3_h=95, k3_l=94, is_short=True)
res1 = evaluate_entry_signal(df1, None, None)
print(f"結果: {res1}\n")

print("--- 測試 2（十字星無實體確認測試） ---")
# k0_c 100, k1_c 102, k2實體 0.1 (atr=1.0)
df2 = create_mock_df(k0_c=100, k1_c=102, k2_o=102.1, k2_c=102.2, k2_h=102.5, k2_l=102, k3_o=102.2, k3_c=102.3, k3_h=102.3, k3_l=102.2)
res2 = evaluate_entry_signal(df2, None, None)
print(f"結果: {res2}\n")

print("--- 測試 3（溫和放量標準開倉測試） ---")
# k0_c 100, k1_c 101.5 (破 101)
# k2 101.5 -> 102.2 (body=0.7), h=102.3, l=101.4 (range=0.9), upper_wick = 0.1/0.9=11%
df3 = create_mock_df(k0_c=100, k1_c=101.5, k2_o=101.5, k2_c=102.2, k2_h=102.3, k2_l=101.4, k3_o=102.2, k3_c=102.2, k3_h=102.2, k3_l=102.2, vol_ma20=1000, k2_vol=1600, k2_atr=1.0)
res3 = evaluate_entry_signal(df3, None, None)
print(f"結果: {res3}\n")

print("--- 測試 4（巨型長針抵抗測試） ---")
# 條件同上，k3 出現 0.5 ATR 影線
df4 = create_mock_df(k0_c=100, k1_c=101.5, k2_o=101.5, k2_c=102.2, k2_h=102.3, k2_l=101.4, k3_o=102.2, k3_c=102.2, k3_h=102.7, k3_l=102.2, vol_ma20=1000, k2_vol=1600, k2_atr=1.0)
res4 = evaluate_entry_signal(df4, None, None)
print(f"結果: {res4}\n")

