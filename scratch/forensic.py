import json

with open('scratch/analysis_dump.json', 'r') as f:
    d = json.load(f)

baseline = d['baseline_details']
modelt = d['model_t_details']

print("Forensic Check for Exit Before Entry:")
for b in baseline:
    if b['exit_time'] < b['trade_id']:
        print(f"FAIL: {b['symbol']} {b['side']} Entry: {b['trade_id']} Exit: {b['exit_time']}")

