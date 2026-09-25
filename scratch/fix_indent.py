with open("core/services/entry_service.py", "r") as f:
    lines = f.readlines()

new_lines = []
in_func = False
for i, line in enumerate(lines):
    if line.startswith("def check_entry_signals("):
        in_func = True
        new_lines.append(line)
        continue
    
    if in_func:
        if line.startswith("if momentum_signal and momentum_signal"):
            new_lines.append("    " + line)
        elif line.startswith("    return momentum_signal") and lines[i-1].startswith("if momentum_signal"):
            new_lines.append("    " + line)
        elif not line.startswith(" ") and line.strip() != "":
            # indent lines that were accidentally dedented
            new_lines.append("    " + line)
        else:
            new_lines.append(line)
    else:
        new_lines.append(line)

with open("core/services/entry_service.py", "w") as f:
    f.writelines(new_lines)
