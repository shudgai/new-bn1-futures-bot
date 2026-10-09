import math

def calculate_sl(side, entry_p, qty, fee, slippage, floor_u, tick_size=0.0001):
    sign = 1.0 if side == "LONG" else -1.0
    exec_price = (floor_u / qty + entry_p * (sign + fee)) / (sign - fee)
    sl_price = exec_price / (1.0 - sign * slippage)
    
    if side == "LONG":
        sl_price = math.ceil(sl_price / tick_size - 1e-9) * tick_size
    else:
        sl_price = math.floor(sl_price / tick_size + 1e-9) * tick_size
        
    execution = sl_price * (1.0 - sign * slippage)
    gross_pnl = sign * (execution - entry_p) * qty
    total_fee = (entry_p + execution) * qty * fee
    net_pnl = gross_pnl - total_fee
    
    return sl_price, net_pnl

print("LONG:", calculate_sl("LONG", 1.0, 100, 0.0005, 0.0001, 2.0))
print("SHORT:", calculate_sl("SHORT", 1.0, 100, 0.0005, 0.0001, 2.0))
