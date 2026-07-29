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
    """持倉手動平倉參考指標：用 EMA20 判斷「跌破/站上均線」，用近
    lookback_bars 根K棒（不含當前這根）的前低/前高判斷「跌破前低/站上
    前高」。純粹是給使用者按網頁「平倉」按鈕前參考用的視覺提示，不是
    自動出場條件。多單看下檔風險（跌破均線／跌破前低），空單看上檔
    風險（站上均線／站上前高），對稱處理。"""
    min_len = lookback_bars + 2
    if df is None or len(df) < min_len:
        return {"active": False, "reasons": []}

    ema = df["close"].ewm(span=ma_period, adjust=False).mean()
    curr_close = float(df["close"].iloc[-1])
    prev_close = float(df["close"].iloc[-2])
    curr_ema = float(ema.iloc[-1])
    prev_ema = float(ema.iloc[-2])

    reasons = []
    if side == "LONG":
        if curr_close < curr_ema and prev_close >= prev_ema:
            reasons.append("跌破均線")
        prior_low = float(df["low"].iloc[-(lookback_bars + 1):-1].min())
        if curr_close < prior_low:
            reasons.append("跌破前低")
    else:
        if curr_close > curr_ema and prev_close <= prev_ema:
            reasons.append("站上均線")
        prior_high = float(df["high"].iloc[-(lookback_bars + 1):-1].max())
        if curr_close > prior_high:
            reasons.append("站上前高")

    return {"active": bool(reasons), "reasons": reasons}


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
