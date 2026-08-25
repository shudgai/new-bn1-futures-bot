import pandas as pd
import numpy as np

prices = [100, 99, 98, 97, 96, 95, 96, 97, 98, 99, 100]
df = pd.DataFrame({'close': prices})
df['ma5'] = df['close'].rolling(window=5).mean()

print(df)
