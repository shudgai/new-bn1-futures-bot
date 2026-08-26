import time

import pandas as pd
import numpy as np

TIMEFRAME_MS = {"1m": 60_000, "5m": 300_000, "15m": 900_000, "1h": 3_600_000}

def get_dynamic_adx_floor(df: pd.DataFrame, direction: int) -> tuple[float, bool]:
    """Return the ADX floor and whether price is in a strong directional trend."""
    from core.config import ADX_MANDATORY_MIN, ADX_STRONG_TREND_MIN

    normal_floor = float(ADX_MANDATORY_MIN)
    strong_floor = float(ADX_STRONG_TREND_MIN)
    if df is None or len(df) < 5 or int(direction or 0) not in (-1, 1):
        return normal_floor, False

    close = pd.to_numeric(df["close"], errors="coerce")
    ma5 = pd.to_numeric(df["ma5"], errors="coerce") if "ma5" in df.columns else close.rolling(5).mean()
    ma15 = (
        pd.to_numeric(df["ma15"], errors="coerce")
        if "ma15" in df.columns else close.rolling(15, min_periods=5).mean()
    )
    if "atr" in df.columns:
        atr = float(pd.to_numeric(df["atr"], errors="coerce").iloc[-1])
    else:
        recent = close.iloc[-5:]
        atr = float((recent.max() - recent.min()) or 0.0)

    values = [close.iloc[-3], close.iloc[-2], close.iloc[-1], ma5.iloc[-2], ma5.iloc[-1], ma15.iloc[-1], atr]
    if any(pd.isna(value) for value in values) or atr <= 0:
        return normal_floor, False

    if direction == 1:
        closes_aligned = close.iloc[-3] < close.iloc[-2] < close.iloc[-1]
        averages_aligned = ma5.iloc[-1] > ma5.iloc[-2] and close.iloc[-1] > ma5.iloc[-1] > ma15.iloc[-1]
        directional_move = float(close.iloc[-1] - close.iloc[-3])
    else:
        closes_aligned = close.iloc[-3] > close.iloc[-2] > close.iloc[-1]
        averages_aligned = ma5.iloc[-1] < ma5.iloc[-2] and close.iloc[-1] < ma5.iloc[-1] < ma15.iloc[-1]
        directional_move = float(close.iloc[-3] - close.iloc[-1])

    strong_trend = bool(closes_aligned and averages_aligned and directional_move >= 0.5 * atr)
    return (strong_floor if strong_trend else normal_floor), strong_trend



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


def detect_ma5_ma25_cross_and_turn(df, allow_live_pivot=False):
    """
    1m MA3 峰谷轉折邏輯：
    做多 (谷底往峰頂)：MA3 線形成谷底並向上確認
    做空 (峰頂到谷底)：MA3 線形成峰頂並向下確認
    """
    if df is None or len(df) < 15:
        return {"signal": None, "reason": "Not enough data", "pivot_confirmed": False, "pivot_score": 0}

    df = df.copy()
    if 'ma3' not in df.columns:
        df['ma3'] = df['close'].rolling(window=3).mean()

    ma3_series = df['ma3'].dropna()
    if len(ma3_series) < 5:
        return {"signal": None, "reason": "Not enough data", "pivot_confirmed": False, "pivot_score": 0}

    ma3_curr  = float(ma3_series.iloc[-1])
    ma3_prev  = float(ma3_series.iloc[-2])
    ma3_prev2 = float(ma3_series.iloc[-3])
    previous_slope = ma3_prev - ma3_prev2
    current_slope = ma3_curr - ma3_prev

    atr = float(df['atr'].iloc[-1]) if 'atr' in df.columns else float(df['close'].iloc[-1]) * 0.015

    # 只有 MA3 形成 V/倒V 才換向；中間趨勢段維持原持倉，不賣出、不追單。
    # 仍只使用已收盤的 3m K，避免未完成 K 線反覆變形造成重複訊號。
    if previous_slope < 0 and current_slope > 0:
        return {
            "signal": "LONG", "entry_type": "TROUGH_TURN",
            "reason": "3m MA3 V字谷底 → 立即轉向開多",
            "atr": atr, "pivot_confirmed": True,
            "pivot_score": 100,
            "fast_pivot": True,
            "live_pivot": allow_live_pivot,
            "ma_alignment": "ABOVE"
        }
    elif previous_slope > 0 and current_slope < 0:
        return {
            "signal": "SHORT", "entry_type": "PEAK_TURN",
            "reason": "3m MA3 倒V字峰頂 → 立即轉向開空",
            "atr": atr, "pivot_confirmed": True,
            "pivot_score": 100,
            "fast_pivot": True,
            "live_pivot": allow_live_pivot,
            "ma_alignment": "BELOW"
        }

    return {
        "signal": None,
        "reason": "3m MA3 尚未形成 V/倒V，持續等待峰頂或谷底",
        "pivot_confirmed": False,
        "pivot_score": 0,
        "ma_alignment": "MIXED",
    }

def compute_position_trigger(df: pd.DataFrame, side: str, ma_period: int = 20, lookback_bars: int = 20) -> dict:
    """持倉平倉訊號（MA5 反轉版：與進場邏輯完全對稱）
    
    多單持倉 → MA5 形成嚴格的峰頂向下滑落 (右側 >= 0.05 ATR) → 出場
    空單持倉 → MA5 形成嚴格的谷底向上翹起 (右側 >= 0.05 ATR) → 出場
    """
    empty = {
        "active": False, "ma_ok": True, "reasons": [], "strong": False,
        "ma5_reversed": False, "ema_breach_confirmed": False,
        "structure_broken": False, "atr": None,
    }
    if df is None or len(df) < 12:
        return empty

    df = df.copy()
    if 'ma5' not in df.columns:
        df['ma5'] = df['close'].rolling(window=5).mean()

    ma5_series = df['ma5'].dropna()
    if len(ma5_series) < 5:
        return empty

    atr_val = float(df['atr'].iloc[-1]) if 'atr' in df.columns and not pd.isna(df['atr'].iloc[-1]) else float(df['close'].iloc[-1]) * 0.015
    
    ma5_curr = float(ma5_series.iloc[-1])
    ma5_prev = float(ma5_series.iloc[-2])
    
    recent_ma5 = ma5_series.iloc[-12:].values if len(ma5_series) >= 12 else ma5_series.values
    history = recent_ma5[:-1]
    recent_max = max(history)
    recent_min = min(history)
    
    idx_max = len(history) - 1 - history[::-1].tolist().index(recent_max)
    idx_min = len(history) - 1 - history[::-1].tolist().index(recent_min)
    
    climb_before_peak = recent_max - (min(history[:idx_max+1]) if idx_max >= 0 else recent_max)
    drop_before_trough = (max(history[:idx_min+1]) if idx_min >= 0 else recent_min) - recent_min
    
    drop_from_peak = recent_max - ma5_curr
    rise_from_trough = ma5_curr - recent_min

    # 嚴格谷底 (與進場相同)：右側往上翹起 >= 0.05 ATR，左側跌幅 >= 0.05 ATR
    is_trough_forming = (ma5_curr > ma5_prev) and (rise_from_trough >= atr_val * 0.05) and (drop_before_trough >= atr_val * 0.05)
    
    # 嚴格峰頂 (與進場相同)：右側向下滑落 >= 0.05 ATR，左側漲幅 >= 0.05 ATR
    is_peak_forming = (ma5_curr < ma5_prev) and (drop_from_peak >= atr_val * 0.05) and (climb_before_peak >= atr_val * 0.05)

    reasons = []
    strong = False

    if side == "LONG":
        if is_peak_forming:
            strong = True
            reasons.append("MA5 嚴格峰頂(倒V型) -> 出場多單")
    else:
        if is_trough_forming:
            strong = True
            reasons.append("MA5 嚴格谷底(V型) -> 出場空單")

    return {
        "active": bool(reasons),
        "ma_ok": not strong,
        "reasons": reasons,
        "strong": strong,
        "ma5_reversed": strong,
        "is_panic_reversal": False,
        "ema_breach_confirmed": strong,
        "structure_broken": strong,
        "pre_peak_exit": False,
        "pre_trough_exit": False,
        "adx": float(df['adx'].iloc[-1]) if 'adx' in df.columns else 0.0,
        "atr": atr_val,
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

