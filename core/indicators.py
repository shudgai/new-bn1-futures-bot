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
    """持倉平倉訊號 (Stop and Reverse)
    與進場邏輯完全對稱，負責判斷何時平倉並反向開倉：
    - 多單 (LONG)：當 MA7 < MA25 且出現 MA7 倒V型峰頂時平倉。
    - 空單 (SHORT)：當 MA7 > MA25 且出現 MA7 V型谷底時平倉。
    """
    if df is None or len(df) < 25:
        return {
            "active": False, "ma_ok": True, "reasons": [], "strong": False,
            "ma7_reversed": False, "ema_breach_confirmed": False,
            "structure_broken": False, "atr": None,
        }

    # 計算均線
    if 'ma7' not in df.columns:
        df['ma7'] = df['close'].rolling(window=7).mean()
    if 'ma25' not in df.columns:
        df['ma25'] = df['close'].rolling(window=25).mean()

    ma7_curr = float(df['ma7'].iloc[-1])
    ma7_prev = float(df['ma7'].iloc[-2])
    ma7_prev2 = float(df['ma7'].iloc[-3])
    ma25_curr = float(df['ma25'].iloc[-1])

    is_trough = (ma7_curr > ma7_prev)
    is_peak = (ma7_curr < ma7_prev)

    # 判斷 MA25 是否平緩 (盤整過濾)
    atr_val = float(df['atr'].iloc[-1]) if 'atr' in df.columns else float(df['close'].iloc[-1]) * 0.015
    ma25_prev3 = float(df['ma25'].iloc[-4]) if len(df) >= 4 else ma25_curr
    ma25_diff = ma25_curr - ma25_prev3
    is_ma25_flat = abs(ma25_diff) < (atr_val * 0.05)

    reasons = []
    strong = False

    if side == "LONG":
        if is_peak:
            if is_ma25_flat:
                reasons.append("MA7 向下指且盤整中 (提早平倉觀望)")
            else:
                reasons.append("MA7 向下指 (反向作空訊號)")
            strong = True
    else:
        if is_trough:
            if is_ma25_flat:
                reasons.append("MA7 向上指且盤整中 (提早平倉觀望)")
            else:
                reasons.append("MA7 向上指 (反向作多訊號)")
            strong = True

    return {
        "active": bool(reasons),
        "ma_ok": not strong,
        "reasons": reasons,
        "strong": strong,
        "ma7_reversed": strong,
        "ema_breach_confirmed": strong,
        "structure_broken": strong,
        "atr": float(df['atr'].iloc[-1]) if 'atr' in df.columns else float(df['close'].iloc[-1]) * 0.015,
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


def analyze_candle_pattern(candle: pd.Series) -> dict:
    """
    分析單根 K 線的形態特徵 (Price Action)。
    回傳字典包含以下布林值特徵：
    - is_long_bull: 長紅 K 線 (實體 > 全長 60%)
    - is_long_bear: 長黑 K 線 (實體 > 全長 60%)
    - is_doji: 十字線 (實體 < 全長 10%)
    - is_hammer: 錘頭線 (下影線 > 實體 2 倍，且上影線 < 全長 10%)
    - is_shooting_star: 流星線 (上影線 > 實體 2 倍，且下影線 < 全長 10%)
    """
    try:
        o = float(candle['open'])
        h = float(candle['high'])
        l = float(candle['low'])
        c = float(candle['close'])
    except KeyError:
        # 如果缺少 o/h/l/c，回傳全部為 False
        return {
            "is_long_bull": False, "is_long_bear": False,
            "is_doji": False, "is_hammer": False, "is_shooting_star": False,
            "pattern_name": "None",
        }

    total_range = h - l
    if total_range <= 0:
        return {
            "is_long_bull": False, "is_long_bear": False,
            "is_doji": True, "is_hammer": False, "is_shooting_star": False,
            "pattern_name": "Doji",
        }

    body = abs(c - o)
    upper_shadow = h - max(o, c)
    lower_shadow = min(o, c) - l

    body_ratio = body / total_range
    upper_ratio = upper_shadow / total_range
    lower_ratio = lower_shadow / total_range

    is_long_bull = body_ratio >= 0.6 and c > o
    is_long_bear = body_ratio >= 0.6 and c < o
    is_doji = body_ratio <= 0.10

    # 錘頭線：下影線長（大於實體 2 倍），且上影線極短（<10% 全長）
    is_hammer = (lower_shadow > body * 2.0) and (upper_ratio <= 0.10)

    # 流星線：上影線長（大於實體 2 倍），且下影線極短（<10% 全長）
    is_shooting_star = (upper_shadow > body * 2.0) and (lower_ratio <= 0.10)

    pattern_name = "None"
    if is_doji:
        pattern_name = "Doji"
    elif is_hammer:
        pattern_name = "Hammer"
    elif is_shooting_star:
        pattern_name = "Shooting Star"
    elif is_long_bull:
        pattern_name = "Long Bull"
    elif is_long_bear:
        pattern_name = "Long Bear"

    return {
        "is_long_bull": is_long_bull,
        "is_long_bear": is_long_bear,
        "is_doji": is_doji,
        "is_hammer": is_hammer,
        "is_shooting_star": is_shooting_star,
        "pattern_name": pattern_name,
        "body_ratio": body_ratio,
    }

