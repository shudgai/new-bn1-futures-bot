"""Candle Solidity Gate: Rejects weak-body or insufficient-ATR breakout bars."""
from typing import Tuple, Optional
import pandas as pd
from core.intelligence.spatial_brain import SpatialBrain, SpatialContext


class CandleSolidityGate:
    """Verifies that the candidate bar possesses genuine body solidity and sufficient ATR length."""

    @staticmethod
    def evaluate(frame: pd.DataFrame, quote: float, side: str,
                 context: Optional[SpatialContext] = None) -> Tuple[bool, Optional[str]]:
        if frame is None or frame.empty:
            return False, 'BLOCKED_BY_WEAK_BODY'

        curr = frame.iloc[-1]
        open_p = float(curr.get('open', quote))
        high_p = max(float(curr.get('high', quote)), quote)
        low_p = min(float(curr.get('low', quote)), quote)

        body = abs(quote - open_p)
        candle_range = high_p - low_p + 1e-9
        solidity_ratio = body / candle_range

        # Fetch ATR
        prev = frame.iloc[-2] if len(frame) >= 2 else curr
        atr = float(prev.get('atr', curr.get('atr', 1.0)))
        if atr <= 0:
            atr = 1.0

        # 實體佔比 < 55% 或 實體長度 < 0.35*ATR 者，判定為微弱偷渡棒，一票否決
        if solidity_ratio < 0.55 or body < 0.35 * atr:
            return False, 'BLOCKED_BY_WEAK_BODY'

        return True, None
