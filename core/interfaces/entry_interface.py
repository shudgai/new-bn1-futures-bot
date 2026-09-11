"""Abstract Interface for Entry Strategy Evaluators."""
from abc import ABC, abstractmethod
from typing import Dict, Any, Tuple, Optional
import pandas as pd

class IEntryStrategy(ABC):
    """Abstract Strategy interface for entry signal evaluation."""

    @abstractmethod
    def evaluate_entry(
        self,
        frame: pd.DataFrame,
        price: float,
        side: str,
        **kwargs: Any
    ) -> Tuple[bool, str, Dict[str, Any]]:
        """Evaluate if an entry signal is valid.
        
        Returns:
            (is_allowed, reason, details)
        """
        pass
