import re

with open("core/services/symbol_runner.py", "r") as f:
    content = f.read()

old_loop = """            for direct_side in ("LONG", "SHORT"):
                allowed, reason, entry_decision = entry_strategy.evaluate_entry(
                    channel_df, channel_price, direct_side, velocity_slowdown=velocity_slowdown
                )
                if not allowed or entry_decision.get("action") != "ENTER":
                    continue"""
new_loop = """            for direct_side in ("LONG", "SHORT"):
                allowed, reason, entry_decision = entry_strategy.evaluate_entry(
                    channel_df, channel_price, direct_side, velocity_slowdown=velocity_slowdown
                )
                if not allowed or entry_decision.get("action") != "ENTER":
                    print(f"[{symbol}] {direct_side} Rejected: {reason}", flush=True)
                    continue"""
content = content.replace(old_loop, new_loop)

with open("core/services/symbol_runner.py", "w") as f:
    f.write(content)
