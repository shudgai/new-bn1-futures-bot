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
        vol_expand_after = (vol_after >= vol_trough * 1.2) if vol_trough > 0 else False
    else:
        vol_expand_after = False
        vol_trough = vol_curr

    if vol_shrunk_at_bottom or vol_expand_after:
        score += 40
        if vol_shrunk_at_bottom:
            reasons.append(f"谷底量縮({vol_trough:.0f} < 均量{vol_ma:.0f}的85%)")
        if vol_expand_after:
            reasons.append(f"反彈量放大({vol_after:.0f} > 谷底{vol_trough:.0f}的120%)")

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


def detect_ma5_ma25_cross_and_turn(df: pd.DataFrame) -> dict:
    """
    連續轉向策略核心邏輯（優化版：純交叉 + 順勢回調上車）

    ✅ 進場邏輯 1（大反轉）：
        - MA5 從下方穿越 MA25（金叉）→ LONG
        - MA5 從上方穿越 MA25（死叉）→ SHORT
    
    ✅ 進場邏輯 2（順勢回調上車 - 解決錯過大趨勢的問題）：
        - 空頭延續 (MA5 < MA25)：出現小反彈結束，MA5 形成峰頂往下 → SHORT (PEAK_TURN)
        - 多頭延續 (MA5 > MA25)：出現小回調結束，MA5 形成谷底往上 → LONG (TROUGH_TURN)

    ✅ 避開舊邏輯陷阱：
        - 舊邏輯是在 MA5 < MA25 時找「谷底做多」(逆勢摸底)
        - 新邏輯是在 MA5 < MA25 時找「峰頂做空」(順勢做空)
    """
    if df is None or len(df) < 25:
        return {"signal": None, "reason": "Not enough data", "pivot_confirmed": False, "pivot_score": 0}

    if 'ma5' not in df.columns:
        df = df.copy()
        df['ma5'] = df['close'].rolling(window=5).mean()
    if 'ma25' not in df.columns:
        df = df.copy()
        df['ma25'] = df['close'].rolling(window=25).mean()

    # 計算 ADX (14) 如果不存在
    if 'adx' not in df.columns:
        adx_period = 14
        high = df['high']
        low = df['low']
        close = df['close']
        
        tr1 = high - low
        tr2 = (high - close.shift(1)).abs()
        tr3 = (low - close.shift(1)).abs()
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        
        up_move = high.diff()
        down_move = -low.diff()
        plus_dm = pd.Series(np.where((up_move > down_move) & (up_move > 0), up_move, 0.0), index=df.index)
        minus_dm = pd.Series(np.where((down_move > up_move) & (down_move > 0), down_move, 0.0), index=df.index)
        
        tr_smooth = tr.ewm(alpha=1 / adx_period, adjust=False).mean()
        plus_di = 100 * (plus_dm.ewm(alpha=1 / adx_period, adjust=False).mean() / (tr_smooth + 1e-9))
        minus_di = 100 * (minus_dm.ewm(alpha=1 / adx_period, adjust=False).mean() / (tr_smooth + 1e-9))
        dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di + 1e-9)
        df['adx'] = dx.ewm(alpha=1 / adx_period, adjust=False).mean()

    # 計算 RSI (14) 如果不存在
    if 'rsi' not in df.columns:
        delta = df['close'].diff()
        gain = delta.where(delta > 0, 0.0)
        loss = -delta.where(delta < 0, 0.0)
        avg_gain = gain.ewm(alpha=1/14, adjust=False).mean()
        avg_loss = loss.ewm(alpha=1/14, adjust=False).mean()
        rs = avg_gain / (avg_loss + 1e-9)
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

    # 2. 判斷峰谷 (回調結束)
    is_trough = (ma5_curr > ma5_prev) and (ma5_prev < ma5_prev2)
    is_peak = (ma5_curr < ma5_prev) and (ma5_prev > ma5_prev2)

    adx_curr = float(df['adx'].iloc[-1])
    # 垃圾時間過濾：ADX 必須大於等於 12 才允許發布任何進場訊號（原為15，依用戶要求調降以增加靈敏度）
    if adx_curr < 12.0:
        return {
            "signal": None,
            "reason": f"盤整過濾 (ADX = {adx_curr:.1f} < 12)",
            "pivot_confirmed": False,
            "pivot_score": 0
        }

    # 優先級 1：大反轉（金叉/死叉第一時間進場）
    if cross_up:
        return {
            "signal": "LONG",
            "entry_type": "CROSS_UP",
            "reason": f"MA5 金叉 (ADX={adx_curr:.1f}) → 多單",
            "atr": atr,
            "pivot_confirmed": True,
            "pivot_score": 80,
        }
    elif cross_down:
        return {
            "signal": "SHORT",
            "entry_type": "CROSS_DOWN",
            "reason": f"MA5 死叉 (ADX={adx_curr:.1f}) → 空單",
            "atr": atr,
            "pivot_confirmed": True,
            "pivot_score": 80,
        }

    # 優先級 2：強勢動能上車 vs 真實大反轉摸底
    # 空頭趨勢中 (MA5 < MA25)
    if ma5_curr < ma25_curr:
        if is_trough:
            trough_info = _confirm_true_trough(df)
            if trough_info.get("confirmed"):
                reasons_str = ", ".join(trough_info.get("reasons", []))
                return {
                    "signal": "LONG",
                    "entry_type": "TROUGH_TURN",
                    "reason": f"空頭中確認為真谷底 (ADX={adx_curr:.1f}, {reasons_str}) → 提前轉向多單",
                    "atr": atr,
                    "pivot_confirmed": True,
                    "pivot_score": trough_info.get("score", 70),
                }
        # 否則無腦順勢追空（無視假小勾）
        return {
            "signal": "SHORT",
            "entry_type": "PEAK_TURN",  # 借用舊名稱讓 engine 接收
            "reason": f"強勢空頭 (MA5<MA25, ADX={adx_curr:.1f}) → 直接追空",
            "atr": atr,
            "pivot_confirmed": True,
            "pivot_score": 75,
        }
    
    # 多頭趨勢中 (MA5 > MA25)
    if ma5_curr > ma25_curr:
        if is_peak:
            peak_info = _confirm_true_peak(df)
            if peak_info.get("confirmed"):
                reasons_str = ", ".join(peak_info.get("reasons", []))
                return {
                    "signal": "SHORT",
                    "entry_type": "PEAK_TURN",
                    "reason": f"多頭中確認為真峰頂 (ADX={adx_curr:.1f}, {reasons_str}) → 提前轉向空單",
                    "atr": atr,
                    "pivot_confirmed": True,
                    "pivot_score": peak_info.get("score", 70),
                }
        # 否則無腦順勢追多（無視假回調）
        return {
            "signal": "LONG",
            "entry_type": "TROUGH_TURN", # 借用舊名稱讓 engine 接收
            "reason": f"強勢多頭 (MA5>MA25, ADX={adx_curr:.1f}) → 直接追多",
            "atr": atr,
            "pivot_confirmed": True,
            "pivot_score": 75,
        }

    return {"signal": None, "reason": "", "pivot_confirmed": False, "pivot_score": 0}




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

