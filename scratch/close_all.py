
import sys
sys.path.append(".")
from core.paper_account import account
if "1000PEPEUSDT" in account.positions:
    pos = account.positions["1000PEPEUSDT"]
    print("Closing 1000PEPEUSDT:", pos)
    account.close_position("1000PEPEUSDT", 0.0, "EMERGENCY_CLOSE")
    print("Closed!")
else:
    print("Not found in paper account.")

