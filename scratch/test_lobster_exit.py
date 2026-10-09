import pandas as pd

closes = [
    0.04882, # 23:47
    0.04905, # 23:48
    0.04920, # 23:49 (Entry)
    0.04932, # 23:50
    0.04949, # 23:51
    0.04953, # 23:52
    0.04969, # 23:53
    0.04981, # 23:54
    0.04963, # 23:55 (Shooting star, our "23:57")
    0.04896, # 23:56 (Waterfall)
]

highs = [
    0.04900,
    0.04920,
    0.04922,
    0.04950,
    0.04977,
    0.04962,
    0.04980,
    0.04991,
    0.05028,
    0.04972,
]

lows = [
    0.04880,
    0.04885,
    0.04896,
    0.04916,
    0.04920,
    0.04940,
    0.04946,
    0.04959,
    0.04944,
    0.04860,
]

opens = [
    0.04880,
    0.04885,
    0.04905,
    0.04922,
    0.04933,
    0.04953,
    0.04952,
    0.04965,
    0.04983,
    0.04963,
]

# We need a robust false positive test.
# Let's test the logic.

def test_logic():
    consecutive_above_ma3 = 0
    consecutive_low_above_ma5 = 0
    atr = 0.000738
    
    for i in range(2, len(closes)):
        c, o, h, l = closes[i], opens[i], highs[i], lows[i]
        
        ma3 = sum(closes[max(0, i-2):i+1]) / 3
        ma5 = sum(closes[max(0, i-4):i+1]) / 5
        
        # mature swing check evaluated BEFORE this bar's close? No, mature swing is the state BEFORE this bar.
        # Let's compute previous state:
        prev_ma3 = sum(closes[max(0, i-3):i]) / 3
        prev_ma5 = sum(closes[max(0, i-5):i]) / 5
        
        if closes[i-1] > prev_ma3:
            consecutive_above_ma3 += 1
        else:
            consecutive_above_ma3 = 0
            
        if lows[i-1] > prev_ma5:
            consecutive_low_above_ma5 += 1
        else:
            consecutive_low_above_ma5 = 0
            
        mature = consecutive_above_ma3 >= 4 or consecutive_low_above_ma5 >= 4
        
        # Qualified pressure on THIS closed bar:
        body = abs(c - o)
        upper_wick = h - max(o, c)
        span = h - l
        body_ratio = body / span if span > 0 else 0
        close_pos = (c - l) / span if span > 0 else 0
        
        cond_pinbar = (upper_wick > 0.5 * atr) and (upper_wick > 2 * body) and (close_pos < 0.4)
        cond_engulfing = (c < o) and (body > 0.5 * atr) and (c < ma3)
        cond_doji = (body_ratio < 0.25) and (c < o) and (c < ma3)
        
        pressure = cond_pinbar or cond_engulfing or cond_doji
        
        print(f"Bar {i} | mature={mature} ({consecutive_above_ma3}, {consecutive_low_above_ma5}) | pinbar={cond_pinbar} engulf={cond_engulfing} doji={cond_doji} -> EXIT={mature and pressure}")

test_logic()
