import json
import pandas as pd
from core.strategy import SuperTrendKeltnerStrategy
import asyncio
from core.engine import TradingEngine

async def run_analysis():
    engine = TradingEngine()
    
    try:
        with open('data/paper_account.json', 'r') as f:
            trades = json.load(f).get('trades', [])
    except Exception:
        return
        
    trades.sort(key=lambda x: x.get('id', 0))
    
    # Get all EXIT_ADVERSE_ABNORMAL_BODY closes
    abnormal_closes = []
    
    for t in trades:
        if 'CLOSE' in t.get('action', '') and 'ABNORMAL_BODY' in t.get('reason', ''):
            abnormal_closes.append(t)
            
    print(f"Total ABNORMAL exits found: {len(abnormal_closes)}")
    
    records = []
    
    for ct in abnormal_closes:
        # Find matching open
        ot = next((t for t in reversed(trades) if t.get('id') < ct['id'] and t.get('symbol') == ct['symbol'] and t.get('side') == ct['side'] and 'OPEN' in t.get('action')), None)
        if not ot: continue
        
        # Determine if premature or protective based on subsequent open
        subsequent_open = next((t for t in trades if t.get('id') >= ct['id'] and t.get('symbol') == ct['symbol'] and t.get('side') == ct['side'] and 'OPEN' in t.get('action')), None)
        
        cat = 'UNRESOLVED'
        if subsequent_open and (subsequent_open['id'] - ct['id']) <= 60000 * 60:
            price_reopen = subsequent_open['price']
            price_open = ot['price']
            side = 1 if ot['side'] == 'LONG' else -1
            hyp_pnl = side * (price_reopen - price_open) * ot['qty']
            sl = float(ot.get('sl', ot.get('initial_sl', 0)))
            hyp_r = hyp_pnl / abs((price_open - sl) * ot['qty']) if sl > 0 and price_open != sl else 0
            
            if hyp_r > -1.0:
                cat = 'PREMATURE'
            else:
                cat = 'PROTECTIVE'
        else:
            cat = 'PROTECTIVE' # If no reopen, assume it was a protective cut
            
        # Get frame data at exit time
        # The exit time is ct['id'] (ms)
        # We fetch klines ending at that minute
        # We need historical klines. We can use holdout_v1_klines or fetch directly.
        df = await engine.fetch_klines(ct['symbol'], limit=100, keep_live=True)
        # We don't have historical fetch easily in engine unless we use CCXT with specific timestamp.
        # But wait, this is paper trading, so the timestamps are from recent live data! We can just fetch historical using CCXT.
        ohlcv = await engine.exchange.fetch_ohlcv(ct['symbol'], '1m', limit=100, params={'endTime': ct['id'] + 60000})
        df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        
        # Calculate indicators
        st = SuperTrendKeltnerStrategy()
        df = st.compute_indicators(df)
        
        exit_row = df.iloc[-1]
        prev_row = df.iloc[-2]
        prev2_row = df.iloc[-3]
        
        # Basic state
        side_val = 1 if ct['side'] == 'LONG' else -1
        
        # MA15 alignment
        ma15 = float(exit_row['ma15'])
        prev_ma15 = float(prev_row['ma15'])
        ma15_direction = 1 if ma15 > prev_ma15 else -1
        ma15_aligned = (ma15_direction == side_val)
        
        # Price vs MA15
        price = ct['price']
        price_vs_ma15_aligned = (side_val * (price - ma15)) > 0
        
        # KC middle alignment
        kc_mid = float(exit_row['kc_middle'])
        prev_kc_mid = float(prev_row['kc_middle'])
        kc_mid_aligned = (kc_mid > prev_kc_mid) if side_val == 1 else (kc_mid < prev_kc_mid)
        
        # MA5 alignment
        ma5 = float(exit_row['ma5'])
        prev_ma5 = float(prev_row['ma5'])
        ma5_aligned = (ma5 > prev_ma5) if side_val == 1 else (ma5 < prev_ma5)
        
        # adverse candle body ATR
        atr = float(prev_row['atr'])
        adverse_body = side_val * (float(exit_row['open']) - price)
        adverse_body_atr = adverse_body / atr if atr > 0 else 0
        
        record = {
            'symbol': ct['symbol'],
            'side': ct['side'],
            'cat': cat,
            'ma15_aligned': ma15_aligned,
            'price_vs_ma15_aligned': price_vs_ma15_aligned,
            'kc_mid_aligned': kc_mid_aligned,
            'ma5_aligned': ma5_aligned,
            'adverse_body_atr': adverse_body_atr,
            'pnl': ct['pnl']
        }
        records.append(record)
        print(f"[{cat}] {ct['symbol']} {ct['side']} - MA15 Aligned: {ma15_aligned}, Price vs MA15: {price_vs_ma15_aligned}, KC Mid: {kc_mid_aligned}, MA5: {ma5_aligned}, Body ATR: {adverse_body_atr:.2f}")

asyncio.run(run_analysis())
