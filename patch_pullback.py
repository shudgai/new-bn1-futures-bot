patch = '''
    # -------------------------------------------------------------------------
    # 軌道 B-3：強趨勢回踩右側確認進場 (Pullback Re-entry)
    # 場景：強趨勢擴張中，出現一根回調棒（觸及上軌/MA3 支撐），
    #       下一根實體陽線突破前根高點 → 確認支撐有效，右側跟進。
    # -------------------------------------------------------------------------
    prev2_open  = float(prev_2["open"])
    prev2_close_val = float(prev_2["close"])
    prev2_high  = float(prev_2["high"])
    prev2_low   = float(prev_2["low"])

    if is_strong_bull_trend and side == "LONG" and is_bullish:
        # prev_2 是回調棒（陰線或縮量小陽），且低點觸及 KC 上軌或 MA3 支撐
        prev2_is_pullback = (prev2_close_val <= prev2_open) or (abs(prev2_close_val - prev2_open) < 0.3 * current_atr)
        pullback_touched   = (prev2_low <= kc_upper_prev2 * 1.002) or (ma3_prev2 > 0 and prev2_low <= ma3_prev2 * 1.002)
        # prev_1 是實體陽線，且收盤突破 prev_2 的高點（右側確認）
        right_side_confirm = is_bullish and (body_ratio >= 0.4) and (prev_close > prev2_high)
        # 進場後仍有足夠空間
        space_to_upper = kc_upper_prev1 - prev_close
        has_space = space_to_upper >= buffer_threshold

        if prev2_is_pullback and pullback_touched and right_side_confirm and has_space:
            return True, "[PULLBACK_RE_ENTRY] Strong Bull Trend Pullback Confirmed LONG", {"action": "ENTER"}

    if is_strong_bear_trend and side == "SHORT" and is_bearish:
        # prev_2 是回調棒（陽線或縮量小陰），且高點觸及 KC 下軌或 MA3 壓力
        prev2_is_pullback = (prev2_close_val >= prev2_open) or (abs(prev2_close_val - prev2_open) < 0.3 * current_atr)
        pullback_touched   = (prev2_high >= kc_lower_prev2 * 0.998) or (ma3_prev2 > 0 and prev2_high >= ma3_prev2 * 0.998)
        # prev_1 是實體陰線，且收盤跌破 prev_2 的低點（右側確認）
        right_side_confirm = is_bearish and (body_ratio >= 0.4) and (prev_close < prev2_low)
        # 進場後仍有足夠空間
        space_to_lower = prev_close - kc_lower_prev1
        has_space = space_to_lower >= buffer_threshold

        if prev2_is_pullback and pullback_touched and right_side_confirm and has_space:
            return True, "[PULLBACK_RE_ENTRY] Strong Bear Trend Pullback Confirmed SHORT", {"action": "ENTER"}

'''

anchor = '    if side == "LONG" and is_bullish:\n        if slope_ma15 < 0 and slope_middle < 0:'

with open("core/services/strategies/unified_entry_strategy.py", "r") as f:
    content = f.read()

if anchor not in content:
    print("ANCHOR NOT FOUND")
else:
    content = content.replace(anchor, patch + '    if side == "LONG" and is_bullish:\n        if slope_ma15 < 0 and slope_middle < 0:')
    with open("core/services/strategies/unified_entry_strategy.py", "w") as f:
        f.write(content)
    print("DONE")
