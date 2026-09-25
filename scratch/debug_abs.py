with open("core/services/exits/profit_protection_service.py", "r") as f:
    content = f.read()

old_block = """        result = protection(position, price, self.fee, self.slippage, frame)
        with open("data/protection_debug.log", "a") as dbgf:
            dbgf.write(f"protection() called for {position.get('symbol')}! result={result}\\n")"""

new_block = """        result = protection(position, price, self.fee, self.slippage, frame)
        with open("/tmp/protection_debug.log", "a") as dbgf:
            dbgf.write(f"protection() called for {position.get('symbol')}! result={result}\\n")"""

content = content.replace(old_block, new_block)

with open("core/services/exits/profit_protection_service.py", "w") as f:
    f.write(content)
