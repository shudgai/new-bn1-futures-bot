import re
with open('scratch/replay_parabolic.py', 'r') as f:
    code = f.read()

calc_code = """
df['ma3'] = df['close'].rolling(3).mean()
df['ma5'] = df['close'].rolling(5).mean()
df['ma15'] = df['close'].rolling(15).mean()
df['kc_mid'] = df['close'].rolling(20).mean()
# The true trade df needs to be recalculated over the whole history so we don't have NaNs for the trade window.
df_full = pd.read_csv('scratch/lobster_real_1m_history.csv').sort_values('timestamp').reset_index(drop=True)
df_full['ma3'] = df_full['close'].rolling(3).mean()
df_full['ma5'] = df_full['close'].rolling(5).mean()
df_full['ma15'] = df_full['close'].rolling(15).mean()
df_full['kc_mid'] = df_full['close'].rolling(20).mean()
df = df_full[(df_full['timestamp'] >= trade['id'])].reset_index(drop=True)
"""
code = code.replace("df = df[(df['timestamp'] >= trade['id'])].sort_values('timestamp').reset_index(drop=True)", calc_code)

with open('scratch/replay_parabolic.py', 'w') as f:
    f.write(code)
