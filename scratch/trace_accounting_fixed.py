import pandas as pd
import json

from core.services.strategies.entry_v2_strategy import EntryV2Strategy
import core.services.context_5m as context_5m
import core.services.strategies.entry_v2_strategy as mod

def main():
    df = pd.read_csv("scratch/lobster_real_1m_history.csv")
    df["timestamp"] = df["timestamp"].astype(int)
    split_idx = int(len(df) * 0.7)
    dev_df = df.iloc[:split_idx].copy()
    
    global_precomputed_bars = mod.compute_closed_1m_bars(dev_df, 10**15)
    table = context_5m.build_closed_5m_table(dev_df, 10**15)
    global_precomputed_table = context_5m.Closed5mTable(
        open_ts=table.open_ts, close_ts=table.close_ts, open=table.open,
        high=table.high, low=table.low, close=table.close, bar_count=table.bar_count,
        ma5=table.ma5, ma15=table.ma15, prev_ma5=table.prev_ma5, built_as_of=10**15
    )

    strat_A = EntryV2Strategy()
    strat_B = EntryV2Strategy()
    
    original_veto = context_5m.is_opposite_slope_veto
    
    dummy_df = dev_df.head(100)
    strat_A.initialize_symbol("LOBSTER", dummy_df, dummy_df["timestamp"].iloc[-1])
    strat_B.initialize_symbol("LOBSTER", dummy_df, dummy_df["timestamp"].iloc[-1])
    # Actually initialize sets the last_processed_bar_id to watermark, which is fine.

    a_events = []
    b_events = []
    
    for bar in global_precomputed_bars[1:]:
        ctx = context_5m.select_closed_5m(global_precomputed_table, bar.close_timestamp)
        
        mod.is_opposite_slope_veto = lambda s, c: False
        cand_A = strat_A.on_closed_bar("LOBSTER", bar, ctx)
        if cand_A: a_events.append((bar.bar_id, cand_A.side))
            
        mod.is_opposite_slope_veto = original_veto
        cand_B = strat_B.on_closed_bar("LOBSTER", bar, ctx)
        if cand_B and not cand_B.is_recovered:
            b_events.append((bar.bar_id, cand_B.side))
            
    a_not_b = set(a_events) - set(b_events)
    
    b_log = strat_B.events("LOBSTER")
    b_log_dict = {}
    for ev in b_log:
        b_log_dict.setdefault(ev[0], []).append(ev[1])
        
    counts = {"VETO_DESTROY": 0, "CONFIRM_DISCARDED_WHILE_VETO": 0, "CANCEL_STRUCTURE": 0, "STALE_OR_OTHER": 0}
    for (bar_id, side) in a_not_b:
        evs = b_log_dict.get(bar_id, [])
        if "VETO_DESTROY" in evs: counts["VETO_DESTROY"] += 1
        elif "CONFIRM_DISCARDED_WHILE_VETO" in evs: counts["CONFIRM_DISCARDED_WHILE_VETO"] += 1
        else: counts["STALE_OR_OTHER"] += 1
        
    print(counts)

if __name__ == "__main__":
    main()
