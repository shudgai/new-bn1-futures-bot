import re

filepath = "core/services/entry_contract.py"
with open(filepath, "r") as f:
    content = f.read()

# Replace the specific indentation error
content = content.replace("        try:\n        side = ck_direction(frame)\n", "    try:\n        side = ck_direction(frame)\n", 1)

with open(filepath, "w") as f:
    f.write(content)

print("entry_contract.py patched.")
