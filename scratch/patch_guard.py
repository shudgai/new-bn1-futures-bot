with open("core/services/manual_order_service.py", "r") as f:
    content = f.read()

content = content.replace('def manual_entry_guard(symbol, side, frame):', 'def manual_entry_guard(symbol, side, frame):\n    import logging')
content = content.replace('return "POSITION_EXISTS：帳戶已有持倉，禁止重複開倉"', 'logging.getLogger("uvicorn.error").info("RETURNING POSITION_EXISTS"); return "POSITION_EXISTS：帳戶已有持倉，禁止重複開倉"')
content = content.replace('return "INVALID_MARKET_DATA：行情資料尚未載入"', 'logging.getLogger("uvicorn.error").info("RETURNING 行情資料尚未載入"); return "INVALID_MARKET_DATA：行情資料尚未載入"')
content = content.replace('return "INVALID_MARKET_DATA：行情或已收線指標無效"', 'logging.getLogger("uvicorn.error").info("RETURNING 行情或已收線指標無效"); return "INVALID_MARKET_DATA：行情或已收線指標無效"')
content = content.replace('return "INVALID_MARKET_DATA：缺少已收線 ATR／KC／MA15"', 'logging.getLogger("uvicorn.error").info("RETURNING 缺少已收線"); return "INVALID_MARKET_DATA：缺少已收線 ATR／KC／MA15"')

with open("core/services/manual_order_service.py", "w") as f:
    f.write(content)
