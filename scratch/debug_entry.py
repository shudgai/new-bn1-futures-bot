import pandas as pd
from core.services.strategies.outer_strategy import continuation_entry
from core.services.strategies.unified_entry_strategy import UnifiedEntryStrategy
import requests

res = requests.get('http://127.0.0.1:8006/api/klines?symbol=%E9%BE%99%E8%99%BE%2FUSDT&timeframe=1m&limit=10&include_live=true')
df = pd.DataFrame(res.json()['data'])
price = df['close'].iloc[-1]
print("Last close:", price)
print("Continuation entry:", continuation_entry(df, price))
strategy = UnifiedEntryStrategy()
print("Unified entry:", strategy.evaluate_entry(df, float(price), "LONG"))
