# R1.6 Alternative ARM Families Design
print("DISCOVERY_SET = 2026-08-01 to 2026-08-31, 1000PEPEUSDT, WIFUSDT, DOGEUSDT")
print("FINAL_HOLDOUT_SET = 2026-07-01 to 2026-07-31, 1000FLOKIUSDT, SHIBUSDT")
print("OVERLAP = NO")

def print_model(name, short_f, long_f):
    print(f"\nMODEL_NAME = {name}")
    print(f"SHORT_FORMULA = {short_f}")
    print(f"LONG_FORMULA = {long_f}")
    print("INPUT_FIELDS = open, high, low, close, kc_upper, kc_lower, kc_middle, ma3, ma5")
    print("CLOSED_CANDLE_FIELDS = close, high, low")
    print("LIVE_FIELDS = live_price (for trigger)")
    print("PARAMETER_GRID = Body/ATR: [0.5, 0.7, 1.0, 1.2]")
    print("ARM_EVENT_ID_FORMULA = {symbol}_{timestamp}_{direction}_{name}")
    
print_model("R1-A WEAK/OPPOSITE STRUCTURE", 
            "2 of last 3 closed candles are green, but total net price change < 0.5 ATR",
            "2 of last 3 closed candles are red, but total net price change > -0.5 ATR")

print_model("R1-C FAILED CONTINUATION",
            "Prior 5 bars bullish trend; last closed high < previous high AND last closed low < previous low",
            "Prior 5 bars bearish trend; last closed low > previous low AND last closed high > previous high")

print_model("R1-D LOCAL STRUCTURE",
            "Live price breaks below the lowest low of the prior 3 closed candles",
            "Live price breaks above the highest high of the prior 3 closed candles")

print_model("R1-E KC LOCATION STRUCTURE",
            "Prior closed candle high > kc_upper AND close < kc_upper",
            "Prior closed candle low < kc_lower AND close > kc_lower")

print("\nR1_F_EXECUTED = NO")
print("R1_F_REASON = PREDECLARED_COMBINATIONS_REQUIRE_INDIVIDUAL_RESULTS_FIRST")
