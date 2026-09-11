"""Abstract Interface for Risk Guards and Rule Enforcers."""
from abc import ABC, abstractmethod
from typing import Dict, Any, Tuple, Optional
import pandas as pd

class IGuardRule(ABC):
    """Abstract Guard interface for risk rules and abnormal market blockers."""

    @abstractmethod
    def check_permission(
        self,
        account: Any,
        symbol: str,
        side: str,
        frame: Optional[pd.DataFrame] = None,
        price: float = 0.0,
        **kwargs: Any
    ) -> Tuple[bool, str]:
        """Check if action is permitted under risk rules.
        
        Returns:
            (is_permitted, refusal_reason)
        """
        pass
