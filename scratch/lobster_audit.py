import json, asyncio
from core.services.market_data import fetch_live_indicators
from core.services.exits.trend_hold_evaluator import evaluate_trend_hold
from core.services.exits.realtime_profit_exit import cached_tick_indicators

async def main():
    pos = {
        'symbol': '龙虾/USDT',
        'side': 'SHORT',
        'entry_price': 0.0520148,
        'entry_snapshot': {'closed_bar': 1790909940000.0} # Just a guess for entry
    }
    
    # We want to see what trend_hold returns at 11:01:15
    exit_ms = 1790910075372.0
    current_price = 0.05173517
    
    # Let's fetch history up to that point
    frame = await fetch_live_indicators('龙虾/USDT', '1m', 100)
    # Filter frame to simulate what was available at 11:01:15
    # The last closed bar at 11:01:15 would be the 11:00 bar (1790910000000)
    frame = frame[frame['timestamp'] <= 1790910060000.0]
    
    print(f"Frame tail:\n{frame.tail(3)}")
    snapshot, atr = cached_tick_indicators(frame, current_price, exit_ms)
    print(f"Snapshot reason: {snapshot.get('reason')}")
    print(f"Snapshot source: {snapshot.get('snapshot_source')}")
    print(f"Fallback used: {snapshot.get('fallback_used')}")
    
    status, reason = evaluate_trend_hold(pos, snapshot, current_price)
    print(f"TREND_HOLD status: {status}")
    print(f"trend_hold_reason: {reason}")
    print(f"MA5: {snapshot.get('ma5')}")
    print(f"MA15: {snapshot.get('ma15')}")
    print(f"KC_MID: {snapshot.get('kc_middle')}")
    
    from core.services.exits.peak_trailing_exit import evaluate_peak_trailing
    pos['peak_trailing_state'] = {'peak_net_pnl': 1.9632}
    decision = evaluate_peak_trailing(pos, current_price, snapshot, atr, fee=0.000, slippage=0.0)
    print(f"Peak decision: {decision}")
    
asyncio.run(main())
