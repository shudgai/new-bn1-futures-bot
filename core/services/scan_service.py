import asyncio
import time
from typing import Dict, Any, List, Tuple

def entry_scan_symbol_snapshot(
    active_trade_symbols: List[str],
    broad_entry_symbols: List[str],
    positions: Dict[str, Any],
    pending_limit_orders: Dict[str, Any],
    candidate_scan_allowed: bool,
    effective_slot_limit: int,
) -> List[str]:
    """決定當前掃描要帶入的幣種清單。"""
    committed = set(positions.keys()) | set(pending_limit_orders.keys())
    if candidate_scan_allowed:
        symbols = list(dict.fromkeys(list(committed) + broad_entry_symbols))
    else:
        symbols = list(dict.fromkeys(list(committed) + active_trade_symbols))
    return symbols

def candidate_board_refresh_needed(
    opened_any: bool,
    position_count: int,
    pending_order_count: int,
    effective_slot_limit: int,
    rescan_elapsed: float,
) -> bool:
    if opened_any:
        return True
    if rescan_elapsed >= 300.0:
        return True
    return False
