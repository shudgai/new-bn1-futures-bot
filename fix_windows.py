import os
import re

files = [
    'core/indicators.py',
    'core/strategy.py',
    'core/engine.py'
]

for filepath in files:
    if not os.path.exists(filepath):
        continue
    with open(filepath, 'r') as f:
        content = f.read()
    
    # Fix rolling(window=7) and rolling(window=3) to rolling(window=5) ONLY for ma5
    # Wait, the lines look like: df['ma5'] = df['close'].rolling(window=7).mean()
    content = re.sub(r"ma5'\].*?rolling\(window=[37]\)", "ma5'].rolling(window=5)", content)
    content = re.sub(r"ma5 = closes\.rolling\(window=[37]\)", "ma5 = closes.rolling(window=5)", content)
    content = re.sub(r"ma5 = close\.rolling\(window=[37]\)", "ma5 = close.rolling(window=5)", content)
    
    # In strategy.py lines 437-450, we have duplicate ma5 assignments and checks
    if 'strategy.py' in filepath:
        # Remove the duplicate check block
        content = content.replace("    if 'ma5' not in df.columns:\n        df['ma5'] = df['close'].rolling(window=3).mean()\n", "")
        # Remove the duplicate check for ma5_series
        content = content.replace("    ma5_series = df['ma5'].dropna()\n    if len(ma5_series) < 3:\n        return {\"detected\": False, \"reason\": \"MA5資料不足\"}\n\n", "")
        
    with open(filepath, 'w') as f:
        f.write(content)
    print(f"Fixed windows in {filepath}")

