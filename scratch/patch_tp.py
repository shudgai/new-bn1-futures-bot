import json

paths = ["core/testnet_account.json", "core/paper_account.json", "data/paper_account.json", "data/testnet_account.json"]
for path in paths:
    try:
        with open(path, "r") as f:
            data = json.load(f)
        modified = False
        for symbol, meta in data.get("position_meta", {}).items():
            if "tp" in meta and meta["tp"] != 0.0:
                meta["tp"] = 0.0
                modified = True
            if "atr_tp" in meta and meta["atr_tp"] != 0.0:
                meta["atr_tp"] = 0.0
                modified = True
        if modified:
            with open(path, "w") as f:
                json.dump(data, f, indent=4)
            print(f"Patched {path}")
    except FileNotFoundError:
        pass
