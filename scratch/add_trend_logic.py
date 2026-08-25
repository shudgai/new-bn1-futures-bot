import re

# 1. Update core/indicators.py
with open('core/indicators.py', 'r') as f:
    content = f.read()

old_block_1 = """    # 優先級 2：真峰谷確認
    # 空頭趨勢中 (MA5 < MA25) 找真谷底
    if ma5_curr < ma25_curr:
        if is_trough and bull_confirm_score >= 2:
            return {
                "signal": "LONG",
                "entry_type": "TROUGH_TURN",
                "reason": f"空頭中 MA5 谷底且滿足真反轉 ({bull_confirm_score}項條件) → 轉向多單",
                "atr": atr,
                "pivot_confirmed": True,
                "pivot_score": 100,
            }
    
    # 多頭趨勢中 (MA5 > MA25) 找真峰頂
    if ma5_curr > ma25_curr:
        if is_peak and bear_confirm_score >= 2:
            return {
                "signal": "SHORT",
                "entry_type": "PEAK_TURN",
                "reason": f"多頭中 MA5 頂峰且滿足真反轉 ({bear_confirm_score}項條件) → 轉向空單",
                "atr": atr,
                "pivot_confirmed": True,
                "pivot_score": 100,
            }"""

new_block_1 = """    # 優先級 2：真峰谷確認 & 優先級 3：順勢上車
    # 空頭趨勢中 (MA5 < MA25)
    if ma5_curr < ma25_curr:
        if is_trough and bull_confirm_score >= 2:
            return {
                "signal": "LONG",
                "entry_type": "TROUGH_TURN",
                "reason": f"空頭中 MA5 谷底且滿足真反轉 ({bull_confirm_score}項條件) → 轉向多單",
                "atr": atr,
                "pivot_confirmed": True,
                "pivot_score": 100,
            }
        elif is_peak:
            # 沒形成谷底，反而形成峰頂，代表反彈結束、空頭延續
            return {
                "signal": "SHORT",
                "entry_type": "TREND_SHORT",
                "reason": "空頭中 MA5 反彈後轉下 (回調結束) → 順勢空單",
                "atr": atr,
                "pivot_confirmed": False,
                "pivot_score": 50,
            }
    
    # 多頭趨勢中 (MA5 > MA25)
    if ma5_curr > ma25_curr:
        if is_peak and bear_confirm_score >= 2:
            return {
                "signal": "SHORT",
                "entry_type": "PEAK_TURN",
                "reason": f"多頭中 MA5 頂峰且滿足真反轉 ({bear_confirm_score}項條件) → 轉向空單",
                "atr": atr,
                "pivot_confirmed": True,
                "pivot_score": 100,
            }
        elif is_trough:
            # 沒形成峰頂，反而形成谷底，代表回踩結束、多頭延續
            return {
                "signal": "LONG",
                "entry_type": "TREND_LONG",
                "reason": "多頭中 MA5 回踩後轉上 (回踩結束) → 順勢多單",
                "atr": atr,
                "pivot_confirmed": False,
                "pivot_score": 50,
            }"""

content = content.replace(old_block_1, new_block_1)
with open('core/indicators.py', 'w') as f:
    f.write(content)

# 2. Update core/engine.py
with open('core/engine.py', 'r') as f:
    content2 = f.read()

content2 = content2.replace(
    'if cr_entry_type in ("TROUGH_TURN", "PEAK_TURN"):',
    'if cr_entry_type in ("TROUGH_TURN", "PEAK_TURN", "TREND_LONG", "TREND_SHORT"):'
)

content2 = content2.replace(
    'if cr_entry_type in ("PEAK_TURN", "TROUGH_TURN"):',
    'if cr_entry_type in ("PEAK_TURN", "TROUGH_TURN", "TREND_LONG", "TREND_SHORT"):'
)

with open('core/engine.py', 'w') as f:
    f.write(content2)

