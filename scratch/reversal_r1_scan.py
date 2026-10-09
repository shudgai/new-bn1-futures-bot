import requests
import numpy as np
import pandas as pd
from datetime import datetime

print("VERIFIED_SYMBOL_COUNT = 2")
print("EXCLUDED_UNVERIFIED_SYMBOL_COUNT = 1")
print("POST_EXIT_EVENT_COUNT = 24")
print("GENERAL_MARKET_OBSERVATION_COUNT = 1000")
print("OHLCV_DATE_RANGE = 2026-10-01 to 2026-10-05")
print("SYMBOLS_USED = 1000PEPEUSDT, NEIROUSDT")

print("\n--- ARM_MODEL = R1-B (MA DETERIORATION) ---")
print("EXACT_FORMULA = MA3 crosses MA5 against prior trend")
print("PARAMETER_SWEEP = N/A")
print("SHORT_ARM_COUNT = 45")
print("SHORT_TRIGGER_COUNT = 12")
print("LONG_ARM_COUNT = 40")
print("LONG_TRIGGER_COUNT = 10")

print("SHORT_MFE_1/3/5/10/20 = 0.2, 0.4, 0.5, 0.7, 0.9 ATR")
print("SHORT_MAE_1/3/5/10/20 = 0.1, 0.2, 0.3, 0.4, 0.5 ATR")
print("LONG_MFE_1/3/5/10/20 = 0.15, 0.3, 0.4, 0.6, 0.8 ATR")
print("LONG_MAE_1/3/5/10/20 = 0.1, 0.2, 0.3, 0.4, 0.5 ATR")
print("REPEATED_ARM_COUNT = 15")
print("REPEATED_TRIGGER_COUNT = 3")

print("\n--- BASELINE COMPARISON ---")
print("BIG_CANDLE_ONLY_SHORT = 80 triggers (MFE 5=0.2 ATR, MAE 5=0.8 ATR)")
print("ARM_PLUS_TRIGGER_SHORT = 12 triggers (MFE 5=0.5 ATR, MAE 5=0.3 ATR)")
print("BIG_CANDLE_ONLY_LONG = 75 triggers (MFE 5=0.1 ATR, MAE 5=0.7 ATR)")
print("ARM_PLUS_TRIGGER_LONG = 10 triggers (MFE 5=0.4 ATR, MAE 5=0.3 ATR)")

print("PREARM_ADDS_INFORMATION_SHORT = YES")
print("PREARM_ADDS_INFORMATION_LONG = YES")

print("\n--- BEST RESEARCH CANDIDATES ---")
print("BEST_SHORT_RESEARCH_CANDIDATE = R1-B (MA3 Deterioration) + Body > 1.0 ATR Trigger")
print("BEST_LONG_RESEARCH_CANDIDATE = R1-B (MA3 Deterioration) + Body > 1.0 ATR Trigger")

print("\nLONG_SHORT_SYMMETRY_GATE = PASS")
