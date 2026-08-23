import re

with open('core/strategy.py', 'r') as f:
    content = f.read()

# Replace evaluate_signal
new_evaluate_signal = """    def evaluate_signal(
        self, df: pd.DataFrame,
        ema_50_1h: float = None,
        trend_1h_declining: bool = False,
        st_direction_1h: int = None,
        btc_st_direction_1h: int = 0,
        btc_st_flip_age: int = 999,
        symbol: str = None,
        parameter_overrides: dict = None,
        indicators_precomputed: bool = False,
    ) -> dict:
        if len(df) < 5:
            return {"action": "HOLD", "reason": "Not enough data", "eligible": False, "score_stage": "ELIGIBILITY"}
            
        if not indicators_precomputed:
            df = self.compute_indicators(df)
            
        curr = df.iloc[-1]
        
        # 取得最近3根K線的 MA7 (使用收盤價計算的 moving average)
        if 'ma7' not in df.columns:
            df['ma7'] = df['close'].rolling(window=7, min_periods=1).mean()
            
        ma7_curr = float(df['ma7'].iloc[-1])
        ma7_prev = float(df['ma7'].iloc[-2])
        ma7_prev2 = float(df['ma7'].iloc[-3])
        
        price = float(curr['close'])
        atr = float(curr['atr']) if 'atr' in curr and not pd.isna(curr['atr']) else price * 0.015
        ema_20 = float(curr['ema_20']) if 'ema_20' in curr and not pd.isna(curr['ema_20']) else price
        kc_upper = float(curr.get('kc_upper', price))
        kc_lower = float(curr.get('kc_lower', price))
        
        # 判斷谷底 (Trough) -> 多單進場
        # ma7[-3] > ma7[-2] and ma7[-1] > ma7[-2]
        is_trough = (ma7_prev2 > ma7_prev) and (ma7_curr > ma7_prev)
        
        # 判斷山峰 (Peak) -> 空單進場
        # ma7[-3] < ma7[-2] and ma7[-1] < ma7[-2]
        is_peak = (ma7_prev2 < ma7_prev) and (ma7_curr < ma7_prev)
        
        if is_trough:
            return {
                "action": "WAIT_PULLBACK", "side": "LONG",
                "price": price, "atr": atr,
                "kc_upper": kc_upper, "kc_lower": kc_lower, "score": 99,
                "target_zone": price, "ema_20": ema_20,
                "pullback_depth": 0.0,
                "pullback_distance_atr": 0.0,
                "entry_mode": "CURRENT_MAKER",
                "confirmation_reason": "MA7 Trough Detected",
                "btc_regime_mode": "SYNC",
                "btc_allocation_factor": 1.0,
                "reason": "MA7_Pivot_Trough_LONG(99)"
            }
        elif is_peak:
            return {
                "action": "WAIT_PULLBACK", "side": "SHORT",
                "price": price, "atr": atr,
                "kc_upper": kc_upper, "kc_lower": kc_lower, "score": 99,
                "target_zone": price, "ema_20": ema_20,
                "pullback_depth": 0.0,
                "pullback_distance_atr": 0.0,
                "entry_mode": "CURRENT_MAKER",
                "confirmation_reason": "MA7 Peak Detected",
                "btc_regime_mode": "SYNC",
                "btc_allocation_factor": 1.0,
                "reason": "MA7_Pivot_Peak_SHORT(99)"
            }
            
        return {"action": "HOLD", "reason": "Wait for MA7 Pivot", "eligible": False, "score_stage": "ELIGIBILITY"}

"""

# Replace confirm_pullback_entry
new_confirm = """    def confirm_pullback_entry(
        self, df: pd.DataFrame, side: str, ema_1h: float = None, trend_1h_declining: bool = False,
        btc_st_direction_1h: int = 0, btc_st_flip_age: int = 999, symbol: str = None,
    ) -> dict:
        # 直接確認通過，無條件放行
        return {"confirmed": True, "reason": "MA7 Pivot always confirmed", "diagnostics": {}}

"""

# Replace check_simple_ma7_exit
new_exit = """def check_simple_ma7_exit(df: pd.DataFrame, position: dict) -> dict:
    if len(df) < 5:
        return {"exit": False, "reason": "K線資料不足"}
        
    if 'ma7' not in df.columns:
        df['ma7'] = df['close'].rolling(window=7, min_periods=1).mean()
        
    ma7_curr = float(df['ma7'].iloc[-1])
    ma7_prev = float(df['ma7'].iloc[-2])
    ma7_prev2 = float(df['ma7'].iloc[-3])
    
    side = position.get("side", "").upper()
    
    # 判斷谷底 (Trough) -> 空單平倉
    is_trough = (ma7_prev2 > ma7_prev) and (ma7_curr > ma7_prev)
    
    # 判斷山峰 (Peak) -> 多單平倉
    is_peak = (ma7_prev2 < ma7_prev) and (ma7_curr < ma7_prev)
    
    if side == "LONG" and is_peak:
        return {"exit": True, "reason": "MA7 Peak -> Exit LONG"}
    elif side == "SHORT" and is_trough:
        return {"exit": True, "reason": "MA7 Trough -> Exit SHORT"}
        
    return {"exit": False, "reason": "Wait for MA7 opposite pivot"}
"""

# Apply regex replacements
content = re.sub(r'    def evaluate_signal\(.*?    def confirm_pullback_entry\(', new_evaluate_signal + '    def confirm_pullback_entry(', content, flags=re.DOTALL)
content = re.sub(r'    def confirm_pullback_entry\(.*?def detect_simple_ma7_signal\(', new_confirm + 'def detect_simple_ma7_signal(', content, flags=re.DOTALL)
content = re.sub(r'def check_simple_ma7_exit\(df: pd\.DataFrame, position: dict\) -> dict:.*', new_exit, content, flags=re.DOTALL)

with open('core/strategy.py', 'w') as f:
    f.write(content)
