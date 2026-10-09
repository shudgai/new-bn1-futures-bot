import pandas as pd
import numpy as np
import json
import core.services.strategies.entry_v2_strategy as mod

def main():
    df = pd.read_csv("scratch/lobster_real_1m_history.csv")
    df["timestamp"] = df["timestamp"].astype(int)
    
    df["prev_close"] = df["close"].shift(1)
    df["tr1"] = df["high"] - df["low"]
    df["tr2"] = (df["high"] - df["prev_close"]).abs()
    df["tr3"] = (df["low"] - df["prev_close"]).abs()
    df["tr"] = df[["tr1", "tr2", "tr3"]].max(axis=1)
    df["atr"] = df["tr"].rolling(14).mean()
    
    split_idx = int(len(df) * 0.7)
    dev_df = df.iloc[:split_idx].copy()
    
    bars = mod.compute_closed_1m_bars(dev_df, 10**15)
    
    # We want features at the time of CANDIDATE CREATION (decision_timestamp = resume bar close)
    # Let's load development_summary.json to get the candidates
    with open("scratch/development_summary.json", "r") as f:
        summary = json.load(f)
        
    cands = summary["A"]["candidates"]
    cand_bar_ids = {c["resume_bar_id"]: c["side"] for c in cands}
    
    all_ma_sep = []
    all_ma15_slope = []
    
    cand_ma_sep_long = []
    cand_ma15_slope_long = []
    cand_ma_sep_short = []
    cand_ma15_slope_short = []
    
    # We need ATR for each bar. Let's make a dict.
    atr_dict = dict(zip(dev_df["timestamp"], dev_df["atr"]))
    
    # For MA15 slope, we need prev MA15 from 5 bars ago.
    # We can compute it by iterating.
    history_ma15 = []
    
    for i, b in enumerate(bars):
        atr = atr_dict.get(b.bar_id)
        if not atr or atr == 0 or np.isnan(atr):
            history_ma15.append(b.ma15)
            continue
            
        sep = abs(b.ma5 - b.ma15) / atr
        all_ma_sep.append(sep)
        
        slope15 = 0
        if i >= 5:
            slope15 = abs(b.ma15 - history_ma15[i-5]) / atr
        all_ma15_slope.append(slope15)
        
        history_ma15.append(b.ma15)
        
        if b.bar_id in cand_bar_ids:
            side = cand_bar_ids[b.bar_id]
            if side == "LONG":
                cand_ma_sep_long.append(sep)
                cand_ma15_slope_long.append(slope15)
            else:
                cand_ma_sep_short.append(sep)
                cand_ma15_slope_short.append(slope15)
                
    print("--- MA5/MA15 Separation / ATR ---")
    print(f"ALL bars: Median={np.nanmedian(all_ma_sep):.2f}, 25th={np.nanpercentile(all_ma_sep, 25):.2f}")
    print(f"CAND LONG: Median={np.nanmedian(cand_ma_sep_long):.2f}, 25th={np.nanpercentile(cand_ma_sep_long, 25):.2f}, 10th={np.nanpercentile(cand_ma_sep_long, 10):.2f}")
    print(f"CAND SHORT: Median={np.nanmedian(cand_ma_sep_short):.2f}, 25th={np.nanpercentile(cand_ma_sep_short, 25):.2f}, 10th={np.nanpercentile(cand_ma_sep_short, 10):.2f}")
    
    print("\n--- MA15 Slope (5 bars) / ATR ---")
    print(f"ALL bars: Median={np.nanmedian(all_ma15_slope):.2f}, 25th={np.nanpercentile(all_ma15_slope, 25):.2f}")
    print(f"CAND LONG: Median={np.nanmedian(cand_ma15_slope_long):.2f}, 25th={np.nanpercentile(cand_ma15_slope_long, 25):.2f}, 10th={np.nanpercentile(cand_ma15_slope_long, 10):.2f}")
    print(f"CAND SHORT: Median={np.nanmedian(cand_ma15_slope_short):.2f}, 25th={np.nanpercentile(cand_ma15_slope_short, 25):.2f}, 10th={np.nanpercentile(cand_ma15_slope_short, 10):.2f}")

if __name__ == "__main__":
    main()
