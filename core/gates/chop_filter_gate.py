"""Chop Filter Gate: Blocks entries in chop compression or intertwined moving averages."""
from typing import Tuple, Optional
import pandas as pd
from core.intelligence.spatial_brain import SpatialBrain, SpatialContext


class ChopFilterGate:
    """Rejects signals when market geometry is in CHOP_COMPRESSION."""

    @staticmethod
    def evaluate(frame: pd.DataFrame, quote: float, side: str,
                 context: Optional[SpatialContext] = None) -> Tuple[bool, Optional[str]]:
        if context is None:
            context = SpatialBrain.analyze(frame, quote)

        if context.state == 'CHOP_COMPRESSION':
            return False, 'BLOCKED_BY_SPATIAL_CHOP'

        return True, None
