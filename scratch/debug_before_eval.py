with open("core/services/symbol_runner.py", "r") as f:
    content = f.read()

old_block = """            exit_strategy = ProfitProtectionExitStrategy()
            
            velocity_drop_ratio = engine.get_velocity_drop_ratio(symbol)
            exit_reason = exit_strategy.evaluate_exit(existing_pos, channel_df, channel_price, velocity_drop_ratio=velocity_drop_ratio)"""

new_block = """            exit_strategy = ProfitProtectionExitStrategy()
            
            velocity_drop_ratio = engine.get_velocity_drop_ratio(symbol)
            with open("/tmp/before_eval.log", "a") as dbgf:
                dbgf.write(f"Calling evaluate_exit for {symbol}\\n")
            exit_reason = exit_strategy.evaluate_exit(existing_pos, channel_df, channel_price, velocity_drop_ratio=velocity_drop_ratio)"""

content = content.replace(old_block, new_block)

with open("core/services/symbol_runner.py", "w") as f:
    f.write(content)
