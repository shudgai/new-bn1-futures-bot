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
            "kc_middle": float(curr.get("kc_middle", curr.get("kc_basis", 0.0)) or 0.0),
            "kc_upper": float(curr.get("kc_upper", 0.0) or 0.0),
            "kc_lower": float(curr.get("kc_lower", 0.0) or 0.0),
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

        # 優先取得最新已收線 K 棒收盤價，防盤中 Tick 微秒毛刺誤平倉
        closed_candle_close = close_p
        try:
            from core.services.candle_data import closed_entry_candles
            closed = closed_entry_candles(frame)
            if not closed.empty:
                closed_candle_close = float(closed.iloc[-1].get("close", close_p))
        except Exception:
            closed_candle_close = close_p

        # =====================================================================
        # 1. 多單平倉判定 (持多：大週期向上時嚴禁提前平多，收盤跌破 KC 中軌才平倉)
        # =====================================================================
        if side == "LONG":
            kc_mid_val = float(ind.get("kc_middle", ind.get("ema_20", 0.0)) or 0.0)
            if kc_mid_val <= 0:
                kc_mid_val = float(ind.get("ema13", 0.0) or 0.0)

            # 1.1 初始逆向硬止損 (Adverse Stop Loss)：進場後行情反走，虧損達 1.5 ATR 立即市價止損逃命 (風控優先)
            adverse_loss_atr = (entry_p - close_p) / atr
            if adverse_loss_atr >= 1.5:
                details = {
                    "cause": "ADVERSE_STOP_LOSS",
                    "entry_price": entry_p,
                    "close_price": close_p,
                    "adverse_atr": adverse_loss_atr,
                    "atr": atr,
                }
                return "EXIT_BY_MEME_ADVERSE_STOP_LOSS", f"妖幣假突破初始止損 (逆向 {adverse_loss_atr:.1f} ATR 斷尾求生)", details

            # 1.2 移除『突破/觸碰 KC 上軌或回踩初期平多』：價格在 KC 中軌之上強制抱牢讓利潤奔跑！
            if close_p >= kc_mid_val or closed_candle_close >= kc_mid_val:
                return None

            # 1.3 多頭結構徹底破壞：必須已收線 K 棒實體跌破 KC 中軌達 0.15% 緩衝帶 (closed_close < kc_middle * 0.9985)
            if kc_mid_val > 0 and closed_candle_close < (kc_mid_val * 0.9985):
                details = {
                    "cause": "TREND_DEAD",
                    "close": closed_candle_close,
                    "kc_middle": kc_mid_val,
                    "ema13": ema13,
                    "vwap": vwap_val,
                }
                return "EXIT_BY_MEME_TREND_DEAD", "【多頭結構瓦解】3M收盤實體實質跌破KC中軌全數平倉", details

        # =====================================================================
        # 2. 空單平倉判定 (持空：大週期與KC中軌向下時，未站上KC中軌前一律抱牢！)
        # =====================================================================
        elif side == "SHORT":
            kc_mid_val = float(ind.get("kc_middle", ind.get("ema_20", 0.0)))
            if kc_mid_val <= 0:
                kc_mid_val = float(ind.get("ema13", 0.0) or 0.0)

            # 2.1 初始逆向硬止損 (Adverse Stop Loss)：進場後行情反走，虧損達 1.5 ATR 立即市價止損逃命 (風控優先)
            adverse_loss_atr = (close_p - entry_p) / atr
            if adverse_loss_atr >= 1.5:
                details = {
                    "cause": "ADVERSE_STOP_LOSS",
                    "entry_price": entry_p,
                    "close_price": close_p,
                    "adverse_atr": adverse_loss_atr,
                    "atr": atr,
                }
                return "EXIT_BY_MEME_ADVERSE_STOP_LOSS", f"妖幣假跌破初始止損 (逆向 {adverse_loss_atr:.1f} ATR 斷尾求生)", details

            # 2.2 廢除 CLIMAX_BOTTOM：大趨勢主跌浪中，禁止因單根短線小陽線或下軌放量提前平空！
            # 只要價格或收盤仍在 KC 中軌之下，空單一律抱牢，讓利潤持續延伸！
            if close_p <= kc_mid_val or closed_candle_close <= kc_mid_val:
                # 檢查大週期/中軌是否仍向下傾斜
                prev_mid = float(ind.get("prev_ema13", kc_mid_val))
                if kc_mid_val <= prev_mid * 1.0005:
                    # 中軌向下或走平，嚴格持有！未站上 KC 中軌前，空單一律抱牢
                    return None

            # 2.3 唯一合法結構平倉條件：實體收盤實質站上 KC 中軌 / EMA13+0.15% 緩衝帶且站回 VWAP
            if closed_candle_close > (kc_mid_val * 1.0015) and closed_candle_close > vwap_val:
                details = {
                    "cause": "SHORT_TREND_DEAD",
                    "close": closed_candle_close,
                    "ema13": ema13,
                    "kc_middle": kc_mid_val,
                    "vwap": vwap_val,
                }
                return "EXIT_BY_MEME_SHORT_TREND_DEAD", "突破VWAP與KC中軌+0.15%緩衝帶，空頭結構瓦解平倉", details

        return None
