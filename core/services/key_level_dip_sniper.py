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


def check_comprehensive_short_entry(candle: Dict[str, Any], prev_candle: Dict[str, Any], kc_mid: float, ma5: float, prev_ma5: float, ma15: float, prev_ma15: float) -> Tuple[str, str, str]:
    """
    修正後的全面開空邏輯：涵蓋「均線死叉起跌」與「回踩遇阻摸高」
    :return: (action, pattern, reason)
    """
    close_p = float(candle['close'])
    open_p = float(candle['open'])
    high_p = float(candle['high'])

    # 1. 均線死叉起跌判定 (抓波段大肉起點，不必等破下軌)
    death_cross = (prev_ma5 >= prev_ma15) and (ma5 < ma15)
    if death_cross and close_p < kc_mid:
        return "OPEN_SHORT", "MA_CROSSOVER", "【波段起跌】MA5死叉MA15且跌破KC中軌，第一時間進場吃波段！"

    # 2. 均線已空頭排列，反彈觸碰 MA5/KC中軌遇阻 (DIP_SNIPER 高拋點)
    is_bearish = (ma5 < ma15) and (close_p < kc_mid)
    if is_bearish:
        # 盤中反彈摸到均線，但收盤收陰被打回
        if high_p >= ma5 and close_p <= open_p:
            return "OPEN_SHORT", "DIP_SNIPER", "【高拋狙擊】空頭排列中，反彈觸均線遇阻回落開空！"

    return "WAIT", "NONE", "無有效開空信號"


def check_comprehensive_long_entry(candle: Dict[str, Any], prev_candle: Dict[str, Any], kc_mid: float, ma5: float, prev_ma5: float, ma15: float, prev_ma15: float) -> Tuple[str, str, str]:
    """
    對稱的多單全面開倉邏輯：涵蓋「均線金叉起漲」與「回踩支撐低吸」
    :return: (action, pattern, reason)
    """
    close_p = float(candle['close'])
    open_p = float(candle['open'])
    low_p = float(candle['low'])

    # 1. 均線金叉起漲判定 (抓主升浪起點，不必等破上軌)
    golden_cross = (prev_ma5 <= prev_ma15) and (ma5 > ma15)
    if golden_cross and close_p > kc_mid:
        return "OPEN_LONG", "MA_CROSSOVER", "【波段起漲】MA5金叉MA15且站上KC中軌，第一時間進場吃主升浪！"

    # 2. 均線已多頭排列，回踩觸碰 MA5/KC中軌有守 (DIP_SNIPER 低吸點)
    is_bullish = (ma5 > ma15) and (close_p > kc_mid)
    if is_bullish:
        # 盤中下探摸到均線/中軌，但收盤收陽轉強
        if low_p <= ma5 and close_p >= open_p:
            return "OPEN_LONG", "DIP_SNIPER", "【低吸狙擊】多頭排列中，回踩均線有守轉陽開多！"

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
        全面趨勢開倉評估器：全面覆蓋均線交叉起步與反彈/回踩高拋低吸。
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
        atr = cls._number(current_bar.get("atr")) or 0.0001

        if any(v is None for v in (ma5, prev_ma5, ma15, prev_ma15, kc_middle, prev_kc_middle)):
            return None

        bar_id = cls._number(current_bar.get("timestamp")) or cls._number(current_bar.get("time")) or 0.0

        action = "WAIT"
        pattern = "NONE"
        reason = ""

        if side == "SHORT":
            action, pattern, reason = check_comprehensive_short_entry(
                live_candle, previous_bar, kc_middle, ma5, prev_ma5, ma15, prev_ma15
            )
        elif side == "LONG":
            action, pattern, reason = check_comprehensive_long_entry(
                live_candle, previous_bar, kc_middle, ma5, prev_ma5, ma15, prev_ma15
            )

        if action == f"OPEN_{side}":
            return {
                "side": side,
                "reason": reason,
                "entry_label": reason,
                "entry_pattern": pattern,
                "entry_signal_mode": pattern,
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
