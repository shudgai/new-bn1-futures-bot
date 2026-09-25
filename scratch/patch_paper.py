import sys
with open("core/paper_account.py", "r") as f:
    pcontent = f.read()

pcontent = pcontent.replace('self.log(f"open_position return False at line {sys._getframe().f_lineno}", "WARNING"); return False', 'import logging; logging.getLogger("uvicorn.error").info(f"PAPER open_position return False at line {sys._getframe().f_lineno}"); return False')
with open("core/paper_account.py", "w") as f:
    f.write(pcontent)
