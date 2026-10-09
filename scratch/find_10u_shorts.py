import json
import pandas as pd

with open("data/paper_account.json", "r") as f:
    data = json.load(f)

df = pd.read_csv("scratch/lobster_real_1m_history.csv")

for t in data.get("trades", []):
    if t.get("action") == "OPEN_SHORT" and ("LOBSTER" in t.get("symbol").upper() or "龙虾" in t.get("symbol")):
        # Find close time
        close_t = None
        for c in data.get("trades", []):
            if c.get("action") == "CLOSE_SHORT" and c.get("symbol") == t.get("symbol") and c.get("id") > t.get("id"):
                if close_t is None or c.get("id") < close_t.get("id"):
                    close_t = c
        
        if not close_t:
            continue
            
        entry_price = t["price"]
        qty = t["qty"]
        start_time = t["id"]
        end_time = close_t["id"]
        
        # Check candles
        mask = (df["timestamp"] >= start_time - 60000) & (df["timestamp"] <= end_time + 60000)
        subset = df[mask]
        
        if subset.empty:
            continue
            
        min_low = subset["low"].min()
        max_gross = (entry_price - min_low) * qty
        fee = t["fee"] + (close_t["fee"] if "fee" in close_t else 0)
        max_net = max_gross - fee
        
        if max_net >= 10:
            print(f"Trade ID {t['id']} (Time: {t['time']}) reached Max Net PNL = {max_net:.2f} at Low {min_low}. Exit PNL was {close_t.get('pnl')}")

