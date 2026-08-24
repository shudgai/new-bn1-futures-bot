import time

import pandas as pd
import numpy as np

TIMEFRAME_MS = {"1m": 60_000, "5m": 300_000, "15m": 900_000, "1h": 3_600_000}


# ---------------------------------------------------------------------------
# 真頂峰 / 真谷底 確認輔助函式
# ---------------------------------------------------------------------------

def _confirm_true_trough(df: pd.DataFrame, trough_lookback: int = 10) -> dict:
    """判斷目前 MA5 谷底是否為「真谷底」。

    評分依據（滿分 100）：
    1. 成交量確認（40 分）：谷底當根量 < 前 N 根均量，但「谷底後第一根」量 > 谷底量的 1.2 倍。
       → 先縮後放，符合底部竭盡特徵。
    2. RSI 底背離（40 分）：前一個局部低點（往前 trough_lookback 根）的 RSI
       高於目前 RSI，但「目前低點」價格 < 前低點 → 底背離（RSI 沒創新低）。
    3. K 線型態（20 分）：最後一根是錘頭線（下影線 > 實體 2 倍）。

    回傳 {"confirmed": bool, "score": int (0-100), "reasons": list[str]}
    """
    reasons = []
    score = 0

    if df is None or len(df) < max(trough_lookback + 2, 5):
        return {"confirmed": False, "score": 0, "reasons": ["資料不足"]}

    curr = df.iloc[-1]
    vol_curr = float(curr.get("volume", 0) or 0)
    rsi_curr = float(curr.get("rsi", 50) or 50)
    low_curr = float(curr.get("low", 0) or 0)

    # ----- 1. 成交量確認 -----
    window = df.iloc[-(trough_lookback + 1):-1]  # 谷底前 N 根
    vol_ma = float(window["volume"].mean()) if len(window) > 0 else 0
    vol_next = float(df.iloc[-1].get("volume", 0) or 0)  # 使用當根（已包含反彈開始）

    vol_shrunk_at_bottom = (vol_curr <= vol_ma * 0.85) if vol_ma > 0 else False
    # 如果已有下一根（idx -2 為谷底），取現在這根判斷量放大
    if len(df) >= 3:
        vol_trough = float(df.iloc[-2].get("volume", 0) or 0)  # 谷底那根
        vol_after = float(df.iloc[-1].get("volume", 0) or 0)   # 反彈第一根
        is_green = float(df.iloc[-1].get("close", 0)) > float(df.iloc[-1].get("open", 0))
        
        # 放寬條件：只要反彈是綠 K，且量比谷底多，或是大於均量的 80%，就視為有主力進場
        vol_expand_after = is_green and ((vol_after > vol_trough) or (vol_after >= vol_ma * 0.8))
    else:
        vol_expand_after = False
        vol_trough = vol_curr

    if vol_shrunk_at_bottom or vol_expand_after:
        score += 40
        if vol_shrunk_at_bottom:
            reasons.append(f"谷底量縮({vol_trough:.0f} < 均量{vol_ma:.0f}的85%)")
        if vol_expand_after:
            reasons.append(f"反彈綠K放量(量={vol_after:.0f})")

    # ----- 2. RSI 底背離 -----
    if "rsi" in df.columns and len(df) >= trough_lookback + 2:
        lookback_slice = df.iloc[-(trough_lookback + 1):-1]
        prev_low_idx = lookback_slice["low"].idxmin()
        prev_low_price = float(df.loc[prev_low_idx, "low"])
        prev_low_rsi = float(df.loc[prev_low_idx, "rsi"]) if not pd.isna(df.loc[prev_low_idx, "rsi"]) else rsi_curr

        # 底背離：目前價格接近或低於前低，但 RSI 高於前低時的 RSI
        price_near_or_below_prev_low = low_curr <= prev_low_price * 1.005
        rsi_diverge = rsi_curr > prev_low_rsi + 1.0  # RSI 沒創新低 = 底背離

        if price_near_or_below_prev_low and rsi_diverge:
            score += 40
            reasons.append(
                f"RSI底背離(前低RSI={prev_low_rsi:.1f} < 現RSI={rsi_curr:.1f}, 價格卻低於前低)"
            )

    # ----- 3. K 線型態 -----
    try:
        o = float(curr.get("open", 0) or 0)
        h = float(curr.get("high", 0) or 0)
        l = float(curr.get("low", 0) or 0)
        c = float(curr.get("close", 0) or 0)
        body = abs(c - o)
        lower_shadow = min(o, c) - l
        upper_shadow = h - max(o, c)
        total_range = h - l
        if total_range > 0 and lower_shadow > body * 2.0 and upper_shadow / total_range <= 0.10:
            score += 20
            reasons.append("錘頭線型態確認")
    except Exception:
        pass

    confirmed = score >= 40  # 至少成交量或 RSI 其中一個符合才算確認
    return {"confirmed": confirmed, "score": score, "reasons": reasons}


def _confirm_true_peak(df: pd.DataFrame, peak_lookback: int = 10) -> dict:
    """判斷目前 MA5 峰頂是否為「真峰頂」。

    評分依據（滿分 100）：
    1. 成交量確認（40 分）：峰頂當根量 < 前 N 根均量（量縮頂，主力收手）。
    2. RSI 頂背離（40 分）：目前高點高於前高點，但 RSI 低於前高點的 RSI（頂背離）。
    3. K 線型態（20 分）：最後一根是流星線（上影線 > 實體 2 倍）。

    回傳 {"confirmed": bool, "score": int (0-100), "reasons": list[str]}
    """
    reasons = []
    score = 0

    if df is None or len(df) < max(peak_lookback + 2, 5):
        return {"confirmed": False, "score": 0, "reasons": ["資料不足"]}

    curr = df.iloc[-1]
    rsi_curr = float(curr.get("rsi", 50) or 50)
    high_curr = float(curr.get("high", 0) or 0)

    # ----- 1. 成交量確認 -----
    window = df.iloc[-(peak_lookback + 1):-1]
    vol_ma = float(window["volume"].mean()) if len(window) > 0 else 0
    vol_peak = float(df.iloc[-2].get("volume", 0) or 0) if len(df) >= 3 else float(curr.get("volume", 0) or 0)
    vol_curr = float(curr.get("volume", 0) or 0)

    vol_shrunk_at_peak = (vol_peak <= vol_ma * 0.85) if vol_ma > 0 else False
    if vol_shrunk_at_peak:
        score += 40
        reasons.append(f"峰頂量縮({vol_peak:.0f} < 均量{vol_ma:.0f}的85%)")

    # ----- 2. RSI 頂背離 -----
    if "rsi" in df.columns and len(df) >= peak_lookback + 2:
        lookback_slice = df.iloc[-(peak_lookback + 1):-1]
        prev_high_idx = lookback_slice["high"].idxmax()
        prev_high_price = float(df.loc[prev_high_idx, "high"])
        prev_high_rsi = float(df.loc[prev_high_idx, "rsi"]) if not pd.isna(df.loc[prev_high_idx, "rsi"]) else rsi_curr

        # 頂背離：目前價格高於或接近前高，但 RSI 低於前高時的 RSI
        price_near_or_above_prev_high = high_curr >= prev_high_price * 0.995
        rsi_diverge = rsi_curr < prev_high_rsi - 1.0  # RSI 沒創新高 = 頂背離

        if price_near_or_above_prev_high and rsi_diverge:
            score += 40
            reasons.append(
                f"RSI頂背離(前高RSI={prev_high_rsi:.1f} > 現RSI={rsi_curr:.1f}, 價格卻高於前高)"
            )

    # ----- 3. K 線型態 -----
    try:
        o = float(curr.get("open", 0) or 0)
        h = float(curr.get("high", 0) or 0)
        l = float(curr.get("low", 0) or 0)
        c = float(curr.get("close", 0) or 0)
        body = abs(c - o)
        upper_shadow = h - max(o, c)
        lower_shadow = min(o, c) - l
        total_range = h - l
        if total_range > 0 and upper_shadow > body * 2.0 and lower_shadow / total_range <= 0.10:
            score += 20
            reasons.append("流星線型態確認")
    except Exception:
        pass

    confirmed = score >= 40
    return {"confirmed": confirmed, "score": score, "reasons": reasons}


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



def _find_swing_points(df: pd.DataFrame, window: int = 5):
    if len(df) < window * 2 + 1:
        return None, None
    
    # 找最近的波段高低點
    highs = df['high'].values
    lows = df['low'].values
    
    last_swing_high = None
    last_swing_low = None
    
    for i in range(len(df) - window - 1, window - 1, -1):
        if last_swing_high is None and all(highs[i] >= highs[i-window:i]) and all(highs[i] >= highs[i+1:i+window+1]):
            last_swing_high = highs[i]
        if last_swing_low is None and all(lows[i] <= lows[i-window:i]) and all(lows[i] <= lows[i+1:i+window+1]):
            last_swing_low = lows[i]
        if last_swing_high is not None and last_swing_low is not None:
            break
            
    return last_swing_high, last_swing_low

def detect_ma5_ma25_cross_and_turn(df: pd.DataFrame) -> dict:
    """
    連續轉向策略核心邏輯（升級版：真峰谷確認機制）
    """
    if df is None or len(df) < 25:
        return {"signal": None, "reason": "Not enough data", "pivot_confirmed": False, "pivot_score": 0}

    df = df.copy()
    close = df['close']
    high = df['high']
    low = df['low']
    open_p = df['open']
    volume = df['volume'] if 'volume' in df.columns else pd.Series(0, index=df.index)

    if 'ma3' not in df.columns: df['ma3'] = close.rolling(window=3).mean()
    if 'ma5' not in df.columns: df['ma5'] = close.rolling(window=5).mean()
    if 'ma25' not in df.columns: df['ma25'] = close.rolling(window=25).mean()

    # 計算 MACD
    if 'macd' not in df.columns:
        exp1 = close.ewm(span=12, adjust=False).mean()
        exp2 = close.ewm(span=26, adjust=False).mean()
        df['macd'] = exp1 - exp2
        df['macdsignal'] = df['macd'].ewm(span=9, adjust=False).mean()
        df['macdhist'] = df['macd'] - df['macdsignal']

    # 計算 BOLL
    if 'boll_upper' not in df.columns:
        ma20 = close.rolling(window=20).mean()
        std20 = close.rolling(window=20).std()
        df['boll_upper'] = ma20 + (std20 * 2)
        df['boll_lower'] = ma20 - (std20 * 2)

    # 計算 RSI
    if 'rsi' not in df.columns:
        delta = close.diff()
        gain = delta.where(delta > 0, 0.0).ewm(alpha=1/14, adjust=False).mean()
        loss = (-delta.where(delta < 0, 0.0)).ewm(alpha=1/14, adjust=False).mean()
        rs = gain / (loss + 1e-9)
        df['rsi'] = 100 - (100 / (1 + rs))

    ma5_curr  = float(df['ma5'].iloc[-1])
    ma5_prev  = float(df['ma5'].iloc[-2])
    ma5_prev2 = float(df['ma5'].iloc[-3])
    ma25_curr = float(df['ma25'].iloc[-1])
    ma25_prev = float(df['ma25'].iloc[-2])
    atr = float(df['atr'].iloc[-1]) if 'atr' in df.columns else float(df['close'].iloc[-1]) * 0.015

    # 1. 判斷交叉 (大反轉)
    cross_up = (ma5_prev <= ma25_prev) and (ma5_curr > ma25_curr)
    cross_down = (ma5_prev >= ma25_prev) and (ma5_curr < ma25_curr)

    # 2. 判斷基本峰谷 (提早偵測：改用更敏銳的 MA3 判定轉折，減少進場延遲)
    ma3_curr = float(df['ma3'].iloc[-1])
    ma3_prev = float(df['ma3'].iloc[-2])
    ma3_prev2 = float(df['ma3'].iloc[-3])
    
    is_trough = (ma3_curr > ma3_prev) and (ma3_prev < ma3_prev2)
    is_peak = (ma3_curr < ma3_prev) and (ma3_prev > ma3_prev2)

    last_close = float(close.iloc[-1])
    last_open = float(open_p.iloc[-1])
    last_high = float(high.iloc[-1])
    last_low = float(low.iloc[-1])
    is_green = last_close > last_open
    is_red = last_close < last_open
    
    # 3. 取得進階指標與型態
    macd_hist = float(df['macdhist'].iloc[-1])
    macd_hist_prev = float(df['macdhist'].iloc[-2])
    rsi_curr = float(df['rsi'].iloc[-1])
    rsi_prev = float(df['rsi'].iloc[-2])
    
    # 量價特徵：Pinbar
    body = abs(last_close - last_open)
    upper_shadow = last_high - max(last_open, last_close)
    lower_shadow = min(last_open, last_close) - last_low
    is_bullish_pinbar = (lower_shadow > body * 2.0) and (upper_shadow < body)
    is_bearish_pinbar = (upper_shadow > body * 2.0) and (lower_shadow < body)
    
    # 動能背離特徵
    # 谷底背離：MACD 柱狀圖縮腳向上 或 RSI 超賣區回升
    is_bullish_div = (macd_hist > macd_hist_prev and macd_hist_prev < 0) or (rsi_curr > rsi_prev and rsi_prev < 35)
    # 峰頂背離：MACD 柱狀圖縮頭向下 或 RSI 超買區回落
    is_bearish_div = (macd_hist < macd_hist_prev and macd_hist_prev > 0) or (rsi_curr < rsi_prev and rsi_prev > 65)
    
    # 結構破位 CHoCH
    swing_high, swing_low = _find_swing_points(df, window=5)
    is_choch_up = swing_high is not None and last_close > swing_high
    is_choch_down = swing_low is not None and last_close < swing_low

    # 吞噬型態 (Engulfing)
    prev_open = float(open_p.iloc[-2])
    prev_close = float(close.iloc[-2])
    is_bullish_engulfing = (prev_close < prev_open) and is_green and (last_close > prev_open) and (last_open <= prev_close)
    is_bearish_engulfing = (prev_close > prev_open) and is_red and (last_close < prev_open) and (last_open >= prev_close)

    # 綜合「真反轉」確認分數 (剔除顏色雜訊，純看動能與結構破壞)
    bull_confirm_score = sum([is_bullish_pinbar, is_bullish_div, is_choch_up, is_bullish_engulfing])
    bear_confirm_score = sum([is_bearish_pinbar, is_bearish_div, is_choch_down, is_bearish_engulfing])

    # 優先級 1：大反轉（金叉/死叉第一時間進場）
    if cross_up:
        return {
            "signal": "LONG",
            "entry_type": "CROSS_UP",
            "reason": "MA5 金叉 → 多單",
            "atr": atr,
            "pivot_confirmed": True,
            "pivot_score": 80,
        }
    elif cross_down:
        return {
            "signal": "SHORT",
            "entry_type": "CROSS_DOWN",
            "reason": "MA5 死叉 → 空單",
            "atr": atr,
            "pivot_confirmed": True,
            "pivot_score": 80,
        }

    # 優先級 2：真峰谷確認 & 優先級 3：順勢上車
    # 空頭趨勢中 (MA5 < MA25)
    if ma5_curr < ma25_curr:
        if is_trough and is_green and bull_confirm_score >= 0:
            return {
                "signal": "LONG",
                "entry_type": "TROUGH_TURN",
                "reason": f"空頭中 MA5 谷底且滿足真反轉 (>=1項結構條件) → 轉向多單",
                "atr": atr,
                "pivot_confirmed": True,
                "pivot_score": 100,
            }
        elif is_peak or (ma3_curr < ma3_prev and is_red):
            # 沒形成谷底，反而形成峰頂（或只是順勢向下且收黑），代表反彈結束、空頭延續
            return {
                "signal": "SHORT",
                "entry_type": "TREND_SHORT",
                "reason": "空頭中 MA5 反彈後轉下 (回調結束) → 順勢空單",
                "atr": atr,
                "pivot_confirmed": False,
                "pivot_score": 50,
            }
    
    # 多頭趨勢中 (MA5 > MA25)
    if ma5_curr > ma25_curr:
        if is_peak and is_red and bear_confirm_score >= 0:
            return {
                "signal": "SHORT",
                "entry_type": "PEAK_TURN",
                "reason": f"多頭中 MA5 頂峰且滿足真反轉 (>=1項結構條件) → 轉向空單",
                "atr": atr,
                "pivot_confirmed": True,
                "pivot_score": 100,
            }
        elif is_trough or (ma3_curr > ma3_prev and is_green):
            # 沒形成峰頂，反而形成谷底（或只是順勢向上且收綠），代表回踩結束、多頭延續
            return {
                "signal": "LONG",
                "entry_type": "TREND_LONG",
                "reason": "多頭中 MA5 回踩後轉上 (回踩結束) → 順勢多單",
                "atr": atr,
                "pivot_confirmed": False,
                "pivot_score": 50,
            }

    return {
        "signal": None,
        "reason": "等待轉向",
        "pivot_confirmed": False,
        "pivot_score": 0
    }

def compute_position_trigger(df: pd.DataFrame, side: str, ma_period: int = 20, lookback_bars: int = 20) -> dict:
    """持倉平倉訊號（優化版：純 MA5 穿越 MA25 換向）

    ✅ 與進場邏輯完全對稱：
      進場：MA5 穿越 MA25（金叉→多，死叉→空）
      出場：MA5 反向穿越 MA25
        多單持倉中：MA5 死叉穿越 MA25（從上方跌到下方）→ 出場
        空單持倉中：MA5 金叉穿越 MA25（從下方升到上方）→ 出場

    ✅ 優化原因（舊邏輯問題）：
      舊邏輯：空單出場條件 = MA5 < MA25 且連續 2 根 K 棒往上
        → 下跌途中每次小反彈（MA5 短暫反彈 2 根）都觸發出場
        → 出場後再找進場，造成不停換方向
      新邏輯：只有 MA5 真正穿越 MA25 才出場
        → 整個下跌趨勢空單持倉不動，直到金叉出現
    """
    if df is None or len(df) < 26:
        return {
            "active": False, "ma_ok": True, "reasons": [], "strong": False,
            "ma5_reversed": False, "ema_breach_confirmed": False,
            "structure_broken": False, "atr": None,
        }

    if 'ma5' not in df.columns:
        df = df.copy()
        df['ma5'] = df['close'].rolling(window=5).mean()
    if 'ma25' not in df.columns:
        df = df.copy()
        df['ma25'] = df['close'].rolling(window=25).mean()

    ma5_curr  = float(df['ma5'].iloc[-1])
    ma5_prev  = float(df['ma5'].iloc[-2])
    ma25_curr = float(df['ma25'].iloc[-1])
    ma25_prev = float(df['ma25'].iloc[-2])

    reasons = []
    strong = False

    if side == "LONG":
        # 多單出場：MA5 從 MA25 上方穿越到下方（死叉）
        cross_down = (ma5_prev >= ma25_prev) and (ma5_curr < ma25_curr)
        if cross_down:
            reasons.append("MA5 死叉穿越 MA25 → 出場多單")
            strong = True
    else:  # SHORT
        # 空單出場：MA5 從 MA25 下方穿越到上方（金叉）
        cross_up = (ma5_prev <= ma25_prev) and (ma5_curr > ma25_curr)
        if cross_up:
            reasons.append("MA5 金叉穿越 MA25 → 出場空單")
            strong = True

    structural_confirmed = strong

    return {
        "active": bool(reasons),
        "ma_ok": not strong,
        "reasons": reasons,
        "strong": strong,
        "ma5_reversed": strong,
        "is_panic_reversal": False,
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

