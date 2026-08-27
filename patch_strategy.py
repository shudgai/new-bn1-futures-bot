import re

with open("core/strategy.py", "r") as f:
    content = f.read()

# Replace the dummy structural_sl with the real one based on recent candles
old_sl = '"structural_sl": price * 0.9 if want_dir == 1 else price * 1.1,'
new_sl = '''"structural_sl": float(df['low'].iloc[-4:].min()) if want_dir == 1 else float(df['high'].iloc[-4:].max()),'''

if old_sl in content:
    content = content.replace(old_sl, new_sl)
    with open("core/strategy.py", "w") as f:
        f.write(content)
    print("Patched structural_sl in strategy.py")
else:
    print("Could not find old_sl to replace")
