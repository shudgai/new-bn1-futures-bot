import json

with open("data/paper_account.json") as f:
    data = json.load(f)

trade_id = 1790941926008
print(f"Tracking trade: {trade_id}")

# Let's see if there are any updates in position_meta
if str(trade_id) in data.get("position_meta", {}):
    meta = data["position_meta"][str(trade_id)]
    print(f"Meta: peak_gain_atr={meta.get('peak_gain_atr')}")

# Trace the logs for this trade
for log in data.get("logs", []):
    if str(trade_id) in str(log) or "LOBSTER" in str(log).upper() or "\u9f99\u867e" in str(log):
        # The logs might contain exit logic evaluations
        if "exit" in str(log).lower() or "eval" in str(log).lower():
            pass
            # print(log)

# Let's get the market data for the period after entry
