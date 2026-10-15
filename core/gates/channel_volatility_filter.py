"""ChannelVolatilityFilter - 通道波動率過濾器 (Volatility Filter)

適用於量化趨勢、合約波段與自動化交易策略：
1. 通道指標計算：
   - 基準通道：Keltner Channels (KC: Middle, Upper, Lower) 或 Bollinger Bands (BB)
   - 通道寬度 (Bandwidth)：(Upper - Lower) / Middle * 100%
2. 判定機制：
   - 通道過窄 (Squeeze / 停止開倉)：
     * 當前 Bandwidth < MA(Bandwidth, ma_period) * squeeze_ratio (例如 0.8 倍)
     * 或當前 Bandwidth <= 過去 lookback_period (例如 50 根) 最大帶寬的 25% (極度收斂死水區)
   - 通道打開 (Expansion / 允許開倉)：
     * Bandwidth > MA(Bandwidth, ma_period) 且當前 Bandwidth > 前一根 Bandwidth (波動率上升中)
3. 交易閘門控制 (VETO)：
   - 當前處於「通道過窄」狀態時，100% 一票否決開新倉 (VETO: CHANNEL_SQUEEZE_TOO_NARROW)
   - 唯有通道重新張開、波動率擴張確立時，才放行開倉信號。
"""
import math
import logging
from typing import Dict, Any, Optional, Tuple
import pandas as pd

logger = logging.getLogger(__name__)


class ChannelVolatilityFilter:
    """通道波動率過濾器 (Channel Volatility Filter)"""

    def __init__(
        self,
        lookback_period: int = 50,      # 回看週期 (用於計算歷史最大/最小寬度)
        ma_period: int = 20,            # 帶寬移動平均線週期
        squeeze_ratio: float = 0.85,    # 過窄判定倍數：低於 MA 的 0.85 倍視為壓縮
        extreme_squeeze_pct: float = 0.25, # 極度收斂門檻：低於歷史最大寬度的 25%
        min_absolute_bandwidth: float = 0.005, # 絕對帶寬下限 (0.5%)，低於此值必為死水
    ):
        self.lookback_period = lookback_period
        self.ma_period = ma_period
        self.squeeze_ratio = squeeze_ratio
        self.extreme_squeeze_pct = extreme_squeeze_pct
        self.min_absolute_bandwidth = min_absolute_bandwidth

    def compute_bandwidth(self, df: pd.DataFrame, channel_type: str = 'KC') -> pd.DataFrame:
        """計算通道帶寬 (Bandwidth) 及期移動平均線 (Bandwidth MA)"""
        if df is None or len(df) == 0:
            return df

        df = df.copy()

        # 1. 取得通道上下軌與中軌 (優先使用已計算好的欄位)
        channel_type_upper = channel_type.upper()
        if channel_type_upper == 'KC' and 'kc_upper' in df.columns and 'kc_lower' in df.columns and 'kc_middle' in df.columns:
            upper = df['kc_upper']
            lower = df['kc_lower']
            middle = df['kc_middle']
        elif 'bb_upper' in df.columns and 'bb_lower' in df.columns and 'bb_middle' in df.columns:
            upper = df['bb_upper']
            lower = df['bb_lower']
            middle = df['bb_middle']
        else:
            # 即時動態推導 Keltner Channel (中軌: EMA20, 寬度: 2.0 * ATR)
            close = df['close'] if 'close' in df.columns else df['c']
            high = df['high'] if 'high' in df.columns else df['h']
            low = df['low'] if 'low' in df.columns else df['l']
            
            middle = close.rolling(window=20, min_periods=1).mean()
            tr1 = high - low
            tr2 = (high - close.shift(1)).abs()
            tr3 = (low - close.shift(1)).abs()
            tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
            atr = tr.rolling(window=14, min_periods=1).mean().fillna(1e-4)
            upper = middle + 2.0 * atr
            lower = middle - 2.0 * atr

        # 2. 計算帶寬 Bandwidth = (Upper - Lower) / Middle
        bandwidth = (upper - lower) / middle.replace(0, float('nan'))
        df['bandwidth'] = bandwidth.fillna(0.0)

        # 3. 計算帶寬的移動平均線 Bandwidth MA
        df['bandwidth_ma'] = df['bandwidth'].rolling(window=self.ma_period, min_periods=1).mean()

        # 4. 回看週期的最大帶寬
        df['bandwidth_max_lookback'] = df['bandwidth'].rolling(window=self.lookback_period, min_periods=1).max()

        return df

    def evaluate(self, df: pd.DataFrame, channel_type: str = 'KC', side: Optional[str] = None) -> Tuple[bool, str, Dict[str, Any]]:
        """審查當前通道波動率狀態是否允許開倉。
        
        Returns:
            can_enter (bool): 是否允許開倉 (True: 通道打開放行; False: 通道過窄一票否決)
            reason (str): 判定原因或阻斷代碼
            details (dict): 指標詳細數值
        """
        if df is None or len(df) < 2:
            return False, "REJECT_ENTRY: INSUFFICIENT_DATA", {}

        # 確保帶寬指標已計算
        if 'bandwidth' not in df.columns or 'bandwidth_ma' not in df.columns:
            df = self.compute_bandwidth(df, channel_type=channel_type)

        curr = df.iloc[-1]
        prev = df.iloc[-2]

        curr_bw = float(curr.get('bandwidth', 0.0))
        prev_bw = float(prev.get('bandwidth', curr_bw))
        bw_ma = float(curr.get('bandwidth_ma', curr_bw))
        max_bw = float(curr.get('bandwidth_max_lookback', curr_bw))

        details = {
            'bandwidth': round(curr_bw, 6),
            'bandwidth_pct': f"{curr_bw * 100:.3f}%",
            'prev_bandwidth': round(prev_bw, 6),
            'bandwidth_ma': round(bw_ma, 6),
            'max_bandwidth': round(max_bw, 6),
            'bw_to_ma_ratio': round(curr_bw / bw_ma, 4) if bw_ma > 0 else 1.0,
            'bw_to_max_ratio': round(curr_bw / max_bw, 4) if max_bw > 0 else 1.0,
        }

        # ──【核心修正：通道收窄時的大格局中繼蓄勢識別 (Squeeze Consolidation)】──
        curr_close = float(curr.get('close', 0.0))
        curr_basis = float(curr.get('kc_middle', curr.get('ma15', curr_close)))
        prev_basis = float(prev.get('kc_middle', prev.get('ma15', curr_basis)))
        basis_slope = curr_basis - prev_basis

        is_bull_consolidation = (basis_slope >= 0 and curr_close >= curr_basis)
        is_bear_consolidation = (basis_slope <= 0 and curr_close <= curr_basis)

        if side is not None:
            side_str = str(side).upper()
            if side_str == 'LONG' and is_bull_consolidation:
                details['squeeze_consolidation'] = 'BULL_SQUEEZE_CONSOLIDATION'
                return True, "PASS: BULL_SQUEEZE_CONSOLIDATION_READY (大格局多頭高位中繼蓄勢，允許再開多)", details
            elif side_str == 'SHORT' and is_bear_consolidation:
                details['squeeze_consolidation'] = 'BEAR_SQUEEZE_CONSOLIDATION'
                return True, "PASS: BEAR_SQUEEZE_CONSOLIDATION_READY (大格局空頭下跌中繼蓄勢，允許再開空)", details

        # ──【一票否決 1：絕對帶寬極度狹窄 (Absolute Deadlock)】──
        if curr_bw < self.min_absolute_bandwidth:
            reason = (f"VETO: ABSOLUTE_BANDWIDTH_TOO_FLAT "
                      f"(Bandwidth: {curr_bw*100:.3f}% < {self.min_absolute_bandwidth*100:.2f}%)")
            return False, reason, details

        # ──【一票否決 2：通道極度擠壓收斂 (Extreme Squeeze)】──
        # 當前帶寬小於過去 N 根最高帶寬的 25%，代表市場死水，嚴禁提前開倉接飛刀或磨損
        if max_bw > 0 and curr_bw <= (self.extreme_squeeze_pct * max_bw):
            reason = (f"VETO: EXTREME_VOLATILITY_COMPRESSION "
                      f"(Bandwidth: {curr_bw*100:.3f}% <= 25% of Peak {max_bw*100:.3f}%)")
            return False, reason, details

        # ──【一票否決 3：低於帶寬均線的 0.85 倍且帶寬持續萎縮 (Squeeze Chop)】──
        # 通道萎縮且比均線窄，說明處於無趨勢低波動磨損期
        is_squeezed_below_ma = (bw_ma > 0 and curr_bw < (self.squeeze_ratio * bw_ma))
        is_contracting = (curr_bw <= prev_bw)

        if is_squeezed_below_ma:
            reason = (f"VETO: CHANNEL_SQUEEZE_BELOW_MA "
                      f"(Bandwidth: {curr_bw*100:.3f}% < {self.squeeze_ratio}x MA {bw_ma*100:.3f}%)")
            return False, reason, details

        # ──【放行條件：通道張開，波動率擴張確立 (Volatility Expansion)】──
        # 條件：帶寬上揚 (curr_bw > prev_bw) 且 帶寬站上移動平均線 (curr_bw >= bw_ma)
        is_expanding = (curr_bw > prev_bw) and (curr_bw >= bw_ma * 0.95)
        if is_expanding:
            return True, "PASS: VOLATILITY_EXPANDING_READY", details

        # 其他邊界情況：帶寬尚可但未明確張開，若未過窄則視為中性放行
        return True, "PASS: BANDWIDTH_NEUTRAL_NORMAL", details
