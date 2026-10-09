"""Pure helpers for pivot and SuperTrend state interpretation."""

import numpy as np
import pandas as pd


def strict_pivot_type(values, index):
    """Use adjacent closed samples; reject ties, missing neighbours and broken pivots.

    Callers supply closed bars only. Confirmation is available at index + 1.
    """
    values = np.asarray(values, dtype=float)
    if index < 0:
        index += len(values)
    if index < 1 or index + 1 >= len(values):
        return None
    if not np.isfinite(values[index - 1:]).all():
        return None
    left, center, right = values[index - 1:index + 2]
    if center > left and center > right and np.all(values[index + 1:] < center):
        return "PEAK_TURN"
    if center < left and center < right and np.all(values[index + 1:] > center):
        return "TROUGH_TURN"
    return None


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
