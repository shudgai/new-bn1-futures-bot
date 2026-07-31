import time

import pandas as pd
import numpy as np

TIMEFRAME_MS = {"1m": 60_000, "5m": 300_000, "15m": 900_000, "1h": 3_600_000}


def drop_unclosed_candle(df: pd.DataFrame, timeframe: str) -> pd.DataFrame:
    """丟棄還沒收盤的最後一根 K 棒。

    交易所回傳的最後一筆是「目前正在跑」的那根，SuperTrend 方向/KC 突破/
    RSI/ATR 算在它上面會隨行情跳動反覆變化，容易在真正收盤前就誤觸發
    訊號、收盤後訊號又消失——這是造成假突破的常見原因之一。
    """
    timeframe_ms = TIMEFRAME_MS.get(timeframe)
    if not timeframe_ms or df.empty:
        return df
    now_ms = time.time() * 1000
    if now_ms < float(df.iloc[-1]["timestamp"]) + timeframe_ms:
        return df.iloc[:-1].reset_index(drop=True)
    return df


def compute_position_trigger(df: pd.DataFrame, side: str, ma_period: int = 20, lookback_bars: int = 20) -> dict:
    """持倉手動與自動平倉參考指標：用 EMA20 與 MA7 拐頭判斷。
    - 多單 (LONG)：MA7 轉彎向下 (ma7_prev > ma7_prev2 且 ma7_curr < ma7_prev) 觸發拐頭平倉警告。
    - 空單 (SHORT)：MA7 轉彎向上 (ma7_prev < ma7_prev2 且 ma7_curr > ma7_prev) 觸發拐頭平倉警告。
    """
    min_len = max(lookback_bars + 1, 10)
    if df is None or len(df) < min_len:
        return {"active": False, "ma_ok": True, "reasons": [], "strong": False, "ma7_reversed": False}

    ema = df["close"].ewm(span=ma_period, adjust=False).mean()
    ma7 = df["close"].rolling(window=7).mean()

    curr_close = float(df["close"].iloc[-1])
    curr_ema = float(ema.iloc[-1])

    ma7_curr = float(ma7.iloc[-1])
    ma7_prev = float(ma7.iloc[-2])
    ma7_prev2 = float(ma7.iloc[-3])

    reasons = []
    prior_break = False
    ma7_reversed = False

    if side == "LONG":
        # MA7 峰頂轉彎向下
        if ma7_prev > ma7_prev2 and ma7_curr < ma7_prev:
            ma7_reversed = True
            reasons.append("MA7轉彎向下")

        ma_ok = curr_close >= curr_ema and not ma7_reversed
        if curr_close < curr_ema:
            reasons.append("跌破均線")
        prior_low = float(df["low"].iloc[-(lookback_bars + 1):-1].min())
        if curr_close < prior_low:
            reasons.append("跌破前低")
            prior_break = True
    else:
        # MA7 谷底轉彎向上
        if ma7_prev < ma7_prev2 and ma7_curr > ma7_prev:
            ma7_reversed = True
            reasons.append("MA7轉彎向上")

        ma_ok = curr_close <= curr_ema and not ma7_reversed
        if curr_close > curr_ema:
            reasons.append("站上均線")
        prior_high = float(df["high"].iloc[-(lookback_bars + 1):-1].max())
        if curr_close > prior_high:
            reasons.append("站上前高")
            prior_break = True

    strong = ma7_reversed or ((curr_close < curr_ema if side == "LONG" else curr_close > curr_ema) and prior_break)

    return {"active": bool(reasons), "ma_ok": ma_ok, "reasons": reasons, "strong": strong, "ma7_reversed": ma7_reversed}



def bars_since_supertrend_flip(direction_series: pd.Series) -> int:
    """
    計算 SuperTrend 方向自上次轉向（Flip）以來經過的 K 棒數量 (Bars)。
    若剛轉向，回傳 0；1 根前轉向，回傳 1；依此類推。
    """
    if direction_series is None or len(direction_series) < 2:
        return 999

    curr_dir = direction_series.iloc[-1]
    bars = 0

    for i in range(len(direction_series) - 1, 0, -1):
        if direction_series.iloc[i] == curr_dir:
            if direction_series.iloc[i - 1] != curr_dir:
                return bars
            bars += 1
        else:
            break

    return bars
