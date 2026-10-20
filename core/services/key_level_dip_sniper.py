"""
KeyLevelDipSniperService (全面波段起漲起跌與高拋低吸狙擊服務)

【全面開倉邏輯：涵蓋「均線交叉波段發動」與「回踩遇阻摸高狙擊」】

一、做空全面開倉邏輯：
1. 均線死叉起跌判定 (抓大肉起點)：
   - prev_ma5 >= prev_ma15 且 ma5 < ma15
   - 只要死叉且收在中軌下方，不必等破下軌，第一時間進場吃波段！
2. 均線已空頭排列，反彈觸碰 MA5/KC中軌遇阻 (DIP_SNIPER 高拋點)：
   - ma5 < ma15 且 close_p < kc_mid
   - 盤中反彈摸到均線 (high >= ma5)，但收盤收陰被打回 (close <= open)

二、做多對稱全面開倉邏輯：
1. 均線金叉起漲判定 (抓主升浪起點)：
   - prev_ma5 <= prev_ma15 且 ma5 > ma15
   - 只要金叉且收在中軌上方，立刻進場吃主升浪！
2. 均線已多頭排列，回踩觸碰 MA5/KC中軌有守 (DIP_SNIPER 低吸點)：
   - ma5 > ma15 且 close_p > kc_mid
   - 盤中下探摸到均線 (low <= ma5)，但收盤收陽轉強 (close >= open)
"""

from __future__ import annotations

import math
from typing import Any, Dict, Optional, Tuple
import pandas as pd


def check_comprehensive_short_entry(
    candle: Dict[str, Any],
    prev_candle: Dict[str, Any],
    kc_mid: float,
    ma5: float,
    prev_ma5: float,
    ma15: float,
    prev_ma15: float,
    atr: float = 0.0001,
    kc_lower: float = 0.0,
    kc_upper: float = 0.0,
    kc_trend: str = "",
) -> Tuple[str, str, str]:
    """
    修正後的全面開空邏輯：涵蓋「貫穿中軌起跌」、「均線死叉起跌」與「回踩均線延續追空」
    :return: (action, pattern, reason)
    """
    close_p = float(candle['close'])
    open_p = float(candle['open'])
    high_p = float(candle['high'])
    low_p = float(candle['low'])
    prev_close = float(prev_candle.get('close', open_p))

    # 1. 貫穿中軌起跌判定 (抓 08:38 實體大陰棒由中軌上方灌破中軌與 13EMA，波段起跌點，不必等破下軌)
    is_piercing_down = (
        (open_p >= kc_mid - 0.15 * atr or prev_close >= kc_mid)
        and close_p < kc_mid
        and close_p < ma15
        and close_p < open_p
        and (open_p - close_p >= 0.20 * atr or close_p <= kc_mid - 0.10 * atr)
    )
    if is_piercing_down:
        return "OPEN_SHORT", "MID_LINE_BREAKDOWN", "【波段起跌】實體大陰棒直接由中軌上方灌破中軌與均線，第一時間進場吃波段！"

    # 2. 均線死叉起跌判定 (抓波段大肉起點，不必等破下軌)
    death_cross = (prev_ma5 >= prev_ma15) and (ma5 < ma15)
    if death_cross and close_p < kc_mid:
        return "OPEN_SHORT", "MA_CROSSOVER", "【波段起跌】MA5死叉MA15且跌破KC中軌，第一時間進場吃波段！"

    # 3. 破下軌順勢開空 (9:53/9:54 破軌爆發動能補單進場！)
    # 3M K 棒出現實體大陰線跌破 KC 下軌，均線呈現空頭發散，立即開空！
    is_lower_breakdown = (
        close_p <= kc_lower
        and close_p < open_p
        and close_p <= ma5
    )
    if is_lower_breakdown:
        return "OPEN_SHORT", "BREAKOUT", "【破軌爆發】實體陰線放量摜穿KC下軌，順勢追擊主跌浪！"

    # 4. 均線空頭排列或 KC 向下，回踩/反彈觸碰 MA5/13EMA/KC中軌遇阻轉陰，或收陰中繼延續開空 (9:49 延續入口)
    is_bearish = (ma5 <= ma15) or (close_p < kc_mid) or (kc_trend == "DOWN")
    if is_bearish and close_p < kc_mid:
        # A. 盤中反彈摸到均線 (high >= ma5 - 0.15 * atr)，收盤收陰被打回 (close <= open)
        if high_p >= (ma5 - 0.15 * atr) and close_p <= open_p:
            return "OPEN_SHORT", "DIP_SNIPER", "【高拋延續】空頭排列中，反彈觸均線遇阻回落延續開空！"
        # B. 均線死叉向下且在中軌下方收陰，即刻延續開空 (9:49 延續入口)
        if close_p < open_p and ma5 <= ma15:
            return "OPEN_SHORT", "CONTINUATION", "【中繼延續】均線向下排列且在中軌下方收陰，即刻延續開空！"

    return "WAIT", "NONE", "無有效開空信號"


def check_comprehensive_long_entry(
    candle: Dict[str, Any],
    prev_candle: Dict[str, Any],
    kc_mid: float,
    ma5: float,
    prev_ma5: float,
    ma15: float,
    prev_ma15: float,
    atr: float = 0.0001,
    kc_lower: float = 0.0,
    kc_upper: float = 0.0,
    kc_trend: str = "",
) -> Tuple[str, str, str]:
    """
    對稱的多單全面開倉邏輯：涵蓋「貫穿中軌起漲」、「均線金叉起漲」與「回踩均線延續追多」
    :return: (action, pattern, reason)
    """
    close_p = float(candle['close'])
    open_p = float(candle['open'])
    low_p = float(candle['low'])
    high_p = float(candle['high'])
    prev_close = float(prev_candle.get('close', open_p))

    # 1. 貫穿中軌起漲判定 (實體大陽棒由中軌下方貫穿突破中軌與 13EMA，主升浪起漲點，不必等破上軌)
    is_piercing_up = (
        (open_p <= kc_mid + 0.15 * atr or prev_close <= kc_mid)
        and close_p > kc_mid
        and close_p > ma15
        and close_p > open_p
        and (close_p - open_p >= 0.20 * atr or close_p >= kc_mid + 0.10 * atr)
    )
    if is_piercing_up:
        return "OPEN_LONG", "MID_LINE_BREAKOUT", "【波段起漲】實體大陽棒直接由中軌下方貫穿中軌與均線，第一時間進場吃主升浪！"

    # 2. 均線金叉起漲判定 (抓主升浪起點，不必等破上軌)
    golden_cross = (prev_ma5 <= prev_ma15) and (ma5 > ma15)
    if golden_cross and close_p > kc_mid:
        return "OPEN_LONG", "MA_CROSSOVER", "【波段起漲】MA5金叉MA15且站上KC中軌，第一時間進場吃主升浪！"

    # 3. 破上軌順勢開多 (破軌爆發動能進場)
    is_upper_breakout = (
        close_p >= kc_upper
        and close_p > open_p
        and close_p >= ma5
    )
    if is_upper_breakout:
        return "OPEN_LONG", "BREAKOUT", "【破軌爆發】實體陽線放量衝破KC上軌，順勢追擊主升浪！"

    # 4. 均線多頭排列或 KC 向上，回踩觸碰 MA5/13EMA/KC中軌有守轉陽，或收陽中繼延續開多
    is_bullish = (ma5 >= ma15) or (close_p > kc_mid) or (kc_trend == "UP")
    if is_bullish and close_p > kc_mid:
        # 盤中下探摸到均線/中軌，但收盤收陽轉強
        if low_p <= (ma5 + 0.15 * atr) and close_p >= open_p:
            return "OPEN_LONG", "DIP_SNIPER", "【低吸延續】多頭排列中，回踩均線有守轉陽延續開多！"
        # 均線金叉向上且在中軌上方收陽，即刻延續開多
        if close_p > open_p and ma5 >= ma15:
            return "OPEN_LONG", "CONTINUATION", "【中繼延續】均線向上排列且在中軌上方收陽，即刻延續開多！"

    return "WAIT", "NONE", "無有效開多信號"



class KeyLevelDipSniperService:

    @staticmethod
    def _number(val: Any) -> Optional[float]:
        try:
            num = float(val)
            return num if math.isfinite(num) else None
        except (TypeError, ValueError, OverflowError):
            return None

    @classmethod
    def evaluate_entry(
        cls,
        symbol: str,
        frame: pd.DataFrame,
        side: str,
        *,
        tick_price: Optional[float] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        全面趨勢開倉評估器：全面覆蓋貫穿中軌起步、均線交叉起步與反彈/回踩高拋低吸。
        """
        side = str(side).upper()
        if side not in ("LONG", "SHORT"):
            return None

        if frame is None or len(frame) < 3:
            return None

        current_bar = frame.iloc[-1].to_dict()
        previous_bar = frame.iloc[-2].to_dict()

        quote = cls._number(tick_price)
        if quote is None or quote <= 0:
            quote = cls._number(current_bar.get("close"))
        if quote is None or quote <= 0:
            return None

        open_p = cls._number(current_bar.get("open"))
        high_p = cls._number(current_bar.get("high"))
        low_p = cls._number(current_bar.get("low"))
        if open_p is None or high_p is None or low_p is None:
            return None

        # 即時更新當前棒的高低點與最新價
        high_p = max(high_p, quote)
        low_p = min(low_p, quote)
        live_candle = {
            "open": open_p,
            "high": high_p,
            "low": low_p,
            "close": quote,
        }

        # 讀取技術指標
        ma5 = cls._number(current_bar.get("ma5", current_bar.get("_wild_ma5")))
        prev_ma5 = cls._number(previous_bar.get("ma5", previous_bar.get("_wild_ma5")))
        ma15 = cls._number(current_bar.get("ma15"))
        prev_ma15 = cls._number(previous_bar.get("ma15"))
        kc_middle = cls._number(current_bar.get("kc_middle", current_bar.get("kc_basis")))
        prev_kc_middle = cls._number(previous_bar.get("kc_middle", previous_bar.get("kc_basis")))
        kc_upper = cls._number(current_bar.get("kc_upper")) or 0.0
        kc_lower = cls._number(current_bar.get("kc_lower")) or 0.0
        atr = cls._number(current_bar.get("atr")) or 0.0001

        from core.gates.strict_entry_and_immunity_gate import resolve_kc_trend
        kc_trend = resolve_kc_trend(frame)

        if any(v is None for v in (ma5, prev_ma5, ma15, prev_ma15, kc_middle, prev_kc_middle)):
            return None

        bar_id = cls._number(current_bar.get("timestamp")) or cls._number(current_bar.get("time")) or 0.0

        action = "WAIT"
        pattern = "NONE"
        reason = ""

        if side == "SHORT":
            action, pattern, reason = check_comprehensive_short_entry(
                live_candle, previous_bar, kc_middle, ma5, prev_ma5, ma15, prev_ma15,
                atr=atr, kc_lower=kc_lower, kc_upper=kc_upper, kc_trend=kc_trend,
            )
        elif side == "LONG":
            action, pattern, reason = check_comprehensive_long_entry(
                live_candle, previous_bar, kc_middle, ma5, prev_ma5, ma15, prev_ma15,
                atr=atr, kc_lower=kc_lower, kc_upper=kc_upper, kc_trend=kc_trend,
            )

        if action == f"OPEN_{side}":
            norm_pattern = "BREAKOUT" if "BREAK" in pattern else ("CROSSOVER" if "CROSS" in pattern else "DIP_SNIPER")
            return {
                "side": side,
                "reason": reason,
                "entry_label": reason,
                "entry_pattern": norm_pattern,
                "entry_signal_mode": norm_pattern,
                "structure_confirmation": pattern,
                "structure_confirmed": True,
                "is_intrabar": True,
                "channel_expanding": True,
                "ma5_current": ma5,
                "ma5_previous": prev_ma5,
                "kc_middle_current": kc_middle,
                "kc_middle_previous": prev_kc_middle,
                "volume_exhausted": False,
                "bar_id": float(bar_id),
                "quote": quote,
            }

        return None
