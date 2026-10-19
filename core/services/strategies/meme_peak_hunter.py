"""
MemePeakHunterStrategy (妖幣 3分鐘 EMA 趨勢與真頂底獵手)

專為幣安 3分鐘圖表高波動妖幣（高波動、劇烈插針、拉盤暴跌的山寨幣）量身打造：
1. 均線系統全面更新：
   - 捨棄舊的 SMA5/SMA15，改用 EMA5（快線/攻擊線）與 EMA13（慢線/防守線）。
   - 加入 EMA40（約 2 小時趨勢保護線）與 VWAP（成交量加權平均價）。
2. 進場規則（杜絕隨機雙巴）：
   - 做多：現價 > VWAP，現價 > EMA40，EMA40 斜率向上，EMA5 金叉 EMA13，且伴隨成交量突破（量大於 20 均量 1.1 倍）。
   - 做空：現價 < VWAP，現價 < EMA40，EMA40 斜率向下，EMA5 死叉 EMA13，且放量下殺（量大於 20 均量 1.1 倍）。
3. 出場與防守機制（解決一會兒多、一會兒空）：
   - 抱單邏輯：只要多頭趨勢未破就持續抱單，忽視微小死叉。
   - 平倉動作：觸發退場時【只平倉退回空手觀望，絕對禁止立刻反向開倉】。
   - 妖幣真逢頂偵測：3分鐘內爆天量（量大於 20 均量 1.8 倍）且收長上影線（射擊之星倒貨）時，立刻市價全平搶跑。
   - 趨勢徹底潰敗：實體收盤跌破 EMA13 且跌破 VWAP 平倉。
   - 空單鏡像：天量錘子線（接盤）或站回 EMA13+VWAP 平倉。
   - 防插針移動鎖利：使用 1.8 倍 ATR 動態追蹤止損，隨著高點推進自動墊高防守點。
"""

from __future__ import annotations

import math
from typing import Any, Dict, Optional, Tuple
import pandas as pd


class MemePeakHunterStrategy:
    EMA5_LEN = 5
    EMA13_LEN = 13
    EMA40_LEN = 40
    ATR_LEN = 14
    ATR_MULT = 1.8       # 防插針 ATR 倍數 (3m 妖幣移動鎖利距 1.8 ATR)
    VOL_MULT = 1.8       # 頂部爆天量倍數 (成交量 > 20 均量 1.8 倍)
    ENTRY_VOL_MULT = 1.1 # 開倉放量倍數 (成交量 > 20 均量 1.1 倍)

    @staticmethod
    def _number(val: Any) -> Optional[float]:
        try:
            num = float(val)
            return num if math.isfinite(num) else None
        except (TypeError, ValueError, OverflowError):
            return None

    @classmethod
    def calculate_indicators(cls, frame: pd.DataFrame) -> Dict[str, Any]:
        """計算 3m 線的各項指標快照 (EMA5, EMA13, EMA40, VWAP, ATR14, VolMA20)。"""
        if frame is None or len(frame) < 3:
            return {}

        df = frame.copy()
        closes = df["close"]
        highs = df["high"]
        lows = df["low"]
        vols = df.get("volume", pd.Series([1.0] * len(df), index=df.index))

        # 1. 均線計算 (EMA5, EMA13, EMA40)
        ema5_s = closes.ewm(span=cls.EMA5_LEN, adjust=False).mean()
        ema13_s = closes.ewm(span=cls.EMA13_LEN, adjust=False).mean()
        ema40_s = closes.ewm(span=cls.EMA40_LEN, adjust=False).mean()

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
        curr_ema40 = float(ema40_s.iloc[-1])
        prev_ema40 = float(ema40_s.iloc[-2]) if len(df) >= 2 else curr_ema40

        curr_ema5 = float(ema5_s.iloc[-1])
        prev_ema5 = float(ema5_s.iloc[-2]) if len(df) >= 2 else curr_ema5
        curr_ema13 = float(ema13_s.iloc[-1])
        prev_ema13 = float(ema13_s.iloc[-2]) if len(df) >= 2 else curr_ema13

        return {
            "ema5": curr_ema5,
            "prev_ema5": prev_ema5,
            "ema13": curr_ema13,
            "prev_ema13": prev_ema13,
            "ema40": curr_ema40,
            "prev_ema40": prev_ema40,
            "ema40_up": curr_ema40 > prev_ema40,
            "ema40_down": curr_ema40 < prev_ema40,
            # 相容舊介面 (ma5/ma15/ma60) 鍵值
            "ma5": curr_ema5,
            "prev_ma5": prev_ema5,
            "ma15": curr_ema13,
            "prev_ma15": prev_ema13,
            "ma60": curr_ema40,
            "prev_ma60": prev_ema40,
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
        3m 妖幣趨勢爆發開倉審查：
        做多條件：現價 > VWAP，現價 > EMA40，EMA40 斜率向上，EMA5 金叉 EMA13，且伴隨成交量突破（量大於 20 均量 1.1 倍）。
        做空條件：現價 < VWAP，現價 < EMA40，EMA40 斜率向下，EMA5 死叉 EMA13，且放量下殺（量大於 20 均量 1.1 倍）。
        """
        side = str(side).upper()
        if side not in ("LONG", "SHORT"):
            return None

        ind = cls.calculate_indicators(frame)
        if not ind:
            return None

        quote = cls._number(tick_price) or ind["close"]
        ema5 = ind["ema5"]
        prev_ema5 = ind["prev_ema5"]
        ema13 = ind["ema13"]
        prev_ema13 = ind["prev_ema13"]
        ema40 = ind["ema40"]
        ema40_up = ind["ema40_up"]
        ema40_down = ind["ema40_down"]
        vwap_val = ind["vwap"]
        vol = ind["volume"]
        vol_ma = ind["vol_ma"]

        # 成交量放量判定 (量大於 20 均量 1.1 倍)
        is_vol_expanding = (vol >= vol_ma * cls.ENTRY_VOL_MULT)

        if side == "LONG":
            above_vwap = quote > vwap_val
            above_ema40 = quote > ema40
            ema_crossover = (ema5 > ema13) and (prev_ema5 <= prev_ema13 or abs(ema5 - ema13) >= 0.05 * ind["atr"])

            # 模式 1: 初始放量金叉開倉
            if above_vwap and above_ema40 and ema40_up and ema_crossover and is_vol_expanding:
                reason = "MEME_PEAK_HUNTER_3M_EMA_LONG"
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
                    "ma5_current": ema5,
                    "ma5_previous": prev_ema5,
                    "volume_exhausted": False,
                    "bar_id": ind["timestamp"],
                    "vwap": vwap_val,
                    "ema40": ema40,
                }

            # 模式 2: 平倉後強勢趨勢延續開倉（強制上車 / Re-entry）
            is_trend_intact = above_vwap and above_ema40 and ema40_up and (ema5 > ema13) and (ema5 >= prev_ema5)
            is_bullish_action = (quote >= ind["open"]) and (quote > ema5) and ((quote - ema5) <= 2.0 * ind["atr"])
            is_continuation_volume = (vol >= vol_ma * 0.6)  # 換手延續量

            if is_trend_intact and is_bullish_action and is_continuation_volume:
                reason = "MEME_PEAK_HUNTER_3M_CONTINUATION_LONG"
                return {
                    "side": "LONG",
                    "reason": reason,
                    "entry_label": reason,
                    "entry_pattern": "CONTINUATION",
                    "entry_signal_mode": "CONTINUATION",
                    "structure_confirmation": "MEME_PEAK_HUNTER",
                    "structure_confirmed": True,
                    "is_intrabar": True,
                    "channel_expanding": True,
                    "ma5_current": ema5,
                    "ma5_previous": prev_ema5,
                    "volume_exhausted": False,
                    "bar_id": ind["timestamp"],
                    "vwap": vwap_val,
                    "ema40": ema40,
                }

        elif side == "SHORT":
            below_vwap = quote < vwap_val
            below_ema40 = quote < ema40
            ema_crossunder = (ema5 < ema13) and (prev_ema5 >= prev_ema13 or abs(ema13 - ema5) >= 0.05 * ind["atr"])

            # 模式 1: 初始放量死叉開倉
            if below_vwap and below_ema40 and ema40_down and ema_crossunder and is_vol_expanding:
                reason = "MEME_PEAK_HUNTER_3M_EMA_SHORT"
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
                    "ma5_current": ema5,
                    "ma5_previous": prev_ema5,
                    "volume_exhausted": False,
                    "bar_id": ind["timestamp"],
                    "vwap": vwap_val,
                    "ema40": ema40,
                }

            # 模式 2: 平倉後強勢趨勢延續開倉（空單順勢補上車 / Re-entry）
            is_bearish_trend = below_vwap and below_ema40 and ema40_down and (ema5 < ema13) and (ema5 <= prev_ema5)
            is_bearish_action = (quote <= ind["open"]) and (quote < ema5) and ((ema5 - quote) <= 2.0 * ind["atr"])
            is_continuation_volume = (vol >= vol_ma * 0.6)

            if is_bearish_trend and is_bearish_action and is_continuation_volume:
                reason = "MEME_PEAK_HUNTER_3M_CONTINUATION_SHORT"
                return {
                    "side": "SHORT",
                    "reason": reason,
                    "entry_label": reason,
                    "entry_pattern": "CONTINUATION",
                    "entry_signal_mode": "CONTINUATION",
                    "structure_confirmation": "MEME_PEAK_HUNTER",
                    "structure_confirmed": True,
                    "is_intrabar": True,
                    "channel_expanding": True,
                    "ma5_current": ema5,
                    "ma5_previous": prev_ema5,
                    "volume_exhausted": False,
                    "bar_id": ind["timestamp"],
                    "vwap": vwap_val,
                    "ema40": ema40,
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
        3m 妖幣專用：真逢頂/真見底判定與 1.8 ATR 防插針移動鎖利。
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
        ema13 = ind["ema13"]
        vwap_val = ind["vwap"]
        atr = ind["atr"] if ind["atr"] > 0 else 0.0001
        entry_p = float(position.get("entry_price", close_p))

        candle_body = abs(open_p - close_p)
        candle_body = max(candle_body, 1e-8)

        # =====================================================================
        # 1. 多單平倉判定
        # =====================================================================
        if side == "LONG":
            # 1.1 妖幣真逢頂偵測：3分鐘內爆天量（量大於 20 均量 1.8 倍）且收長上影線（射擊之星倒貨）
            high_wick = high_p - max(open_p, close_p)
            has_high_wick = high_wick > (candle_body * 1.5)
            is_huge_volume = (volume >= vol_ma * cls.VOL_MULT)
            is_above_ema13 = (close_p > ema13)

            if is_huge_volume and has_high_wick and is_above_ema13:
                details = {
                    "cause": "CLIMAX_TOP",
                    "high_wick": high_wick,
                    "candle_body": candle_body,
                    "volume": volume,
                    "vol_ma": vol_ma,
                }
                return "EXIT_BY_MEME_CLIMAX_TOP", "妖幣天量見頂搶跑 (天量長上影線倒貨)", details

            # 1.2 趨勢徹底潰敗：實體收盤跌破 EMA13 且跌破 VWAP
            if close_p < ema13 and close_p < vwap_val:
                details = {
                    "cause": "TREND_DEAD",
                    "close": close_p,
                    "ema13": ema13,
                    "vwap": vwap_val,
                }
                return "EXIT_BY_MEME_TREND_DEAD", "跌破VWAP與EMA13多頭趨勢潰敗平倉", details

            # 1.3 防插針移動鎖利：使用 1.8 倍 ATR 動態追蹤止損
            peak_price = float(position.get("highest_price") or position.get("peak_price") or entry_p)
            peak_price = max(peak_price, high_p)
            profit_reach_atr = (peak_price - entry_p) / atr
            pullback_atr = (peak_price - close_p) / atr

            # 當利潤曾達到 1.5 ATR 以上，且從最高點回吐超過 1.8 ATR 時觸發鎖利
            if profit_reach_atr >= 1.5 and pullback_atr >= cls.ATR_MULT:
                details = {
                    "cause": "ATR_TRAILING_LOCK",
                    "peak_price": peak_price,
                    "pullback_atr": pullback_atr,
                    "atr": atr,
                }
                return "EXIT_BY_MEME_ATR_TRAILING", f"妖幣1.8 ATR防插針移動鎖利 (回吐 {pullback_atr:.1f} ATR)", details

        # =====================================================================
        # 2. 空單平倉判定
        # =====================================================================
        elif side == "SHORT":
            # 2.1 妖幣真見底偵測：3分鐘內爆天量（量大於 20 均量 1.8 倍）且收長下影線（錘子線接盤）
            low_wick = min(open_p, close_p) - low_p
            has_low_wick = low_wick > (candle_body * 1.5)
            is_huge_volume = (volume >= vol_ma * cls.VOL_MULT)
            is_below_ema13 = (close_p < ema13)

            if is_huge_volume and has_low_wick and is_below_ema13:
                details = {
                    "cause": "CLIMAX_BOTTOM",
                    "low_wick": low_wick,
                    "candle_body": candle_body,
                    "volume": volume,
                    "vol_ma": vol_ma,
                }
                return "EXIT_BY_MEME_CLIMAX_BOTTOM", "妖幣天量見底回補 (天量長下影線接盤)", details

            # 2.2 空方結構被反噬：實體收盤站回 EMA13 且站回 VWAP
            if close_p > ema13 and close_p > vwap_val:
                details = {
                    "cause": "SHORT_TREND_DEAD",
                    "close": close_p,
                    "ema13": ema13,
                    "vwap": vwap_val,
                }
                return "EXIT_BY_MEME_SHORT_TREND_DEAD", "突破VWAP與EMA13空頭趨勢潰敗平倉", details

            # 2.3 防插針移動鎖利：使用 1.8 倍 ATR 動態追蹤止損
            trough_price = float(position.get("lowest_price") or position.get("trough_price") or entry_p)
            trough_price = min(trough_price, low_p)
            profit_reach_atr = (entry_p - trough_price) / atr
            pullback_atr = (close_p - trough_price) / atr

            # 當利潤曾達到 1.5 ATR 以上，且從最低點反彈超過 1.8 ATR 時觸發鎖利
            if profit_reach_atr >= 1.5 and pullback_atr >= cls.ATR_MULT:
                details = {
                    "cause": "ATR_TRAILING_LOCK",
                    "trough_price": trough_price,
                    "pullback_atr": pullback_atr,
                    "atr": atr,
                }
                return "EXIT_BY_MEME_ATR_TRAILING", f"妖幣1.8 ATR防插針移動鎖利 (反彈 {pullback_atr:.1f} ATR)", details

        return None
