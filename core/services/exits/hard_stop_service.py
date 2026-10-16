"""Account-level circuit breakers are separate from position exit rules."""
from typing import Dict, Any, Optional
import pandas as pd
from core.interfaces.exit_interface import IExitStrategy

POSITION_HARD_STOPS_ENABLED = False


def hard_stop_reason(position, price):
    """Per-position hard stops are disabled; account circuit breakers own this risk."""
    return None


async def enforce_hard_stop(account, symbol, price):
    """No single-position loss threshold may close a trade."""
    return False


class HardStopExitStrategy(IExitStrategy):
    """OOP Strategy class implementing IExitStrategy for hard stop loss evaluation."""

    def evaluate_exit(
        self,
        position: Dict[str, Any],
        frame: pd.DataFrame,
        price: float,
        **kwargs: Any
    ) -> Optional[str]:
        reason = hard_stop_reason(position, price)
        if reason:
            return "HARD_STOP_" + reason
        return None
