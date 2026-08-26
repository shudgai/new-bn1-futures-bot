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
    ma25 = (
        pd.to_numeric(df["ma25"], errors="coerce")
        if "ma25" in df.columns else close.rolling(25, min_periods=5).mean()
    )
    if "atr" in df.columns:
        atr = float(pd.to_numeric(df["atr"], errors="coerce").iloc[-1])
    else:
        recent = close.iloc[-5:]
        atr = float((recent.max() - recent.min()) or 0.0)

    values = [close.iloc[-3], close.iloc[-2], close.iloc[-1], ma5.iloc[-2], ma5.iloc[-1], ma25.iloc[-1], atr]
    if any(pd.isna(value) for value in values) or atr <= 0:
        return normal_floor, False

    if direction == 1:
        closes_aligned = close.iloc[-3] < close.iloc[-2] < close.iloc[-1]
        averages_aligned = ma5.iloc[-1] > ma5.iloc[-2] and close.iloc[-1] > ma5.iloc[-1] > ma25.iloc[-1]
        directional_move = float(close.iloc[-1] - close.iloc[-3])
    else:
        closes_aligned = close.iloc[-3] > close.iloc[-2] > close.iloc[-1]
        averages_aligned = ma5.iloc[-1] < ma5.iloc[-2] and close.iloc[-1] < ma5.iloc[-1] < ma25.iloc[-1]
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


def detect_ma5_ma25_cross_and_turn(df: pd.DataFrame, allow_live_pivot: bool = False) -> dict:
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

    if 'ma3' not in df.columns:
        df = df.copy()
        df['ma3'] = df['close'].rolling(window=3).mean()
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

    ma3_curr  = float(df['ma3'].iloc[-1])
    ma3_prev  = float(df['ma3'].iloc[-2])
    ma3_prev2 = float(df['ma3'].iloc[-3])
    ma3_prev3 = float(df['ma3'].iloc[-4]) if len(df) >= 5 else ma3_prev2  # 第二根確認用

    ma5_curr  = float(df['ma5'].iloc[-1])
    ma5_prev  = float(df['ma5'].iloc[-2])
    ma5_prev2 = float(df['ma5'].iloc[-3])
    ma25_curr = float(df['ma25'].iloc[-1])
    ma25_prev = float(df['ma25'].iloc[-2])
    atr = float(df['atr'].iloc[-1]) if 'atr' in df.columns else float(df['close'].iloc[-1]) * 0.015

    # 1. 判斷交叉 (大趨勢反轉 - 依然看 MA5/MA25)
    cross_up = (ma5_prev <= ma25_prev) and (ma5_curr > ma25_curr)
    cross_down = (ma5_prev >= ma25_prev) and (ma5_curr < ma25_curr)

    # ============================================================
    # 2. 開倉/不開倉判斷規則（依照用戶的號碼圖說明）
    # ============================================================
    #
    # 【可開倉形態 - 看 MA3 紫線形狀】
    #
    # ✅ V 型谷底 → 開多單 (LONG)
    #    條件：
    #      - 左側：紫線必須先有「明顯下坡」（幅度 >= 0.05 ATR），不是水平或微小抖動
    #      - 右側：紫線轉折後「明顯向上翹起」（幅度 >= 0.01 ATR），不能只有1小格或平的
    #      - 觸發：ma3_curr > ma3_prev（當下這根紫線比上一根高）
    #
    # ✅ U 型谷底（梯形底部翹起）→ 開多單 (LONG)
    #    條件：同 V 型谷底，但允許右側緩緩上升，只要右側不是完全水平即可
    #    （梯形底部：橫盤一段後，紫線右側開始翹起）
    #
    # ✅ ∩ 型頂峰 / 梯形頂部下滑 → 開空單 (SHORT)
    #    條件：
    #      - 左側：紫線必須先有「明顯上坡」（幅度 >= 0.05 ATR），不是水平或微小抖動
    #      - 右側：紫線轉折後「明顯向下滑」（幅度 >= 0.01 ATR），不能只有1小格或平的
    #      - 觸發：ma3_curr < ma3_prev（當下這根紫線比上一根低）
    #
    # ❌ 不開倉情形
    #    - 紫線右側只有「1小格」的波動（低於 0.01 ATR）→ 視為雜訊，忽略
    #    - 紫線右側幾乎「水平/平坦」（沒有轉折角度）→ 還未轉向，不開倉
    #    - 左側沒有明顯的爬坡或下坡（幅度低於 0.05 ATR）→ 沒有足夠動能支撐反轉，不開倉
    #    - K線顏色：完全忽略！紅K綠K都可以開倉，只看紫線形狀
    #
    # ============================================================
    recent_ma3 = df['ma3'].iloc[-12:].values if len(df) >= 12 else df['ma3'].values
    ma3_curr = recent_ma3[-1]
    ma3_prev = recent_ma3[-2]
    
    if len(recent_ma3) >= 3:
        history = recent_ma3[:-1]
        recent_max = max(history)
        recent_min = min(history)
        
        # 尋找最高/低點發生的位置（取最靠近現在的）
        idx_max = len(history) - 1 - history[::-1].tolist().index(recent_max)
        idx_min = len(history) - 1 - history[::-1].tolist().index(recent_min)
        
        # 【左半邊幅度】先漲/先跌的距離 (過濾無意義的左側平坦)
        climb_before_peak = recent_max - (min(history[:idx_max+1]) if idx_max >= 0 else recent_max)
        drop_before_trough = (max(history[:idx_min+1]) if idx_min >= 0 else recent_min) - recent_min
        
        # 【右半邊幅度】轉折後下跌/上漲的距離 (過濾「只有1小格或平的」)
        drop_from_peak = recent_max - ma3_curr
        rise_from_trough = ma3_curr - recent_min
        
        # 【巨棒吞噬例外】：若單根K棒實體巨大且方向反轉，直接視為右側條件已達標
        candle_body = abs(float(df['close'].iloc[-1]) - float(df['open'].iloc[-1]))
        is_massive_red = (df['close'].iloc[-1] < df['open'].iloc[-1]) and (candle_body >= atr * 0.8) and (df['close'].iloc[-1] < df['close'].iloc[-2])
        is_massive_green = (df['close'].iloc[-1] > df['open'].iloc[-1]) and (candle_body >= atr * 0.8) and (df['close'].iloc[-1] > df['close'].iloc[-2])
        
        # ✅ 真谷底判定（V型/U型底部翹起）→ 開多單
        # 條件：紫線當前 > 前一根（轉折朝上） AND 右側漲幅夠（不是1小格） AND 左側跌幅夠（有真正下坡）
        is_trough_forming = (ma3_curr > ma3_prev) and (rise_from_trough >= atr * 0.01 or is_massive_green) and (drop_before_trough >= atr * 0.05)
        
        # ✅ 真頂峰判定（∩型/梯形頂部下滑）→ 開空單
        # 條件：紫線當前 < 前一根（轉折朝下） AND 右側跌幅夠（不是1小格或平的） AND 左側漲幅夠（有真正上坡）
        is_peak_forming = (ma3_curr < ma3_prev) and (drop_from_peak >= atr * 0.01 or is_massive_red) and (climb_before_peak >= atr * 0.05)
        
        # 早期預警 (提早平倉用)：只要左側幅度夠大，且現在「剛開始」反轉 (即使右側還沒達標)，就發出預警
        is_trough_forming_early = (ma3_curr > ma3_prev) and (drop_before_trough >= atr * 0.05)
        is_peak_forming_early = (ma3_curr < ma3_prev) and (climb_before_peak >= atr * 0.05)
    else:
        is_trough_forming = (ma3_curr > ma3_prev) and (ma3_prev < ma3_prev2)
        is_peak_forming = (ma3_curr < ma3_prev) and (ma3_prev > ma3_prev2)
        is_trough_forming_early = is_trough_forming
        is_peak_forming_early = is_peak_forming

    is_trough_confirmed = (ma3_prev > ma3_prev2) and (ma3_prev2 <= ma3_prev3)
    is_peak_confirmed = (ma3_prev < ma3_prev2) and (ma3_prev2 >= ma3_prev3)

    adx_curr = float(df['adx'].iloc[-1]) if 'adx' in df.columns and not pd.isna(df['adx'].iloc[-1]) else 0.0
    # 強勢單邊走勢使用 ADX 10；盤整或方向不明仍使用 ADX 15。
    trend_direction = 1 if ma5_curr > ma25_curr else -1 if ma5_curr < ma25_curr else 0
    _adx_min, strong_trend = get_dynamic_adx_floor(df, trend_direction)
    # 為了實現極致的「無腦 V 線秒轉向」，如果允許即時轉向 (allow_live_pivot)，則完全無視 ADX 盤整過濾
    if not allow_live_pivot and adx_curr < _adx_min:
        return {
            "signal": None,
            "reason": f"盤整過濾 (ADX = {adx_curr:.1f} < {_adx_min:.1f}; 模式={'強趨勢' if strong_trend else '盤整'})",
            "pivot_confirmed": False,
            "pivot_score": 0
        }

    # 活 K 線濾網：確保轉向當下的 K 棒顏色正確，並防禦極端反轉K線 (長上下影線)
    last_close = float(df['close'].iloc[-1])
    last_open = float(df['open'].iloc[-1])
    last_high = float(df['high'].iloc[-1])
    last_low = float(df['low'].iloc[-1])
    
    is_green = last_close > last_open
    is_red = last_close < last_open
    
    # 計算影線比例 (防禦圖表上的「長下影線誘空」與「長上影線誘多」陷阱)
    candle_range = last_high - last_low
    lower_wick = min(last_open, last_close) - last_low
    upper_wick = last_high - max(last_open, last_close)
    
    # 如果下影線佔整根K線一半以上 (如槌子線)，嚴格禁止做空！
    is_hammer_trap = (candle_range > 0) and (lower_wick / candle_range > 0.4)
    # 如果上影線佔整根K線一半以上 (如避雷針)，嚴格禁止做多！
    is_shooting_star_trap = (candle_range > 0) and (upper_wick / candle_range > 0.4)

    # Fake-breakout volume filter: the closed confirmation candle needs at least 0.8x prior volume.
    min_confirmation_volume_ratio = 0.65 if allow_live_pivot else 0.8
    if 'volume' in df.columns:
        prior_volume = df['volume'].iloc[-21:-1]
        average_volume = float(prior_volume.mean()) if len(prior_volume) else 0.0
        current_volume = float(df['volume'].iloc[-1])
        volume_ratio = current_volume / average_volume if average_volume > 0 else 0.0
    else:
        volume_ratio = 0.0

    def reject_false_breakout(side: str):
        # 即時與收線訊號都必須通過假突破防護；假突破不開倉、不反手。
        if side == "LONG" and is_shooting_star_trap:
            return {"signal": None, "reason": "假突破過濾：多單確認K帶長上影線", "pivot_confirmed": False, "pivot_score": 0, "volume_ratio": volume_ratio}
        if side == "SHORT" and is_hammer_trap:
            return {"signal": None, "reason": "假突破過濾：空單確認K帶長下影線", "pivot_confirmed": False, "pivot_score": 0, "volume_ratio": volume_ratio}
        # 活動 K 雖然量能仍在累積，但不足門檻仍視為假突破，不開倉。
        if volume_ratio < min_confirmation_volume_ratio:
            return {"signal": None, "reason": f"假突破過濾：確認量能 {volume_ratio:.2f}x < {min_confirmation_volume_ratio:.2f}x", "pivot_confirmed": False, "pivot_score": 0, "volume_ratio": volume_ratio}
        return None

    # 峰谷反轉優先：嚴格形態成立時不等待 MA5 越過 MA25，避免確認太晚追高追低。
    candle_body = abs(last_close - last_open)
    fast_trough_recovery = ma3_curr - ma3_prev
    fast_peak_decline = ma3_prev - ma3_curr
    prior_lows = df['low'].iloc[-9:-1]
    prior_highs = df['high'].iloc[-9:-1]
    near_recent_low = bool(len(prior_lows)) and last_low <= float(prior_lows.min()) + atr * 0.25
    near_recent_high = bool(len(prior_highs)) and last_high >= float(prior_highs.max()) - atr * 0.25
    confirmed_trough_recovery = ma3_curr - ma3_prev2
    confirmed_peak_decline = ma3_prev2 - ma3_curr
    
    # 移除 last_close 限制與影線防護，只要 MA3 勾頭且是同向 K 就直接反手
    # (「角度不夠/微弱彎折」的過濾已經內建在 is_trough_forming 和 is_peak_forming 的滑動視窗邏輯中了)
    live_fast_trough = (
        allow_live_pivot and is_trough_forming
    )
    live_fast_peak = (
        allow_live_pivot and is_peak_forming
    )
    clear_fast_trough = live_fast_trough or (
        is_trough_forming and is_green 
        and fast_trough_recovery >= atr * 0.20
        and candle_body >= atr * 0.45
        and volume_ratio >= min_confirmation_volume_ratio
        and near_recent_low and not is_shooting_star_trap
    )
    clear_fast_peak = live_fast_peak or (
        is_peak_forming and is_red 
        and fast_peak_decline >= atr * 0.20
        and candle_body >= atr * 0.45
        and volume_ratio >= min_confirmation_volume_ratio
        and near_recent_high and not is_hammer_trap
    )
    confirmed_trough = (
        is_trough_confirmed and is_green and last_close > ma3_curr
        and confirmed_trough_recovery >= atr * 0.4 and near_recent_low
    )
    confirmed_peak = (
        is_peak_confirmed and is_red and last_close < ma3_curr
        and confirmed_peak_decline >= atr * 0.4 and near_recent_high
    )

    if clear_fast_trough or confirmed_trough:
        rejected = reject_false_breakout("LONG")
        if rejected:
            rejected.update({"is_peak_early": is_peak_forming_early, "is_trough_early": is_trough_forming_early})
            return rejected
        return {
            "signal": "LONG", "entry_type": "TROUGH_TURN",
            "reason": f"MA3 真谷底向上 (ADX={adx_curr:.1f}) → 立即開多",
            "atr": atr, "pivot_confirmed": True,
            "pivot_score": 95 if clear_fast_trough else 100,
            "fast_pivot": bool(clear_fast_trough),
            "live_pivot": bool(allow_live_pivot),
            "ma_alignment": "ABOVE" if ma3_curr > ma25_curr and ma5_curr > ma25_curr else "BELOW" if ma3_curr < ma25_curr and ma5_curr < ma25_curr else "MIXED",
            "is_peak_early": is_peak_forming_early,
            "is_trough_early": is_trough_forming_early,
        }

    if clear_fast_peak or confirmed_peak:
        rejected = reject_false_breakout("SHORT")
        if rejected:
            rejected.update({"is_peak_early": is_peak_forming_early, "is_trough_early": is_trough_forming_early})
            return rejected
        return {
            "signal": "SHORT", "entry_type": "PEAK_TURN",
            "reason": f"MA3 真頂峰向下 (ADX={adx_curr:.1f}) → 立即開空",
            "atr": atr, "pivot_confirmed": True,
            "pivot_score": 95 if clear_fast_peak else 100,
            "fast_pivot": bool(clear_fast_peak),
            "live_pivot": bool(allow_live_pivot),
            "ma_alignment": "ABOVE" if ma3_curr > ma25_curr and ma5_curr > ma25_curr else "BELOW" if ma3_curr < ma25_curr and ma5_curr < ma25_curr else "MIXED",
            "is_peak_early": is_peak_forming_early,
            "is_trough_early": is_trough_forming_early,
        }

    # 無腦波段方向只看 MA3：峰頂往谷底開空，谷底往峰頂開多。
    # 同方向已有持倉時由 engine 保持原單，不重複加倉。
    both_below_ma25 = ma3_curr < ma25_curr and ma5_curr < ma25_curr
    both_above_ma25 = ma3_curr > ma25_curr and ma5_curr > ma25_curr

    if ma3_curr < ma3_prev:
        rejected = reject_false_breakout("SHORT")
        if rejected:
            rejected.update({"is_peak_early": is_peak_forming_early, "is_trough_early": is_trough_forming_early})
            return rejected
        return {
            "signal": "SHORT",
            "entry_type": "TREND_SHORT",
            "reason": f"MA3 峰頂往谷底下降 (ADX={adx_curr:.1f}) → 無腦開空",
            "atr": atr,
            "pivot_confirmed": False,
            "pivot_score": 85,
            "ma_alignment": "ABOVE" if both_above_ma25 else "BELOW" if both_below_ma25 else "MIXED",
            "is_peak_early": is_peak_forming_early,
            "is_trough_early": is_trough_forming_early,
        }

    if ma3_curr > ma3_prev:
        rejected = reject_false_breakout("LONG")
        if rejected:
            rejected.update({"is_peak_early": is_peak_forming_early, "is_trough_early": is_trough_forming_early})
            return rejected
        return {
            "signal": "LONG",
            "entry_type": "TREND_LONG",
            "reason": f"MA3 谷底往峰頂上升 (ADX={adx_curr:.1f}) → 無腦開多",
            "atr": atr,
            "pivot_confirmed": False,
            "pivot_score": 85,
            "ma_alignment": "ABOVE" if both_above_ma25 else "BELOW" if both_below_ma25 else "MIXED",
            "is_peak_early": is_peak_forming_early,
            "is_trough_early": is_trough_forming_early,
        }

    return {
        "signal": None,
        "reason": "MA3 目前走平，等待往峰頂或谷底移動",
        "pivot_confirmed": False,
        "pivot_score": 0,
        "ma_alignment": "BELOW" if both_below_ma25 else "ABOVE" if both_above_ma25 else "MIXED",
        "is_peak_early": is_peak_forming_early,
        "is_trough_early": is_trough_forming_early,
    }

def compute_position_trigger(df: pd.DataFrame, side: str, ma_period: int = 20, lookback_bars: int = 20) -> dict:
    """持倉平倉訊號（MA3 反轉版：與進場邏輯完全對稱）

    出場條件與 detect_ma5_reversal 進場條件完全鏡射：
      多單持倉 → MA3 出現倒V型峰頂 / 梯形頂 / 圓弧頂 / 倒U形頂 → 出場
      空單持倉 → MA3 出現V型谷底 / 梯形底 / 圓弧底 / U形底 → 出場
    """
    empty = {
        "active": False, "ma_ok": True, "reasons": [], "strong": False,
        "ma5_reversed": False, "ema_breach_confirmed": False,
        "structure_broken": False, "atr": None,
    }
    if df is None or len(df) < 5:
        return empty

    df = df.copy()
    if 'ma3' not in df.columns:
        df['ma3'] = df['close'].rolling(window=3).mean()

    ma3_series = df['ma3'].dropna()
    if len(ma3_series) < 5:
        return empty

    ma3_curr  = float(ma3_series.iloc[-1])
    ma3_prev  = float(ma3_series.iloc[-2])
    ma3_prev2 = float(ma3_series.iloc[-3])
    ma3_prev3 = float(ma3_series.iloc[-4])
    ma3_prev4 = float(ma3_series.iloc[-5])

    atr_val = float(df['atr'].iloc[-1]) if 'atr' in df.columns and not pd.isna(df['atr'].iloc[-1]) else float(df['close'].iloc[-1]) * 0.015
    flat_threshold = atr_val * 0.15

    c1, c2, c3, c4 = df.iloc[-1], df.iloc[-2], df.iloc[-3], df.iloc[-4]
    greens = sum(1 for c in [c1, c2, c3, c4] if float(c['close']) > float(c['open']))
    reds   = sum(1 for c in [c1, c2, c3, c4] if float(c['close']) < float(c['open']))
    last_close = float(df['close'].iloc[-1])
    last_open  = float(df['open'].iloc[-1])
    is_green = last_close > last_open
    is_red   = last_close < last_open

    reasons = []
    strong  = False

    if side == "LONG":
        is_peak = False
        if ma3_prev2 < ma3_prev and ma3_curr < ma3_prev:
            is_peak = True
            reasons.append("MA3 尖端(倒V型)峰頂 -> 出場多單")
        elif ma3_prev3 < ma3_prev2 and ma3_curr < ma3_prev and (ma3_prev <= ma3_prev2):
            is_peak = True
            reasons.append("MA3 梯形峰頂 -> 出場多單")
        elif ma3_curr < ma3_prev and ma3_prev4 < ma3_prev3 and reds >= 2 and is_red and last_close < ma3_curr:
            is_peak = True
            reasons.append(f"MA3 圓弧頂(附{reds}根紅K) -> 出場多單")
        elif ma3_prev4 < ma3_prev3 and abs(ma3_prev3 - ma3_prev) <= flat_threshold and ma3_curr < ma3_prev:
            is_peak = True
            reasons.append("MA3 倒U形頂 -> 出場多單")
        if is_peak:
            strong = True
    else:
        is_trough = False
        if ma3_prev2 > ma3_prev and ma3_curr > ma3_prev:
            is_trough = True
            reasons.append("MA3 尖端(V型)谷底 -> 出場空單")
        elif ma3_prev3 > ma3_prev2 and ma3_curr > ma3_prev and (ma3_prev >= ma3_prev2):
            is_trough = True
            reasons.append("MA3 梯形谷底 -> 出場空單")
        elif ma3_curr > ma3_prev and ma3_prev4 > ma3_prev3 and greens >= 2 and is_green and last_close > ma3_curr:
            is_trough = True
            reasons.append(f"MA3 圓弧底(附{greens}根綠K) -> 出場空單")
        elif ma3_prev4 > ma3_prev3 and abs(ma3_prev3 - ma3_prev) <= flat_threshold and ma3_curr > ma3_prev:
            is_trough = True
            reasons.append("MA3 U形底 -> 出場空單")
        if is_trough:
            strong = True

    return {
        "active": bool(reasons),
        "ma_ok": not strong,
        "reasons": reasons,
        "strong": strong,
        "ma5_reversed": strong,
        "is_panic_reversal": False,
        "ema_breach_confirmed": strong,
        "structure_broken": strong,
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

