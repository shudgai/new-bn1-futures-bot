import re

with open('core/indicators.py', 'r') as f:
    content = f.read()

# Find the start and end of detect_ma5_ma25_cross_and_turn
start_idx = content.find("def detect_ma5_ma25_cross_and_turn(df: pd.DataFrame) -> dict:")
# Find the next function definition after it, which is compute_position_trigger
end_idx = content.find("def compute_position_trigger(df: pd.DataFrame", start_idx)

new_logic = """
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
    \"\"\"
    連續轉向策略核心邏輯（升級版：真峰谷確認機制）
    \"\"\"
    if df is None or len(df) < 25:
        return {"signal": None, "reason": "Not enough data", "pivot_confirmed": False, "pivot_score": 0}

    df = df.copy()
    close = df['close']
    high = df['high']
    low = df['low']
    open_p = df['open']
    volume = df['volume'] if 'volume' in df.columns else pd.Series(0, index=df.index)

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

    # 2. 判斷基本峰谷
    is_trough = (ma5_curr > ma5_prev) and (ma5_prev < ma5_prev2)
    is_peak = (ma5_curr < ma5_prev) and (ma5_prev > ma5_prev2)

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

    # 綜合「真反轉」確認分數
    bull_confirm_score = sum([is_bullish_pinbar, is_bullish_div, is_choch_up, is_green])
    bear_confirm_score = sum([is_bearish_pinbar, is_bearish_div, is_choch_down, is_red])

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

    # 優先級 2：真峰谷確認
    # 空頭趨勢中 (MA5 < MA25) 找真谷底
    if ma5_curr < ma25_curr:
        if is_trough and bull_confirm_score >= 2:
            return {
                "signal": "LONG",
                "entry_type": "TROUGH_TURN",
                "reason": f"空頭中 MA5 谷底且滿足真反轉 ({bull_confirm_score}項條件) → 轉向多單",
                "atr": atr,
                "pivot_confirmed": True,
                "pivot_score": 100,
            }
    
    # 多頭趨勢中 (MA5 > MA25) 找真峰頂
    if ma5_curr > ma25_curr:
        if is_peak and bear_confirm_score >= 2:
            return {
                "signal": "SHORT",
                "entry_type": "PEAK_TURN",
                "reason": f"多頭中 MA5 頂峰且滿足真反轉 ({bear_confirm_score}項條件) → 轉向空單",
                "atr": atr,
                "pivot_confirmed": True,
                "pivot_score": 100,
            }

    return {
        "signal": None,
        "reason": "等待轉向",
        "pivot_confirmed": False,
        "pivot_score": 0
    }

"""

new_content = content[:start_idx] + new_logic + content[end_idx:]

with open('core/indicators.py', 'w') as f:
    f.write(new_content)

