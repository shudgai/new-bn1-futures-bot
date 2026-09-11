import re
import math
import time
import pandas as pd
from typing import Dict, Any, List
from core.config import (
    MA5_EARLY_CONFIRM_SCANS, MA5_REVERSAL_MIN_ATR_MULT, MA5_FAST_MIN_ATR_MULT,
    MA5_FAST_MAX_ATR_MULT, MA5_FAST_MIN_VOLUME_RATIO, MA5_EXIT_MIN_HOLD_SEC,
    MA5_EXIT_MIN_ADVERSE_PCT, MA5_EXIT_MIN_ADVERSE_ATR_MULT, EXHAUSTION_SNIPER_GRACE_SEC,
    MA5_BOTTOM_MIN_HOLD_SEC, HISTORY_RECENCY_DECAY, MIN_FRESHNESS_SCORE,
    ENTRY_FRESHNESS_SCORE_MAX, MIN_SCORE_THRESHOLD, ADX_QUALITY_MIN, DEFAULT_SYMBOLS
)

def format_signal_progress(
    symbol: str,
    signal: dict,
    current_direction: str,
) -> str:
    """將策略結果壓縮成適合系統日誌的一行進度。"""
    eligible = signal.get("eligible")
    score = signal.get("score")
    if score is None:
        match = re.search(r"Score\((\d+)\)", signal.get("reason", ""))
        if match:
            score = int(match.group(1))
        else:
            score = signal.get("btc_adjusted_score") or signal.get("raw_score")
    direction_text = {"LONG": "多單", "SHORT": "空單"}.get(
        current_direction, "雙向"
    )
    action = signal.get("action", "HOLD")
    reason = signal.get("reason", "")
    score_components = signal.get("score_components") or {}
    component_text = ""
    if score_components:
        component_text = (
            f"KC{score_components.get('kc', 0)}/量{score_components.get('volume', 0)}/"
            f"RSI{score_components.get('rsi', 0)}/新鮮{score_components.get('freshness', 0)}/"
            f"品質{score_components.get('quality', 0)}"
        )
    diagnostics = signal.get("diagnostics") or {}
    raw_score = signal.get("raw_score")
    btc_score = signal.get("btc_adjusted_score", score)
    history_score = signal.get("history_adjusted_score")
    if eligible is False:
        score_text = "資格未通過"
    elif history_score is not None and history_score != btc_score:
        score_text = f"原{raw_score}→BTC{btc_score}→歷史{history_score}分"
    elif raw_score is not None and btc_score != raw_score:
        score_text = f"原{raw_score}→BTC{btc_score}分"
    else:
        score_text = f"{int(score or 0)}分"
    if signal.get("history_blocked"):
        stage = (
            f"歷史績效降分後取消（{component_text}）"
            if component_text else "歷史績效降分後取消"
        )
    elif action in ("BUY", "SELL"):
        stage = "符合立即開倉"
    elif action == "WAIT_PULLBACK":
        stage = signal.get(
            "confirmation_reason", "等待回調至KC區後二次確認"
        )
    elif "BTC_1h_ST_JustFlipped" in reason:
        stage = "BTC剛翻轉緩衝期過濾"
    elif "BTC_Regime" in reason:
        stage = "BTC大盤方向不符"
    elif "Symbol_1h_ST" in reason:
        stage = (
            f"個幣1h趨勢不符（5m={diagnostics.get('st_direction_5m')}/"
            f"1h={diagnostics.get('st_direction_1h')}）"
        )
    elif "1h_EMA50" in reason:
        stage = (
            f"1h EMA50方向不符（價格{diagnostics.get('price', 0):.6g}/"
            f"EMA50={diagnostics.get('ema_50_1h', 0):.6g}）"
        )
    elif "ADX_Too_Low" in reason:
        stage = f"ADX過低{diagnostics.get('adx', 0):.1f}<10過濾"
    elif "Score_Low" in reason:
        stage = f"分數不足（{component_text}）" if component_text else "分數不足"
    elif "Entry_Quality_Too_Low" in reason:
        quality_match = re.search(r"Entry_Quality_Too_Low\(([\d.]+<[\d.]+)\)", reason)
        stage = (
            f"進場品質不足{quality_match.group(1)}"
            if quality_match else "進場品質不足"
        )
    elif "Freshness_Too_Stale" in reason or "SuperTrend_Stale" in reason:
        stale = re.search(r"(?:Freshness_Too_Stale|SuperTrend_Stale)\((\d+)(?:bars)?\)", reason)
        stage = f"訊號新鮮度不足{stale.group(1)}根" if stale else "訊號新鮮度不足"
    elif "ADX_Declining_Exhaustion" in reason:
        adx_match = re.search(r"ADX_Declining_Exhaustion\(([\d.]+)<([\d.]+)\)", reason)
        stage = (
            f"ADX {adx_match.group(1)}←{adx_match.group(2)}且低於{ADX_QUALITY_MIN:g}，動能衰退過濾"
            if adx_match else "ADX低於品質底線且動能衰退過濾"
        )
    elif "Pullback_Range_Too_Narrow" in reason:
        stage = "KC至EMA20回踩空間不足0.10 ATR"
    elif "Price_Overextended" in reason:
        overext_match = re.search(r"Price_Overextended\(([\d.]+x_ATR)\)", reason)
        stage = f"價格乖離過大{overext_match.group(1)}" if overext_match else "價格乖離過大"
    elif "1h_Trend_Declining" in reason:
        stage = "大週期動能衰退過濾"
    elif "Mandatory_Fail: KC_Breakout_Unconfirmed" in reason:
        stage = "待KC突破"
    elif "EMA20" in reason or "1h_Trend" in reason:
        stage = "趨勢方向不符"
    elif "ATR_Too_High" in reason:
        atr_match = re.search(r"ATR_Too_High\(([\d.]+%)\)", reason)
        stage = f"波動過大{atr_match.group(1)}過濾" if atr_match else "波動過大過濾"
    elif "ATR_Too_Low" in reason:
        atr_match = re.search(r"ATR_Too_Low\(([\d.]+%)", reason)
        stage = f"波動過低{atr_match.group(1)}過濾" if atr_match else "波動過低過濾"
    elif "Volume" in reason:
        stage = f"量能不足（現量/均量={diagnostics.get('volume_ratio', 0):.2f}）"
    elif "RSI_Overbought" in reason:
        stage = f"RSI過熱{diagnostics.get('rsi', 0):.1f}>68"
    elif "RSI_Oversold" in reason:
        stage = f"RSI過冷{diagnostics.get('rsi', 0):.1f}<32"
    elif "RSI" in reason:
        stage = f"RSI方向不足（RSI={diagnostics.get('rsi', 0):.1f}）"
    else:
        stage = "條件未完成"
    coin = symbol.replace("/USDT", "")
    return f"{coin} {direction_text} {score_text},{stage}"

def format_ma5_wait_detail(df: pd.DataFrame, side: str) -> str:
    if df is None or df.empty or "ma5" not in df.columns:
        return "等待MA5拐頭轉彎"
    ma5 = df["ma5"].dropna()
    if len(ma5) < 4:
        return f"等待MA5有效資料（{len(ma5)}/4根）"

    values = [float(value) for value in ma5.iloc[-4:]]
    prev3, prev2, prev, curr = values
    row = df.iloc[-1]
    atr = max(float(row.get("atr") or 0.0), 1e-12)
    volume = float(row.get("volume") or 0.0)
    volume_ma = float(row.get("vol_ma_20") or 0.0)
    volume_ratio = volume / volume_ma if volume_ma > 0 else 0.0
    rsi = float(row.get("rsi") or 0.0)

    if side == "LONG":
        regular_shape = prev2 < prev3 and prev > prev2 and curr > prev
        first_turn = prev < prev2 and curr > prev
        still_retracing = curr <= prev
        no_pullback = prev3 <= prev2 <= prev <= curr
        turn_distance = curr - prev
        retrace_label = "回撤中，等待向上轉彎"
    else:
        regular_shape = prev2 > prev3 and prev < prev2 and curr < prev
        first_turn = prev > prev2 and curr < prev
        still_retracing = curr >= prev
        no_pullback = prev3 >= prev2 >= prev >= curr
        turn_distance = prev - curr
        retrace_label = "反彈中，等待向下轉彎"

    if regular_shape:
        regular_turn_atr = abs(curr - prev2) / atr
        stage = (
            f"兩根已轉向但拐幅{regular_turn_atr:.2f}ATR"
            f"<{MA5_REVERSAL_MIN_ATR_MULT:.2f}ATR"
        )
    elif first_turn:
        fast_turn_atr = max(0.0, turn_distance) / atr
        if fast_turn_atr < MA5_FAST_MIN_ATR_MULT:
            stage = (
                f"已轉向第1根，但拐幅{fast_turn_atr:.2f}ATR"
                f"<{MA5_FAST_MIN_ATR_MULT:.2f}ATR"
            )
        elif fast_turn_atr > MA5_FAST_MAX_ATR_MULT:
            stage = (
                f"已轉向第1根，但拐幅{fast_turn_atr:.2f}ATR"
                f">{MA5_FAST_MAX_ATR_MULT:.2f}ATR，等待第2根"
            )
        else:
            stage = (
                f"已轉向第1根，但量能{volume_ratio:.2f}x"
                f"<{MA5_FAST_MIN_VOLUME_RATIO:.2f}x，等待第2根"
            )
    elif still_retracing:
        stage = retrace_label
    elif no_pullback:
        stage = "尚未回撤，MA5仍沿原方向"
    else:
        stage = "峰谷型態尚未完成"

    ma_text = "→".join(f"{value:.6g}" for value in values)
    return (
        f"{stage}｜MA5 {ma_text}｜量{volume_ratio:.2f}x/"
        f"快線{MA5_FAST_MIN_VOLUME_RATIO:.2f}x｜RSI {rsi:.1f}"
    )

def ma5_exit_ready(
    position: dict, trigger: dict, mark_price: float, now: float
) -> tuple[bool, str]:
    entry_price = float(position.get("entry_price") or 0.0)
    raw_opened_at = position.get("open_timestamp")
    opened_at = float(raw_opened_at if raw_opened_at is not None else now)
    age_sec = max(0.0, now - opened_at)
    if age_sec < MA5_EXIT_MIN_HOLD_SEC:
        return False, f"持倉{age_sec / 60:.1f}分<{MA5_EXIT_MIN_HOLD_SEC / 60:.0f}分"
    if entry_price <= 0:
        return False, "缺少進場價"

    adverse_pct = (
        (entry_price - mark_price) / entry_price
        if position.get("side") == "LONG"
        else (mark_price - entry_price) / entry_price
    )
    atr_pct = max(0.0, float(trigger.get("atr") or 0.0) / entry_price)
    required_pct = max(
        MA5_EXIT_MIN_ADVERSE_PCT,
        atr_pct * MA5_EXIT_MIN_ADVERSE_ATR_MULT,
    )
    if adverse_pct < required_pct:
        return (
            False,
            f"逆向{max(adverse_pct, 0.0):.2%}<門檻{required_pct:.2%}",
        )
    return True, f"持倉{age_sec / 60:.1f}分，逆向{adverse_pct:.2%}"

def bottom_entry_grace(position: dict, now: float) -> tuple[bool, float]:
    entry_mode = position.get("entry_mode")
    if entry_mode not in ("MA5_BOTTOM_LIMIT", "MA3_PIVOT", "EXHAUSTION_SNIPER", "PIVOT_TURN"):
        return False, 0.0
    opened_at = float(position.get("open_timestamp") or now)
    age_sec = max(0.0, now - opened_at)
    if entry_mode in ("EXHAUSTION_SNIPER", "PIVOT_TURN"):
        return age_sec < EXHAUSTION_SNIPER_GRACE_SEC, age_sec
    if entry_mode == "MA3_PIVOT":
        return age_sec < 180, age_sec
    return age_sec < MA5_BOTTOM_MIN_HOLD_SEC, age_sec

def trend_follow_breach(df: pd.DataFrame, side: str) -> dict:
    if df.empty or len(df) < 20:
        return {"breached": False}
    work = df.copy()
    high_low = work["high"] - work["low"]
    high_cp = (work["high"] - work["close"].shift()).abs()
    low_cp = (work["low"] - work["close"].shift()).abs()
    work["tr"] = pd.concat([high_low, high_cp, low_cp], axis=1).max(axis=1)
    work["atr"] = work["tr"].rolling(window=14).mean()
    work["ema_20"] = work["close"].ewm(span=20, adjust=False).mean()
    last_bar, prev_bar = work.iloc[-1], work.iloc[-2]
    close1, close2 = float(last_bar["close"]), float(prev_bar["close"])
    ema1, ema2 = float(last_bar["ema_20"]), float(prev_bar["ema_20"])
    atr1 = float(last_bar["atr"]) if not pd.isna(last_bar["atr"]) else close1 * 0.015
    atr2 = float(prev_bar["atr"]) if not pd.isna(prev_bar["atr"]) else close2 * 0.015
    buffer1 = max(close1 * 0.003, 0.5 * atr1)
    buffer2 = max(close2 * 0.003, 0.5 * atr2)
    breached = (
        close1 < ema1 - buffer1 and close2 < ema2 - buffer2
        if side == "LONG"
        else close1 > ema1 + buffer1 and close2 > ema2 + buffer2
    )
    return {
        "breached": bool(breached),
        "close1": close1, "close2": close2,
        "ema1": ema1, "ema2": ema2,
        "buffer1": buffer1, "buffer2": buffer2,
    }

def history_adjusted_score(raw_score: int, performance: dict) -> tuple[int, float]:
    return int(raw_score), 1.0

def entry_filter_outcome(signal: dict) -> str:
    reason = str(signal.get("reason", ""))
    if signal.get("history_blocked"):
        return "history_score_block"
    if signal.get("action") == "WAIT_PULLBACK":
        return "pullback_candidate_ready"
    mapping = (
        ("BTC_1h_ST_JustFlipped", "btc_flip_block"),
        ("Symbol_1h_ST", "symbol_1h_mismatch"),
        ("1h_EMA50", "ema50_1h_mismatch"),
        ("ADX_Too_Low", "adx_too_low"),
        ("ATR_Too_High", "atr_too_high"),
        ("ATR_Too_Low", "atr_too_low"),
        ("RSI_Overbought", "rsi_overbought"),
        ("RSI_Oversold", "rsi_oversold"),
        ("KC_Breakout_Unconfirmed", "kc_unconfirmed"),
        ("Entry_Quality_Too_Low", "quality_too_low"),
        ("Freshness_Too_Stale", "freshness_too_stale"),
        ("ADX_Declining_Exhaustion", "adx_declining_below_floor"),
        ("Pullback_Range_Too_Narrow", "pullback_room_narrow"),
        ("Price_Overextended", "price_overextended"),
        ("1h_Trend_Declining", "trend_1h_declining"),
        ("Score_Low", "score_low"),
    )
    for marker, outcome in mapping:
        if marker in reason:
            return outcome
    return "other_hold"

def shadow_ready(signal: dict) -> bool:
    return (
        signal.get("action") == "WAIT_PULLBACK"
        and not signal.get("history_blocked")
        and int(signal.get("score") or 0) >= MIN_SCORE_THRESHOLD
    )
