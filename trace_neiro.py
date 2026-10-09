import json
import numpy as np
import pandas as pd
import math
import asyncio
from core.strategy import SuperTrendKeltnerStrategy
import core.services.exits.peak_trailing_exit as pte
from core.engine import TradingEngine

engine = TradingEngine()

async def get_historical_data(symbol, start_time):
    limit = 1500
    ohlcv = await engine.exchange.fetch_ohlcv(symbol, '1m', limit=limit, params={'startTime': start_time - 60000 * 5})
    data = []
    for k in ohlcv:
        data.append({
            'timestamp': int(k[0]), 'open': float(k[1]), 'high': float(k[2]), 
            'low': float(k[3]), 'close': float(k[4]), 'volume': float(k[5]),
            'is_closed': True
        })
    df = pd.DataFrame(data)
    if len(df) == 0: return None
    df = SuperTrendKeltnerStrategy().compute_indicators(df)
    df.attrs['timeframe_ms'] = 60000
    return df

async def main():
    try:
        with open('data/paper_account.json', 'r') as f:
            paper_data = json.load(f)
            trades = paper_data.get('trades', [])
    except:
        trades = []

    active = {}
    pairings = []
    for t in sorted(trades, key=lambda x: x.get('id', 0)):
        key = (t.get('symbol'), t.get('side'))
        if t.get('action') in ('OPEN_LONG', 'OPEN_SHORT'):
            active.setdefault(key, []).append(t)
        elif t.get('action') in ('CLOSE_LONG', 'CLOSE_SHORT'):
            if key in active and len(active[key]) > 0:
                pairings.append({'open': active[key].pop(0), 'close': t})

    target_pair = None
    for p in pairings:
        if p['open']['symbol'] == '龙虾/USDT' and p['open']['side'] == 'LONG' and p['open']['time'][:19] == '10/02 20:59:07':
            target_pair = p
            break
            
    if not target_pair:
        print("Target pair not found.")
        return
        
    ot = target_pair['open']
    ct = target_pair['close']
    
    df = await get_historical_data(ot['symbol'], ct['id'])
    if df is None: return

    position = dict(ot)
    position['side'] = ot['side']
    position['open_timestamp'] = ot['id']
    position['entry_price'] = ot['price']
    position['initial_sl'] = ot.get('sl', ot.get('initial_sl', 0))
    position['sl'] = position['initial_sl']
    
    sign = 1 if position['side'] == 'LONG' else -1
    entry = float(position['entry_price'])
    
    exit_ms = ct['id']
    start_idx = -1
    for i in range(len(df)):
        if df.iloc[i]['timestamp'] <= exit_ms < df.iloc[i]['timestamp'] + 60000:
            start_idx = i
            break
            
    print(f"TRACING {ot['symbol']} {ot['side']} {ot['time'][:19]}")
    print(f"Entry: {entry}, Initial SL: {position['initial_sl']}")
    
    first_3atr = None
    max_favorable = entry
    max_gain_atr = 0
    parabolic_armed = None
    ma3_armed = None
    
    for i in range(start_idx, len(df)):
        live_bar = df.iloc[i]
        bar_ms = live_bar['timestamp']
        
        if i == 0: continue
        closed_bar = df.iloc[i-1]
        
        snapshot = {
            'quote_ms': bar_ms,
            'live_open': live_bar['open'],
            'live_bar_ms': bar_ms,
            'closed_bar_ms': closed_bar['timestamp'],
            'atr': closed_bar['atr'],
            'ma15': closed_bar['ma15'],
            'ma5': closed_bar['ma5'],
            'ma3': closed_bar['ma3'],
            'last_ma5': df.iloc[i-2]['ma5'] if i >= 2 else None,
            'kc_middle': closed_bar['kc_middle']
        }
        
        ticks = [live_bar['open'], live_bar['low'], live_bar['high'], live_bar['close']] 
            
        for price in ticks:
            # Update MFE
            if sign == 1: max_favorable = max(max_favorable, price)
            else: max_favorable = min(max_favorable, price)
            
            curr_gain = sign * (max_favorable - entry)
            scale = float(snapshot['atr'])
            if scale > 0:
                curr_gain_atr = curr_gain / scale
                max_gain_atr = max(max_gain_atr, curr_gain_atr)
                if curr_gain_atr >= 3.0 and not first_3atr:
                    first_3atr = bar_ms
                    print(f"Reached 3 ATR at {pd.to_datetime(bar_ms, unit='ms')}")
                    
            sl = float(position.get('sl', 0))
            if sl > 0:
                if (position['side'] == 'LONG' and price <= sl) or (position['side'] == 'SHORT' and price >= sl):
                    print(f"EXIT_INITIAL_ATR_HARD_STOP triggered at {pd.to_datetime(bar_ms, unit='ms')} price {price}")
                    return
                    
            res = pte.evaluate_peak_trailing(position, price, snapshot, atr=snapshot['atr'])
            state = position.get(pte.STATE_KEY, {})
            
            curr_drawdown_atr = 0
            if scale > 0:
                curr_drawdown_atr = (state.get('peak_price', entry) - price) / scale if sign == 1 else (price - state.get('peak_price', entry)) / scale
                max_drawdown_atr = max(max_drawdown_atr if 'max_drawdown_atr' in locals() else 0, curr_drawdown_atr)
            
            if scale > 0 and (sign*(state.get('peak_price', entry)-entry))/scale >= 3.0:
                drawdown_atr = (state['peak_price'] - price) / scale if sign == 1 else (price - state['peak_price']) / scale
                ma5 = snapshot.get('ma5')
                last_ma5 = snapshot.get('last_ma5')
                
                if drawdown_atr >= 1.0:
                    if not parabolic_armed:
                        parabolic_armed = bar_ms
                        print(f"Parabolic Pullback >= 1.0 ATR at {pd.to_datetime(bar_ms, unit='ms')}, price: {price}, peak: {state['peak_price']}, trend_status: {position.get('trend_hold_status')}")
                
                if ma5 and last_ma5 and ((sign == 1 and ma5 < last_ma5) or (sign == -1 and ma5 > last_ma5)):
                    if not ma3_armed:
                        ma3_armed = bar_ms
                        print(f"Parabolic MA3 Turn at {pd.to_datetime(bar_ms, unit='ms')}, ma5: {ma5}, last_ma5: {last_ma5}, trend_status: {position.get('trend_hold_status')}")
            
            if res:
                action, reason = res['action'], res['reason']
                if action == 'EXIT' and reason == 'EXIT_ADVERSE_ABNORMAL_BODY':
                    body = sign*(float(snapshot['live_open'])-price)
                    atr_val = float(snapshot['atr'])
                    if body >= 1.5 * atr_val:
                        print(f"WATERFALL at {pd.to_datetime(bar_ms, unit='ms')}")
                        return
                    else:
                        ma15 = float(snapshot['ma15'])
                        prev_ma15 = float(df.iloc[i-2]['ma15'])
                        ma15_aligned = (ma15 > prev_ma15) if sign == 1 else (ma15 < prev_ma15)
                        price_aligned = (sign * (price - ma15) > 0)
                        
                        if ma15_aligned and price_aligned:
                            pass # Blocked by Candidate A
                        else:
                            print(f"UNBLOCKED ABNORMAL at {pd.to_datetime(bar_ms, unit='ms')}")
                            return
                elif action == 'EXIT':
                    print(f"EXIT {reason} at {pd.to_datetime(bar_ms, unit='ms')} price {price}")
                    return
                    
    print("OPEN_AT_DATA_END")
    print(f"Max Favorable Price: {max_favorable}, Max Gain ATR: {max_gain_atr}")

if __name__ == '__main__':
    asyncio.run(main())
