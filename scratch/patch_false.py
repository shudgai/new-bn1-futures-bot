import sys

with open("core/paper_account.py", "r") as f:
    lines = f.readlines()

out = []
false_count = 1
for line in lines:
    if "return False" in line:
        # replace `return False` with `import logging; logging.getLogger("uvicorn.error").info(f"PAPER RET FALSE {false_count}"); return False`
        if line.strip() == "return False":
            indent = line[:line.find("return False")]
            out.append(f'{indent}import logging; logging.getLogger("uvicorn.error").info("PAPER RET FALSE {false_count}")\n')
            out.append(line)
            false_count += 1
        else:
            out.append(line)
    else:
        out.append(line)

with open("core/paper_account.py", "w") as f:
    f.writelines(out)

