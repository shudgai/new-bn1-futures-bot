"""Pure Keltner outer-run exit state evaluation."""

import pandas as pd


def evaluate_kc_outer_run_lock(
    df: pd.DataFrame, side: str, armed: bool = False,
    outer_run_active: bool = False,
) -> dict:
    """辨識 KC 外軌延伸。

    單根影線觸軌只會 armed；連續兩根收在同側外軌、MA3 也在軌外且
    MA15 同向才進入 OUTER_RUN。外軌外的反向 K 不解除；第一根反向 K
    收回該側外軌內才解除，讓持倉在 MA3/MA15 完整轉彎前先退出。
    """
    result = {
        "armed": bool(armed), "blocked": bool(armed), "released": False,
        "touched_outer": False, "reached_middle": False,
        "outer_run_active": bool(outer_run_active),
        "returned_inside_outer": False,
        "outside_close_count": 0, "ma3_outside": False,
        "ma15_aligned": False,
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
    ma3 = (
        pd.to_numeric(work["ma3"], errors="coerce")
        if "ma3" in work.columns else close.rolling(3).mean()
    )
    ma15 = (
        pd.to_numeric(work["ma15"], errors="coerce")
        if "ma15" in work.columns else close.rolling(15).mean()
    )
    outside_close_count = 0
    for offset in range(1, min(len(work), 2) + 1):
        close_value = float(close.iloc[-offset])
        rail_value = float(upper.iloc[-offset] if side == "LONG" else lower.iloc[-offset])
        if (side == "LONG" and close_value >= rail_value) or (
            side == "SHORT" and close_value <= rail_value
        ):
            outside_close_count += 1
        else:
            break
    ma3_outside = bool(
        pd.notna(ma3.iloc[-1])
        and (
            (side == "LONG" and float(ma3.iloc[-1]) >= kc_upper)
            or (side == "SHORT" and float(ma3.iloc[-1]) <= kc_lower)
        )
    )
    ma15_aligned = bool(
        len(ma15.dropna()) >= 2
        and (
            (side == "LONG" and float(ma15.dropna().iloc[-1]) > float(ma15.dropna().iloc[-2]))
            or (side == "SHORT" and float(ma15.dropna().iloc[-1]) < float(ma15.dropna().iloc[-2]))
        )
    )
    confirmed_outer_run = bool(
        outside_close_count >= 2 and ma3_outside and ma15_aligned
    )
    reached_middle = bool(
        now_armed
        and (
            (side == "LONG" and candle_close < candle_open and candle_low <= kc_middle)
            or (side == "SHORT" and candle_close > candle_open and candle_high >= kc_middle)
        )
    )
    outer_run_was_active = bool(outer_run_active or confirmed_outer_run)
    returned_inside_outer = bool(
        outer_run_was_active
        and (
            (side == "LONG" and candle_close < candle_open and candle_close < kc_upper)
            or (side == "SHORT" and candle_close > candle_open and candle_close > kc_lower)
        )
    )
    active_outer_run = bool(
        outer_run_was_active and not returned_inside_outer
    )
    released = bool(returned_inside_outer or (not outer_run_was_active and reached_middle))
    result.update({
        "armed": bool(now_armed and not released),
        "blocked": bool((now_armed and not released) or active_outer_run),
        "released": released, "touched_outer": touched_outer,
        "outer_run_active": active_outer_run,
        "returned_inside_outer": returned_inside_outer,
        "outside_close_count": outside_close_count,
        "ma3_outside": ma3_outside, "ma15_aligned": ma15_aligned,
        "reached_middle": reached_middle, "kc_upper": kc_upper,
        "kc_middle": kc_middle, "kc_lower": kc_lower,
    })
    return result
