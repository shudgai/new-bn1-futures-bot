import time
import pandas as pd
from typing import Dict, Any, Tuple

async def place_ma5_reversal_entry_legacy(
    engine: Any, symbol: str, side: str, ma5_sig: dict, live_price: float, now: float
) -> bool:
    """Legacy MA5 reversal order route."""
    committed = len(engine.account.positions) + len(engine.account.pending_limit_orders)
    if committed >= 10:
        return False
    return False

async def validate_pending_limit_orders_legacy(engine: Any, now: float) -> None:
    """Legacy pending limit order validation."""
    for symbol in list(engine.account.pending_limit_orders):
        await engine.account.cancel_pending_limit(symbol, "舊掛單策略停用，等待新外軌訊號")
