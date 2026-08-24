import time

import pandas as pd
import numpy as np

TIMEFRAME_MS = {"1m": 60_000, "5m": 300_000, "15m": 900_000, "1h": 3_600_000}


def _prepare_pyramid_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Return an isolated frame with the indicators required by the 1m pyramid strategy."""
    frame = df.copy()
    close = pd.to_numeric(frame["close"], errors="coerce")
    frame["ma3"] = close.rolling(3).mean()
    frame["ma5"] = close.rolling(5).mean()
    frame["ma25"] = close.rolling(25).mean()
    if "atr" not in frame.columns:
        high = pd.to_numeric(frame["high"], errors="coerce")
        low = pd.to_numeric(frame["low"], errors="coerce")
        true_range = pd.concat(
            [(high - low), (high - close.shift()).abs(), (low - close.shift()).abs()],
            axis=1,
        ).max(axis=1)
        frame["atr"] = true_range.ewm(alpha=1 / 14, adjust=False).mean()
    return frame


def detect_diminishing_pyramid_entry(
    df: pd.DataFrame, allow_live_pivot: bool = False
) -> dict:
    """融合 2a053b4 快速 MA3 峰谷與新版防追價風控的 1m 進場。"""
    from core.config import (
        PYRAMID_LIQUIDITY_GRAB_BARS,
        PYRAMID_ENTRY_MAX_MA_DISTANCE_ATR,
        PYRAMID_MIN_COST_MULT,
        PYRAMID_PULLBACK_ZONE_ATR,
        PYRAMID_STRUCTURE_LOOKBACK,
        PYRAMID_TSL_ATR_MULT,
        PYRAMID_VOLUME_SPIKE_RATIO,
        SLIPPAGE_PCT,
        TAKER_FEE_RATE,
    )

    if df is None or len(df) < 30:
        return {"signal": None, "reason": "1m 資料不足"}
    frame = _prepare_pyramid_indicators(df)
    current = frame.iloc[-1]
    previous = frame.iloc[-2]
    values = [current.get(name) for name in ("close", "open", "high", "low", "ma3", "ma5", "ma25", "atr")]
    if any(pd.isna(value) for value in values):
        return {"signal": None, "reason": "1m 指標尚未完成暖機"}

    close = float(current["close"])
    open_price = float(current["open"])
    high = float(current["high"])
    low = float(current["low"])
    ma3 = float(current["ma3"])
    ma5 = float(current["ma5"])
    ma25 = float(current["ma25"])
    atr = max(float(current["atr"]), close * 1e-6)
    bullish_zone = ma3 > ma25 and ma5 > ma25
    bearish_zone = ma3 < ma25 and ma5 < ma25

    # 2a053b4 的峰谷必須優先於 MA25 排列判斷。谷底剛上彎時 MA3 常會先
    # 穿越 MA25、MA5 還來不及跟上；若先擋 MIXED，最重要的第一彎會被漏掉。
    fast_signal = detect_ma5_ma25_cross_and_turn(
        frame, allow_live_pivot=allow_live_pivot
    )
    if not fast_signal.get("signal"):
        return {
            **fast_signal,
            "trend_zone": "BULLISH" if bullish_zone else "BEARISH",
        }
    entry_type = fast_signal.get("entry_type") or (
        "TREND_LONG" if fast_signal.get("signal") == "LONG" else "TREND_SHORT"
    )
    pivot_override = bool(
        fast_signal.get("pivot_confirmed")
        and entry_type in ("TROUGH_TURN", "PEAK_TURN")
    )
    if pivot_override:
        side = fast_signal["signal"]
        bullish_zone = side == "LONG"
        bearish_zone = side == "SHORT"
    else:
        if not bullish_zone and not bearish_zone:
            return {
                "signal": None,
                "reason": "MA3、MA5 分居 MA25 兩側且尚無真峰谷，暫不開倉",
                "trend_zone": "MIXED",
            }
        side = "LONG" if bullish_zone else "SHORT"

    if fast_signal.get("signal") != side and not pivot_override:
        return {
            "signal": None,
            "reason": f"MA3、MA5 位於 MA25 {'上方' if bullish_zone else '下方'}，拒絕逆勢 {fast_signal.get('signal')}",
            "trend_zone": "BULLISH" if bullish_zone else "BEARISH",
            "alignment_blocked": True,
        }
    if entry_type in ("TREND_LONG", "TREND_SHORT"):
        candle_confirms = close > open_price if side == "LONG" else close < open_price
        ma3_turn_confirms = (
            ma3 > float(previous["ma3"])
            if side == "LONG" else ma3 < float(previous["ma3"])
        )
        if not candle_confirms or not ma3_turn_confirms:
            return {
                "signal": None,
                "reason": (
                    f"{side} 均線同側，但 MA3 尚未同向；"
                    f"谷底上彎時禁止追空、頂峰下彎時禁止追多"
                ),
                "trend_zone": "BULLISH" if bullish_zone else "BEARISH",
                "candle_confirmation": False,
                "ma3_turn_confirmation": False,
            }

    # 只限制「順勢方向」的乖離：多單價格高於 MA3、空單價格低於 MA3
    # 才視為追價；回踩到 MA3 另一側反而是更好的成交位置。
    ma_distance_atr = max(
        0.0,
        (close - ma3) / atr if side == "LONG" else (ma3 - close) / atr,
    )
    if ma_distance_atr > max(0.0, PYRAMID_ENTRY_MAX_MA_DISTANCE_ATR):
        return {
            "signal": None,
            "reason": f"{side} 趨勢成立，但現價順向離 MA3 {ma_distance_atr:.2f}ATR，避免追高／追空",
            "trend_zone": "BULLISH" if bullish_zone else "BEARISH",
            "execution_too_far": True,
            "ma_distance_atr": ma_distance_atr,
        }

    zone_buffer = atr * max(0.0, PYRAMID_PULLBACK_ZONE_ATR)
    zone_low = ma3 - zone_buffer
    zone_high = ma3 + zone_buffer
    touched_ma_zone = low <= zone_high and high >= zone_low

    body = abs(close - open_price)
    candle_range = max(high - low, close * 1e-9)
    upper_wick = high - max(open_price, close)
    lower_wick = min(open_price, close) - low
    prior_volume = pd.to_numeric(frame["volume"].iloc[-21:-1], errors="coerce")
    average_volume = float(prior_volume.mean()) if len(prior_volume) else 0.0
    volume_ratio = float(current.get("volume", 0.0) or 0.0) / average_volume if average_volume > 0 else 0.0
    long_pinbar = (
        close > open_price
        and lower_wick >= max(body * 2.0, atr * 0.15)
        and upper_wick <= candle_range * 0.35
        and volume_ratio >= PYRAMID_VOLUME_SPIKE_RATIO
    )
    short_pinbar = (
        close < open_price
        and upper_wick >= max(body * 2.0, atr * 0.15)
        and lower_wick <= candle_range * 0.35
        and volume_ratio >= PYRAMID_VOLUME_SPIKE_RATIO
    )

    lookback = max(3, int(PYRAMID_STRUCTURE_LOOKBACK))
    local_window = frame.iloc[-(lookback + 2):-2]
    local_high = float(local_window["high"].max())
    local_low = float(local_window["low"].min())
    retest_buffer = zone_buffer
    breakout_retest_long = (
        float(previous["close"]) > local_high
        and low <= local_high + retest_buffer
        and close >= local_high
    )
    breakout_retest_short = (
        float(previous["close"]) < local_low
        and high >= local_low - retest_buffer
        and close <= local_low
    )

    grab_bars = max(1, int(PYRAMID_LIQUIDITY_GRAB_BARS))
    liquidity_grab = False
    for offset in range(2, min(grab_bars + 2, len(frame) - lookback)):
        breakout_bar = frame.iloc[-offset]
        range_before_breakout = frame.iloc[-(offset + lookback):-offset]
        prior_range_high = float(range_before_breakout["high"].max())
        prior_range_low = float(range_before_breakout["low"].min())
        returned_inside = (
            side == "LONG"
            and float(breakout_bar["high"]) > prior_range_high + atr * 0.50
            and close < prior_range_high
        ) or (
            side == "SHORT"
            and float(breakout_bar["low"]) < prior_range_low - atr * 0.50
            and close > prior_range_low
        )
        if returned_inside:
            liquidity_grab = True
            break
    if liquidity_grab:
        return {"signal": None, "reason": f"{side} 流動性掃單後收回前區間，忽略假突破", "liquidity_grab": True}

    trigger = entry_type
    fast_entry = entry_type in ("TROUGH_TURN", "PEAK_TURN", "CROSS_UP", "CROSS_DOWN")
    if not touched_ma_zone and not fast_entry:
        return {
            "signal": None,
            "reason": f"{side} 均線同側，等待價格回踩 MA3 附近再現價進場",
            "trend_zone": "BULLISH" if bullish_zone else "BEARISH",
            "pullback_touched": touched_ma_zone,
            "volume_ratio": volume_ratio,
        }

    projected_move_pct = PYRAMID_TSL_ATR_MULT * atr / max(close, 1e-12)
    round_trip_cost_pct = 2 * TAKER_FEE_RATE + SLIPPAGE_PCT
    if projected_move_pct < PYRAMID_MIN_COST_MULT * round_trip_cost_pct:
        return {
            "signal": None,
            "reason": f"預估波幅 {projected_move_pct:.3%} 不足交易成本 {round_trip_cost_pct:.3%} 的 {PYRAMID_MIN_COST_MULT:.1f} 倍",
            "cost_filter": True,
        }

    structural_stop = local_low if side == "LONG" else local_high
    return {
        "signal": side,
        "entry_type": trigger,
        "reason": f"融合版 1m {side}: {fast_signal.get('reason', trigger)}",
        "trend_zone": "PIVOT_REVERSAL" if pivot_override else ("BULLISH" if bullish_zone else "BEARISH"),
        "atr": atr,
        "ma3": ma3,
        "ma5": ma5,
        "ma25": ma25,
        "ma_distance_atr": ma_distance_atr,
        "pivot_ready": bool(fast_signal.get("pivot_confirmed")),
        "structural_stop": structural_stop,
        "volume_ratio": volume_ratio,
        "projected_move_pct": projected_move_pct,
    }


def detect_diminishing_global_exit(
    df: pd.DataFrame,
    side: str,
    entry_price: float = None,
    atr: float = None,
    adverse_reference_price: float = None,
) -> dict:
    """Priority-1 exit: CHoCH/cross plus a confirmed 0.8 ATR emergency reversal."""
    from core.config import (
        PYRAMID_EMERGENCY_REVERSAL_ATR_MULT,
        PYRAMID_STRUCTURE_LOOKBACK,
        PYRAMID_VOLUME_SPIKE_RATIO,
    )

    if df is None or len(df) < 30 or side not in ("LONG", "SHORT"):
        return {"exit": False, "reason": "資料不足"}
    frame = _prepare_pyramid_indicators(df)
    current = frame.iloc[-1]
    previous = frame.iloc[-2]
    close = float(current["close"])
    ma3 = frame["ma3"]
    ma5 = frame["ma5"]
    adverse_cross = (
        float(ma3.iloc[-2]) >= float(ma5.iloc[-2]) and float(ma3.iloc[-1]) < float(ma5.iloc[-1])
        if side == "LONG"
        else float(ma3.iloc[-2]) <= float(ma5.iloc[-2]) and float(ma3.iloc[-1]) > float(ma5.iloc[-1])
    )
    lookback = max(3, int(PYRAMID_STRUCTURE_LOOKBACK))
    structure = frame.iloc[-(lookback + 1):-1]
    structural_level = float(structure["low"].min()) if side == "LONG" else float(structure["high"].max())
    structure_broken = close < structural_level if side == "LONG" else close > structural_level

    # 急速反轉必須同時具備「至少 0.8 ATR 不利回撤」與一項反轉證據，
    # 避免只有單根影線或一般回踩就把趨勢單洗掉。
    valid_atr = max(float(atr or 0.0), close * 1e-12)
    reference = float(adverse_reference_price or entry_price or close)
    adverse_distance = max(0.0, reference - close) if side == "LONG" else max(0.0, close - reference)
    adverse_move_atr = adverse_distance / valid_atr if atr and float(atr) > 0 else 0.0
    ma3_turn = (
        float(ma3.iloc[-1]) < float(ma3.iloc[-2])
        if side == "LONG" else float(ma3.iloc[-1]) > float(ma3.iloc[-2])
    )
    open_price = float(current.get("open", close))
    adverse_candle = close < open_price if side == "LONG" else close > open_price
    prior_volume = pd.to_numeric(frame["volume"].iloc[-21:-1], errors="coerce") if "volume" in frame else pd.Series(dtype=float)
    average_volume = float(prior_volume.mean()) if len(prior_volume) else 0.0
    volume_ratio = float(current.get("volume", 0.0) or 0.0) / average_volume if average_volume > 0 else 0.0
    adverse_volume_spike = adverse_candle and volume_ratio >= PYRAMID_VOLUME_SPIKE_RATIO
    prior_structure_break = (
        close < float(previous["low"])
        if side == "LONG" else close > float(previous["high"])
    )
    emergency_confirmations = []
    if ma3_turn:
        emergency_confirmations.append("MA3反向")
    if adverse_volume_spike:
        emergency_confirmations.append("放量反向K")
    if prior_structure_break:
        emergency_confirmations.append("突破前一根結構")
    emergency_reversal = (
        adverse_move_atr >= PYRAMID_EMERGENCY_REVERSAL_ATR_MULT
        and bool(emergency_confirmations)
    )
    reasons = []
    if structure_broken:
        reasons.append(f"CHoCH 結構破位 {structural_level:.6g}")
    if adverse_cross:
        reasons.append("MA3/MA5 反向交叉")
    if emergency_reversal:
        reasons.append(
            f"緊急反轉 {adverse_move_atr:.2f}ATR + {'/'.join(emergency_confirmations)}"
        )
    return {
        "exit": bool(reasons),
        "reason": " + ".join(reasons),
        "structural_level": structural_level,
        "adverse_cross": adverse_cross,
        "structure_broken": structure_broken,
        "emergency_reversal": emergency_reversal,
        "adverse_move_atr": adverse_move_atr,
        "emergency_confirmations": emergency_confirmations,
    }


def detect_diminishing_profit_pivot(df: pd.DataFrame, side: str) -> dict:
    """Confirm a closed-candle peak for longs or trough for shorts."""
    from core.config import PYRAMID_PIVOT_LOOKBACK

    lookback = max(3, int(PYRAMID_PIVOT_LOOKBACK))
    if df is None or len(df) < lookback + 5 or side not in ("LONG", "SHORT"):
        return {"pivot": False, "reason": "峰谷資料不足"}
    frame = _prepare_pyramid_indicators(df)
    current = frame.iloc[-1]
    previous = frame.iloc[-2]
    ma3 = frame["ma3"]
    if any(pd.isna(ma3.iloc[index]) for index in (-3, -2, -1)):
        return {"pivot": False, "reason": "MA3峰谷尚未暖機"}

    history = frame.iloc[-(lookback + 2):-2]
    close = float(current["close"])
    open_price = float(current.get("open", close))
    if side == "LONG":
        extreme_confirmed = float(previous["high"]) >= float(history["high"].max())
        first_ma3_turn = float(ma3.iloc[-2]) >= float(ma3.iloc[-3]) and float(ma3.iloc[-1]) < float(ma3.iloc[-2])
        reversal_candle = close < open_price and close < float(ma3.iloc[-1])
        pivot_name = "頂峰"
        pivot_price = float(previous["high"])
    else:
        extreme_confirmed = float(previous["low"]) <= float(history["low"].min())
        first_ma3_turn = float(ma3.iloc[-2]) <= float(ma3.iloc[-3]) and float(ma3.iloc[-1]) > float(ma3.iloc[-2])
        reversal_candle = close > open_price and close > float(ma3.iloc[-1])
        pivot_name = "谷底"
        pivot_price = float(previous["low"])

    pivot = extreme_confirmed and first_ma3_turn and reversal_candle
    return {
        "pivot": pivot,
        "pivot_name": pivot_name,
        "pivot_price": pivot_price,
        "extreme_confirmed": extreme_confirmed,
        "first_ma3_turn": first_ma3_turn,
        "reversal_candle": reversal_candle,
        "reason": (
            f"{pivot_name}確認：近{lookback}根極值 + MA3首次反向 + 反向K收回MA3"
            if pivot else f"等待{pivot_name}完整確認"
        ),
    }

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


def _score_reversal_evidence(df: pd.DataFrame, side: str, atr: float) -> dict:
    """用量價、背離、布林收回及右側結構評估 1m 真峰谷。"""
    if df is None or len(df) < 22 or side not in ("LONG", "SHORT"):
        return {"confirmed": False, "score": 0, "strong_right_side": False,
                "reasons": ["峰谷證據資料不足"]}

    work = df.copy()
    for name in ("open", "high", "low", "close", "volume"):
        work[name] = pd.to_numeric(work[name], errors="coerce")
    close = work["close"]
    atr = max(float(atr or 0.0), abs(float(close.iloc[-1])) * 1e-6)
    if "rsi" not in work.columns:
        delta = close.diff()
        gain = delta.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
        loss = (-delta.clip(upper=0)).ewm(alpha=1 / 14, adjust=False).mean()
        work["rsi"] = 100 - 100 / (1 + gain / (loss + 1e-9))
    else:
        work["rsi"] = pd.to_numeric(work["rsi"], errors="coerce")
    macd = close.ewm(span=12, adjust=False).mean() - close.ewm(span=26, adjust=False).mean()
    work["_macd_hist"] = macd - macd.ewm(span=9, adjust=False).mean()
    boll_mid = close.rolling(20).mean()
    boll_std = close.rolling(20).std(ddof=0)
    work["_boll_upper"] = boll_mid + 2 * boll_std
    work["_boll_lower"] = boll_mid - 2 * boll_std

    last_two = work.iloc[-2:]
    pivot_idx = last_two["low"].idxmin() if side == "LONG" else last_two["high"].idxmax()
    pivot = work.loc[pivot_idx]
    history = work.iloc[-22:-2]
    reference_idx = history["low"].idxmin() if side == "LONG" else history["high"].idxmax()
    reference = work.loc[reference_idx]
    volume_average = float(history["volume"].mean())
    pivot_volume = float(pivot["volume"] or 0.0)
    candle_range = max(float(pivot["high"] - pivot["low"]), atr * 1e-6)
    lower_wick = max(min(float(pivot["open"]), float(pivot["close"])) - float(pivot["low"]), 0.0)
    upper_wick = max(float(pivot["high"]) - max(float(pivot["open"]), float(pivot["close"])), 0.0)
    wick_ratio = (lower_wick if side == "LONG" else upper_wick) / candle_range
    new_extreme = (float(pivot["low"]) <= float(reference["low"]) + atr * 0.10
                   if side == "LONG" else
                   float(pivot["high"]) >= float(reference["high"]) - atr * 0.10)
    score, reasons = 0, []

    if volume_average > 0 and pivot_volume >= volume_average * 1.40 and wick_ratio >= 0.35:
        score += 3
        reasons.append("爆量長下影止跌" if side == "LONG" else "爆量長上影出貨")
    reference_volume = float(reference["volume"] or 0.0)
    if new_extreme and reference_volume > 0 and pivot_volume <= reference_volume * 0.80:
        score += 2
        reasons.append("二腳底量縮、空壓枯竭" if side == "LONG" else "二次摸高量縮、買力枯竭")

    pivot_rsi = float(pivot["rsi"]) if not pd.isna(pivot["rsi"]) else 50.0
    reference_rsi = float(reference["rsi"]) if not pd.isna(reference["rsi"]) else 50.0
    rsi_divergence = (new_extreme and pivot_rsi >= reference_rsi + 1.5
                      if side == "LONG" else
                      new_extreme and pivot_rsi <= reference_rsi - 1.5)
    if rsi_divergence:
        score += 2
        reasons.append("RSI底背離" if side == "LONG" else "RSI頂背離")
    pivot_macd, reference_macd = float(pivot["_macd_hist"]), float(reference["_macd_hist"])
    macd_divergence = (new_extreme and pivot_macd > reference_macd
                       if side == "LONG" else
                       new_extreme and pivot_macd < reference_macd)
    if macd_divergence:
        score += 2
        reasons.append("MACD底背離" if side == "LONG" else "MACD頂背離")

    current, previous = work.iloc[-1], work.iloc[-2]
    if side == "LONG":
        pierced = bool((last_two["low"] < last_two["_boll_lower"]).fillna(False).any())
        reclaimed = pierced and float(current["close"]) >= float(current["_boll_lower"])
        strong_right_side = float(current["close"]) > float(previous["high"])
    else:
        pierced = bool((last_two["high"] > last_two["_boll_upper"]).fillna(False).any())
        reclaimed = pierced and float(current["close"]) <= float(current["_boll_upper"])
        strong_right_side = float(current["close"]) < float(previous["low"])
    if reclaimed:
        score += 2
        reasons.append("刺穿布林下軌後收回" if side == "LONG" else "刺穿布林上軌後收回")
    if strong_right_side:
        score += 2
        reasons.append("突破前一根高點、右側確認" if side == "LONG" else "跌破前一根低點、右側確認")

    key_window = work.iloc[-17:-2]
    near_key = (float(pivot["low"]) <= float(key_window["low"].min()) + atr * 0.25
                if side == "LONG" else
                float(pivot["high"]) >= float(key_window["high"].max()) - atr * 0.25)
    if near_key:
        score += 1
        reasons.append("接近15m歷史支撐" if side == "LONG" else "接近15m歷史阻力")
    return {"confirmed": bool(score >= 3 or strong_right_side), "score": score,
            "strong_right_side": strong_right_side,
            "reasons": reasons or ["無明顯量價、背離或結構證據"]}


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

    # 2. 判斷峰谷 (極速轉彎 - 改看 MA3)
    # 第一根初彎：只是「可能要彎」，需待第二根同向才確認
    is_peak_forming   = (ma3_curr < ma3_prev) and (ma3_prev > ma3_prev2)  # 第一根下彎
    is_trough_forming = (ma3_curr > ma3_prev) and (ma3_prev < ma3_prev2)  # 第一根上彎
    # 第二根確認：兩根連續同向——真頂峰 / 真谷底
    is_peak_confirmed   = (ma3_curr < ma3_prev) and (ma3_prev < ma3_prev2) and (ma3_prev2 > ma3_prev3)
    is_trough_confirmed = (ma3_curr > ma3_prev) and (ma3_prev > ma3_prev2) and (ma3_prev2 < ma3_prev3)
    # 保留舊名稱相容（CROSS 路徑使用）
    is_peak   = is_peak_confirmed
    is_trough = is_trough_confirmed

    # 3. 判斷斜率 (動能疲乏過濾 - 改看 MA3)
    ma5_slope = ma5_curr - ma5_prev
    ma3_slope = ma3_curr - ma3_prev
    ma3_slope_prev = ma3_prev - ma3_prev2
    
    # 判斷是否快到頂峰/谷底 (極速動能嚴重衰退，MA3 斜率萎縮超過 50%)
    approaching_peak = (ma3_slope > 0) and (ma3_slope < max(0, ma3_slope_prev) * 0.5)
    approaching_trough = (ma3_slope < 0) and (ma3_slope > min(0, ma3_slope_prev) * 0.5)

    adx_curr = float(df['adx'].iloc[-1])
    # 強勢單邊走勢使用 ADX 10；盤整或方向不明仍使用 ADX 15。
    trend_direction = 1 if ma5_curr > ma25_curr else -1 if ma5_curr < ma25_curr else 0
    _adx_min, strong_trend = get_dynamic_adx_floor(df, trend_direction)
    if adx_curr < _adx_min:
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
        if side == "LONG" and is_shooting_star_trap:
            return {"signal": None, "reason": "假突破過濾：多單確認K帶長上影線", "pivot_confirmed": False, "pivot_score": 0, "volume_ratio": volume_ratio}
        if side == "SHORT" and is_hammer_trap:
            return {"signal": None, "reason": "假突破過濾：空單確認K帶長下影線", "pivot_confirmed": False, "pivot_score": 0, "volume_ratio": volume_ratio}
        # 活動 K 的成交量仍在累積；峰谷第一彎已由 MA3 方向、K 色與影線確認，
        # 不再等待量能比例，以免已平倉卻錯過立即反手。
        if not allow_live_pivot and volume_ratio < min_confirmation_volume_ratio:
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
    confirmed_prior_lows = df['low'].iloc[-9:-3]
    confirmed_prior_highs = df['high'].iloc[-9:-3]
    pivot_low = float(df['low'].iloc[-3:].min())
    pivot_high = float(df['high'].iloc[-3:].max())
    confirmed_near_recent_low = bool(len(confirmed_prior_lows)) and pivot_low <= float(confirmed_prior_lows.min()) + atr * 0.25
    confirmed_near_recent_high = bool(len(confirmed_prior_highs)) and pivot_high >= float(confirmed_prior_highs.max()) - atr * 0.25
    confirmed_trough_recovery = ma3_curr - ma3_prev2
    confirmed_peak_decline = ma3_prev2 - ma3_curr
    live_fast_trough = (
        allow_live_pivot and is_trough_forming and is_green
        and last_close > ma3_curr and not is_shooting_star_trap
    )
    live_fast_peak = (
        allow_live_pivot and is_peak_forming and is_red
        and last_close < ma3_curr and not is_hammer_trap
    )
    clear_fast_trough = live_fast_trough or (
        is_trough_forming and is_green and last_close > ma3_curr
        and fast_trough_recovery >= atr * 0.20
        and candle_body >= atr * 0.45
        and volume_ratio >= min_confirmation_volume_ratio
        and near_recent_low and not is_shooting_star_trap
    )
    clear_fast_peak = live_fast_peak or (
        is_peak_forming and is_red and last_close < ma3_curr
        and fast_peak_decline >= atr * 0.20
        and candle_body >= atr * 0.45
        and volume_ratio >= min_confirmation_volume_ratio
        and near_recent_high and not is_hammer_trap
    )
    confirmed_trough = (
        is_trough_confirmed and is_green and last_close > ma3_curr
        and confirmed_trough_recovery >= atr * 0.4 and confirmed_near_recent_low
    )
    confirmed_peak = (
        is_peak_confirmed and is_red and last_close < ma3_curr
        and confirmed_peak_decline >= atr * 0.4 and confirmed_near_recent_high
    )

    trough_evidence = _score_reversal_evidence(df, "LONG", atr) if (clear_fast_trough or confirmed_trough) else None
    peak_evidence = _score_reversal_evidence(df, "SHORT", atr) if (clear_fast_peak or confirmed_peak) else None

    if clear_fast_trough or confirmed_trough:
        # 第一根 MA3 初彎最容易是假反轉；必須另有量價、背離、BOLL 或結構證據。
        # 連續兩根同向的 confirmed_trough 本身已是右側確認，不再額外拖延。
        trough_evidence_ready = trough_evidence["confirmed"] and (
            not allow_live_pivot or trough_evidence["score"] >= 4
            or trough_evidence["strong_right_side"]
        )
        if clear_fast_trough and not confirmed_trough and not trough_evidence_ready:
            return {
                "signal": None,
                "reason": f"假反轉過濾：谷底第一彎證據不足 ({', '.join(trough_evidence['reasons'])})",
                "pivot_confirmed": False,
                "pivot_score": 0,
                "pivot_evidence_score": trough_evidence["score"],
                "wait_right_side_confirmation": True,
            }
        rejected = reject_false_breakout("LONG")
        if rejected:
            return rejected
        evidence_text = "、".join(trough_evidence["reasons"])
        return {
            "signal": "LONG", "entry_type": "TROUGH_TURN",
            "reason": f"MA3 真谷底向上 (ADX={adx_curr:.1f}; {evidence_text}) → 立即開多",
            "atr": atr, "pivot_confirmed": True,
            "pivot_score": 95 if clear_fast_trough else 100,
            "pivot_evidence_score": trough_evidence["score"],
            "pivot_evidence": trough_evidence["reasons"],
            "right_side_confirmed": bool(confirmed_trough or trough_evidence["strong_right_side"]),
            "fast_pivot": bool(clear_fast_trough),
            "live_pivot": bool(allow_live_pivot),
            "ma_alignment": "ABOVE" if ma3_curr > ma25_curr and ma5_curr > ma25_curr else "BELOW" if ma3_curr < ma25_curr and ma5_curr < ma25_curr else "MIXED",
        }

    if clear_fast_peak or confirmed_peak:
        peak_evidence_ready = peak_evidence["confirmed"] and (
            not allow_live_pivot or peak_evidence["score"] >= 4
            or peak_evidence["strong_right_side"]
        )
        if clear_fast_peak and not confirmed_peak and not peak_evidence_ready:
            return {
                "signal": None,
                "reason": f"假反轉過濾：頂峰第一彎證據不足 ({', '.join(peak_evidence['reasons'])})",
                "pivot_confirmed": False,
                "pivot_score": 0,
                "pivot_evidence_score": peak_evidence["score"],
                "wait_right_side_confirmation": True,
            }
        rejected = reject_false_breakout("SHORT")
        if rejected:
            return rejected
        evidence_text = "、".join(peak_evidence["reasons"])
        return {
            "signal": "SHORT", "entry_type": "PEAK_TURN",
            "reason": f"MA3 真頂峰向下 (ADX={adx_curr:.1f}; {evidence_text}) → 立即開空",
            "atr": atr, "pivot_confirmed": True,
            "pivot_score": 95 if clear_fast_peak else 100,
            "pivot_evidence_score": peak_evidence["score"],
            "pivot_evidence": peak_evidence["reasons"],
            "right_side_confirmed": bool(confirmed_peak or peak_evidence["strong_right_side"]),
            "fast_pivot": bool(clear_fast_peak),
            "live_pivot": bool(allow_live_pivot),
            "ma_alignment": "ABOVE" if ma3_curr > ma25_curr and ma5_curr > ma25_curr else "BELOW" if ma3_curr < ma25_curr and ma5_curr < ma25_curr else "MIXED",
        }

    # 開倉方向只依 MA3、MA5 相對 MA25 的共同位置決定。
    # 風控（ADX、量能、長影線）仍須通過，但不得在均線下方做多或上方做空。
    both_below_ma25 = ma3_curr < ma25_curr and ma5_curr < ma25_curr
    both_above_ma25 = ma3_curr > ma25_curr and ma5_curr > ma25_curr

    if both_below_ma25:
        rejected = reject_false_breakout("SHORT")
        if rejected:
            return rejected
        return {
            "signal": "SHORT",
            "entry_type": "TREND_SHORT",
            "reason": f"MA3、MA5 同在 MA25 下方 (ADX={adx_curr:.1f}) → 現價開空",
            "atr": atr,
            "pivot_confirmed": False,
            "pivot_score": 85,
            "ma_alignment": "BELOW",
        }

    if both_above_ma25:
        rejected = reject_false_breakout("LONG")
        if rejected:
            return rejected
        return {
            "signal": "LONG",
            "entry_type": "TREND_LONG",
            "reason": f"MA3、MA5 同在 MA25 上方 (ADX={adx_curr:.1f}) → 現價開多",
            "atr": atr,
            "pivot_confirmed": False,
            "pivot_score": 85,
            "ma_alignment": "ABOVE",
        }

    return {
        "signal": None,
        "reason": "MA3、MA5 分居 MA25 兩側，等待方向一致",
        "pivot_confirmed": False,
        "pivot_score": 0,
        "ma_alignment": "MIXED",
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
