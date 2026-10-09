import pandas as pd
import numpy as np
import json
import hashlib
import os

from core.services.strategies.entry_v2_strategy import EntryV2Strategy
import core.services.context_5m as context_5m
import core.services.strategies.entry_v2_strategy as entry_v2_strategy

def audit_dataset(df):
    row_count = len(df)
    first_ts = df["timestamp"].min()
    last_ts = df["timestamp"].max()
    duplicates = df.duplicated(subset=["timestamp"]).sum()
    
    diffs = df["timestamp"].diff().dropna()
    non_monotonic = (diffs < 0).sum()
    missing_1m = (diffs > 60000).sum()
    
    invalid_ohlc = ((df["high"] < df["low"]) | (df["high"] < df["open"]) | (df["high"] < df["close"]) | (df["low"] > df["open"]) | (df["low"] > df["close"])).sum()
    
    print("--- DATA AUDIT ---")
    print(f"row count: {row_count}")
    print(f"first timestamp: {first_ts}")
    print(f"last timestamp: {last_ts}")
    print(f"duplicate timestamps: {duplicates}")
    print(f"missing 1M intervals: {missing_1m}")
    print(f"non-monotonic timestamps: {non_monotonic}")
    print(f"OHLC validity failures: {invalid_ohlc}")

    open_time_aligned = (df["timestamp"] % 60000 == 0).all()
    print(f"timestamps aligned to minute: {open_time_aligned}")
    print("------------------\n")
    return df

def get_forward_metrics(df, entry_ts, entry_price, side, entry_atr):
    start_idx = df["timestamp"].searchsorted(entry_ts, side='right')
    future = df.iloc[start_idx:start_idx+30]
    
    res = {}
    if len(future) < 30:
        return None
        
    def calc_mfe_mae(slice_df):
        if side == "LONG":
            mfe = (slice_df["high"].max() - entry_price) / entry_atr
            mae = (entry_price - slice_df["low"].min()) / entry_atr
        else:
            mfe = (entry_price - slice_df["low"].min()) / entry_atr
            mae = (slice_df["high"].max() - entry_price) / entry_atr
        return mfe, mae

    res["MFE_5m"], res["MAE_5m"] = calc_mfe_mae(future.head(5))
    res["MFE_10m"], res["MAE_10m"] = calc_mfe_mae(future.head(10))
    res["MFE_20m"], res["MAE_20m"] = calc_mfe_mae(future.head(20))
    res["MFE_30m"], res["MAE_30m"] = calc_mfe_mae(future.head(30))
    
    def first_touch(target_mfe):
        for _, row in future.iterrows():
            if side == "LONG":
                mfe_hit = row["high"] >= entry_price + target_mfe * entry_atr
                mae_hit = row["low"] <= entry_price - 1.0 * entry_atr
            else:
                mfe_hit = row["low"] <= entry_price - target_mfe * entry_atr
                mae_hit = row["high"] >= entry_price + 1.0 * entry_atr
                
            if mfe_hit and mae_hit:
                return False
            if mfe_hit:
                return True
            if mae_hit:
                return False
        return False

    res["R0.5"] = first_touch(0.5)
    res["R1.0"] = first_touch(1.0)
    res["R1.5"] = first_touch(1.5)
    res["R2.0"] = first_touch(2.0)
    
    return res

# --- Fast Monkey Patching ---
global_precomputed_bars = []
global_precomputed_table = None
global_last_idx = 0

def fast_compute_closed_1m_bars(frame, decision_timestamp):
    global global_last_idx
    idx = global_last_idx
    while idx < len(global_precomputed_bars) and global_precomputed_bars[idx].close_timestamp <= decision_timestamp:
        idx += 1
    # Fast iteration by yielding only the delta!
    res = global_precomputed_bars[global_last_idx:idx]
    global_last_idx = idx
    return res

def fast_build_closed_5m_table(frame, decision_timestamp):
    return global_precomputed_table

def run_model(df, model_type):
    global global_last_idx
    global_last_idx = 0
    
    original_veto = context_5m.is_opposite_slope_veto
    
    def patch_veto(side, ctx):
        if model_type in ("A", "A0"):
            return False
        return original_veto(side, ctx)
        
    original_is_range = entry_v2_strategy._is_range
    def patch_is_range(bar):
        if model_type == "A0":
            return False
        return original_is_range(bar)
        
    entry_v2_strategy.is_opposite_slope_veto = patch_veto
    entry_v2_strategy._is_range = patch_is_range
    entry_v2_strategy.compute_closed_1m_bars = fast_compute_closed_1m_bars
    entry_v2_strategy.build_closed_5m_table = fast_build_closed_5m_table
    
    strategy = EntryV2Strategy()
    candidates = []
    
    df_len = len(df)
    ts_array = df["timestamp"].values
    
    # Initialize the watermark using the first chunk
    # This matches behavior of initialize_symbol internally when we first pass bars
    
    for i in range(21, df_len):
        decision_timestamp = int(ts_array[i] + 60000)
        
        cand = strategy.evaluate("LOBSTER", None, decision_timestamp)
        
        if cand:
            if model_type in ("A", "A0", "B") and cand.is_recovered:
                continue
            if cand.veto_5m_close_timestamp > decision_timestamp:
                 print("CAUSALITY VIOLATION: 5M candle close is in future!")
                 exit(1)
                
            atr_idx = df["timestamp"].searchsorted(cand.candidate_created_bar_id); entry_atr = df["atr"].iloc[atr_idx]
            cand_dict = vars(cand)
            cand_dict["entry_atr"] = entry_atr
            cand_dict["entry_price"] = cand.resume_close
            metrics = get_forward_metrics(df, cand.decision_timestamp, cand.resume_close, cand.side, entry_atr)
            if metrics:
                cand_dict.update(metrics)
                candidates.append(cand_dict)
                
    range_blocked = []
    if model_type == "A":
        for ev in strategy.events("LOBSTER"):
            if ev[1] == "RANGE_BLOCKED":
                bar_id = ev[0]
                side = ev[2]
                bar_idx = df["timestamp"].searchsorted(bar_id)
                row = df.iloc[bar_idx]
                decision_ts = int(bar_id + 60000)
                metrics = get_forward_metrics(df, decision_ts, row["close"], side, row["atr"])
                if metrics:
                    metrics["side"] = side
                    range_blocked.append(metrics)
                    
    entry_v2_strategy.is_opposite_slope_veto = original_veto
    entry_v2_strategy._is_range = original_is_range
    return candidates, strategy.counters, range_blocked

def main():
    global global_precomputed_bars, global_precomputed_table
    
    print("Loading data...")
    df = pd.read_csv("scratch/lobster_real_1m_history.csv")
    df["timestamp"] = df["timestamp"].astype(int)
    
    df["prev_close"] = df["close"].shift(1)
    df["tr1"] = df["high"] - df["low"]
    df["tr2"] = (df["high"] - df["prev_close"]).abs()
    df["tr3"] = (df["low"] - df["prev_close"]).abs()
    df["tr"] = df[["tr1", "tr2", "tr3"]].max(axis=1)
    df["atr"] = df["tr"].rolling(14).mean()
    
    df = audit_dataset(df)
    
    split_idx = int(len(df) * 0.7)
    dev_df = df.iloc[:split_idx].copy()
    
    print(f"Total bars: {len(df)}")
    print(f"Development bars: {len(dev_df)}")
    
    print("Precomputing indicators...")
    original_compute = getattr(entry_v2_strategy, "compute_closed_1m_bars_original", None)
    if original_compute is None:
        original_compute = entry_v2_strategy.compute_closed_1m_bars
        entry_v2_strategy.compute_closed_1m_bars_original = original_compute
        
    original_build_5m = getattr(entry_v2_strategy, "build_closed_5m_table_original", None)
    if original_build_5m is None:
        original_build_5m = entry_v2_strategy.build_closed_5m_table
        entry_v2_strategy.build_closed_5m_table_original = original_build_5m
        
    global_precomputed_bars = original_compute(dev_df, 10**15)
    
    table = original_build_5m(dev_df, 10**15)
    import copy
    global_precomputed_table = copy.copy(table)
    global_precomputed_table = context_5m.Closed5mTable(
        open_ts=table.open_ts, close_ts=table.close_ts, open=table.open,
        high=table.high, low=table.low, close=table.close, bar_count=table.bar_count,
        ma5=table.ma5, ma15=table.ma15, prev_ma5=table.prev_ma5, built_as_of=10**15
    )
    
    results = {}
    for model in ["A0", "A", "B", "C"]:
        print(f"Running Model {model}...")
        cands, counters, range_blocked = run_model(dev_df, model)
        results[model] = {"candidates": cands, "counters": counters, "range_blocked": range_blocked}
        
    with open("scratch/development_summary.json", "w") as f:
        json.dump(results, f, indent=2)
    print("Done! Wrote scratch/development_summary.json")

if __name__ == "__main__":
    main()
