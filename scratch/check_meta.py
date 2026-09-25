import json
for file in ["data/paper_account.json", "data/testnet_account.json"]:
    try:
        with open(file) as f:
            data = json.load(f)
            print(f"File: {file}")
            meta = data.get("position_meta", {})
            for sym, m in meta.items():
                print(f"  {sym}: has cpp={'channel_profit_protection' in m}")
    except Exception as e:
        print(f"Error {file}: {e}")
