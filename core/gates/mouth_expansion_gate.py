"""Mouth Expansion Gate: Blocks overextended chases and false breakouts with flat MA15."""
from typing import Tuple, Optional
import pandas as pd
from core.intelligence.spatial_brain import SpatialBrain, SpatialContext


class MouthExpansionGate:
    """Blocks overextended chases and false breakouts with flat MA15."""

    @staticmethod
    def evaluate(frame: pd.DataFrame, quote: float, side: str,
                 context: Optional[SpatialContext] = None) -> Tuple[bool, Optional[str]]:
        if context is None:
            context = SpatialBrain.analyze(frame, quote)

        consecutive_outside = context.details.get('consecutive_outside', 0)

        # 1. 帶寬極端延伸者禁止追單 (只有已連續 3 根在軌外運行才評估力竭封鎖；剛突破時嚴格禁止判定為力竭)
        if context.state == 'OVEREXTENDED_EXHAUSTION' and consecutive_outside >= 3:
            return False, 'BLOCKED_BY_KC_OVEREXTENDED'

        # 2. 喇叭口張開但 MA15 走平者判定為假突破一票否決
        if len(frame) >= 3:
            curr = frame.iloc[-1]
            prev = frame.iloc[-2]
            atr = float(prev.get('atr', curr.get('atr', 1.0)))
            if atr <= 0:
                atr = 1.0

            baseline_bw = context.details.get('baseline_bw', context.bandwidth)
            prev_bandwidth = context.details.get('prev_bandwidth', context.bandwidth)
            is_mouth_expanding = prev_bandwidth > baseline_bw * 1.08

            ma15 = float(curr.get('ma15', 0.0))
            prev_ma15 = float(prev.get('ma15', ma15))
            ma15_diff = abs(ma15 - prev_ma15)

            # If mouth is expanding but MA15 is flat (or opposite to trade side)
            if is_mouth_expanding:
                if ma15_diff < 0.02 * atr:
                    return False, 'BLOCKED_BY_FLAT_MA15'
                if side == 'LONG' and ma15 < prev_ma15:
                    return False, 'BLOCKED_BY_FLAT_MA15'
                elif side == 'SHORT' and ma15 > prev_ma15:
                    return False, 'BLOCKED_BY_FLAT_MA15'

        return True, None
