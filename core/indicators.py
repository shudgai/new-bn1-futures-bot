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
    """持倉手動與自動平倉參考指標：用 EMA20（含ATR緩衝帶、需連續兩根收線
    確認）與 MA7 拐頭判斷。
    - 多單 (LONG)：MA7 峰頂後連續兩根已收盤 K 棒向下才觸發拐頭平倉警告。
    - 空單 (SHORT)：MA7 谷底後連續兩根已收盤 K 棒向上才觸發拐頭平倉警告。
    MA7 不再把峰谷後第一根反向 K 棒直接視為強制出場。
    EMA20 判斷跟15m趨勢止損用同一套「ATR緩衝帶 + 連續兩根收線確認」，
    避免窄幅盤整時單一根雜訊貼著均線來回就誤判為反轉（實測 RLC/USDT
    07/31 20:30~21:08 這種原地雜訊晃動，單根判斷會頻繁觸發但根本沒有
    真正反轉）。
    """
    min_len = max(lookback_bars + 1, 15)
    if df is None or len(df) < min_len:
        return {
            "active": False, "ma_ok": True, "reasons": [], "strong": False,
            "ma7_reversed": False, "ema_breach_confirmed": False,
            "structure_broken": False, "atr": None,
        }

    ema = df["close"].ewm(span=ma_period, adjust=False).mean()
    ma7 = df["close"].rolling(window=7).mean()

    # ATR 緩衝帶，跟 15m 趨勢止損同一套公式
    high_low = df["high"] - df["low"]
    high_cp = (df["high"] - df["close"].shift()).abs()
    low_cp = (df["low"] - df["close"].shift()).abs()
    atr = pd.concat([high_low, high_cp, low_cp], axis=1).max(axis=1).rolling(window=14).mean()

    curr_close = float(df["close"].iloc[-1])
    curr_ema = float(ema.iloc[-1])
    prev_close = float(df["close"].iloc[-2])
    prev_ema = float(ema.iloc[-2])
    curr_atr = float(atr.iloc[-1]) if not pd.isna(atr.iloc[-1]) else curr_close * 0.015
    prev_atr = float(atr.iloc[-2]) if not pd.isna(atr.iloc[-2]) else prev_close * 0.015
    curr_buffer = max(curr_close * 0.003, 0.5 * curr_atr)
    prev_buffer = max(prev_close * 0.003, 0.5 * prev_atr)

    ma7_curr = float(ma7.iloc[-1])
    ma7_prev = float(ma7.iloc[-2])
    ma7_prev2 = float(ma7.iloc[-3])
    ma7_prev3 = float(ma7.iloc[-4])

    reasons = []
    prior_break = False
    ma7_reversed = False

    if side == "LONG":
        # MA7 峰頂後連續兩根已收盤 K 棒向下，排除第一根假轉彎。
        if ma7_prev2 > ma7_prev3 and ma7_prev < ma7_prev2 and ma7_curr < ma7_prev:
            ma7_reversed = True
            reasons.append("MA7連續兩根轉彎向下")

        ema_breach_confirmed = (
            curr_close < (curr_ema - curr_buffer) and prev_close < (prev_ema - prev_buffer)
        )
        ma_ok = curr_close >= curr_ema and not ma7_reversed
        if curr_close < curr_ema:
            reasons.append("跌破均線")
        if ema_breach_confirmed:
            reasons.append("連續兩根收線跌破EMA20緩衝帶")
        prior_low = float(df["low"].iloc[-(lookback_bars + 1):-1].min())
        if curr_close < prior_low:
            reasons.append("跌破前低")
            prior_break = True
    else:
        # MA7 谷底後連續兩根已收盤 K 棒向上，排除第一根假轉彎。
        if ma7_prev2 < ma7_prev3 and ma7_prev > ma7_prev2 and ma7_curr > ma7_prev:
            ma7_reversed = True
            reasons.append("MA7連續兩根轉彎向上")

        ema_breach_confirmed = (
            curr_close > (curr_ema + curr_buffer) and prev_close > (prev_ema + prev_buffer)
        )
        ma_ok = curr_close <= curr_ema and not ma7_reversed
        if curr_close > curr_ema:
            reasons.append("站上均線")
        if ema_breach_confirmed:
            reasons.append("連續兩根收線站上EMA20緩衝帶")
        prior_high = float(df["high"].iloc[-(lookback_bars + 1):-1].max())
        if curr_close > prior_high:
            reasons.append("站上前高")
            prior_break = True

    strong = ma7_reversed or (ema_breach_confirmed and prior_break)

    return {
        "active": bool(reasons),
        "ma_ok": ma_ok,
        "reasons": reasons,
        "strong": strong,
        "ma7_reversed": ma7_reversed,
        "ema_breach_confirmed": ema_breach_confirmed,
        "structure_broken": prior_break,
        "atr": curr_atr,
    }



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
