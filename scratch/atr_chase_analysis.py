"""
ATR Chase Limit Analysis

Evaluate historical KC 3-Bar candidate signals (Bar1, Bar2, Bar3) across multiple symbols.
Filter to cases where all MA, KC, and direction gates PASS.
Collect the distance_from_KC / ATR for Bar3 and evaluate against different thresholds.
"""
import asyncio, os, sys, datetime, math
from collections import defaultdict
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

SYMBOLS = ['BTC/USDT', 'ETH/USDT', 'SOL/USDT', '1000PEPE/USDT', '1000BONK/USDT']
TIMEFRAME = '1m'
LIMIT_PER_FETCH = 1000
FETCHES = 3 # ~50 hours of data per symbol

THRESHOLDS = [0.5, 0.75, 1.0, 1.25, 1.5, 2.0]

async def fetch_data():
    from dotenv import load_dotenv
    load_dotenv()
    import ccxt.async_support as ccxt
    
    exchange = ccxt.binanceusdm({'options': {'defaultType': 'future'}})
    await exchange.load_markets()
    
    data_dict = {}
    
    for symbol in SYMBOLS:
        print(f"Fetching {symbol}...")
        all_ohlcv = []
        since = None
        
        # We fetch backwards by not specifying since but using end time, or we just fetch current and go back.
        # ccxt fetch_ohlcv with since is easier
        now = exchange.milliseconds()
        since = now - (FETCHES * LIMIT_PER_FETCH * 60 * 1000)
        
        for _ in range(FETCHES):
            try:
                ohlcv = await exchange.fetch_ohlcv(symbol, TIMEFRAME, since=since, limit=LIMIT_PER_FETCH)
                if not ohlcv:
                    break
                all_ohlcv.extend(ohlcv)
                since = ohlcv[-1][0] + 1
            except Exception as e:
                print(f"Error fetching {symbol}: {e}")
                break
                
        if all_ohlcv:
            df = pd.DataFrame(all_ohlcv, columns=['timestamp','open','high','low','close','volume'])
            for col in ['open','high','low','close']:
                df[col] = df[col].astype(float)
            df['timestamp'] = df['timestamp'].astype(float)
            
            # Compute indicators
            from core.strategy import SuperTrendKeltnerStrategy
            strat = SuperTrendKeltnerStrategy()
            df = strat.compute_indicators(df)
            df['is_closed'] = True
            
            data_dict[symbol] = df
            print(f"  Got {len(df)} candles for {symbol}")
            
    await exchange.close()
    return data_dict

def analyze_candidates(data_dict):
    candidates = []
    
    for symbol, df in data_dict.items():
        seed = pending = None
        previous = None
        
        for index, row in enumerate(df.itertuples(index=False)):
            try:
                # check validity
                numbers = [float(getattr(row, k)) for k in ('timestamp', 'open', 'close', 'atr', 'kc_upper', 'kc_lower', 'ma5', 'ma15')]
                if not all(math.isfinite(v) and v > 0 for v in numbers):
                    continue
            except:
                continue
                
            if previous is not None and float(row.timestamp) - float(previous.timestamp) != 60000:
                seed = pending = None
                
            prior = previous
            previous = row
            
            if pending is not None:
                first, second, side, second_index = pending
                sign = 1 if side == 'LONG' else -1
                edge = float(row.kc_upper if side == 'LONG' else row.kc_lower)
                body = sign * (float(row.close) - float(row.open))
                distance = sign * (float(row.close) - edge) / float(row.atr)
                waited = index - second_index
                
                if waited > 2:
                    pending = None # EXPIRED
                elif waited == 1 and body < 0:
                    pending = None # INVALIDATED
                elif waited <= 2 and distance > 0 and sign * (float(row.ma5) - float(row.ma15)) > 0 and body > 0 and sign * (float(row.ma5) - float(prior.ma5)) > 0:
                    # ALL GATES PASS EXCEPT MAYBE DISTANCE
                    # Classify surge
                    b3_body_atr = abs(float(row.close) - float(row.open)) / float(row.atr)
                    b2_body_atr = abs(float(second.close) - float(second.open)) / float(second.atr)
                    
                    is_surge = b3_body_atr > 1.2 or b2_body_atr > 1.5
                    surge_type = "Extreme Surge" if is_surge else "Normal Trend"
                    
                    candidates.append({
                        'symbol': symbol,
                        'side': side,
                        'b1_ts': first.timestamp,
                        'b2_ts': second.timestamp,
                        'b3_ts': row.timestamp,
                        'distance': distance,
                        'surge_type': surge_type,
                        'b3_body_atr': b3_body_atr,
                        'b2_body_atr': b2_body_atr
                    })
                    pending = None # Consumed
                    continue
                # Keep pending if waited == 1 and we didn't consume or invalidate
                # wait, if it didn't pass, it stays pending for the next bar if waited < 2
                
            if seed is not None:
                first, side = seed
                seed = None
                sign = 1 if side == 'LONG' else -1
                edge = float(row.kc_upper if side == 'LONG' else row.kc_lower)
                
                if (sign * (float(row.close) - float(row.open)) > 0 and
                    sign * (float(row.close) - edge) > 0 and
                    sign * (float(row.ma5) - float(row.ma15)) > 0 and
                    sign * (float(row.ma5) - float(first.ma5)) > 0):
                    pending = (first, row, side, index)
                    continue
                    
            # check for seed
            for side, sign, edge in (('LONG', 1, float(row.kc_upper)), ('SHORT', -1, float(row.kc_lower))):
                if (sign * (float(row.close) - float(row.open)) > 0 and
                    sign * (float(row.open) - edge) <= 0 and
                    sign * (float(row.close) - edge) > 0):
                    seed = (row, side)
                    break
                    
    return candidates

def main():
    loop = asyncio.get_event_loop()
    data_dict = loop.run_until_complete(fetch_data())
    
    candidates = analyze_candidates(data_dict)
    
    print("\\n" + "="*80)
    print(f"Total Candidates Found (All gates pass except distance): {len(candidates)}")
    print("="*80)
    
    # Analyze thresholds
    for thresh in THRESHOLDS:
        allowed = [c for c in candidates if c['distance'] <= thresh]
        blocked = [c for c in candidates if c['distance'] > thresh]
        
        allowed_pct = (len(allowed) / len(candidates) * 100) if candidates else 0
        
        print(f"\\nThreshold: {thresh} ATR")
        print(f"  candidate_count:    {len(candidates)}")
        print(f"  allowed_count:      {len(allowed)}")
        print(f"  blocked_count:      {len(blocked)}")
        print(f"  allowed_percentage: {allowed_pct:.1f}%")
        
    # List cases blocked by 0.5 but allowed by others
    print("\\n" + "="*80)
    print("Cases Blocked by 0.5 ATR (but otherwise perfectly valid):")
    print("="*80)
    
    blocked_by_05 = [c for c in candidates if c['distance'] > 0.5]
    
    for c in blocked_by_05:
        b3_dt = datetime.datetime.utcfromtimestamp(c['b3_ts']/1000).strftime('%Y-%m-%d %H:%M:%S')
        print(f"{c['symbol']:<15} {c['side']:<5} {b3_dt} | dist={c['distance']:.2f} ATR | {c['surge_type']} (B3 body: {c['b3_body_atr']:.2f} ATR, B2 body: {c['b2_body_atr']:.2f} ATR)")
        
if __name__ == '__main__':
    main()
