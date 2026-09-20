import re

with open("core/services/strategies/unified_entry_strategy.py", "r") as f:
    content = f.read()

# Add check_structural_alignment function
alignment_func = """def check_structural_alignment(side: str, prev_1: pd.Series, prev_2: pd.Series, current_atr: float) -> tuple[bool, str]:
    ma15_prev1 = float(prev_1.get('ma15', 0))
    ma15_prev2 = float(prev_2.get('ma15', 0))
    kc_mid_prev1 = float(prev_1.get("kc_middle", prev_1.get("ema_20", 0)))
    kc_mid_prev2 = float(prev_2.get("kc_middle", prev_2.get("ema_20", 0)))
    close_prev1 = float(prev_1['close'])
    
    slope_ma15 = ma15_prev1 - ma15_prev2
    slope_kc_mid = kc_mid_prev1 - kc_mid_prev2
    
    if side == "LONG":
        ma15_ok = slope_ma15 > 0.05 * current_atr
        kc_mid_ok = slope_kc_mid > 0
        alignment_ok = (close_prev1 > kc_mid_prev1) and (kc_mid_prev1 >= ma15_prev1)
        distance_ok = (close_prev1 - ma15_prev1) > (current_atr * 0.5)
        
        if not ma15_ok: return False, "FILTERED_STRUCTURE: MA15 not trending up"
        if not kc_mid_ok: return False, "FILTERED_STRUCTURE: KC Middle not trending up"
        if not alignment_ok: return False, "FILTERED_STRUCTURE: MA Alignment Bullish failed"
        if not distance_ok: return False, "FILTERED_STRUCTURE: Price too close to MA15 (<0.5 ATR)"
        return True, "OK"
        
    elif side == "SHORT":
        ma15_ok = slope_ma15 < -0.05 * current_atr
        kc_mid_ok = slope_kc_mid < 0
        alignment_ok = (close_prev1 < kc_mid_prev1) and (kc_mid_prev1 <= ma15_prev1)
        distance_ok = (ma15_prev1 - close_prev1) > (current_atr * 0.5)
        
        if not ma15_ok: return False, "FILTERED_STRUCTURE: MA15 not trending down"
        if not kc_mid_ok: return False, "FILTERED_STRUCTURE: KC Middle not trending down"
        if not alignment_ok: return False, "FILTERED_STRUCTURE: MA Alignment Bearish failed"
        if not distance_ok: return False, "FILTERED_STRUCTURE: Price too close to MA15 (<0.5 ATR)"
        return True, "OK"
        
    return False, "INVALID_SIDE"

def check_streamlined_entry_signal(df, side: str, live_price: float, **kwargs) -> tuple[bool, str, dict]:"""

content = re.sub(r'def check_streamlined_entry_signal\(df, side: str, live_price: float, \*\*kwargs\) -> tuple\[bool, str, dict\]:', alignment_func, content)


# Now modify the logic inside check_streamlined_entry_signal
# Remove the old strong trend definitions:
#     # --- 強勢趨勢判定 (Strong Trend Detection) ---
#     is_strong_bear_trend = (slope_ma15 < -0.05 * current_atr) and (slope_middle < -0.05 * current_atr)
#     is_strong_bull_trend = (slope_ma15 > 0.05 * current_atr) and (slope_middle > 0.05 * current_atr)
# And replace with nothing since we handle it in check_structural_alignment
content = re.sub(
    r'    # --- 強勢趨勢判定 \(Strong Trend Detection\) ---\n    is_strong_bear_trend = \(slope_ma15 < -0.05 \* current_atr\) and \(slope_middle < -0.05 \* current_atr\)\n    is_strong_bull_trend = \(slope_ma15 > 0.05 \* current_atr\) and \(slope_middle > 0.05 \* current_atr\)\n',
    '',
    content
)

# Insert the global structural check right after Track V block
pattern = r'(            if high_in_recent_3 and structure_break and ma_cross_down and rsi_rebound:\n                return True, "\[V_REVERSAL_ENTRY\] Extreme Top V-Shape SHORT", \{"action": "ENTER"\}\n)'
replacement = r"""\1
    # =========================================================================
    # 全局結構審查 (Global Structural Gatekeeper)
    # =========================================================================
    # 除了 V型轉折 (逆勢摸底) 以外，所有順勢進場皆須受結構與均線引力過濾
    is_aligned, reject_reason = check_structural_alignment(side, prev_1, prev_2, current_atr)
    if not is_aligned:
        return False, reject_reason, {}
"""
content = re.sub(pattern, replacement, content)

# Now fix the tracks that relied on is_strong_bull_trend / is_strong_bear_trend or exemptions

# Track 0:
#         if side == "LONG" and is_strong_bull_trend:
#         elif side == "SHORT" and is_strong_bear_trend:
# -> Replace with just side checks because structural gatekeeper already ensures trend.
content = re.sub(
    r'if side == "LONG" and is_strong_bull_trend:',
    'if side == "LONG":',
    content
)
content = re.sub(
    r'elif side == "SHORT" and is_strong_bear_trend:',
    'elif side == "SHORT":',
    content
)

# Track A:
#         if side == "LONG" and is_bullish:
#             if is_strong_bear_trend:
#                 return False, "FILTERED_EXTREME_COUNTER_TREND: Fighting Strong Bearish Trend", {}
#             return True, "[SPECIAL_ENTRY] Extreme Impulse LONG (MARKET)", {"action": "ENTER"}
# -> We don't need to check counter trend, if it passed structure gate, it's already aligned with the trend!
content = re.sub(
    r'        if side == "LONG" and is_bullish:\n            if is_strong_bear_trend:\n                return False, "FILTERED_EXTREME_COUNTER_TREND: Fighting Strong Bearish Trend", \{\}\n            return True, "\[SPECIAL_ENTRY\] Extreme Impulse LONG \(MARKET\)", \{"action": "ENTER"\}',
    r'        if side == "LONG" and is_bullish:\n            return True, "[SPECIAL_ENTRY] Extreme Impulse LONG (MARKET)", {"action": "ENTER"}',
    content
)
content = re.sub(
    r'        elif side == "SHORT" and is_bearish:\n            if is_strong_bull_trend:\n                return False, "FILTERED_EXTREME_COUNTER_TREND: Fighting Strong Bullish Trend", \{\}\n            return True, "\[SPECIAL_ENTRY\] Extreme Impulse SHORT \(MARKET\)", \{"action": "ENTER"\}',
    r'        elif side == "SHORT" and is_bearish:\n            return True, "[SPECIAL_ENTRY] Extreme Impulse SHORT (MARKET)", {"action": "ENTER"}',
    content
)

# Track P:
#             # 豁免 MA15 斜率與軌道邊緣空間限制，只要在多頭半場 (中軌上方) 且動能破前高即開倉
#             if is_solid_breakout and broke_platform and prev_close > kc_mid_prev1:
# -> The exemption comment is obsolete, but the logic condition is fine to keep, because it already passed the gatekeeper.
# We will just change the comment for clarity.
content = re.sub(
    r'# 豁免 MA15 斜率與軌道邊緣空間限制，只要在多頭半場 \(中軌上方\) 且動能破前高即開倉',
    '# 平台破位判定 (已通過全局結構審查)',
    content
)
content = re.sub(
    r'# 豁免 MA15 斜率與軌道邊緣空間限制，只要在空頭半場 \(中軌下方\) 且動能破前低即開倉',
    '# 平台破位判定 (已通過全局結構審查)',
    content
)

# Track R:
#             # 趨勢鎖定 (MA15 強烈向上且價格在中軌上方)
#             trend_up = (slope_ma15 > 0.05 * current_atr) and (prev_close > kc_mid_prev1)
#             # 飽滿實體
# -> trend_up is now redundant but we can keep it as True or remove it.
# Actually let's just leave it, it's a subset of the gatekeeper anyway. Same for trend_down.

with open("core/services/strategies/unified_entry_strategy.py", "w") as f:
    f.write(content)
print("Patch applied successfully")
