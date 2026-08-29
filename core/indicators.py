import time

import pandas as pd
import numpy as np

TIMEFRAME_MS = {"1m": 60_000, "3m": 180_000, "5m": 300_000, "15m": 900_000, "1h": 3_600_000}


def classify_wave_regime(
    df: pd.DataFrame,
    previous_regime: str = "RANGE",
    confirmation_bars: int = 3,
    range_adx_max: float = 20.0,
    range_spread_atr_max: float = 0.35,
    trend_adx_min: float = 25.0,
    trend_spread_atr_min: float = 0.50,
) -> dict:
    """用已收盤 K 的 ADX 與 MA3/MA15 距離判斷短波動或長趨勢。

    RANGE/TREND 都必須連續成立 confirmation_bars 根才切換；落在兩組
    門檻中間時維持前一狀態，避免模式在臨界值附近來回跳動。
    """
    prior = "TREND" if str(previous_regime).upper() == "TREND" else "RANGE"
    required = max(1, int(confirmation_bars))
    needed_columns = {"adx", "atr", "ma3", "ma15"}
    if df is None or len(df) < required or not needed_columns.issubset(df.columns):
        return {
            "regime": prior, "candidate": "HOLD", "confirmed": False,
            "adx": None, "spread_atr": None, "confirmation_bars": required,
        }

    recent = df.iloc[-required:]
    states = []
    last_adx = None
    last_spread = None
    for _, row in recent.iterrows():
        adx = float(row["adx"])
        atr = float(row["atr"])
        ma3 = float(row["ma3"])
        ma15 = float(row["ma15"])
        if any(pd.isna(value) for value in (adx, atr, ma3, ma15)) or atr <= 0:
            states.append("HOLD")
            continue
        spread_atr = abs(ma3 - ma15) / atr
        last_adx, last_spread = adx, spread_atr
        if adx < range_adx_max and spread_atr < range_spread_atr_max:
            states.append("RANGE")
        elif adx >= trend_adx_min and spread_atr >= trend_spread_atr_min:
            states.append("TREND")
        else:
            states.append("HOLD")

    candidate = states[-1] if states else "HOLD"
    confirmed = bool(states and len(set(states)) == 1 and states[0] in ("RANGE", "TREND"))
    regime = states[0] if confirmed else prior
    return {
        "regime": regime, "candidate": candidate, "confirmed": confirmed,
        "adx": last_adx, "spread_atr": last_spread,
        "confirmation_bars": required,
    }

def evaluate_kc_outer_run_lock(df: pd.DataFrame, side: str, armed: bool = False) -> dict:
    """外軌延伸後鎖住技術反轉，直到反向 K 至少回到 KC 中軌。"""
    result = {
        "armed": bool(armed), "blocked": bool(armed), "released": False,
        "touched_outer": False, "reached_middle": False,
        "kc_upper": None, "kc_middle": None, "kc_lower": None,
    }
    if df is None or df.empty or str(side or "").upper() not in ("LONG", "SHORT"):
        return result

    work = df.copy()
    close = pd.to_numeric(work["close"], errors="coerce")
    high = pd.to_numeric(work["high"], errors="coerce")
    low = pd.to_numeric(work["low"], errors="coerce")
    if "kc_middle" in work.columns:
        middle = pd.to_numeric(work["kc_middle"], errors="coerce")
    elif "ema_20" in work.columns:
        middle = pd.to_numeric(work["ema_20"], errors="coerce")
    else:
        middle = close.ewm(span=20, adjust=False).mean()
    if "atr" in work.columns:
        atr = pd.to_numeric(work["atr"], errors="coerce")
    else:
        previous_close = close.shift(1)
        tr = pd.concat([
            high - low, (high - previous_close).abs(), (low - previous_close).abs(),
        ], axis=1).max(axis=1)
        atr = tr.rolling(10, min_periods=3).mean()
    from core.config import KELTNER_ATR_MULTIPLIER
    upper = (
        pd.to_numeric(work["kc_upper"], errors="coerce")
        if "kc_upper" in work.columns else middle + atr * KELTNER_ATR_MULTIPLIER
    )
    lower = (
        pd.to_numeric(work["kc_lower"], errors="coerce")
        if "kc_lower" in work.columns else middle - atr * KELTNER_ATR_MULTIPLIER
    )
    values = [work["open"].iloc[-1], close.iloc[-1], high.iloc[-1], low.iloc[-1],
              upper.iloc[-1], middle.iloc[-1], lower.iloc[-1]]
    if any(pd.isna(value) for value in values):
        return result

    candle_open, candle_close, candle_high, candle_low, kc_upper, kc_middle, kc_lower = map(float, values)
    side = str(side).upper()
    touched_outer = bool(
        candle_high >= kc_upper if side == "LONG" else candle_low <= kc_lower
    )
    now_armed = bool(armed or touched_outer)
    reached_middle = bool(
        now_armed
        and (
            (side == "LONG" and candle_close < candle_open and candle_low <= kc_middle)
            or (side == "SHORT" and candle_close > candle_open and candle_high >= kc_middle)
        )
    )
    result.update({
        "armed": bool(now_armed and not reached_middle),
        "blocked": bool(now_armed and not reached_middle),
        "released": reached_middle, "touched_outer": touched_outer,
        "reached_middle": reached_middle, "kc_upper": kc_upper,
        "kc_middle": kc_middle, "kc_lower": kc_lower,
    })
    return result


def detect_strong_trend_exhaustion(
    df: pd.DataFrame, side: str, previous_extreme: float = None,
    previous_ma3_extreme: float = None, retrace_atr: float = 0.15,
) -> dict:
    """強趨勢只在結構真正衰退後退出，不因第一個局部峰谷提早止盈。"""
    result = {
        "exit": False, "extreme_price": previous_extreme,
        "ma3_extreme": previous_ma3_extreme, "retrace_atr": 0.0,
        "two_bar_confirmed": False, "strength_fading": False,
    }
    required = {"close", "high", "low", "ma3", "ma15", "atr", "adx"}
    if df is None or len(df) < 3 or not required.issubset(df.columns):
        return result

    work = df.dropna(subset=list(required)).copy()
    if len(work) < 3:
        return result
    side = str(side or "").upper()
    if side not in ("LONG", "SHORT"):
        return result

    ma3 = pd.to_numeric(work["ma3"], errors="coerce")
    ma15 = pd.to_numeric(work["ma15"], errors="coerce")
    adx = pd.to_numeric(work["adx"], errors="coerce")
    atr = max(float(work["atr"].iloc[-1]), 1e-12)
    close = pd.to_numeric(work["close"], errors="coerce")
    spread = (ma3 - ma15).abs()

    if side == "LONG":
        extreme_price = max(
            float(previous_extreme) if previous_extreme is not None else float("-inf"),
            float(work["high"].max()),
        )
        ma3_extreme = max(
            float(previous_ma3_extreme) if previous_ma3_extreme is not None else float("-inf"),
            float(ma3.max()),
        )
        retrace = max(0.0, ma3_extreme - float(ma3.iloc[-1])) / atr
        two_bar_confirmed = bool(
            close.iloc[-1] < ma3.iloc[-1]
            and close.iloc[-2] < ma3.iloc[-2]
            and ma3.iloc[-1] < ma3.iloc[-2] <= ma3.iloc[-3]
        )
    else:
        extreme_price = min(
            float(previous_extreme) if previous_extreme is not None else float("inf"),
            float(work["low"].min()),
        )
        ma3_extreme = min(
            float(previous_ma3_extreme) if previous_ma3_extreme is not None else float("inf"),
            float(ma3.min()),
        )
        retrace = max(0.0, float(ma3.iloc[-1]) - ma3_extreme) / atr
        two_bar_confirmed = bool(
            close.iloc[-1] > ma3.iloc[-1]
            and close.iloc[-2] > ma3.iloc[-2]
            and ma3.iloc[-1] > ma3.iloc[-2] >= ma3.iloc[-3]
        )

    strength_fading = bool(
        float(adx.iloc[-1]) < float(adx.iloc[-2])
        or float(spread.iloc[-1]) < float(spread.iloc[-2])
    )
    result.update({
        "exit": bool(retrace >= retrace_atr and two_bar_confirmed and strength_fading),
        "extreme_price": extreme_price, "ma3_extreme": ma3_extreme,
        "retrace_atr": retrace, "two_bar_confirmed": two_bar_confirmed,
        "strength_fading": strength_fading,
        "adx": float(adx.iloc[-1]), "spread_atr": float(spread.iloc[-1]) / atr,
    })
    return result


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


def detect_ma3_ma15_cross_and_turn(df, allow_live_pivot=False):
    """
    1m MA3 / MA15 連續轉向邏輯：
    MA3 在 MA15 上方時順勢做多，除非峰頂轉彎向下。
    MA3 在 MA15 下方時順勢做空，除非谷底轉彎向上。
    """
    if df is None or len(df) < 15:
        return {"signal": None, "reason": "Not enough data", "pivot_confirmed": False, "pivot_score": 0}

    df = df.copy()
    if 'ma3' not in df.columns:
        df['ma3'] = df['close'].rolling(window=3).mean()
    if 'ema_20' not in df.columns:
        df['ema_20'] = df['close'].ewm(span=20, adjust=False).mean()
    if 'ma15' not in df.columns:
        df['ma15'] = df['close'].rolling(window=15).mean()

    ma3_series = df['ma3'].dropna()
    if len(ma3_series) < 5:
        return {"signal": None, "reason": "Not enough data", "pivot_confirmed": False, "pivot_score": 0}

    ma3_curr  = float(ma3_series.iloc[-1])
    ma3_prev  = float(ma3_series.iloc[-2])
    ma3_prev2 = float(ma3_series.iloc[-3])
    ma3_prev3 = float(ma3_series.iloc[-4])
    ma15_curr = float(df['ma15'].iloc[-1])
    if pd.isna(ma15_curr):
        return {"signal": None, "reason": "MA15 not ready", "pivot_confirmed": False, "pivot_score": 0}
    previous_slope = ma3_prev - ma3_prev2
    current_slope = ma3_curr - ma3_prev
    ma_alignment = "ABOVE" if ma3_curr > ma15_curr else "BELOW" if ma3_curr < ma15_curr else "EQUAL"

    atr_raw = float(df['atr'].iloc[-1]) if 'atr' in df.columns else float("nan")
    if pd.notna(atr_raw) and atr_raw > 0:
        atr = atr_raw
    else:
        # 連續模式直接取得交易所原始 1m K 線，尚未預先加上 atr 欄位。
        # 以實際 True Range 算 ATR，不能用價格的固定百分比代替，否則
        # MA3 斜率與「離 MA3 多遠」的門檻會和當前波動完全失真。
        previous_close = df['close'].shift(1)
        true_range = pd.concat([
            df['high'] - df['low'],
            (df['high'] - previous_close).abs(),
            (df['low'] - previous_close).abs(),
        ], axis=1).max(axis=1)
        calculated_atr = float(true_range.rolling(14, min_periods=5).mean().iloc[-1])
        atr = calculated_atr if pd.notna(calculated_atr) and calculated_atr > 0 else float(df['close'].iloc[-1]) * 0.015
    # 放寬趨勢延續的最小變動門檻：仍要求 MA3 確實轉折／分離，
    # 為了達到極致靈敏度 (用戶要求)，大幅降低斜率門檻，只要有明確 V 型就算。
    min_pivot_slope = max(abs(atr) * 0.02, abs(ma3_prev) * 0.00005)
    recent_ma3_range = max(ma3_prev2, ma3_prev, ma3_curr) - min(ma3_prev2, ma3_prev, ma3_curr)
    min_directional_range = max(abs(atr) * 0.08, abs(ma3_curr) * 0.0002)
    # fast_pivot_slope 更小，只要 MA3 彎折方向改變，就應該視為 V 轉（應對暴漲暴跌後的MA3平滑效應）
    fast_pivot_slope = max(abs(atr) * 0.01, abs(ma3_prev) * 0.00002)
    ma15_distance = abs(ma3_curr - ma15_curr)
    ma15_distance_weight = ma15_distance / max(abs(atr), 1e-12)
    ma15_far_enough = ma15_distance_weight >= 0.75

    # Two same-direction, above-average candles can confirm a fast peak/trough
    # before MA15 has time to cross.  Both candles must carry volume, so a
    # single thin wick cannot trigger a reversal entry.
    previous_candle = df.iloc[-2]
    current_candle = df.iloc[-1]
    volume_baseline = float(df["volume"].iloc[-12:-2].mean()) if "volume" in df.columns else 0.0
    previous_volume = float(previous_candle.get("volume", 0) or 0)
    current_volume = float(current_candle.get("volume", 0) or 0)
    two_candle_volume_confirmed = volume_baseline > 0 and min(previous_volume, current_volume) >= volume_baseline * 1.20
    # 峰／谷固定在倒數第二根 MA3（iloc[-2]），倒數第一根只負責確認右側
    # 已經反向。左右兩側都必須有足夠斜率，不能把幾乎水平的位置誤標成
    # 目前峰頂／谷底。
    two_red_peak = bool(
        previous_slope >= fast_pivot_slope and current_slope <= -fast_pivot_slope
        and float(previous_candle["close"]) < float(previous_candle["open"])
        and float(current_candle["close"]) < float(current_candle["open"])
        and two_candle_volume_confirmed
    )
    two_green_trough = bool(
        previous_slope <= -fast_pivot_slope and current_slope >= fast_pivot_slope
        and float(previous_candle["close"]) > float(previous_candle["open"])
        and float(current_candle["close"]) > float(current_candle["open"])
        and two_candle_volume_confirmed
    )
    # 峰谷點固定在倒數第二根；若它仍貼著 KC 中軌或 MA15，代表只是
    # 中間雜訊，不允許平倉反手。這個守衛必須先於所有峰谷 return。
    step_trough = bool(
        ma3_prev <= ma3_prev2 <= ma3_prev3
        and ma3_prev3 - ma3_prev >= fast_pivot_slope
        and current_slope >= fast_pivot_slope
    )
    step_peak = bool(
        ma3_prev >= ma3_prev2 >= ma3_prev3
        and ma3_prev - ma3_prev3 >= fast_pivot_slope
        and current_slope <= -fast_pivot_slope
    )
    v_trough = previous_slope < 0 and current_slope > 0
    v_peak = previous_slope > 0 and current_slope < 0
    pivot_shape_detected = bool(
        two_red_peak or two_green_trough or step_trough or step_peak
        or v_trough or v_peak
    )

    current_candle = df.iloc[-1]
    last_candle_low = float(current_candle["low"])
    last_candle_high = float(current_candle["high"])
    kc_middle_now = float(df["ema_20"].iloc[-1])
    
    # 計算 Keltner Channel 外軌
    from core.config import KELTNER_ATR_MULTIPLIER
    kc_upper_now = kc_middle_now + atr * KELTNER_ATR_MULTIPLIER
    kc_lower_now = kc_middle_now - atr * KELTNER_ATR_MULTIPLIER

    # ==============================================================================
    # 盤旋過濾 (Hovering Filter) - 最高優先級
    # 用戶要求：「無論是ma15 上中下軌只要是盤旋在上面的(就是離他們太近的)就是不轉向」
    # 若 V 轉剛好貼在某條線上，且變動幅度（斜率）不夠大，則判定為盤旋，忽略此轉向。
    # ==============================================================================
    is_hovering = False
    if pivot_shape_detected:
        ma15_at_pivot = float(df["ma15"].iloc[-2])
        kc_mid_at_pivot = float(df["ema_20"].iloc[-2])
        kc_up_at_pivot = kc_mid_at_pivot + atr * KELTNER_ATR_MULTIPLIER
        kc_low_at_pivot = kc_mid_at_pivot - atr * KELTNER_ATR_MULTIPLIER
        
        min_dist_to_lines = min(
            abs(ma3_prev - ma15_at_pivot),
            abs(ma3_prev - kc_mid_at_pivot),
            abs(ma3_prev - kc_up_at_pivot),
            abs(ma3_prev - kc_low_at_pivot)
        )
        
        # 如果離線太近 (< 0.15 ATR) 且 斜率偏小 (< 0.20 ATR)
        if min_dist_to_lines < atr * 0.15:
            if abs(previous_slope) < atr * 0.20 and abs(current_slope) < atr * 0.20:
                is_hovering = True

    if pivot_shape_detected and is_hovering:
        ma15_at_pivot = float(df["ma15"].iloc[-2])
        kc_middle_at_pivot = float(df["ema_20"].iloc[-2])
        return {
            "signal": None, "entry_type": "WAIT_MA_NOISE",
            "reason": "1m MA3 在軌道或MA15上微幅盤旋，忽略此微弱轉向",
            "atr": atr, "pivot_confirmed": False,
            "pivot_score": 0, "ma3_slope": current_slope,
            "live_pivot": False, "pivot_offset": 0,
            "ma_alignment": ma_alignment,
            "ma15_distance_at_pivot": abs(ma3_prev - ma15_at_pivot),
            "kc_middle_distance_at_pivot": abs(ma3_prev - kc_middle_at_pivot),
            "middle_noise_threshold": min_pivot_slope
        }
    
    previous_candle = df.iloc[-2]
    prev_candle_high = float(previous_candle["high"])
    prev_candle_low = float(previous_candle["low"])
    
    # 外軌突破反轉：V/倒V已形成 + 反向 K 穿過相反外軌 = 確定轉向
    # 例如：綠K衝到上軌外 -> 下一根紅K已在下軌 = 轉向開空
    outer_rail_reversal_to_short = bool(
        v_peak and previous_slope >= fast_pivot_slope * 0.5
        and current_slope <= -fast_pivot_slope
        and prev_candle_high > kc_upper_now  # 前根K衝到上軌外（多強）
        and last_candle_low <= kc_lower_now  # 當前紅K已在下軌（反轉確認）
    )
    outer_rail_reversal_to_long = bool(
        v_trough and previous_slope <= -fast_pivot_slope * 0.5
        and current_slope >= fast_pivot_slope
        and prev_candle_low < kc_lower_now  # 前根K衝到下軌外（空強）
        and last_candle_high >= kc_upper_now  # 當前綠K已在上軌（反轉確認）
    )
    
    # 外軌突破反轉優先於中軌回撤判定：這是最清楚的轉向信號
    if outer_rail_reversal_to_short:
        return {
            "signal": "SHORT", "entry_type": "PEAK_TURN",
            "reason": "1m 倒V已形成，紅K已穿過下軌 → 立即平多開空",
            "atr": atr, "pivot_confirmed": True, "pivot_score": 100,
            "fast_pivot": True, "live_pivot": allow_live_pivot,
            "pivot_offset": -2, "ma_alignment": ma_alignment,
        }
    if outer_rail_reversal_to_long:
        return {
            "signal": "LONG", "entry_type": "TROUGH_TURN",
            "reason": "1m V已形成，綠K已穿過上軌 → 立即平空開多",
            "atr": atr, "pivot_confirmed": True, "pivot_score": 100,
            "fast_pivot": True, "live_pivot": allow_live_pivot,
            "pivot_offset": -2, "ma_alignment": ma_alignment,
        }
    # 「見好就收」極速平倉：三個軌都要做一樣的處理！
    # 只要出現 V 型反轉，且價格已經穿過（或摸到）上、中、下任何一軌，立刻認定為轉向！
    immediate_peak_hit_band = False
    if v_peak:
        prev_high = float(df["high"].iloc[-2])
        curr_low = last_candle_low
        if prev_high > kc_upper_now and curr_low <= kc_upper_now:
            immediate_peak_hit_band = True
        elif prev_high > kc_middle_now and curr_low <= kc_middle_now:
            immediate_peak_hit_band = True
        elif prev_high > kc_lower_now and curr_low <= kc_lower_now:
            immediate_peak_hit_band = True

    immediate_trough_hit_band = False
    if v_trough:
        prev_low = float(df["low"].iloc[-2])
        curr_high = last_candle_high
        if prev_low < kc_lower_now and curr_high >= kc_lower_now:
            immediate_trough_hit_band = True
        elif prev_low < kc_middle_now and curr_high >= kc_middle_now:
            immediate_trough_hit_band = True
        elif prev_low < kc_upper_now and curr_high >= kc_upper_now:
            immediate_trough_hit_band = True

    if immediate_peak_hit_band:
        return {
            "signal": "SHORT", "entry_type": "PEAK_TURN",
            "reason": "1m MA3 倒V已形成且紅K已跌破任一軌道(上/中/下) → 立即反手開空",
            "atr": atr, "pivot_confirmed": True, "pivot_score": 100,
            "fast_pivot": True, "live_pivot": allow_live_pivot,
            "pivot_offset": -2, "ma_alignment": ma_alignment,
        }
    if immediate_trough_hit_band:
        return {
            "signal": "LONG", "entry_type": "TROUGH_TURN",
            "reason": "1m MA3 V已形成且綠K已突破任一軌道(上/中/下) → 立即反手開多",
            "atr": atr, "pivot_confirmed": True, "pivot_score": 100,
            "fast_pivot": True, "live_pivot": allow_live_pivot,
            "pivot_offset": -2, "ma_alignment": ma_alignment,
        }

    # 一般的轉折噪音過濾
    # 真正的峰頂/谷底不能因為剛好貼近中軌或 MA15 就被噪音過濾。
    if pivot_shape_detected and not (
        two_red_peak or two_green_trough or step_trough or step_peak
        or immediate_peak_hit_band or immediate_trough_hit_band
        or (
            v_trough and abs(previous_slope) >= min_pivot_slope
            and current_slope >= min_pivot_slope
        )
        or (
            v_peak and previous_slope >= min_pivot_slope
            and abs(current_slope) >= min_pivot_slope
        )
    ):
        ma15_at_pivot = float(df["ma15"].iloc[-2])
        kc_middle_at_pivot = float(df["ema_20"].iloc[-2])
        middle_noise_threshold = min_pivot_slope
        ma15_distance_at_pivot = abs(ma3_prev - ma15_at_pivot)
        kc_middle_distance_at_pivot = abs(ma3_prev - kc_middle_at_pivot)
        return {
            "signal": None, "entry_type": "WAIT_MA_NOISE",
            "reason": "1m MA3 轉折過弱，未達真峰谷確認門檻",
            "atr": atr, "pivot_confirmed": False,
            "pivot_score": 0, "ma3_slope": current_slope,
            "ma3_curr": ma3_curr, "ma15_curr": ma15_curr,
            "ma15_distance": ma15_distance_at_pivot,
            "kc_middle_distance": kc_middle_distance_at_pivot,
            "noise_threshold": middle_noise_threshold,
            "pivot_offset": -2, "ma_alignment": "NEUTRAL",
        }

    if two_red_peak:
        return {
            "signal": "SHORT", "entry_type": "PEAK_TURN",
            "reason": "1m 兩根放量紅K確認峰頂向下 → 立即開空",
            "atr": atr, "pivot_confirmed": True, "pivot_score": 100,
            "fast_pivot": True, "volume_confirmed": True, "live_pivot": allow_live_pivot,
            "pivot_offset": -2,
            "ma_alignment": ma_alignment,
        }
    if two_green_trough:
        return {
            "signal": "LONG", "entry_type": "TROUGH_TURN",
            "reason": "1m 兩根放量綠K確認谷底向上 → 立即開多",
            "atr": atr, "pivot_confirmed": True, "pivot_score": 100,
            "fast_pivot": True, "volume_confirmed": True, "live_pivot": allow_live_pivot,
            "pivot_offset": -2,
            "ma_alignment": ma_alignment,
        }

    # Allow a stair-step plateau at a top or bottom to enter while flat.
    if step_trough:
        return {
            "signal": "LONG", "entry_type": "TROUGH_TURN",
            "reason": "1m MA3 V/梯形谷底向上 → 立即轉向開多",
            "atr": atr, "pivot_confirmed": True, "pivot_score": 100,
            "fast_pivot": True, "live_pivot": allow_live_pivot,
            "pivot_offset": -2,
            "ma_alignment": ma_alignment,
        }
    if step_peak:
        return {
            "signal": "SHORT", "entry_type": "PEAK_TURN",
            "reason": "1m MA3 倒V/梯形峰頂向下 → 立即轉向開空",
            "atr": atr, "pivot_confirmed": True, "pivot_score": 100,
            "fast_pivot": True, "live_pivot": allow_live_pivot,
            "pivot_offset": -2,
            "ma_alignment": ma_alignment,
        }

    # MA3 自身最近三根幾乎走平時，不論位於 MA15 上方或下方都不開新方向。
    # 回傳空訊號可讓既有持倉保持原方向。
    if (
        ma3_curr != ma15_curr
        and recent_ma3_range <= min_directional_range
    ):
        return {
            "signal": None, "entry_type": "WAIT_MA_NOISE",
            "reason": "1m MA3 最近3根振幅過小，不開新倉並維持原持倉方向",
            "atr": atr, "pivot_confirmed": False,
            "pivot_score": 0, "ma3_slope": current_slope,
            "ma3_curr": ma3_curr, "ma15_curr": ma15_curr,
            "ma3_range": recent_ma3_range,
            "noise_threshold": min_directional_range,
            "ma_alignment": "NEUTRAL"
        }

    # 明顯 V/倒V 才換向；微小轉折先等待，避免一點點彎就平倉反手。
    # 仍只使用已收盤的 1m K，避免未完成 K 線反覆變形造成重複訊號。
    if previous_slope < 0 and current_slope > 0:
        if abs(previous_slope) >= min_pivot_slope and current_slope >= min_pivot_slope:
            return {
                "signal": "LONG", "entry_type": "TROUGH_TURN",
                "reason": "1m MA3 V字谷底 → 立即轉向開多",
                "atr": atr, "pivot_confirmed": True,
                "pivot_score": 100,
                "fast_pivot": True,
                "live_pivot": allow_live_pivot,
                "pivot_offset": -2,
                "ma_alignment": ma_alignment
            }
        return {
            "signal": None, "entry_type": "WAIT_PRE_PIVOT",
            "reason": "1m MA3 谷底轉折幅度不足，維持原開倉方向",
            "atr": atr, "pivot_confirmed": False,
            "pivot_score": 0, "ma3_slope": current_slope,
            "ma_alignment": "WAIT"
        }
    if previous_slope > 0 and current_slope < 0:
        if previous_slope >= min_pivot_slope and abs(current_slope) >= min_pivot_slope:
            return {
                "signal": "SHORT", "entry_type": "PEAK_TURN",
                "reason": "1m MA3 倒V字峰頂 → 立即轉向開空",
                "atr": atr, "pivot_confirmed": True,
                "pivot_score": 100,
                "fast_pivot": True,
                "live_pivot": allow_live_pivot,
                "pivot_offset": -2,
                "ma_alignment": ma_alignment
            }
        return {
            "signal": None, "entry_type": "WAIT_PRE_PIVOT",
            "reason": "1m MA3 峰頂轉折幅度不足，維持原開倉方向",
            "atr": atr, "pivot_confirmed": False,
            "pivot_score": 0, "ma3_slope": current_slope,
            "ma_alignment": "WAIT"
        }

    # 峰谷沒有真正轉向時，以 MA3 相對 MA15 的位置決定方向。
    # MA3 貼近 MA15（距離 <= 0.05 ATR）視為小波動，不產生反向開倉訊號。
    # 若 MA3 距離 MA15 較大，代表市場已偏離均線，這時更容易出現真峰谷。
    if 0 < ma15_distance <= min_pivot_slope:
        return {
            "signal": None, "entry_type": "WAIT_MA_NOISE",
            "reason": "1m MA3 貼近 MA15，小波動不改變開倉方向",
            "atr": atr, "pivot_confirmed": False,
            "pivot_score": 0, "ma3_slope": current_slope,
            "ma3_curr": ma3_curr, "ma15_curr": ma15_curr,
            "ma15_distance": ma15_distance,
            "noise_threshold": min_pivot_slope,
            "ma_alignment": "NEUTRAL"
        }
    if ma3_curr < ma15_curr:
        return {
            "signal": "SHORT", "entry_type": "TREND_SHORT",
            "reason": "1m MA3 位於 MA15 下方且未形成谷底向上 → 順勢延續開空",
            "atr": atr, "pivot_confirmed": False,
            "pivot_score": 85, "ma3_slope": current_slope,
            "ma3_curr": ma3_curr, "ma15_curr": ma15_curr,
            "ma_alignment": "BELOW"
        }

    if ma3_curr > ma15_curr:
        return {
            "signal": "LONG", "entry_type": "TREND_LONG",
            "reason": "1m MA3 位於 MA15 上方且未形成峰頂向下 → 順勢延續開多",
            "atr": atr, "pivot_confirmed": False,
            "pivot_score": 85, "ma3_slope": current_slope,
            "ma3_curr": ma3_curr, "ma15_curr": ma15_curr,
            "ma_alignment": "ABOVE"
        }

    return {
        "signal": None, "entry_type": "WAIT_MA_EQUAL",
        "reason": "1m MA3 與 MA15 重疊，等待方向明確",
        "pivot_confirmed": False,
        "pivot_score": 0,
        "ma3_curr": ma3_curr, "ma15_curr": ma15_curr,
        "ma_alignment": "EQUAL",
    }


def get_ma3_ma15_limit_target(df: pd.DataFrame, side: str, lookback: int = 3) -> float:
    """多單取近期最低價，空單取近期最高價作為 Maker 掛單目標。"""
    if df is None or df.empty:
        raise ValueError("K線資料不足，無法計算掛單價")
    side = str(side).upper()
    price_column = "low" if side == "LONG" else "high" if side == "SHORT" else None
    if price_column is None:
        raise ValueError(f"不支援的掛單方向: {side}")
    source = df[price_column] if price_column in df.columns else df["close"]
    recent = pd.to_numeric(source.tail(max(1, int(lookback))), errors="coerce").dropna()
    if recent.empty:
        raise ValueError(f"{price_column} 資料不足，無法計算掛單價")
    return float(recent.min() if side == "LONG" else recent.max())


def compute_position_trigger(df: pd.DataFrame, side: str, ma_period: int = 20, lookback_bars: int = 20) -> dict:
    """持倉平倉訊號：MA5 正式反轉，或強反向 K 穿越 MA3 時保護性離場。

    強反向 K 只負責先平倉，不代表可以立即反手；反向進場另等峰谷確認。
    """
    empty = {
        "active": False, "ma_ok": True, "reasons": [], "strong": False,
        "ma5_reversed": False, "ema_breach_confirmed": False,
        "structure_broken": False, "atr": None,
        "pre_peak_exit": False, "pre_trough_exit": False,
    }
    if df is None or len(df) < 12:
        return empty

    df = df.copy()
    if 'ma5' not in df.columns:
        df['ma5'] = df['close'].rolling(window=5).mean()
    if 'ma3' not in df.columns:
        df['ma3'] = df['close'].rolling(window=3).mean()
    if 'ema_20' not in df.columns:
        df['ema_20'] = df['close'].ewm(span=20, adjust=False).mean()

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

    curr = df.iloc[-1]
    candle_open = float(curr.get('open', curr['close']))
    candle_close = float(curr['close'])
    candle_body = abs(candle_close - candle_open)
    ma3_curr = float(df['ma3'].iloc[-1])
    kc_middle = float(df['ema_20'].iloc[-1])
    
    # 快速止損邏輯：一旦發現方向錯誤就立即平倉
    # 多單被強紅K擊中：實體>=0.5ATR 且穿破MA3 就平仓
    # 不需等到红K回到中軌，早發現早止損
    strong_opposite_body = candle_body >= atr_val * 0.50
    pre_peak_exit = bool(
        side == "LONG" and strong_opposite_body
        and candle_close < candle_open and candle_close < ma3_curr
    )
    
    # 空單被強綠K擊中：實體>=0.5ATR 且穿破MA3 就平仓
    pre_trough_exit = bool(
        side == "SHORT" and strong_opposite_body
        and candle_close > candle_open and candle_close > ma3_curr
    )

    reasons = []
    strong = False

    if side == "LONG":
        if pre_peak_exit:
            reasons.append("強紅K實體>=0.5ATR且收破MA3 -> 保護性平多")
        if is_peak_forming and float(curr['low']) <= kc_middle:
            strong = True
            reasons.append("MA5 嚴格峰頂且紅K到KC中軌(倒V型) -> 出場多單")
    else:
        if pre_trough_exit:
            reasons.append("強綠K實體>=0.5ATR且收上MA3 -> 保護性平空")
        if is_trough_forming and float(curr['high']) >= kc_middle:
            strong = True
            reasons.append("MA5 嚴格谷底且綠K到KC中軌(V型) -> 出場空單")

    return {
        "active": bool(reasons),
        "ma_ok": not strong,
        "reasons": reasons,
        "strong": strong,
        "ma5_reversed": strong,
        "is_panic_reversal": False,
        "ema_breach_confirmed": strong,
        "structure_broken": strong,
        "pre_peak_exit": pre_peak_exit,
        "pre_trough_exit": pre_trough_exit,
        "opposite_candle_body_atr": candle_body / max(atr_val, 1e-12),
        "ma3_curr": ma3_curr,
        "kc_middle": kc_middle,
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
