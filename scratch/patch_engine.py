with open("core/engine.py", "r") as f:
    content = f.read()

old_block = """            # 過濾掉像「龙虾/USDT」這種含有中文的模擬幣種，避免 CCXT fetch_tickers 整批報錯崩潰
            import re
            valid_monitored = [
                sym for sym in monitored_symbols 
                if re.match(r'^[A-Za-z0-9/:-]+$', sym)
            ]"""

new_block = """            # 允許所有幣種（包含中文如「龙虾/USDT」）
            valid_monitored = monitored_symbols"""

content = content.replace(old_block, new_block)

with open("core/engine.py", "w") as f:
    f.write(content)
