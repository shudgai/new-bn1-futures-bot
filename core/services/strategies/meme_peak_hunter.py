"""
MemePeakHunterStrategy (妖幣 1分鐘 趨勢與真頂底獵手)

專為妖幣（高波動、劇烈插針、拉盤暴跌的山寨幣）量身打造：
1. 嚴苛開倉 (波動放大突破)：
   - 多單：站穩 VWAP + 站穩 60MA + 60MA向上 + 5MA金叉15MA + 伴隨放量 1.2 倍
   - 空單：灌破 VWAP + 壓在 60MA下 + 60MA向下 + 5MA死叉15MA + 放量下殺 1.2 倍
2. 妖幣「真逢頂 / 真見底」閃電逃生：
   - 天量射擊之星 / 爆量滯漲 (Climax Top: 成交量 > 2.0倍均量 且 上影線 > 1.5倍實體) -> 秒平多單
   - 趨勢結構實體潰敗 (Trend Dead: 收盤跌破 15MA 且跌破 VWAP) -> 秒平多單
   - 天量錘子線 / 恐慌抄底 (Climax Bottom: 成交量 > 2.0倍均量 且 下影線 > 1.5倍實體) -> 秒平空單
   - 空方結構實體被反噬 (Short Trend Dead: 收盤站回 15MA 且站回 VWAP) -> 秒平空單
3. 動態防插針 ATR 移動鎖利 (ATR Trailing):
   - 採用 2.5 倍 ATR 撐開安全呼吸距離，防止 1%~2% 常態插針冤死，回吐超過 2.5 ATR 時落袋。
4. 拒絕頻繁反手：平倉後強制冷卻，保護本金。
"""

from __future__ import annotations

import math
from typing import Any, Dict, Optional, Tuple
import pandas as pd


class MemePeakHunterStrategy:
    MA5_LEN = 5
    MA15_LEN = 15
    MA60_LEN = 60
    ATR_LEN = 14
    ATR_MULT = 2.5       # 防插針 ATR 倍數 (移動鎖利距 2.5 ATR)
    VOL_MULT = 2.0       # 頂部天量倍數 (成交量 > 20 均量 2.0 倍)
    ENTRY_VOL_MULT = 1.2 # 開倉放量倍數 (成交量 > 20 均量 1.2 倍)

    @staticmethod
    def _number(val: Any) -> Optional[float]:
        try:
            num = float(val)
            return num if math.isfinite(num) else None
        except (TypeError, ValueError, OverflowError):
            return None

    @classmethod
    def calculate_indicators(cls, frame: pd.DataFrame) -> Dict[str, Any]:
        """計算 1m 線的各項指標快照。"""
        if frame is None or len(frame) < 3:
            return {}

        df = frame.copy()
        closes = df["close"]
        highs = df["high"]
        lows = df["low"]
        vols = df.get("volume", pd.Series([1.0] * len(df), index=df.index))

        # 1. 均線計算
        ma5_s = closes.rolling(cls.MA5_LEN, min_periods=1).mean()
        ma15_s = closes.rolling(cls.MA15_LEN, min_periods=1).mean()
        ma60_s = closes.rolling(cls.MA60_LEN, min_periods=1).mean()

        # 2. VWAP 計算
        typical_price = (highs + lows + closes) / 3.0
        cum_vol = vols.cumsum()
        cum_tp_vol = (typical_price * vols).cumsum()
        vwap_s = cum_tp_vol / cum_vol.replace(0, 1)

        # 3. ATR 計算
        tr1 = highs - lows
        tr2 = (highs - closes.shift(1)).abs()
        tr3 = (lows - closes.shift(1)).abs()
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        atr_s = tr.rolling(cls.ATR_LEN, min_periods=1).mean()

        # 4. 成交量 20 均量
        vol_ma_s = vols.rolling(20, min_periods=1).mean()

        curr = df.iloc[-1]
        prev = df.iloc[-2] if len(df) >= 2 else curr

        curr_ma60 = float(ma60_s.iloc[-1])
        prev_ma60 = float(ma60_s.iloc[-2]) if len(df) >= 2 else curr_ma60

        return {
            "ma5": float(ma5_s.iloc[-1]),
            "prev_ma5": float(ma5_s.iloc[-2]) if len(df) >= 2 else float(ma5_s.iloc[-1]),
            "ma15": float(ma15_s.iloc[-1]),
            "prev_ma15": float(ma15_s.iloc[-2]) if len(df) >= 2 else float(ma15_s.iloc[-1]),
            "ma60": curr_ma60,
            "prev_ma60": prev_ma60,
            "ma60_up": curr_ma60 > prev_ma60,
            "ma60_down": curr_ma60 < prev_ma60,
            "vwap": float(vwap_s.iloc[-1]),
            "atr": float(atr_s.iloc[-1]),
            "volume": float(vols.iloc[-1]),
            "vol_ma": float(vol_ma_s.iloc[-1]),
            "open": float(curr.get("open", closes.iloc[-1])),
            "high": float(curr.get("high", closes.iloc[-1])),
            "low": float(curr.get("low", closes.iloc[-1])),
            "close": float(closes.iloc[-1]),
            "timestamp": float(curr.get("timestamp", 0.0)),
        }

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
        妖幣趨勢爆發開倉審查：
        多單：站穩 VWAP + 站穩 60MA + 60MA向上 + 5MA金叉15MA + 伴隨放量 1.2 倍
        空單：灌破 VWAP + 壓在 60MA下 + 60MA向下 + 5MA死叉15MA + 放量下殺 1.2 倍
        """
        side = str(side).upper()
        if side not in ("LONG", "SHORT"):
            return None

        ind = cls.calculate_indicators(frame)
        if not ind:
            return None

        quote = cls._number(tick_price) or ind["close"]
        ma5 = ind["ma5"]
        prev_ma5 = ind["prev_ma5"]
        ma15 = ind["ma15"]
        prev_ma15 = ind["prev_ma15"]
        ma60 = ind["ma60"]
        ma60_up = ind["ma60_up"]
        ma60_down = ind["ma60_down"]
        vwap_val = ind["vwap"]
        vol = ind["volume"]
        vol_ma = ind["vol_ma"]

        # 成交量放量判定
        is_vol_expanding = (vol >= vol_ma * cls.ENTRY_VOL_MULT)

        if side == "LONG":
            # 多單進場條件
            above_vwap = quote > vwap_val
            above_ma60 = quote > ma60
            ma_crossover = (ma5 > ma15) and (prev_ma5 <= prev_ma15 or abs(ma5 - ma15) >= 0.05 * ind["atr"])

            if above_vwap and above_ma60 and ma60_up and ma_crossover and is_vol_expanding:
                reason = "MEME_PEAK_HUNTER_VOL_EXPANSION_LONG"
                return {
                    "side": "LONG",
                    "reason": reason,
                    "entry_label": reason,
                    "entry_pattern": "BREAKOUT",
                    "entry_signal_mode": "BREAKOUT",
                    "structure_confirmation": "MEME_PEAK_HUNTER",
                    "structure_confirmed": True,
                    "is_intrabar": True,
                    "channel_expanding": True,
                    "ma5_current": ma5,
                    "ma5_previous": prev_ma5,
                    "volume_exhausted": False,
                    "bar_id": ind["timestamp"],
                    "vwap": vwap_val,
                    "ma60": ma60,
                }

        elif side == "SHORT":
            # 空單進場條件
            below_vwap = quote < vwap_val
            below_ma60 = quote < ma60
            ma_crossunder = (ma5 < ma15) and (prev_ma5 >= prev_ma15 or abs(ma15 - ma5) >= 0.05 * ind["atr"])

            if below_vwap and below_ma60 and ma60_down and ma_crossunder and is_vol_expanding:
                reason = "MEME_PEAK_HUNTER_VOL_EXPANSION_SHORT"
                return {
                    "side": "SHORT",
                    "reason": reason,
                    "entry_label": reason,
                    "entry_pattern": "BREAKOUT",
                    "entry_signal_mode": "BREAKOUT",
                    "structure_confirmation": "MEME_PEAK_HUNTER",
                    "structure_confirmed": True,
                    "is_intrabar": True,
                    "channel_expanding": True,
                    "ma5_current": ma5,
                    "ma5_previous": prev_ma5,
                    "volume_exhausted": False,
                    "bar_id": ind["timestamp"],
                    "vwap": vwap_val,
                    "ma60": ma60,
                }

        return None

    @classmethod
    def evaluate_exit(
        cls,
        symbol: str,
        position: Dict[str, Any],
        frame: pd.DataFrame,
        quote: float,
    ) -> Optional[Tuple[str, str, Dict[str, Any]]]:
        """
        妖幣專用：真逢頂/真見底判定與 ATR 防插針移動鎖利。
        Returns:
            (exit_code: str, reason_text: str, details: dict) 或 None
        """
        if not position or frame is None or len(frame) < 2:
            return None

        side = str(position.get("side", "")).upper()
        ind = cls.calculate_indicators(frame)
        if not ind:
            return None

        close_p = float(quote)
        open_p = ind["open"]
        high_p = max(ind["high"], close_p)
        low_p = min(ind["low"], close_p)
        volume = ind["volume"]
        vol_ma = ind["vol_ma"]
        ma15 = ind["ma15"]
        vwap_val = ind["vwap"]
        atr = ind["atr"] if ind["atr"] > 0 else 0.0001
        entry_p = float(position.get("entry_price", close_p))

        body = abs(open_p - close_p)
        body = max(body, 1e-8)

        # =====================================================================
        # 1. 多單平倉判定
        # =====================================================================
        if side == "LONG":
            # 1.1 天量射擊之星 / 爆量滯漲 (Climax Top: 主力天量出貨典型特徵)
            upper_wick = high_p - max(open_p, close_p)
            has_high_wick = upper_wick > (body * 1.5)
            is_huge_volume = (volume >= vol_ma * cls.VOL_MULT)
            is_above_ma15 = (close_p > ma15)

            if is_huge_volume and has_high_wick and is_above_ma15:
                details = {
                    "cause": "CLIMAX_TOP",
                    "upper_wick": upper_wick,
                    "body": body,
                    "volume": volume,
                    "vol_ma": vol_ma,
                }
                return "EXIT_BY_MEME_CLIMAX_TOP", "妖幣天量見頂跑路 (天量長上影線滯漲)", details

            # 1.2 趨勢結構實體潰敗 (收盤跌破 15MA 且跌破 VWAP)
            if close_p < ma15 and close_p < vwap_val:
                details = {
                    "cause": "TREND_DEAD",
                    "close": close_p,
                    "ma15": ma15,
                    "vwap": vwap_val,
                }
                return "EXIT_BY_MEME_TREND_DEAD", "跌破VWAP與15MA多頭結構潰敗平倉", details

            # 1.3 妖幣動態 ATR 移動鎖利 (防插針安全距離)
            peak_price = float(position.get("highest_price") or position.get("peak_price") or entry_p)
            peak_price = max(peak_price, high_p)
            profit_reach_atr = (peak_price - entry_p) / atr
            pullback_atr = (peak_price - close_p) / atr

            # 當利潤曾達到 1.5 ATR 以上，且從最高點回吐超過 2.5 ATR 時觸發鎖利
            if profit_reach_atr >= 1.5 and pullback_atr >= cls.ATR_MULT:
                details = {
                    "cause": "ATR_TRAILING_LOCK",
                    "peak_price": peak_price,
                    "pullback_atr": pullback_atr,
                    "atr": atr,
                }
                return "EXIT_BY_MEME_ATR_TRAILING", f"妖幣ATR防插針移動鎖利 (回吐 {pullback_atr:.1f} ATR)", details

        # =====================================================================
        # 2. 空單平倉判定
        # =====================================================================
        elif side == "SHORT":
            # 2.1 天量錘子線 / 恐慌抄底 (Climax Bottom: 恐慌拋盤後主力強接盤)
            lower_wick = min(open_p, close_p) - low_p
            has_low_wick = lower_wick > (body * 1.5)
            is_huge_volume = (volume >= vol_ma * cls.VOL_MULT)
            is_below_ma15 = (close_p < ma15)

            if is_huge_volume and has_low_wick and is_below_ma15:
                details = {
                    "cause": "CLIMAX_BOTTOM",
                    "lower_wick": lower_wick,
                    "body": body,
                    "volume": volume,
                    "vol_ma": vol_ma,
                }
                return "EXIT_BY_MEME_CLIMAX_BOTTOM", "妖幣天量見底回補 (天量長下影線接盤)", details

            # 2.2 空方結構實體被反噬 (收盤站回 15MA 且站回 VWAP)
            if close_p > ma15 and close_p > vwap_val:
                details = {
                    "cause": "SHORT_TREND_DEAD",
                    "close": close_p,
                    "ma15": ma15,
                    "vwap": vwap_val,
                }
                return "EXIT_BY_MEME_SHORT_TREND_DEAD", "突破VWAP與15MA空頭結構潰敗平倉", details

            # 2.3 妖幣動態 ATR 移動鎖利 (防插針安全距離)
            trough_price = float(position.get("lowest_price") or position.get("trough_price") or entry_p)
            trough_price = min(trough_price, low_p)
            profit_reach_atr = (entry_p - trough_price) / atr
            pullback_atr = (close_p - trough_price) / atr

            # 當利潤曾達到 1.5 ATR 以上，且從最低點反彈超過 2.5 ATR 時觸發鎖利
            if profit_reach_atr >= 1.5 and pullback_atr >= cls.ATR_MULT:
                details = {
                    "cause": "ATR_TRAILING_LOCK",
                    "trough_price": trough_price,
                    "pullback_atr": pullback_atr,
                    "atr": atr,
                }
                return "EXIT_BY_MEME_ATR_TRAILING", f"妖幣ATR防插針移動鎖利 (反彈 {pullback_atr:.1f} ATR)", details

        return None
