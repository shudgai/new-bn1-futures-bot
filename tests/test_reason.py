import pandas as pd
from core.engine import TradingEngine
from tests.test_channel_swing import _generate_macro_frame

df = _generate_macro_frame("DOWN", 70)
df.loc[38, "ma15"] = 96.2
df.loc[53, "ma15"] = 94.7
df.loc[68, "ma15"] = 95.0
df.loc[68, "close"] = 95.5
df.loc[68, "ma3"] = 95.2

res = TradingEngine._channel_swing_action(df, 95.5, None)
print("REASON IS:", res.get("reason"))
