with open("services/api.py", "r") as f:
    content = f.read()

old_block = """    for symbol, pos in engine.account.positions.items():
        merged = dict(pos)
        merged["trigger"] = engine.position_triggers.get(symbol, {"active": False, "reasons": []})
        entry = float(merged.get("entry_price") or 0.0)"""

new_block = """    for symbol, pos in engine.account.positions.items():
        merged = dict(pos)
        merged["trigger"] = engine.position_triggers.get(symbol, {"active": False, "reasons": []})
        
        # 確保前端能拿到最新的鎖利狀態 (從 meta 中還原，避免被 API refresh 覆蓋)
        meta = engine.account.position_meta.get(symbol, {})
        if "channel_profit_protection" in meta:
            merged["channel_profit_protection"] = meta["channel_profit_protection"]
            
        entry = float(merged.get("entry_price") or 0.0)"""

content = content.replace(old_block, new_block)

with open("services/api.py", "w") as f:
    f.write(content)
