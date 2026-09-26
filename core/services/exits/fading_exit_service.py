"""Post-entry reversal exit (Peak-to-Peak structure break).
Implements IExitStrategy interface.
"""
from typing import Dict, Any, Optional
import pandas as pd
from core.interfaces.exit_interface import IExitStrategy
from core.services.candle_data import closed_entry_candles

EXIT_REASON = 'TOP_BOTTOM_REVERSAL_BREAK (High/Low structure broken)'

class FadingExitStrategy(IExitStrategy):
    """Exit when a local top/bottom structure breaks. (DISABLED by user request)"""
    def evaluate_exit(self, position: Dict[str, Any], frame: pd.DataFrame, price: float, **kwargs: Any) -> Optional[str]:
        # 已依照使用者要求停用：「不要一出現陰線就平倉」
        return None
