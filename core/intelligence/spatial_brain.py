"""Spatial Brain: Geometric market space perception and micro-structure bending."""
from dataclasses import dataclass
from typing import Optional, Dict, Any, Tuple
import pandas as pd

REALTIME_BREAKOUT_MIN_SOLIDITY = 0.15


@dataclass
class SpatialContext:
    state: str  # 'CHOP_COMPRESSION', 'EXPLOSIVE_EXPANSION', 'OVEREXTENDED_EXHAUSTION', 'NORMAL_TREND'
    bandwidth: float
    bandwidth_ratio: float
    chop_index: float
    solidity_ratio: float
    ma3_slope: float
    ma5_slope: float
    ma15_slope: float
    details: Dict[str, Any]


class SpatialBrain:
    """Perceives market geometry, bandwidth expansion, chop compression, and micro-structure bends."""

    @staticmethod
    def analyze(frame: pd.DataFrame, live_quote: Optional[float] = None) -> SpatialContext:
        if frame is None or len(frame) < 3:
            return SpatialContext(
                state='NORMAL_TREND',
                bandwidth=0.0,
                bandwidth_ratio=1.0,
                chop_index=1.0,
                solidity_ratio=1.0,
                ma3_slope=0.0,
                ma5_slope=0.0,
                ma15_slope=0.0,
                details={}
            )

        curr = frame.iloc[-1]
        prev = frame.iloc[-2]
        prev2 = frame.iloc[-3] if len(frame) >= 3 else prev

        # Pricing and Bars
        close = float(live_quote) if live_quote is not None else float(curr.get('close', 0.0))
        open_p = float(curr.get('open', close))
        high_p = max(float(curr.get('high', close)), close)
        low_p = min(float(curr.get('low', close)), close)

        atr = float(prev.get('atr', curr.get('atr', 1.0)))
        if atr <= 0:
            atr = 1.0

        kc_upper = float(curr.get('kc_upper', 0.0))
        kc_lower = float(curr.get('kc_lower', 0.0))
        kc_basis = float(curr.get('kc_middle', curr.get('kc_basis', (kc_upper + kc_lower) / 2.0)))
        if kc_basis <= 0:
            kc_basis = (kc_upper + kc_lower) / 2.0 or 1.0

        ma5 = float(curr.get('ma5', close))
        prev_ma5 = float(prev.get('ma5', ma5))
        ma15 = float(curr.get('ma15', close))
        prev_ma15 = float(prev.get('ma15', ma15))

        # Calculate MA3 if available, or compute on close
        if 'ma3' in curr:
            ma3 = float(curr['ma3'])
            prev_ma3 = float(prev.get('ma3', ma3))
        else:
            # Estimate fast MA3 from last 3 closes
            closes = list(frame['close'].iloc[-3:])
            if live_quote is not None:
                closes[-1] = float(live_quote)
            ma3 = sum(closes) / len(closes)
            prev_closes = list(frame['close'].iloc[-4:-1]) if len(frame) >= 4 else closes
            prev_ma3 = sum(prev_closes) / len(prev_closes)

        # 1. Bandwidth
        # 計算 Bandwidth_Ratio 時，分母與分子基準必須取【破軌前一根收線 K 棒 (Bar[-1]) 的帶寬】，
        # 避免當前正在急拉的浮動數值扭曲膨脹比率
        prev_kc_upper = float(prev.get('kc_upper', kc_upper))
        prev_kc_lower = float(prev.get('kc_lower', kc_lower))
        prev_kc_basis = float(prev.get('kc_middle', prev.get('kc_basis', (prev_kc_upper + prev_kc_lower) / 2.0)))
        if prev_kc_basis <= 0:
            prev_kc_basis = (prev_kc_upper + prev_kc_lower) / 2.0 or 1.0
        prev_bandwidth = (prev_kc_upper - prev_kc_lower) / (prev_kc_basis + 1e-9)

        # 當前帶寬 (即時)
        bandwidth = (kc_upper - kc_lower) / (kc_basis + 1e-9)

        # Baseline bandwidth: 依前 20 根已收線 K 棒 (排除當前正在急拉的 K 棒) 取中位數
        baseline_bw = prev_bandwidth
        if len(frame) >= 10 and 'kc_upper' in frame.columns and 'kc_lower' in frame.columns:
            # 排除最後一根進行中的 K 棒，取破軌前已收線棒的基準
            closed_frame = frame.iloc[:-1] if len(frame) > 1 else frame
            recent_bws = (
                (closed_frame['kc_upper'] - closed_frame['kc_lower'])
                / (closed_frame['kc_middle'].replace(0, 1e-9))
            ).iloc[-20:].dropna()
            if not recent_bws.empty:
                baseline_bw = float(recent_bws.median())
                max_bw_20 = float(recent_bws.max())
            else:
                max_bw_20 = prev_bandwidth
        else:
            max_bw_20 = prev_bandwidth

        # 以破軌前一根已收線 K 棒的帶寬計算比率
        bandwidth_ratio = (prev_bandwidth / baseline_bw) if baseline_bw > 0 else 1.0

        # 2. Chop Index (均線麻花纏繞度)
        chop_index = abs(ma5 - ma15) / atr

        # 3. K 棒飽滿度 (Solidity Ratio)
        candle_range = high_p - low_p + 1e-9
        solidity_ratio = abs(close - open_p) / candle_range

        # 4. Slopes (Derivatives)
        ma3_slope = ma3 - prev_ma3
        ma5_slope = ma5 - prev_ma5
        ma15_slope = ma15 - prev_ma15

        # 檢查過去是否已連續 3 根在軌外運行 (只有連續 3 根在軌外才判定力竭)
        consecutive_outside = 0
        if len(frame) >= 4 and 'kc_upper' in frame.columns and 'kc_lower' in frame.columns:
            for i in range(-4, -1):
                b = frame.iloc[i]
                b_c = float(b.get('close', 0.0))
                b_u = float(b.get('kc_upper', 0.0))
                b_l = float(b.get('kc_lower', 0.0))
                if (b_c > b_u > 0) or (0 < b_c < b_l):
                    consecutive_outside += 1
                else:
                    consecutive_outside = 0

        # 5. Geometry State Classification
        # - CHOP_COMPRESSION: Chop_Index < 0.20 或 通道極度收斂 (<= 25% max bandwidth)
        is_extreme_squeeze = (max_bw_20 > 0 and prev_bandwidth <= 0.25 * max_bw_20)
        if chop_index < 0.20 or is_extreme_squeeze:
            state = 'CHOP_COMPRESSION'
        # - OVEREXTENDED_EXHAUSTION: 只有已連續 3 根在軌外運行，且帶寬 > 2.2 倍基準時才力竭封鎖；剛突破時嚴禁判定力竭
        elif consecutive_outside >= 3 and baseline_bw > 0 and prev_bandwidth > 2.2 * baseline_bw:
            state = 'OVEREXTENDED_EXHAUSTION'
        # - EXPLOSIVE_EXPANSION: 通道剛張嘴 (帶寬 > 基準帶寬 * 1.1)
        elif (bandwidth > baseline_bw * 1.1) or (prev_bandwidth > baseline_bw * 1.1):
            state = 'EXPLOSIVE_EXPANSION'
        else:
            state = 'NORMAL_TREND'

        details = {
            'baseline_bw': baseline_bw,
            'max_bw_20': max_bw_20,
            'prev_bandwidth': prev_bandwidth,
            'consecutive_outside': consecutive_outside,
            'is_extreme_squeeze': is_extreme_squeeze,
            'ma3': ma3,
            'prev_ma3': prev_ma3,
            'ma5': ma5,
            'ma15': ma15,
            'atr': atr,
        }

        return SpatialContext(
            state=state,
            bandwidth=bandwidth,
            bandwidth_ratio=bandwidth_ratio,
            chop_index=chop_index,
            solidity_ratio=solidity_ratio,
            ma3_slope=ma3_slope,
            ma5_slope=ma5_slope,
            ma15_slope=ma15_slope,
            details=details
        )

    @classmethod
    def diagnose(cls, symbol: str, frame: pd.DataFrame,
                 live_quote: Optional[float] = None) -> Tuple[SpatialContext, str]:
        """Diagnose spatial geometry for a symbol and determine action."""
        context = cls.analyze(frame, live_quote)
        if context.state == 'CHOP_COMPRESSION':
            action = 'FILTER_CHOP_WAIT_EXPANSION'
        elif context.state == 'OVEREXTENDED_EXHAUSTION':
            action = 'FILTER_EXHAUSTION_PREVENT_CHASING'
        elif context.state == 'EXPLOSIVE_EXPANSION':
            action = 'ALLOW_EXPLOSIVE_BREAKOUT'
        else:
            action = 'ALLOW_NORMAL_TREND'
        return context, action
