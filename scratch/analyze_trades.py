import json

try:
    with open('data/paper_account.json', 'r') as f:
        data = json.load(f)
    
    trades = data.get('trades', [])
    
    # Map OPEN trades
    open_trades = {}
    for t in trades:
        if 'OPEN' in t.get('action', ''):
            key = f"{t.get('symbol')}_{t.get('side')}"
            if key not in open_trades:
                open_trades[key] = []
            open_trades[key].append(t)
            
    print("| 平倉時間 | 幣種 | 方向 | 虧損金額 | 開倉位置 (Entry Reason) | 虧損平倉原因 (Exit Reason) |")
    print("|---|---|---|---|---|---|")
    total_loss = 0
    for t in trades:
        action = t.get('action', '')
        if 'CLOSE' in action:
            pnl = t.get('pnl', 0)
            if pnl < 0:
                time = t.get('time', '')
                symbol = t.get('symbol', '')
                side = t.get('side', '')
                
                # Exit reason
                exit_reason = t.get('exit_reason', 'Unknown')
                if exit_reason == 'Unknown' and 'exit_context' in t:
                    exit_reason = t.get('exit_context', {}).get('reason', 'Unknown')
                
                # Find matching OPEN trade
                entry_trade = None
                key = f"{symbol}_{side}"
                if key in open_trades and len(open_trades[key]) > 0:
                    possible_opens = [o for o in open_trades[key] if o.get('time') < time]
                    if possible_opens:
                        entry_trade = possible_opens[-1]
                
                entry_loc = "Unknown"
                
                if entry_trade:
                    entry_loc = entry_trade.get('reason', 'Unknown')
                
                total_loss += pnl
                print(f"| {time} | {symbol} | {side} | {pnl:.4f} | {entry_loc} | {exit_reason} |")
                
except Exception as e:
    print(f"Error: {e}")
