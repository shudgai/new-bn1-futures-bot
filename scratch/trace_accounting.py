import pandas as pd
import json

from core.services.strategies.entry_v2_strategy import EntryV2Strategy
import core.services.context_5m as context_5m

def main():
    print("Loading data...")
    df = pd.read_csv("scratch/lobster_real_1m_history.csv")
    df["timestamp"] = df["timestamp"].astype(int)
    
    # Development slice only
    split_idx = int(len(df) * 0.7)
    dev_df = df.iloc[:split_idx].copy()
    
    original_compute = getattr(EntryV2Strategy, "compute_closed_1m_bars_original", None)
    if original_compute is None:
        import core.services.strategies.entry_v2_strategy as mod
        original_compute = mod.compute_closed_1m_bars
    
    global_precomputed_bars = original_compute(dev_df, 10**15)
    
    table = context_5m.build_closed_5m_table(dev_df, 10**15)
    import copy
    global_precomputed_table = context_5m.Closed5mTable(
        open_ts=table.open_ts, close_ts=table.close_ts, open=table.open,
        high=table.high, low=table.low, close=table.close, bar_count=table.bar_count,
        ma5=table.ma5, ma15=table.ma15, prev_ma5=table.prev_ma5, built_as_of=10**15
    )

    strat_A = EntryV2Strategy()
    strat_B = EntryV2Strategy()
    
    # We will feed them exactly the same bars
    # But wait, evaluate() takes frame and decision_timestamp.
    # Let's just mock the veto function per call.
    original_veto = context_5m.is_opposite_slope_veto
    
    # We can just iterate through precomputed bars!
    # And manually call on_closed_bar for both strategies.
    
    # Initialize symbols
    strat_A._symbols["LOBSTER"] = strat_A._symbols.get("LOBSTER") or __import__("core.services.strategies.entry_v2_strategy", fromlist=["_SymbolState"])._SymbolState(startup_watermark_bar_id=global_precomputed_bars[0].bar_id, last_processed_bar_id=global_precomputed_bars[0].bar_id)
    strat_B._symbols["LOBSTER"] = strat_B._symbols.get("LOBSTER") or __import__("core.services.strategies.entry_v2_strategy", fromlist=["_SymbolState"])._SymbolState(startup_watermark_bar_id=global_precomputed_bars[0].bar_id, last_processed_bar_id=global_precomputed_bars[0].bar_id)

    events_A = []
    events_B = []
    
    for bar in global_precomputed_bars[1:]:
        ctx = context_5m.select_closed_5m(global_precomputed_table, bar.close_timestamp)
        
        import core.services.strategies.entry_v2_strategy as mod
        mod.is_opposite_slope_veto = lambda s, c: False
        cand_A = strat_A.on_closed_bar("LOBSTER", bar, ctx)
        if cand_A:
            events_A.append((cand_A.resume_bar_id, cand_A.side))
            
        # Run B (Veto, No Recovery)
        mod.is_opposite_slope_veto = original_veto
        cand_B = strat_B.on_closed_bar("LOBSTER", bar, ctx)
        if cand_B:
            if not cand_B.is_recovered:
                events_B.append((cand_B.resume_bar_id, cand_B.side))
                
    context_5m.is_opposite_slope_veto = original_veto
    
    set_A = set(events_A)
    set_B = set(events_B)
    
    print(f"Model A total: {len(set_A)}")
    print(f"Model B total: {len(set_B)}")
    
    # Events in A but not in B
    a_not_b = set_A - set_B
    print(f"In A but not in B: {len(a_not_b)}")
    
    # Now we need to classify why they are in A but not in B.
    # For each such event (resume_bar_id, side), let's look at strat_B's event log around that time!
    # We can rebuild the B log.
    b_log = strat_B.events("LOBSTER")
    
    b_log_dict = {}
    for ev in b_log:
        bar_id = ev[0]
        event = ev[1]
        b_log_dict.setdefault(bar_id, []).append(event)
        
    counts = {
        "VETO_DESTROY": 0,
        "CONFIRM_DISCARDED_WHILE_VETO": 0,
        "CANCEL_STRUCTURE": 0,
        "STALE_OR_OTHER": 0
    }
    
    for (bar_id, side) in a_not_b:
        # What happened in B at this exact bar?
        b_events_at_bar = b_log_dict.get(bar_id, [])
        if "VETO_DESTROY" in b_events_at_bar:
            counts["VETO_DESTROY"] += 1
        elif "CONFIRM_DISCARDED_WHILE_VETO" in b_events_at_bar:
            counts["CONFIRM_DISCARDED_WHILE_VETO"] += 1
        else:
            # Maybe it wasn't even a pullback in B because B was in SEARCH but had no formation?
            # Or B had its structure cancelled earlier?
            counts["STALE_OR_OTHER"] += 1
            
    print("Breakdown of A not in B:")
    for k, v in counts.items():
        print(f"  {k}: {v}")
        
    # We also need to check ORIGINAL_VETOED counter in B.
    print(f"\nModel B Counters:")
    for k, v in strat_B.counters.items():
        print(f"  {k}: {v}")

if __name__ == "__main__":
    main()
