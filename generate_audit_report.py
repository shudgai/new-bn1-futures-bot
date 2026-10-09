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
    ohlcv = await engine.exchange.fetch_ohlcv(symbol, '1m', limit=limit, params={'startTime': start_time - 60000 * 100})
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
    with open('data/paper_account.json', 'r') as f:
        paper_data = json.load(f)
        trades = paper_data.get('trades', [])

    active = {}
    pairings = []
    for t in sorted(trades, key=lambda x: x.get('id', 0)):
        key = (t.get('symbol'), t.get('side'))
        if t.get('action') in ('OPEN_LONG', 'OPEN_SHORT'):
            active.setdefault(key, []).append(t)
        elif t.get('action') in ('CLOSE_LONG', 'CLOSE_SHORT'):
            if key in active and len(active[key]) > 0:
                pairings.append({'open': active[key].pop(0), 'close': t})

    target_pair = next((p for p in pairings if p['open']['symbol'] == '龙虾/USDT' and p['open']['side'] == 'LONG' and p['open']['time'][:19] == '10/02 20:59:07'), None)
    
    if not target_pair: return
    ot = target_pair['open']
    ct = target_pair['close']
    
    df = await get_historical_data(ot['symbol'], ct['id'])
    if df is None: return

    position = dict(ot)
    position['side'] = ot['side']
    position['open_timestamp'] = float(ot['id']) / 1000.0
    position['entry_price'] = float(ot['price'])
    position['initial_sl'] = float(ot.get('sl', ot.get('initial_sl', 0)))
    position['sl'] = position['initial_sl']
    
    sign = 1 if position['side'] == 'LONG' else -1
    entry = float(position['entry_price'])
    
    exit_ms = ct['id']
    start_idx = next(i for i in range(len(df)) if df.iloc[i]['timestamp'] <= exit_ms < df.iloc[i]['timestamp'] + 60000)
    
    # Need to set initial ATR correctly
    initial_atr = df.iloc[start_idx-1]['atr']
    
    print("==================================================")
    print("647-BAR TRADE TRACE")
    print("==================================================")
    print(f"entry timestamp: {ot['time']}")
    print(f"entry price: {entry:.8f}")
    print(f"ATR at entry: {initial_atr:.8f}")
    print(f"initial risk: {entry - position['initial_sl']:.8f} ({(entry - position['initial_sl'])/initial_atr:.2f} ATR)")
    
    max_favorable = entry
    reached = {1: False, 2: False, 3: False, 5: False}
    
    first_expected = None
    block_reason = None
    
    for i in range(start_idx, len(df)):
        live_bar = df.iloc[i]
        bar_ms = live_bar['timestamp']
        if i == 0: continue
        closed_bar = df.iloc[i-1]
        
        snapshot = {
            'quote_ms': bar_ms, 'live_open': live_bar['open'], 'live_bar_ms': bar_ms,
            'closed_bar_ms': closed_bar['timestamp'], 'atr': closed_bar['atr'],
            'ma15': closed_bar['ma15'], 'ma5': closed_bar['ma5'], 'ma3': closed_bar['ma3'],
            'last_ma5': df.iloc[i-2]['ma5'] if i >= 2 else None, 'kc_middle': closed_bar['kc_middle']
        }
        
        ticks = [live_bar['open'], live_bar['low'], live_bar['high'], live_bar['close']] 
        for price in ticks:
            max_favorable = max(max_favorable, price) if sign == 1 else min(max_favorable, price)
            gain = sign * (price - entry)
            gain_atr = gain / initial_atr
            
            for lvl in [1, 2, 3, 5]:
                if not reached[lvl] and gain_atr >= lvl:
                    reached[lvl] = True
                    dt = pd.to_datetime(bar_ms, unit='ms')
                    print(f"第一次達到 +{lvl} ATR timestamp: {dt}")
                    
            ident = pte.position_identity(position)
            if bar_ms < ident[1]*1000:
                print(f"Skipping tick because bar_ms {bar_ms} < entry {ident[1]*1000}")
                
            res = pte.evaluate_peak_trailing(position, price, snapshot, atr=snapshot['atr'])
            state = position.get(pte.STATE_KEY, {})
            
            # Reconstruct peak trailing internal logic
            peak_price = state.get('peak_price', entry)
            peak_gain_atr = (sign * (peak_price - entry)) / state.get('atr', initial_atr)
            drawdown_atr = (peak_price - price) / state.get('atr', initial_atr) if sign == 1 else (price - peak_price) / state.get('atr', initial_atr)
            
            parabolic_trigger = None
            if peak_gain_atr >= 3.0:
                if drawdown_atr >= 1.0:
                    parabolic_trigger = 'EXIT_PARABOLIC_PULLBACK_1_ATR'
                elif snapshot.get('ma5') and snapshot.get('last_ma5'):
                    if sign == 1 and snapshot['ma5'] < snapshot['last_ma5']:
                        parabolic_trigger = 'EXIT_PARABOLIC_MA3_TURN'
            
            if parabolic_trigger and not first_expected:
                first_expected = pd.to_datetime(bar_ms, unit='ms')
                from core.services.exits.trend_hold_evaluator import evaluate_trend_hold
                trend_status, trend_reason = evaluate_trend_hold(position, snapshot, price)
                block_reason = f"TREND_HOLD status {trend_status} ({trend_reason})" if trend_status in ('HOLD', 'WARNING', 'UNKNOWN') else "NONE (Should have exited!)"
                
                print("\n[Trigger Event Detected]")
                print(f"first expected profit-exit timestamp: {first_expected}")
                print(f"trigger: {parabolic_trigger}")
                print(f"peak state: price={peak_price:.8f}, gain_atr={peak_gain_atr:.2f}, drawdown_atr={drawdown_atr:.2f}")
                print(f"parabolic armed? True")
                print(f"MA3 armed? {'Yes' if 'MA3' in parabolic_trigger else 'No'}")
                print(f"trend_status: {trend_status} ({trend_reason})")
                print(f"block condition: {block_reason}")
                
            if res and res.get('action') == 'FULL_CLOSE':
                print(f"\n[PRODUCTION EXIT TRIGGERED]")
                print(f"exit timestamp: {pd.to_datetime(bar_ms, unit='ms')}")
                print(f"exit price: {price:.8f}")
                print(f"reason: {res.get('reason')} ({res.get('trigger')})")
                
                max_gain_pct = (max_favorable - entry) / entry * 100 * sign
                max_gain_atr = sign * (max_favorable - entry) / initial_atr
                max_gain_r = sign * (max_favorable - entry) / (entry - position['initial_sl'])
                
                print(f"\nmax favorable price: {max_favorable:.8f}")
                print(f"max gain %: {max_gain_pct:.2f}%")
                print(f"max gain R: {max_gain_r:.2f}R")
                print(f"\nFINAL STATE: {position.get(pte.STATE_KEY, {})}")
                return

    max_gain_pct = (max_favorable - entry) / entry * 100 * sign
    max_gain_atr = sign * (max_favorable - entry) / initial_atr
    max_gain_r = sign * (max_favorable - entry) / (entry - position['initial_sl'])
    
    print(f"\nmax favorable price: {max_favorable:.8f}")
    print(f"max gain %: {max_gain_pct:.2f}%")
    print(f"max gain R: {max_gain_r:.2f}R")
    print(f"\nFINAL STATE: {position.get(pte.STATE_KEY, {})}")
    
if __name__ == '__main__':
    asyncio.run(main())
