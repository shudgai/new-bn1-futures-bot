import sys
import re

with open("core/testnet_account.py", "r") as f:
    tcontent = f.read()

replacement = """
    ) -> bool:
        \"\"\"市價進場（手動下單、或任何需要立即成交的路徑用這個）。
        訊號驅動的回調進場改用 place_limit_entry()，見下方。\"\"\"
        import logging
        logging.getLogger("uvicorn.error").info("ENTER TESTNET OPEN_POSITION")
"""
tcontent = re.sub(
    r'\s*\)\s*->\s*bool:\s*\"\"\"市價進場（手動下單、或任何需要立即成交的路徑用這個）。\s*訊號驅動的回調進場改用 place_limit_entry\(\)，見下方。\"\"\"',
    replacement,
    tcontent
)

with open("core/testnet_account.py", "w") as f:
    f.write(tcontent)
