"""Post-entry MA3 turning exit (Peak-to-Peak).
Implements IExitStrategy interface.
"""
import math
from typing import Dict, Any, Optional
import pandas as pd
from core.interfaces.exit_interface import IExitStrategy
from core.services.swing_service import significant_ma3_turn

EXIT_REASON = 'TRUE_TOP_STRUCTURE_BREAK (MA3 reversed >= 0.10 ATR)'

class FadingExitStrategy(IExitStrategy):
    """Clean peak-to-peak exit strategy tracking MA3 reversal."""
    def evaluate_exit(self, position: Dict[str, Any], frame: pd.DataFrame, price: float, **kwargs: Any) -> Optional[str]:
        if significant_ma3_turn(position, frame, price):
            return EXIT_REASON
        return None
