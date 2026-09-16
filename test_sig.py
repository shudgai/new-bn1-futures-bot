from core.services.strategies.unified_entry_strategy import UnifiedEntryStrategy
import pandas as pd
df = pd.DataFrame()
s = UnifiedEntryStrategy()
try:
    s.evaluate_entry(df, 100.0)
    print("Success")
except Exception as e:
    print(f"Failed: {repr(e)}")
