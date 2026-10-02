import sys, os, time, pandas as pd, asyncio
sys.path.append(os.getcwd())
from core.engine import TradingEngine
from core.strategy import SuperTrendKeltnerStrategy
from core.services.exits.trend_hold_evaluator import evaluate_trend_hold

async def main():
    engine = TradingEngine()
    df = await engine.fetch_klines("龙虾/USDT", timeframe="1m", limit=200, keep_live=True)
    strat = SuperTrendKeltnerStrategy()
    df = strat.compute_indicators(df)
    
    exit_ts = 1790907026000 # 02:10:26 UTC (10:10:26)
    df_closed = df[df['timestamp'] < exit_ts - 26000].tail(5).copy() # up to 10:09
    
    # Calculate slopes
    df_closed['last_ma5'] = df_closed['ma5'].shift(1)
    df_closed['last_ma15'] = df_closed['ma15'].shift(1)
    df_closed['last_close'] = df_closed['close'].shift(1)
    df_closed['ma5_slope'] = df_closed['ma5'] - df_closed['last_ma5']
    df_closed['ma15_slope'] = df_closed['ma15'] - df_closed['last_ma15']
    
    print("Candles:")
    for _, row in df_closed.iterrows():
        dt = pd.to_datetime(row['timestamp'], unit='ms').tz_localize('UTC').tz_convert('Asia/Taipei')
        print(f"Time: {dt.strftime('%H:%M')} | close: {row['close']} | MA5: {row['ma5']} | MA15: {row['ma15']} | MA5_slope: {row.get('ma5_slope')} | MA15_slope: {row.get('ma15_slope')} | KC_mid: {row['kc_middle']} | KC_lower: {row['kc_lower']}")
        
    # Run evaluate_trend_hold for 10:07, 10:08, 10:09
    for idx in range(len(df_closed)-3, len(df_closed)):
        snapshot = df_closed.iloc[idx].to_dict()
        position = {'side': 'SHORT', 'entry_price': 0.06294371}
        current_price = snapshot['close']  # if closed, current_price is close
        res = evaluate_trend_hold(position, snapshot, current_price)
        dt = pd.to_datetime(snapshot['timestamp'], unit='ms').tz_localize('UTC').tz_convert('Asia/Taipei')
        print(f"evaluate_trend_hold at {dt.strftime('%H:%M')} = {res}")
        
if __name__ == "__main__":
    asyncio.run(main())
