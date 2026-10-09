import json
import numpy as np
import pandas as pd

with open('scratch/analysis_dump_clean.json', 'r') as f:
    data = json.load(f)

model_t = data['model_t_summary']
baseline = data['baseline_summary']
metadata = data['metadata']

print(f"CACHE_WINDOWS_REUSED = {metadata['cache_reused']}")
print(f"CACHE_WINDOWS_DOWNLOADED = {metadata['cache_downloaded']}")
print(f"RATE_LIMIT_FAILURES = {metadata['rate_limit_failures']}")
print(f"ENTRY_TIME_ASSERTIONS_FAILED = {metadata['entry_time_assertions_failed']}")
print(f"SIMULATED_EXIT_ASSERTIONS_FAILED = {metadata['simulated_exit_assertions_failed']}")
print("")

df = pd.DataFrame(model_t)
unknown_total = df['unknown_count'].sum()
print(f"DATA_UNRESOLVED_COUNT = {unknown_total}")

print("\nCLEAN_LONG_BASELINE:")
for r in baseline:
    if r['side'] == 'LONG':
        if r['valid_exits'] > 0:
            mean_exit = r['exit_gain_atr_sum'] / r['valid_exits']
            mean_mfe = r['mfe_atr_sum'] / r['valid_exits']
            ratio = mean_exit / mean_mfe if mean_mfe > 0 else 0
        else: mean_exit = mean_mfe = ratio = 0
        print(f"Sample: {r['sample_count']}, Exits: {r['exit_count']}, Mean Exit: {mean_exit:.4f}, Mean MFE: {mean_mfe:.4f}, Retained: {ratio:.4f}")

print("\nCLEAN_SHORT_BASELINE:")
for r in baseline:
    if r['side'] == 'SHORT':
        if r['valid_exits'] > 0:
            mean_exit = r['exit_gain_atr_sum'] / r['valid_exits']
            mean_mfe = r['mfe_atr_sum'] / r['valid_exits']
            ratio = mean_exit / mean_mfe if mean_mfe > 0 else 0
        else: mean_exit = mean_mfe = ratio = 0
        print(f"Sample: {r['sample_count']}, Exits: {r['exit_count']}, Mean Exit: {mean_exit:.4f}, Mean MFE: {mean_mfe:.4f}, Retained: {ratio:.4f}")

for side in ['LONG', 'SHORT']:
    side_df = df[df['side'] == side].copy()
    side_df['mean_exit_gain_atr'] = side_df['exit_gain_atr_sum'] / side_df['valid_exits'].replace(0, 1)
    side_df['mean_mfe_atr'] = side_df['mfe_atr_sum'] / side_df['valid_exits'].replace(0, 1)
    side_df['profit_retained_ratio'] = side_df['mean_exit_gain_atr'] / side_df['mean_mfe_atr'].replace(0, 1)
    
    side_df = side_df.sort_values(by=['profit_retained_ratio', 'mean_exit_gain_atr'], ascending=[False, False])
    print(f"\nCLEAN_{side}_MODEL_T_SUMMARY (TOP 3):")
    print(side_df.head(3)[['arm_atr', 'trailing_distance_atr', 'armed_count', 'floor_hit_count', 'mean_exit_gain_atr', 'mean_mfe_atr', 'profit_retained_ratio']].to_string(index=False))

# Update parameter_grid_results_clean.csv
df.to_csv('scratch/parameter_grid_results_clean.csv', index=False)
