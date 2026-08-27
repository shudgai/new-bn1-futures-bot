import re

with open("core/engine.py", "r", encoding="utf-8") as f:
    code = f.read()

start_marker = "                    for symbol in symbols_snapshot:"
end_marker = "                    if detected_candidates:"

start_idx = code.find(start_marker)
end_idx = code.find(end_marker, start_idx)

if start_idx == -1 or end_idx == -1:
    print("Could not find the loop block")
    exit(1)

loop_body = code[start_idx:end_idx]

new_method_lines = []
new_method_lines.append("    async def _process_single_symbol(self, symbol, now_time, btc_1m_turn, daily_halt):")
new_method_lines.append("        signal_progress = []")
new_method_lines.append("        detected_candidates = []")
new_method_lines.append("        try:")

for line in loop_body.split('\n')[1:]:
    if not line.strip():
        new_method_lines.append("")
        continue
    
    if line.startswith(" " * 24):
        unindented = line[12:]
        if unindented.strip() == "continue":
            unindented = unindented.replace("continue", "return signal_progress, detected_candidates")
        new_method_lines.append(unindented)
    else:
        new_method_lines.append(line)

new_method_lines.append("        except Exception as e:")
new_method_lines.append("            self.account.log(f'⚠️ [{symbol}] 處理失敗: {e}', 'WARNING')")
new_method_lines.append("        return signal_progress, detected_candidates")

new_method_code = "\n".join(new_method_lines)

new_loop = """                    tasks = [
                        self._process_single_symbol(symbol, now_time, btc_1m_turn, daily_halt)
                        for symbol in symbols_snapshot
                    ]
                    results = await asyncio.gather(*tasks, return_exceptions=True)
                    for res in results:
                        if isinstance(res, Exception):
                            self.account.log(f"⚠️ 幣種掃描例外: {res}", "WARNING")
                        else:
                            prog, cands = res
                            signal_progress.extend(prog)
                            detected_candidates.extend(cands)
"""

main_loop_def_idx = code.rfind("    async def _main_loop(self):", 0, start_idx)
if main_loop_def_idx == -1:
    print("Could not find _main_loop def")
    exit(1)

new_code = code[:main_loop_def_idx] + new_method_code + "\n\n" + code[main_loop_def_idx:start_idx] + new_loop + code[end_idx:]

with open("core/engine.py", "w", encoding="utf-8") as f:
    f.write(new_code)

print("Successfully refactored _main_loop!")
