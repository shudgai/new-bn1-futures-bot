import re

with open("core/engine.py", "r") as f:
    content = f.read()

# 1. Update _place_structured_entry_locked to use Limit orders when velocity slows down
target_1 = """            entry_mode = signal["entry_mode"]"""
replacement_1 = """            entry_mode = signal["entry_mode"]
            # V5.0 極致點位捕捉：若速度放緩，則強制轉為限價單(LIMIT)進場
            if self.get_velocity_slowdown(symbol):
                self.account.log(f"⚡ {symbol} 偵測到價格降速 >= 30%，自動轉為限價單 (Limit Order) 進場", "INFO")
                signal["is_limit"] = True
"""
content = content.replace(target_1, replacement_1)

# 2. Update _process_single_symbol to inject velocity_slowdown into the position state for trailing exit
target_2 = """                    if getattr(self.account, "pending_limit_orders", {}).get(symbol):
                        continue"""
replacement_2 = """                    if getattr(self.account, "pending_limit_orders", {}).get(symbol):
                        continue
                    
                    # V5.0 動態回吐：若進入目標區且速度放緩，標記供退場服務使用
                    if self.get_velocity_slowdown(symbol):
                        pos["velocity_slowdown"] = True
                    else:
                        pos.pop("velocity_slowdown", None)
"""
content = content.replace(target_2, replacement_2)

with open("core/engine.py", "w") as f:
    f.write(content)
print("Updated core/engine.py for Order Hooks")
