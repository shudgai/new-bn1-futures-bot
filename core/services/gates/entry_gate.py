"""Entry gate exports and definitions: StrictCrossAndKcTrendGate & EntryGate.

================================================================================
【完整閘門規則：Gate: StrictCrossAndKcTrendGate】
================================================================================

凡欲產生開倉訊號（無論破軌開倉或斷頭開倉），必須 100% 通過以下前置硬檢核，否則一票否決（REJECT_ENTRY）：

一、 多單進場硬門檻（Long Gate Criteria）
1. 通道大方向共振：
   - kc_trend 必須明確為 'UP'（KC向上）。
   - 若 kc_trend == 'DOWN' 或不為 'UP'，一票否決開多（REJECT_ENTRY: KC_TREND_NOT_UP）。

2. 實質金叉驗證（Strict Golden Cross）：
   - 在最近 1~3 根 K 棒內必須發生「實質向上穿越」：ma5_prev <= ma15_prev 且當前 ma5_curr > ma15_curr。
   - 當前必須維持多頭排列：ma5_curr > ma15_curr。
   - 若 ma5 <= ma15 或未完成由下往上實質穿越，一票否決開多。

3. MA5 即時斜率防向下彎折（Anti-Hooking Slope Filter - 核心防線）：
   - 狀況場景：即使剛剛發生過金叉且 MA5 仍在 MA15 上方，若 MA5 當前開始走平或向下彎折（ma5_slope <= 0 或 ma5_curr <= ma5_prev），代表多頭動能熄火或遭遇阻力。
   - 處置動作：100% 絕對禁止開多（REJECT_ENTRY: MA5_HOOKING_DOWN_AFTER_CROSS）！
   - 放行唯一條件：MA5 當根必須處於堅定向上發散狀態（ma5_slope > 0 且 ma5_curr > ma5_prev）。

二、 空單進場硬門檻（Short Gate Criteria）
1. 通道大方向共振：
   - kc_trend 必須明確為 'DOWN'（KC向下）。
   - 若 kc_trend == 'UP' 或不為 'DOWN'，一票否決開空（REJECT_ENTRY: KC_TREND_NOT_DOWN）。

2. 實質死叉驗證（Strict Death Cross）：
   - 在最近 1~3 根 K 棒內必須發生「實質向下貫穿」：ma5_prev >= ma15_prev 且當前 ma5_curr < ma15_curr。
   - 當前必須維持空頭排列：ma5_curr < ma15_curr 且 ma15_slope <= 0。
   - 【絕對否決鐵律】：若當前 MA5 仍在 MA15 上方（ma5 >= ma15），無論 MA5 當根如何向下勾頭，判定為多頭回踩雜訊，100% 絕對禁止放行開空訊號（REJECT_ENTRY: NO_DEATH_CROSS_MA5_ABOVE_MA15）！

3. MA5 即時斜率防向上折起（Anti-Hooking Slope Filter - 核心防線）：
   - 狀況場景：即使剛剛發生過死叉且 MA5 仍在 MA15 下方，若 MA5 當前開始走平或向上勾頭（ma5_slope >= 0 或 ma5_curr >= ma5_prev），代表空頭動能衰退或反彈托底。
   - 處置動作：100% 絕對禁止開空（REJECT_ENTRY: MA5_HOOKING_UP_AFTER_CROSS）！
   - 放行唯一條件：MA5 當根必須處於堅定向下俯衝狀態（ma5_slope < 0 且 ma5_curr < ma5_prev）。
"""
import math
from typing import Optional, Tuple
import pandas as pd


def has_recent_golden_cross(
    df: pd.DataFrame,
    max_lookback: int = 6,
    curr_ma5: Optional[float] = None,
    curr_ma15: Optional[float] = None,
) -> bool:
    """檢查最近 1~max_lookback 根 K 棒內是否發生過實質金叉（ma5 由 <= ma15 穿越為 > ma15）"""
    if df is None or len(df) < 2:
        return False
    n = len(df)
    lookback = min(max_lookback, n - 1)
    for i in range(n - lookback, n):
        p_ma5 = float(df.iloc[i - 1].get('ma5', 0.0))
        p_ma15 = float(df.iloc[i - 1].get('ma15', 0.0))
        if i == n - 1 and curr_ma5 is not None and curr_ma15 is not None:
            c_ma5 = float(curr_ma5)
            c_ma15 = float(curr_ma15)
        else:
            c_ma5 = float(df.iloc[i].get('ma5', 0.0))
            c_ma15 = float(df.iloc[i].get('ma15', 0.0))
        if p_ma5 <= p_ma15 and c_ma5 > c_ma15:
            return True
    return False


def has_recent_death_cross(
    df: pd.DataFrame,
    max_lookback: int = 6,
    curr_ma5: Optional[float] = None,
    curr_ma15: Optional[float] = None,
) -> bool:
    """檢查最近 1~max_lookback 根 K 棒內是否發生過實質死叉（ma5 由 >= ma15 穿越為 < ma15）"""
    if df is None or len(df) < 2:
        return False
    n = len(df)
    lookback = min(max_lookback, n - 1)
    for i in range(n - lookback, n):
        p_ma5 = float(df.iloc[i - 1].get('ma5', 0.0))
        p_ma15 = float(df.iloc[i - 1].get('ma15', 0.0))
        if i == n - 1 and curr_ma5 is not None and curr_ma15 is not None:
            c_ma5 = float(curr_ma5)
            c_ma15 = float(curr_ma15)
        else:
            c_ma5 = float(df.iloc[i].get('ma5', 0.0))
            c_ma15 = float(df.iloc[i].get('ma15', 0.0))
        if p_ma5 >= p_ma15 and c_ma5 < c_ma15:
            return True
    return False


class StrictCrossAndKcTrendGate:
    """Gate: StrictCrossAndKcTrendGate - 真均線交叉、通道共振與斜率防彎折閘門"""

    @classmethod
    def evaluate(
        cls, frame: pd.DataFrame, side: str, quote: Optional[float] = None, bypass_spatial_check: bool = False
    ) -> Tuple[bool, str]:
        if frame is None or len(frame) < 2:
            return False, "REJECT_ENTRY: INSUFFICIENT_DATA"
        if side not in ('LONG', 'SHORT'):
            return False, "REJECT_ENTRY: INVALID_SIDE"

        curr = frame.iloc[-1]
        prev = frame.iloc[-2]

        # 提取 KC 通道方向
        curr_kc_mid = float(curr.get('kc_middle', curr.get('kc_basis', 0.0)))
        prev_kc_mid = float(prev.get('kc_middle', prev.get('kc_basis', curr_kc_mid)))
        kc_trend = str(curr.get('kc_trend', '')).strip().upper()
        if not kc_trend:
            if curr_kc_mid > prev_kc_mid + 1e-6:
                kc_trend = 'UP'
            elif curr_kc_mid < prev_kc_mid - 1e-6:
                kc_trend = 'DOWN'
            else:
                kc_trend = 'FLAT'

        # 提取均線與即時調整
        is_closed = bool(curr.get('is_closed', True))
        raw_close = float(curr.get('close', quote if quote is not None else 0.0))
        current_price = float(quote) if quote is not None else raw_close

        curr_ma5 = float(curr.get('ma5', current_price))
        prev_ma5 = float(prev.get('ma5', curr_ma5))
        curr_ma15 = float(curr.get('ma15', current_price))
        prev_ma15 = float(prev.get('ma15', curr_ma15))

        if not is_closed and quote is not None and raw_close > 0:
            curr_ma5 += (current_price - raw_close) / 5.0
            curr_ma15 += (current_price - raw_close) / 15.0

        ma5_diff = curr_ma5 - prev_ma5
        ma15_diff = curr_ma15 - prev_ma15

        curr_kc_upper = float(curr.get('kc_upper', 0.0))
        curr_kc_lower = float(curr.get('kc_lower', 0.0))
        # 通道帶寬與死水震盪過濾：帶寬小於 1.2% 或方向不明，嚴禁開倉
        if curr_kc_upper > 0 and curr_kc_lower > 0 and curr_kc_mid > 0:
            kc_width_ratio = ((curr_kc_upper - curr_kc_lower) / curr_kc_mid) * 100.0
            if kc_width_ratio < 1.2 or kc_trend == 'FLAT':
                return False, "REJECT_ENTRY: CHOP_BANDWIDTH_TOO_NARROW"

        # ══════════════════════════════════════════════════════════════════════
        # 一、 多單進場硬門檻（Long Gate Criteria）
        # ══════════════════════════════════════════════════════════════════════
        if side == 'LONG':
            # 1. 通道大方向共振：kc_trend 必須明確為 'UP'（KC向上）。若 kc_trend == 'DOWN' 或非 'UP' 一票否決
            if kc_trend != 'UP' or curr_kc_mid < prev_kc_mid - 1e-6:
                return False, "REJECT_ENTRY: KC_TREND_NOT_UP"

            # 2. 當前必須維持多頭排列：ma5_curr > ma15_curr
            if curr_ma5 <= curr_ma15:
                return False, "REJECT_ENTRY: NO_GOLDEN_CROSS_MA5_BELOW_MA15"

            # 實質金叉驗證：在最近 1~6 根 K 棒內必須發生「實質向上穿越」
            if not has_recent_golden_cross(frame, max_lookback=6, curr_ma5=curr_ma5, curr_ma15=curr_ma15):
                return False, "REJECT_ENTRY: NO_VALID_GOLDEN_CROSS_FOUND"

            # 3. MA5 即時斜率防向下彎折（軟化死區計算：微幅浮點波動視為順勢延續，明確反向才否決）
            if ma5_diff < -1e-5:
                return False, "REJECT_ENTRY: MA5_HOOKING_DOWN_AFTER_CROSS"

            # 4. 獲利空間與防貼軌追多：距上軌不足 20% 通道空間或超買貼軌，嚴禁開多（收線破軌/貫穿豁免 25% 空間門檻）
            if not bypass_spatial_check and curr_kc_mid > 0 and curr_kc_upper > curr_kc_mid:
                too_close_upper = current_price >= (curr_kc_upper - (curr_kc_upper - curr_kc_mid) * 0.20)
                if too_close_upper:
                    return False, "REJECT_ENTRY: LONG_TOO_CLOSE_TO_UPPER_RAIL"

            return True, "PASSED"

        # ══════════════════════════════════════════════════════════════════════
        # 二、 空單進場硬門檻（Short Gate Criteria）
        # ══════════════════════════════════════════════════════════════════════
        elif side == 'SHORT':
            # 1. 通道大方向共振：kc_trend 必須明確為 'DOWN'（KC向下）。若 kc_trend == 'UP' 或非 'DOWN' 一票否決
            if kc_trend != 'DOWN' or curr_kc_mid > prev_kc_mid + 1e-6:
                return False, "REJECT_ENTRY: KC_TREND_NOT_DOWN"

            # 【絕對否決鐵律】：若當前 MA5 仍在 MA15 上方（ma5 >= ma15），100% 絕對禁止放行開空訊號
            if curr_ma5 >= curr_ma15:
                return False, "REJECT_ENTRY: NO_DEATH_CROSS_MA5_ABOVE_MA15"

            # 2. 實質死叉驗證：在最近 1~6 根 K 棒內必須發生「實質向下貫穿」
            if not has_recent_death_cross(frame, max_lookback=6, curr_ma5=curr_ma5, curr_ma15=curr_ma15):
                return False, "REJECT_ENTRY: NO_VALID_DEATH_CROSS_FOUND"

            # 當前必須維持空頭排列：ma5_curr < ma15_curr 且 ma15_slope <= 0
            if ma15_diff > 1e-6:
                return False, "REJECT_ENTRY: MA15_SLOPE_NOT_DOWN"

            # 3. MA5 即時斜率防向上折起（軟化死區計算：微幅浮點波動視為順勢延續，明確反向才否決）
            if ma5_diff > 1e-5:
                return False, "REJECT_ENTRY: MA5_HOOKING_UP_AFTER_CROSS"

            # 4. 獲利空間與防貼軌追空：距下軌不足 20% 通道空間或超賣貼軌（防追在波段最低點地板），嚴禁開空（收線跌破/貫穿豁免 25% 空間門檻）
            if not bypass_spatial_check and curr_kc_mid > 0 and curr_kc_lower > 0 and curr_kc_mid > curr_kc_lower:
                too_close_lower = current_price <= (curr_kc_lower + (curr_kc_mid - curr_kc_lower) * 0.20)
                if too_close_lower:
                    return False, "REJECT_ENTRY: SHORT_TOO_CLOSE_TO_LOWER_RAIL"

            return True, "PASSED"

        return False, "REJECT_ENTRY: INVALID_SIDE"



class MemeSharedProtectionGate:
    """Gate: MemeSharedProtectionGate - 妖幣共用防禦閘門 (Anti-Wick & Max Slippage / Price Deviation Cap)
    
    所有開倉模式在發出訊號前，必須 100% 通過以下前置硬檢核，否則一票否決（REJECT_ENTRY）：
    1. 影線與實體過濾（Anti-Wick Gate - 杜絕避雷針與地板陷阱）：
       - 計算當根 1m K 棒實體佔比：body_ratio = abs(close - open) / (high - low + 1e-9)
       - 若 body_ratio < 0.25（只擋極端十字星，放行一般波動 K 棒）。
       - 多單開倉：上影線不得超過實體的 0.8 倍（High - max(Open, Close) <= 0.8 * Body）。若超標，禁止追多。
       - 空單開倉：下影線不得超過實體的 0.8 倍（min(Open, Close) - Low <= 0.8 * Body）。若超標，禁止追空。
    2. 防天花板與追高乖離限制（Max Slippage / Price Deviation Cap）：
       - 多單：(Price - MA5) / MA5 > 1.2% 時，判定為短線嚴重超買天花板，一票否決開多（REJECT_ENTRY: LONG_MA5_DEVIATION_CAP）。
       - 空單：(MA5 - Price) / MA5 > 1.2% 時，判定為短線嚴重超賣地板，一票否決開空（REJECT_ENTRY: SHORT_MA5_DEVIATION_CAP）。
    """

    @classmethod
    def evaluate(
        cls, frame: pd.DataFrame, side: str, quote: Optional[float] = None, symbol: Optional[str] = None,
        bypass_spatial_check: bool = False, mode: Optional[str] = None
    ) -> Tuple[bool, str]:
        if frame is None or len(frame) < 1:
            return False, "REJECT_ENTRY: INSUFFICIENT_DATA"
        side = side.upper()
        if side not in ('LONG', 'SHORT'):
            return False, "REJECT_ENTRY: INVALID_SIDE"

        curr = frame.iloc[-1]
        c_open = float(curr.get('open', 0.0))
        c_close = float(curr.get('close', 0.0))
        c_high = float(curr.get('high', 0.0))
        c_low = float(curr.get('low', 0.0))
        price = float(quote) if quote is not None else c_close

        candle_range = max(c_high - c_low, 1e-9)
        body = abs(c_close - c_open)
        body_ratio = body / candle_range

        curr_ma5 = float(curr.get('ma5', price))
        is_closed = bool(curr.get('is_closed', True))
        if not is_closed and quote is not None and c_close > 0:
            curr_ma5 += (price - c_close) / 5.0

        dev = abs(price - curr_ma5) / curr_ma5 if curr_ma5 > 0 else 0.0

        # 當 bypass_spatial_check == True 時，100% 豁免乖離率與影線過濾
        if not bypass_spatial_check:
            # 1. 影線與實體過濾（放寬門檻至 0.25 與 0.8）
            if body_ratio < 0.25:
                return False, "REJECT_ENTRY: DOMINATED_BY_WICKS"

            if side == 'LONG':
                upper_wick = c_high - max(c_open, c_close)
                if upper_wick > 0.8 * body:
                    return False, "REJECT_ENTRY: EXCESSIVE_UPPER_WICK"
            elif side == 'SHORT':
                lower_wick = min(c_open, c_close) - c_low
                if lower_wick > 0.8 * body:
                    return False, "REJECT_ENTRY: EXCESSIVE_LOWER_WICK"

            # 2. 防天花板與追高乖離限制 (Max Slippage / Price Deviation Cap > 1.2%)
            if curr_ma5 > 0:
                if side == 'LONG' and dev > 0.012:
                    return False, "REJECT_ENTRY: LONG_MA5_DEVIATION_CAP"
                elif side == 'SHORT' and dev > 0.012:
                    return False, "REJECT_ENTRY: SHORT_MA5_DEVIATION_CAP"

        return True, "PASSED"



from core.gates.macro_candle_structure_gate import MacroCandleStructureGate
from core.gates.five_lines_confluence_gate import FiveLinesConfluenceGate
from core.gates.trend_confirmation_filter_gate import TrendConfirmationFilterGate
from core.gates.strict_entry_and_immunity_gate import StrictEntryAndImmunityGate

strict_gate = StrictEntryAndImmunityGate(min_slope_pct=0.0010, cooldown_bars=3)

EntryGate = StrictCrossAndKcTrendGate

__all__ = [
    "StrictCrossAndKcTrendGate",
    "MemeSharedProtectionGate",
    "MacroCandleStructureGate",
    "FiveLinesConfluenceGate",
    "TrendConfirmationFilterGate",
    "StrictEntryAndImmunityGate",
    "strict_gate",
    "EntryGate",
    "has_recent_golden_cross",
    "has_recent_death_cross",
]

