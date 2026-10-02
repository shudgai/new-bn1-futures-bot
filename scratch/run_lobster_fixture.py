import json
from swing_replay import SwingReplayEngine

with open('scratch/lobster_klines.json', 'r') as f:
    klines = json.load(f)
    
engine = SwingReplayEngine(
    entry_price=0.0520148,
    side='SHORT',
    initial_stop=0.05543,
    entry_atr=0.002277
)

start_ms = 1790910000

for i, k in enumerate(klines):
    if k['time'] < start_ms: continue
    t_str = f"11:{int((k['time'] - 1790906400)/60)%60:02d}"
    
    prev_k = klines[i-1] if i > 0 else k
    k['ma5_slope'] = (k['ma5'] - prev_k['ma5']) if k['ma5'] and prev_k['ma5'] else 0
    k['ma15_slope'] = (k['ma15'] - prev_k['ma15']) if k['ma15'] and prev_k['ma15'] else 0
    
    engine.process_kline(k, t_str)
    
print("Lobster Fixture Replay Complete!")
for name, f in engine.floors.items():
    print(f"\n{name}:")
    for k, v in f.items():
        if k != 'active':
            print(f"  {k}: {v}")
