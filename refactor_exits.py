import re

with open("core/engine.py", "r", encoding="utf-8") as f:
    code = f.read()

def_idx = code.find("    async def _run_structured_exits(self):")
if def_idx == -1:
    print("Could not find _run_structured_exits def")
    exit(1)

start_marker = "                for symbol, position in list(self.account.positions.items()):"
end_marker = "            except asyncio.CancelledError:"

start_idx = code.find(start_marker, def_idx)
end_idx = code.find(end_marker, start_idx)

if start_idx == -1 or end_idx == -1:
    print("Could not find the loop block")
    exit(1)

loop_body = code[start_idx:end_idx]

new_method_lines = []
new_method_lines.append("    async def _process_single_exit(self, symbol, position):")
new_method_lines.append("        try:")
new_method_lines.append("            managed_modes = {")
new_method_lines.append("                'BREAKOUT', 'SUPPORT_PULLBACK', 'MOMENTUM_CROSS',")
new_method_lines.append("                'MA5_REVERSAL', 'MA5_BOTTOM_LIMIT', 'CURRENT_MAKER', 'PULLBACK',")
new_method_lines.append("            }")

# Extract lines before the last `await asyncio.sleep(STRUCTURED_EXIT_INTERVAL_SEC)`
loop_lines = loop_body.split('\n')
# Remove trailing empty lines and the final sleep
while loop_lines and (not loop_lines[-1].strip() or loop_lines[-1].strip() == "await asyncio.sleep(STRUCTURED_EXIT_INTERVAL_SEC)"):
    loop_lines.pop()

for line in loop_lines[1:]:
    if not line.strip():
        new_method_lines.append("")
        continue
    
    if line.startswith(" " * 20):
        unindented = line[8:]
        if unindented.strip() == "continue":
            unindented = unindented.replace("continue", "return")
        if unindented.strip() == "break":
            unindented = unindented.replace("break", "return")
        new_method_lines.append(unindented)
    else:
        new_method_lines.append(line)

new_method_lines.append("        except Exception as e:")
new_method_lines.append("            self.account.log(f'⚠️ [{symbol}] 出場監控例外: {e}', 'WARNING')")

new_method_code = "\n".join(new_method_lines)

new_loop = """                tasks = [
                    self._process_single_exit(symbol, position)
                    for symbol, position in list(self.account.positions.items())
                ]
                if tasks:
                    await asyncio.gather(*tasks, return_exceptions=True)
                await asyncio.sleep(STRUCTURED_EXIT_INTERVAL_SEC)
"""

new_code = code[:def_idx] + new_method_code + "\n\n" + code[def_idx:start_idx] + new_loop + code[end_idx:]

with open("core/engine.py", "w", encoding="utf-8") as f:
    f.write(new_code)

print("Successfully refactored _run_structured_exits!")
