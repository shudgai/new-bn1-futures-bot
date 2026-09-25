import sys
import re

with open("core/paper_account.py", "r") as f:
    tcontent = f.read()

replacement = """
    ) -> bool:
        print("!!! INSIDE PAPER ACCOUNT OPEN_POSITION !!!", flush=True)
"""
tcontent = re.sub(
    r'\s*\)\s*->\s*bool:',
    replacement,
    tcontent,
    count=1
)

with open("core/paper_account.py", "w") as f:
    f.write(tcontent)

