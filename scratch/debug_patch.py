import sys

with open("core/services/manual_order_service.py", "r") as f:
    content = f.read()

# Add logging
content = content.replace('success = await engine.account.open_position(',
'''
        import logging
        logging.getLogger("uvicorn.error").info(f"Manual order target: {symbol} {side} {target_amount} {exec_price} {atr} {leverage}")
        success = await engine.account.open_position(''')

with open("core/services/manual_order_service.py", "w") as f:
    f.write(content)

with open("core/paper_account.py", "r") as f:
    pcontent = f.read()

pcontent = pcontent.replace('return False', 'self.log(f"open_position return False at line {sys._getframe().f_lineno}", "WARNING"); return False')
with open("core/paper_account.py", "w") as f:
    f.write(pcontent)

