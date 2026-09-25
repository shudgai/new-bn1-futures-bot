import sys

with open("core/services/entry_gatekeeper.py", "r") as f:
    lines = f.readlines()

out = []
counter = 1
for line in lines:
    if "return False" in line:
        if line.strip() == "return False":
            indent = line[:line.find("return False")]
            out.append(f'{indent}print("!!! GUARDED_ENTRY RETURN FALSE {counter} !!!", flush=True)\n')
            counter += 1
        out.append(line)
    else:
        out.append(line)

with open("core/services/entry_gatekeeper.py", "w") as f:
    f.writelines(out)

