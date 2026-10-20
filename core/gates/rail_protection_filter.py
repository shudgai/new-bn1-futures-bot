"""
RailProtectionFilter (貼軌過濾器)
修正後的貼軌過濾器：只在橫盤時攔截，單邊下殺/上衝時全力放行開倉。
"""

from typing import Any, Dict, Tuple


def can_enter_short_near_band(
    candle: Dict[str, Any],
    kc: Dict[str, Any],
    ma5: float,
    ma15: float,
    kc_mid_slope: float,
) -> Tuple[bool, str]:
    """
    判斷貼著下軌時是否允許開空
    """
    close_p = float(candle['close'])
    open_p = float(candle['open'])
    high_p = float(candle.get('high', max(close_p, open_p)))
    low_p = float(candle.get('low', min(close_p, open_p)))
    kc_lower = float(kc.get('lower', kc.get('kc_lower', 0.0)))
    kc_upper = float(kc.get('upper', kc.get('kc_upper', 0.0)))

    # 判斷是否為「單邊強空頭格局」：
    # 條件 1：KC 中軌具有明確向下的斜率
    # 條件 2：均線空頭排列 (MA5 < MA15)
    is_strong_downtrend = (kc_mid_slope < -0.0001) and (ma5 < ma15)

    # 1. 如果是大通道向下傾斜的「單邊主跌浪」：
    if is_strong_downtrend:
        # 只要不是收盤帶有極長下影線的深 V 反轉棒，貼著下軌收陰繼續殺，完全放行！
        if close_p <= open_p:
            return True, "【單邊主跌推軌】KC向下且均線發散，貼下軌順勢下殺，放行開空！"

    # 2. 如果通道走平或震盪：
    else:
        # 震盪期若貼著下軌，才需要防禦反彈
        dist_to_lower = abs(close_p - kc_lower)
        candle_range = high_p - low_p
        kc_range = kc_upper - kc_lower
        threshold = max(candle_range * 0.2, kc_range * 0.2)
        if threshold > 0 and dist_to_lower < threshold:
            return False, "【震盪防誘空】通道走平無斜率，貼下軌禁止追空！"

    return True, "通過檢查"


def can_enter_long_near_band(
    candle: Dict[str, Any],
    kc: Dict[str, Any],
    ma5: float,
    ma15: float,
    kc_mid_slope: float,
) -> Tuple[bool, str]:
    """
    判斷貼著上軌時是否允許開多
    """
    close_p = float(candle['close'])
    open_p = float(candle['open'])
    high_p = float(candle.get('high', max(close_p, open_p)))
    low_p = float(candle.get('low', min(close_p, open_p)))
    kc_upper = float(kc.get('upper', kc.get('kc_upper', 0.0)))
    kc_lower = float(kc.get('lower', kc.get('kc_lower', 0.0)))

    # 判斷是否為「單邊強多頭格局」：
    # 條件 1：KC 中軌具有明確向上的斜率
    # 條件 2：均線多頭排列 (MA5 > MA15)
    is_strong_uptrend = (kc_mid_slope > 0.0001) and (ma5 > ma15)

    # 1. 如果是大通道向上傾斜的「單邊主升浪」：
    if is_strong_uptrend:
        # 只要不是收盤帶有極長上影線的避雷針反轉棒，貼著上軌收陽繼續衝，完全放行！
        if close_p >= open_p:
            return True, "【單邊主升推軌】KC向上且均線發散，貼上軌順勢上衝，放行開多！"

    # 2. 如果通道走平或震盪：
    else:
        # 震盪期若貼著上軌，才需要防禦回落
        dist_to_upper = abs(close_p - kc_upper)
        candle_range = high_p - low_p
        kc_range = kc_upper - kc_lower
        threshold = max(candle_range * 0.2, kc_range * 0.2)
        if threshold > 0 and dist_to_upper < threshold:
            return False, "【震盪防誘多】通道走平無斜率，貼上軌禁止追多！"

    return True, "通過檢查"
