import re

with open("core/services/strategies/unified_entry_strategy.py", "r") as f:
    content = f.read()

# Replace the beginning of check_streamlined_entry_signal
old_str = """    if df is None or len(df) < 5:
        return False, "WAIT_INSUFFICIENT_DATA", {}

    # 檢查 MA3 / MA15 交叉開倉 (具有最高優先權)
    ma_cross_signal = check_ma_cross_entry(df)
    if ma_cross_signal and ma_cross_signal.get("side") == side:
        return True, f"🚀 [MA Cross] {side}: 均線交叉動能確認", ma_cross_signal

    # 取已收線的 K 線數據
    # df.iloc[-1] 是未收線(Live)，iloc[-2] 是剛收線(Current Confirmed)，iloc[-3] 是前一根收線(Prev Confirmed)
    current_candle = df.iloc[-2]
    prev_candle = df.iloc[-3]"""

new_str = """    from core.services.candle_data import closed_entry_candles
    df_closed = closed_entry_candles(df) if df is not None else None
    if df_closed is None or len(df_closed) < 5:
        return False, "WAIT_INSUFFICIENT_DATA", {}

    # 檢查 MA3 / MA15 交叉開倉 (具有最高優先權)
    ma_cross_signal = check_ma_cross_entry(df_closed)
    if ma_cross_signal and ma_cross_signal.get("side") == side:
        return True, f"🚀 [MA Cross] {side}: 均線交叉動能確認", ma_cross_signal

    # 取已收線的 K 線數據 (徹底屏蔽未收線的 Tick)
    current_candle = df_closed.iloc[-1]
    prev_candle = df_closed.iloc[-2]"""

content = content.replace(old_str, new_str)

# Now fix the hard blocks to NOT use live_candle = df.iloc[-1] but current_candle instead
old_live = """        live_candle = df.iloc[-1]
        live_close = float(live_candle['close'])
        live_open = float(live_candle['open'])
        live_ma7 = float(live_candle.get('ma7', live_candle.get('ma5', live_candle.get('ma3', live_close))))
        live_atr = float(live_candle.get('atr', 0))"""

new_live = """        live_candle = current_candle
        live_close = close
        live_open = open_p
        live_ma7 = float(live_candle.get('ma7', live_candle.get('ma5', live_candle.get('ma3', live_close))))
        live_atr = current_atr_val"""

content = content.replace(old_live, new_live)

with open("core/services/strategies/unified_entry_strategy.py", "w") as f:
    f.write(content)

print("Patched unified_entry_strategy.py")
