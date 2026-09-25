import sys
import re
with open("core/paper_account.py", "r") as f:
    pcontent = f.read()

pcontent = re.sub(r'(\s+)return False\b', r'\1import logging, sys; logging.getLogger("uvicorn.error").info(f"PAPER open_position return False at line {sys._getframe().f_lineno}"); return False', pcontent)
with open("core/paper_account.py", "w") as f:
    f.write(pcontent)
