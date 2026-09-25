import sys

with open("core/services/entry_gatekeeper.py", "r") as f:
    lines = f.readlines()

out = []
counter = 1
for line in lines:
    if "return False" in line:
        indent = line[:line.find("return False")]
        out.append(f'{indent}import logging; logging.getLogger("uvicorn.error").info("!!! GUARDED_ENTRY RETURN FALSE {counter} !!!")\n')
        out.append(line)
        counter += 1
    else:
        out.append(line)

with open("core/services/entry_gatekeeper.py", "w") as f:
    f.writelines(out)

with open("core/paper_account.py", "r") as f:
    lines = f.readlines()

out = []
counter = 1
for line in lines:
    if "return False" in line:
        indent = line[:line.find("return False")]
        out.append(f'{indent}import logging; logging.getLogger("uvicorn.error").info("!!! PAPER_ACCOUNT RETURN FALSE {counter} !!!")\n')
        out.append(line)
        counter += 1
    elif ") -> bool:" in line:
        out.append(line)
        out.append('        import logging; logging.getLogger("uvicorn.error").info("!!! INSIDE PAPER ACCOUNT OPEN_POSITION !!!")\n')
    else:
        out.append(line)

with open("core/paper_account.py", "w") as f:
    f.writelines(out)

