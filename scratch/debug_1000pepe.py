import asyncio
import sys
import pandas as pd
from core.engine import Engine
from core.services.entry_contract import evaluate_entry_contract

async def main():
    engine = Engine()
    await engine.initialize()
    klines = await engine.fetch_klines('1000PEPE/USDT', '1m', 10)
    print("Recent Klines for 1000PEPE:")
    print(klines[['timestamp', 'open', 'high', 'low', 'close', 'kc_upper', 'kc_middle', 'kc_lower']])
    
    # Run through the evaluator to see why it fails
    # simulate what symbol_runner does
    account = engine.paper_account
    from core.services.entry_contract import is_solid_push, is_bad_reverse, is_doji
    
    closed = klines[klines.is_closed].copy()
    print("\n--- Strict 3-bar rules evaluation ---")
    if len(closed) >= 3:
        bar1 = closed.iloc[-3]
        bar2 = closed.iloc[-2]
        bar3 = closed.iloc[-1]
        
        print("Bar 1 ([-3]):", dict(close=bar1.close, open=bar1.open, high=bar1.high, low=bar1.low, kc_upper=bar1.kc_upper, kc_lower=bar1.kc_lower))
        print("Bar 2 ([-2]):", dict(close=bar2.close, open=bar2.open, high=bar2.high, low=bar2.low))
        print("Bar 3 ([-1]):", dict(close=bar3.close, open=bar3.open, high=bar3.high, low=bar3.low))
        
        for side in ['LONG', 'SHORT']:
            if side == 'LONG':
                b1 = float(bar1.close) > float(bar1.kc_upper) and is_solid_push(bar1, 'LONG')
                b2 = is_solid_push(bar2, 'LONG') and not is_doji(bar2)
                b3 = not is_bad_reverse(bar3, 'LONG') and is_solid_push(bar3, 'LONG')
                print(f"LONG Check: Bar1={b1}, Bar2={b2}, Bar3={b3}")
                if float(bar1.close) > float(bar1.kc_upper):
                    print("  Bar1 close > kc_upper: True")
                else:
                    print(f"  Bar1 close > kc_upper: False (close={bar1.close}, kc_upper={bar1.kc_upper})")
                print(f"  Bar1 is_solid_push: {is_solid_push(bar1, 'LONG')}")
                print(f"  Bar2 is_solid_push: {is_solid_push(bar2, 'LONG')}")
                print(f"  Bar2 not is_doji: {not is_doji(bar2)}")
                print(f"  Bar3 not is_bad_reverse: {not is_bad_reverse(bar3, 'LONG')}")
                print(f"  Bar3 is_solid_push: {is_solid_push(bar3, 'LONG')}")
            else:
                b1 = float(bar1.close) < float(bar1.kc_lower) and is_solid_push(bar1, 'SHORT')
                b2 = is_solid_push(bar2, 'SHORT') and not is_doji(bar2)
                b3 = not is_bad_reverse(bar3, 'SHORT') and is_solid_push(bar3, 'SHORT')
                print(f"SHORT Check: Bar1={b1}, Bar2={b2}, Bar3={b3}")
                if float(bar1.close) < float(bar1.kc_lower):
                    print("  Bar1 close < kc_lower: True")
                else:
                    print(f"  Bar1 close < kc_lower: False (close={bar1.close}, kc_lower={bar1.kc_lower})")
                print(f"  Bar1 is_solid_push: {is_solid_push(bar1, 'SHORT')}")
                print(f"  Bar2 is_solid_push: {is_solid_push(bar2, 'SHORT')}")
                print(f"  Bar2 not is_doji: {not is_doji(bar2)}")
                print(f"  Bar3 not is_bad_reverse: {not is_bad_reverse(bar3, 'SHORT')}")
                print(f"  Bar3 is_solid_push: {is_solid_push(bar3, 'SHORT')}")
                
        diagnostics = {}
        res = evaluate_entry_contract(klines, diagnostics=diagnostics, account=account, symbol='1000PEPE/USDT')
        print(f"\nFinal result from evaluate_entry_contract: {res}")
        print(f"Diagnostics: {diagnostics}")

asyncio.run(main())
