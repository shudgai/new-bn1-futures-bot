"""Pure MA3 turn signal helpers kept behind their legacy facade names."""

from typing import Any

import pandas as pd


def detect_turn_entry(
    rules: Any, df: pd.DataFrame, live_price: float | None = None,
) -> dict:
    """
    不看任何K線圖，只看MA3的線。
    只要 MA3 呈尖端 (V/倒V) 或 小梯形，就轉向。
    若 MA3 呈大V括弧 (較寬的轉折)，且近4根內有2根以上同色K線，也轉向。
    """
    if len(df) < 5:
        return {"detected": False, "reason": "Data too short"}

    # 取消所有K線限制，直接計算 MA3
    if 'ma3' not in df.columns:
        df['ma3'] = df['close'].rolling(window=3).mean()
    ma3_series = df['ma3'].dropna()
    if len(ma3_series) < 5:
        return {"detected": False, "reason": "MA3 not ready"}

    # 為了滿足「一有轉折立刻開倉」的需求，改回使用正在跳動的即時 K 線 (iloc[-1])
    ma3_curr = float(ma3_series.iloc[-1])
    ma3_prev = float(ma3_series.iloc[-2])
    ma3_prev2 = float(ma3_series.iloc[-3])
    ma3_prev3 = float(ma3_series.iloc[-4])
    ma3_prev4 = float(ma3_series.iloc[-5])

    price = float(live_price) if live_price is not None and float(live_price) > 0 else float(df['close'].iloc[-1])
    if price <= 0:
        return {"detected": False, "reason": "Invalid price"}

    c1 = df.iloc[-1]
    c2 = df.iloc[-2]
    c3 = df.iloc[-3]
    c4 = df.iloc[-4]
    greens = sum([1 for c in [c1, c2, c3, c4] if float(c['close']) > float(c['open'])])
    reds = sum([1 for c in [c1, c2, c3, c4] if float(c['close']) < float(c['open'])])

    is_valley = False
    is_peak = False
    valley_reason = ""
    peak_reason = ""

    c1_is_green = float(c1['close']) >= float(c1['open'])
    c1_is_red = float(c1['close']) <= float(c1['open'])

    # 1. 尖端 (V 型谷底)
    if ma3_prev2 > ma3_prev and ma3_curr > ma3_prev:
        is_valley = True
        valley_reason = "MA3 尖端谷底"
    # 2. 小梯形 (底平緩：左側下降，底部平/微升降，右側上升)
    elif ma3_prev3 > ma3_prev2 and ma3_curr > ma3_prev and (ma3_prev >= ma3_prev2):
        is_valley = True
        valley_reason = "MA3 小梯形谷底"
    # 3. 大V括弧 + 2根以上綠K
    elif ma3_curr > ma3_prev and ma3_prev4 > ma3_prev3 and greens >= 2:
        is_valley = True
        valley_reason = f"MA3 大V括弧谷底 (附{greens}根綠K)"
    # 4. 提早試單：K線出現反轉跡象 (突破MA3 或 吞噬上一根紅K)
    elif rules.MA5_EARLY_ENTRY_ENABLED and c1_is_green and (
        (float(c1['close']) > ma3_curr and float(c2['close']) < ma3_prev) or
        (float(c2['close']) < float(c2['open']) and float(c1['close']) > float(c2['open']))
    ):
        is_valley = True
        valley_reason = "K線提早反轉跡象(突破MA3或吞噬)"

    # 1. 尖端 (倒 V 型峰頂)
    if ma3_prev2 < ma3_prev and ma3_curr < ma3_prev:
        is_peak = True
        peak_reason = "MA3 尖端峰頂"
    # 2. 小梯形 (頂平緩：左側上升，頂部平/微升降，右側下降)
    elif ma3_prev3 < ma3_prev2 and ma3_curr < ma3_prev and (ma3_prev <= ma3_prev2):
        is_peak = True
        peak_reason = "MA3 小梯形峰頂"
    # 3. 大V括弧 + 2根以上紅K
    elif ma3_curr < ma3_prev and ma3_prev4 < ma3_prev3 and reds >= 2:
        is_peak = True
        peak_reason = f"MA3 大V括弧峰頂 (附{reds}根紅K)"
    # 4. 提早試單：K線出現反轉跡象 (跌破MA3 或 吞噬上一根綠K)
    elif rules.MA5_EARLY_ENTRY_ENABLED and c1_is_red and (
        (float(c1['close']) < ma3_curr and float(c2['close']) > ma3_prev) or
        (float(c2['close']) > float(c2['open']) and float(c1['close']) < float(c2['open']))
    ):
        is_peak = True
        peak_reason = "K線提早反轉跡象(跌破MA3或吞噬)"

    # 這裡的 signal_score 隨便給 100 即可，不用看 K 線實體比例
    signal_score = 100
    atr14 = price * 0.015 # 給個默認 atr，因為取消了原始計算

    if is_valley:
        full_wave = rules.evaluate_minimum_kc_wave(df, -2, "TROUGH_TURN")
        if not full_wave["passed"]:
            return {"detected": False, "reason": full_wave["reason"]}
        return {
            "detected": True,
            "side": "LONG",
            "score": signal_score,
            "price": price,
            "atr": atr14,
            "reason": valley_reason,
        }
    elif is_peak:
        full_wave = rules.evaluate_minimum_kc_wave(df, -2, "PEAK_TURN")
        if not full_wave["passed"]:
            return {"detected": False, "reason": full_wave["reason"]}
        return {
            "detected": True,
            "side": "SHORT",
            "score": signal_score,
            "price": price,
            "atr": atr14,
            "reason": peak_reason,
        }

    return {"detected": False, "reason": "No MA3 valley/peak"}


def evaluate_turn_exit(
    rules: Any, df: pd.DataFrame, position: dict,
) -> dict:
    """
    不看任何K線圖，只看MA3的線。
    只要 MA3 呈尖端 (V/倒V)、小梯形、或大V括弧，就平倉並轉向。
    Long: 當 MA3 出現峰頂 (Peak) 時平倉。
    Short: 當 MA3 出現谷底 (Valley) 時平倉。
    """
    if len(df) < 5:
        return {"close": False, "reason": "Data too short"}

    if 'ma3' not in df.columns:
        df['ma3'] = df['close'].rolling(window=3).mean()
    ma3_series = df['ma3'].dropna()
    if len(ma3_series) < 5:
        return {"close": False, "reason": "MA3 not ready"}

    # 為了滿足「一有轉折立刻平倉」的需求，改回使用正在跳動的即時 K 線 (iloc[-1])
    ma3_curr = float(ma3_series.iloc[-1])
    ma3_prev = float(ma3_series.iloc[-2])
    ma3_prev2 = float(ma3_series.iloc[-3])
    ma3_prev3 = float(ma3_series.iloc[-4])
    ma3_prev4 = float(ma3_series.iloc[-5])

    c1 = df.iloc[-1]
    c2 = df.iloc[-2]
    c3 = df.iloc[-3]
    c4 = df.iloc[-4]
    greens = sum([1 for c in [c1, c2, c3, c4] if float(c['close']) > float(c['open'])])
    reds = sum([1 for c in [c1, c2, c3, c4] if float(c['close']) < float(c['open'])])

    side = position.get("side")
    is_valley = False
    is_peak = False
    reason_text = ""

    c1_is_green = float(c1['close']) >= float(c1['open'])
    c1_is_red = float(c1['close']) <= float(c1['open'])

    # 1. 尖端 (V 型谷底)
    if ma3_prev2 > ma3_prev and ma3_curr > ma3_prev:
        is_valley = True
        reason_text = "MA3 尖端谷底向上轉折，空單平倉"
    # 2. 小梯形 (底平緩)
    elif ma3_prev3 > ma3_prev2 and ma3_curr > ma3_prev and (ma3_prev >= ma3_prev2):
        is_valley = True
        reason_text = "MA3 小梯形谷底向上轉折，空單平倉"
    # 3. 大V括弧 + 2根以上綠K
    elif ma3_curr > ma3_prev and ma3_prev4 > ma3_prev3 and greens >= 2:
        is_valley = True
        reason_text = f"MA3 大V括弧谷底(附{greens}根綠K)向上轉折，空單平倉"

    # 1. 尖端 (倒 V 型峰頂)
    if ma3_prev2 < ma3_prev and ma3_curr < ma3_prev:
        is_peak = True
        reason_text = "MA3 尖端峰頂向下轉折，多單平倉"
    # 2. 小梯形 (頂平緩)
    elif ma3_prev3 < ma3_prev2 and ma3_curr < ma3_prev and (ma3_prev <= ma3_prev2):
        is_peak = True
        reason_text = "MA3 小梯形峰頂向下轉折，多單平倉"
    # 3. 大V括弧 + 2根以上紅K
    elif ma3_curr < ma3_prev and ma3_prev4 < ma3_prev3 and reds >= 2:
        is_peak = True
        reason_text = f"MA3 大V括弧峰頂(附{reds}根紅K)向下轉折，多單平倉"

    if side == "LONG" and is_peak:
        full_wave = rules.evaluate_minimum_kc_wave(df, -2, "PEAK_TURN")
        if not full_wave["passed"]:
            return {"close": False, "reason": full_wave["reason"]}
        return {"close": True, "reason": reason_text}
    elif side == "SHORT" and is_valley:
        full_wave = rules.evaluate_minimum_kc_wave(df, -2, "TROUGH_TURN")
        if not full_wave["passed"]:
            return {"close": False, "reason": full_wave["reason"]}
        return {"close": True, "reason": reason_text}

    return {"close": False, "reason": "MA3 尚未出現反向轉折"}
