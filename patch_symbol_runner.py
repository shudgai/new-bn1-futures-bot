import re

with open("core/services/symbol_runner.py", "r") as f:
    content = f.read()

old_call = """            for direct_side in ("LONG", "SHORT"):
                allowed, reason, entry_decision = entry_strategy.evaluate_entry(
                    channel_df, channel_price, direct_side,
                )"""
new_call = """            from core.engine import get_velocity_slowdown
            velocity_slowdown = get_velocity_slowdown(engine.tick_buffers.get(symbol, []))
            for direct_side in ("LONG", "SHORT"):
                allowed, reason, entry_decision = entry_strategy.evaluate_entry(
                    channel_df, channel_price, direct_side, velocity_slowdown=velocity_slowdown
                )"""
content = content.replace(old_call, new_call)

with open("core/services/symbol_runner.py", "w") as f:
    f.write(content)
