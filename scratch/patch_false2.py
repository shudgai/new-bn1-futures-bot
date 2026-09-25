import sys

with open("core/paper_account.py", "r") as f:
    lines = f.readlines()

out = []
for line in lines:
    if "return False" in line:
        if line.strip() == "return False":
            indent = line[:line.find("return False")]
            out.append(f'{indent}print("!!! PAPER_ACCOUNT RETURN FALSE !!!", flush=True)\n')
        out.append(line)
    else:
        out.append(line)

with open("core/paper_account.py", "w") as f:
    f.writelines(out)

with open("core/testnet_account.py", "r") as f:
    lines = f.readlines()

out = []
for line in lines:
    if "return False" in line:
        if line.strip() == "return False":
            indent = line[:line.find("return False")]
            out.append(f'{indent}print("!!! TESTNET_ACCOUNT RETURN FALSE !!!", flush=True)\n')
        out.append(line)
    else:
        out.append(line)

with open("core/testnet_account.py", "w") as f:
    f.writelines(out)

