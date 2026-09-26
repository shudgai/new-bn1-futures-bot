"""Fee-aware fixed USDT profit floors; targets are not locked profits."""
import math
from typing import Any


def fixed_profit_lock(position: dict, price: float, fee: float, slippage: float) -> dict | None:
    return None


async def enforce_fixed_profit_lock(account: Any, symbol: str, price: float,
                                    fee: float, slippage: float) -> bool:
    return False

