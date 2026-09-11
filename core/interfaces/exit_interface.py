"""Abstract Interface for Exit Strategy Evaluators."""
from abc import ABC, abstractmethod
from typing import Dict, Any, Optional
import pandas as pd

class IExitStrategy(ABC):
    """Abstract Strategy interface for exit signal and profit protection evaluation."""

    @abstractmethod
    def evaluate_exit(
        self,
        position: Dict[str, Any],
        frame: pd.DataFrame,
        price: float,
        **kwargs: Any
    ) -> Optional[str]:
        """Evaluate if a position should be closed.
        
        Returns:
            exit_reason string or None
        """
        pass
