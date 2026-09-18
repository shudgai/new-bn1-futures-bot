import re

with open("core/services/symbol_runner.py", "r") as f:
    content = f.read()

old_exit = """            exit_strategy = DualTrackExitStrategy(fee=TAKER_FEE_RATE, slippage=SLIPPAGE_PCT)
            exit_reason = exit_strategy.evaluate_exit(existing_pos, channel_df, channel_price)"""
new_exit = """            exit_strategy = DualTrackExitStrategy(fee=TAKER_FEE_RATE, slippage=SLIPPAGE_PCT)
            from core.engine import get_velocity_slowdown
            velocity_slowdown = get_velocity_slowdown(engine.tick_buffers.get(symbol, []))
            exit_reason = exit_strategy.evaluate_exit(existing_pos, channel_df, channel_price, velocity_slowdown=velocity_slowdown)"""
content = content.replace(old_exit, new_exit)

with open("core/services/symbol_runner.py", "w") as f:
    f.write(content)
