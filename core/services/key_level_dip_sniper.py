"""
KeyLevelDipSniperService (回踩低吸多 / 反彈摸高空狙擊服務)

核心哲學：
徹底摒棄追漲殺跌（追在天花板或地板）的追價邏輯，
改為「回測關鍵支撐/壓力位收針起步瞬間進場（右側）」。

1. 多單開在 K 線低點 (回踩低吸)：
   - 整體趨勢向上 (KC 中軌走平或向上，MA15 走平或向上)
   - 回踩關鍵支撐 (踩在 MA5 或 KC 中軌附近)
   - 位於整根 K 棒低檔區 (price_position_ratio <= 0.40)
   - 下探有守開始回抽 (現價高於最低點，且回到/超越開盤價，形成下影線起步陽棒)
   - 極小試錯止損 (低點稍下方)

2. 空單開在 K 線高點 (反彈摸高)：
   - 整體趨勢向下 (KC 中軌走平或向下，MA15 走平或向下)
   - 反彈測試關鍵壓力 (摸到 MA5 或 KC 中軌附近)
   - 位於整根 K 棒高檔區 (price_position_ratio >= 0.60)
   - 衝高遇阻開始回落 (現價低於最高點，且回到/跌破開盤價，形成上影線遇阻陰棒)
   - 極小試錯止損 (高點稍上方)
"""

from __future__ import annotations

import math
from typing import Any, Dict, Optional, Tuple
import pandas as pd


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
        評估單邊幣種當前是否觸發「低吸高拋狙擊」進場訊號。
        :param symbol: 交易幣種 (如 'CAP/USDT')
        :param frame: 1m K 線 DataFrame (包含 open, high, low, close, ma5, ma15, kc_middle, atr 等)
        :param side: 'LONG' 或 'SHORT'
        :param tick_price: 最新報價 (quote)
        :return: 結構化入場訊號字典 或 None
        """
        side = str(side).upper()
        if side not in ("LONG", "SHORT"):
            return None

        if frame is None or len(frame) < 3:
            return None

        # 獲取最新與上一根 K 線數據
        current_bar = frame.iloc[-1]
        previous_bar = frame.iloc[-2]

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

        # 即時更新當前棒的高低點
        high_p = max(high_p, quote)
        low_p = min(low_p, quote)

        candle_range = high_p - low_p
        if candle_range <= 0:
            return None

        # 價格在整根 K 棒高低點中的相對百分比位置 (0% 為最低, 100% 為最高)
        pos_ratio = (quote - low_p) / candle_range

        # 讀取技術指標
        ma5 = cls._number(current_bar.get("ma5"))
        prev_ma5 = cls._number(previous_bar.get("ma5"))
        ma15 = cls._number(current_bar.get("ma15"))
        prev_ma15 = cls._number(previous_bar.get("ma15"))
        kc_middle = cls._number(current_bar.get("kc_middle"))
        prev_kc_middle = cls._number(previous_bar.get("kc_middle"))
        atr = cls._number(current_bar.get("atr")) or (candle_range if candle_range > 0 else 0.001)

        if any(v is None for v in (ma5, prev_ma5, ma15, prev_ma15, kc_middle, prev_kc_middle)):
            return None

        bar_id = cls._number(current_bar.get("timestamp")) or cls._number(current_bar.get("bar_id"))
        if bar_id is None:
            bar_id = 0.0

        # =========================================================================
        # 1. 多單開在 K 線低點 (回踩低吸)
        # =========================================================================
        if side == "LONG":
            # 鐵律 1：大趨勢必須為多頭 (KC 中軌向上或走平，MA15 向上或走平，MA5 > MA15)
            # 空頭趨勢絕對一票否決！
            if kc_middle < prev_kc_middle or ma15 < prev_ma15:
                return None
            if ma5 < ma15:
                return None

            # 條件 2：回踩測試關鍵支撐 (K 棒最低價下探到 MA5 或 KC 中軌附近，容差 0.1 ATR)
            support_level = max(ma5, kc_middle)
            tested_support = (low_p <= support_level + 0.1 * atr)

            # 條件 3：買在整根 K 棒的相對低檔區 (0% ~ 40%)，拒絕追高
            at_candle_bottom = (pos_ratio <= 0.40)

            # 條件 4：下探有守並開始起抽收陽 (現價高於最低點，且站回/超越開盤價)
            rebound_confirmed = (quote > low_p) and (quote >= open_p)

            # 條件 5：下影線具備支撐動能
            lower_wick = min(open_p, quote) - low_p
            has_support_wick = (lower_wick >= 0.15 * atr)

            if tested_support and at_candle_bottom and rebound_confirmed and has_support_wick:
                stop_loss = low_p - 0.1 * atr
                reason = "DIP_SNIPER_LOW_REBOUND_ENTRY"
                return {
                    "side": "LONG",
                    "reason": reason,
                    "entry_label": reason,
                    "entry_pattern": "DIP_SNIPER",
                    "entry_signal_mode": "DIP_SNIPER",
                    "structure_confirmation": "DIP_SNIPER",
                    "structure_confirmed": True,
                    "is_intrabar": True,
                    "channel_expanding": True,
                    "ma5_current": ma5,
                    "ma5_previous": prev_ma5,
                    "kc_middle_current": kc_middle,
                    "kc_middle_previous": prev_kc_middle,
                    "volume_exhausted": False,
                    "bar_id": float(bar_id),
                    "pos_ratio": pos_ratio,
                    "stop_loss": stop_loss,
                    "support_level": support_level,
                }

        # =========================================================================
        # 2. 空單開在 K 線高點 (反彈摸高)
        # =========================================================================
        elif side == "SHORT":
            # 鐵律 1：大趨勢必須為空頭 (KC 中軌向下或走平，MA15 向下或走平，MA5 < MA15)
            # 多頭趨勢絕對一票否決！
            if kc_middle > prev_kc_middle or ma15 > prev_ma15:
                return None
            if ma5 > ma15:
                return None

            # 條件 2：反彈測試關鍵壓力 (K 棒最高價衝高到 MA5 或 KC 中軌附近，容差 0.1 ATR)
            resistance_level = min(ma5, kc_middle)
            tested_resistance = (high_p >= resistance_level - 0.1 * atr)

            # 條件 3：賣在整根 K 棒的相對高檔區 (60% ~ 100%)，拒絕殺跌
            at_candle_top = (pos_ratio >= 0.60)

            # 條件 4：衝高遇阻並開始回落收陰 (現價低於最高點，且跌破/低於開盤價)
            reject_confirmed = (quote < high_p) and (quote <= open_p)

            # 條件 5：上影線具備壓制動能
            upper_wick = high_p - max(open_p, quote)
            has_resist_wick = (upper_wick >= 0.15 * atr)

            if tested_resistance and at_candle_top and reject_confirmed and has_resist_wick:
                stop_loss = high_p + 0.1 * atr
                reason = "DIP_SNIPER_HIGH_REJECTION_ENTRY"
                return {
                    "side": "SHORT",
                    "reason": reason,
                    "entry_label": reason,
                    "entry_pattern": "DIP_SNIPER",
                    "entry_signal_mode": "DIP_SNIPER",
                    "structure_confirmation": "DIP_SNIPER",
                    "structure_confirmed": True,
                    "is_intrabar": True,
                    "channel_expanding": True,
                    "ma5_current": ma5,
                    "ma5_previous": prev_ma5,
                    "kc_middle_current": kc_middle,
                    "kc_middle_previous": prev_kc_middle,
                    "volume_exhausted": False,
                    "bar_id": float(bar_id),
                    "pos_ratio": pos_ratio,
                    "stop_loss": stop_loss,
                    "resistance_level": resistance_level,
                }

        return None
