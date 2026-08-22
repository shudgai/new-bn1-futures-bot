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


def detect_ma7_ma25_cross_and_turn(df: pd.DataFrame) -> dict:
    """
    連續轉向策略核心邏輯：

    訊號優先序（由高到低）：
    1. 谷底向上轉折（MA7 < MA25 且谷底確認）→ LONG 訊號，同時平倉空單
    2. 頂部向下轉折（MA7 > MA25 且頂部確認）→ SHORT 訊號，同時平倉多單
    3. MA7 向上穿越 MA25（死叉→金叉）→ LONG 補開訊號（谷底未成交的補救）
    4. MA7 向下穿越 MA25（金叉→死叉）→ SHORT 補開訊號（頂部未成交的補救）
    """
    if df is None or len(df) < 25:
        return {"signal": None, "reason": "Not enough data"}

    if 'ma7' not in df.columns:
        df['ma7'] = df['close'].rolling(window=7).mean()
    if 'ma25' not in df.columns:
        df['ma25'] = df['close'].rolling(window=25).mean()

    ma7_curr  = float(df['ma7'].iloc[-1])
    ma7_prev  = float(df['ma7'].iloc[-2])
    ma7_prev2 = float(df['ma7'].iloc[-3])
    ma25_curr = float(df['ma25'].iloc[-1])
    ma25_prev = float(df['ma25'].iloc[-2])
    atr = float(df['atr'].iloc[-1]) if 'atr' in df.columns else float(df['close'].iloc[-1]) * 0.015

    # 連續兩根確認轉折
    is_confirmed_peak   = (ma7_curr < ma7_prev)  and (ma7_prev < ma7_prev2)
    is_confirmed_trough = (ma7_curr > ma7_prev)  and (ma7_prev > ma7_prev2)

    # --- 優先訊號：谷底/頂部轉折 ---
    if ma7_curr < ma25_curr and is_confirmed_trough:
        return {
            "signal": "LONG",
            "entry_type": "TROUGH_TURN",
            "reason": "MA7<MA25 且 MA7 谷底確認向上反轉 → 平空開多",
            "atr": atr,
        }
    if ma7_curr > ma25_curr and is_confirmed_peak:
        return {
            "signal": "SHORT",
            "entry_type": "PEAK_TURN",
            "reason": "MA7>MA25 且 MA7 頂部確認向下反轉 → 平多開空",
            "atr": atr,
        }

    # --- 補開訊號：MA7 穿越 MA25（谷底後未能進場的補救）---
    # 金叉：前根 ma7 <= ma25，本根 ma7 > ma25
    ma7_cross_up   = (ma7_prev <= ma25_prev) and (ma7_curr > ma25_curr)
    # 死叉：前根 ma7 >= ma25，本根 ma7 < ma25
    ma7_cross_down = (ma7_prev >= ma25_prev) and (ma7_curr < ma25_curr)

    if ma7_cross_up:
        return {
            "signal": "LONG",
            "entry_type": "MA_CROSS_UP",
            "reason": "MA7 向上穿越 MA25（金叉）→ 補開多單",
            "atr": atr,
        }
    if ma7_cross_down:
        return {
            "signal": "SHORT",
            "entry_type": "MA_CROSS_DOWN",
            "reason": "MA7 向下穿越 MA25（死叉）→ 補開空單",
            "atr": atr,
        }

    return {"signal": None, "reason": ""}



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
    reasons = []
    strong = False

    # 結構性反轉需要連續2根K棒都同向，避免一根K棒的微小波動就觸發強制平倉
    # is_peak: MA7 本根向下指 (short-term)
    # is_confirmed_peak: 前一根也向下（即 prev > prev2），才算結構確認
    is_confirmed_peak = (ma7_curr < ma7_prev) and (ma7_prev < ma7_prev2)
    is_confirmed_trough = (ma7_curr > ma7_prev) and (ma7_prev > ma7_prev2)

    if side == "LONG":
        if is_peak:
            reasons.append("MA7 向下指 (反向作空訊號)")
            strong = True
    else:
        if is_trough:
            reasons.append("MA7 向上指 (反向作多訊號)")
            strong = True

    # structural_strong 需要連續 2 根確認才成立，避免單根震盪即強制平倉
    structural_confirmed = (is_confirmed_peak if side == "LONG" else is_confirmed_trough)

    return {
        "active": bool(reasons),
        "ma_ok": not strong,
        "reasons": reasons,
        "strong": strong,
        "ma7_reversed": strong,
        "ema_breach_confirmed": structural_confirmed,
        "structure_broken": structural_confirmed,
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

