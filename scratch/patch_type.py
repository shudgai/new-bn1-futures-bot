import sys
import re

with open("core/services/manual_order_service.py", "r") as f:
    tcontent = f.read()

tcontent = tcontent.replace(
    'logger.info(f"E2E TEST: manual_order starting for {symbol}. target_amount={target_amount}")',
    'logger.info(f"E2E TEST: manual_order starting for {symbol}. target_amount={target_amount} account_type={type(engine.account)}")'
)

with open("core/services/manual_order_service.py", "w") as f:
    f.write(tcontent)

