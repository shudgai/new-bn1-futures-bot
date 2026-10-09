"""Pure technical indicator calculations used by strategy evaluation."""

import numpy as np
import pandas as pd


def compute_technical_indicators(
    df: pd.DataFrame,
    *,
    atr_period: int,
    atr_multiplier: float,
    adx_period: int,
    keltner_atr_multiplier: float,
) -> pd.DataFrame:
    """Return a copied frame enriched with the strategy's existing indicators.

    This function owns calculations only. Signal selection and order execution
    remain in their respective strategy and execution layers.
    """
    df = df.copy()
    high = df['high']
    low = df['low']

    # 防插針價格選擇
    if 'close_price_spike_filtered' in df.columns:
        close = df['close_price_spike_filtered'].fillna(df['close'])
    else:
        close = df['close']

    volume = df['volume']

    # ATR 計算
    tr1 = high - low
    tr2 = (high - close.shift(1)).abs()
    tr3 = (low - close.shift(1)).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    df['atr'] = tr.rolling(window=atr_period).mean()

    # EMAs
    df['ema_10'] = close.ewm(span=10, adjust=False).mean()
    df['ema_20'] = close.ewm(span=20, adjust=False).mean()
    df['ema_50'] = close.ewm(span=50, adjust=False).mean()

    # MA3 負責快速峰谷轉折，MA5 負責中短線方向。
    df['ma3'] = close.rolling(window=3).mean()
    df['ma5'] = close.rolling(window=5).mean()
    df['ma7'] = close.rolling(window=7).mean()
    df['ma15'] = close.rolling(window=15).mean()
    df['ma15_slope'] = df['ma15'] - df['ma15'].shift(1)

    # 成交量均線
    df['vol_ma_5'] = volume.rolling(window=5).mean()
    df['vol_ma_20'] = volume.rolling(window=20).mean()

    # RSI
    delta = close.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
    rs = gain / (loss + 1e-9)
    df['rsi'] = 100 - (100 / (1 + rs))

    # 15m RSI 估算 (window = 14 * 3 = 42)
    gain_15m = (delta.where(delta > 0, 0)).rolling(window=42).mean()
    loss_15m = (-delta.where(delta < 0, 0)).rolling(window=42).mean()
    rs_15m = gain_15m / (loss_15m + 1e-9)
    df['rsi_15m'] = 100 - (100 / (1 + rs_15m))

    # MACD
    fast_ema = close.ewm(span=12, adjust=False).mean()
    slow_ema = close.ewm(span=26, adjust=False).mean()
    df['macd_line'] = fast_ema - slow_ema
    df['macd_signal'] = df['macd_line'].ewm(span=9, adjust=False).mean()
    df['macd_hist'] = df['macd_line'] - df['macd_signal']

    # ADX（趨勢強度濾網）：KC 突破配上低 ADX，是盤整期假突破的常見樣貌，
    # 用來在評分裡分辨「真的有趨勢動能撐著的突破」跟「雜訊型突破」。
    up_move = high.diff()
    down_move = -low.diff()
    plus_dm = pd.Series(np.where((up_move > down_move) & (up_move > 0), up_move, 0.0), index=df.index)
    minus_dm = pd.Series(np.where((down_move > up_move) & (down_move > 0), down_move, 0.0), index=df.index)
    tr_smooth = tr.ewm(alpha=1 / adx_period, adjust=False).mean()
    plus_di = 100 * (plus_dm.ewm(alpha=1 / adx_period, adjust=False).mean() / (tr_smooth + 1e-9))
    minus_di = 100 * (minus_dm.ewm(alpha=1 / adx_period, adjust=False).mean() / (tr_smooth + 1e-9))
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di + 1e-9)
    df['adx'] = dx.ewm(alpha=1 / adx_period, adjust=False).mean()

    # Keltner Channels
    df['kc_upper'] = df['ema_20'] + (df['atr'] * keltner_atr_multiplier)
    df['kc_lower'] = df['ema_20'] - (df['atr'] * keltner_atr_multiplier)
    df['kc_middle'] = df['ema_20']
    df['kc_width'] = df['kc_upper'] - df['kc_lower']

    # SuperTrend
    hl2 = (high + low) / 2
    basic_upper = hl2 + (atr_multiplier * df['atr'])
    basic_lower = hl2 - (atr_multiplier * df['atr'])

    final_upper = pd.Series(index=df.index, dtype=float)
    final_lower = pd.Series(index=df.index, dtype=float)
    supertrend = pd.Series(index=df.index, dtype=float)
    direction = pd.Series(index=df.index, dtype=int)

    # 遞迴起點必須是 ATR 第一個有效值的那根，不能是第 0 根：ATR 是
    # atr_period 期滾動平均，前面幾根一定是 NaN，basic_upper/lower 算出來
    # 也是 NaN。如果從第 0 根開始遞迴，下面的棘輪邏輯全部是「跟前一根
    # 比大小」，只要比較對象是 NaN，Python/pandas 的比較結果永遠是
    # False，會一路落入 else 分支維持前一根的 NaN，一路傳染到最後一根，
    # 導致 final_upper/final_lower 永遠是 NaN、方向永遠卡在初始值 1
    # （多頭），不管實際價格怎麼走都不會翻轉——這是先前空單訊號被完全
    # 堵死、新鮮度分數永遠拿不到的根本原因。
    first_valid = df['atr'].first_valid_index()
    start_pos = df.index.get_loc(first_valid) if first_valid is not None else len(df)

    for i in range(len(df)):
        if i < start_pos:
            direction.iloc[i] = 1
            continue
        if i == start_pos:
            final_upper.iloc[i] = basic_upper.iloc[i]
            final_lower.iloc[i] = basic_lower.iloc[i]
            direction.iloc[i] = 1
            supertrend.iloc[i] = final_lower.iloc[i]
            continue

        if basic_upper.iloc[i] < final_upper.iloc[i-1] or close.iloc[i-1] > final_upper.iloc[i-1]:
            final_upper.iloc[i] = basic_upper.iloc[i]
        else:
            final_upper.iloc[i] = final_upper.iloc[i-1]

        if basic_lower.iloc[i] > final_lower.iloc[i-1] or close.iloc[i-1] < final_lower.iloc[i-1]:
            final_lower.iloc[i] = basic_lower.iloc[i]
        else:
            final_lower.iloc[i] = final_lower.iloc[i-1]

        prev_dir = direction.iloc[i-1]
        if prev_dir == 1:
            if close.iloc[i] < final_lower.iloc[i]:
                direction.iloc[i] = -1
                supertrend.iloc[i] = final_upper.iloc[i]
            else:
                direction.iloc[i] = 1
                supertrend.iloc[i] = final_lower.iloc[i]
        else:
            if close.iloc[i] > final_upper.iloc[i]:
                direction.iloc[i] = 1
                supertrend.iloc[i] = final_lower.iloc[i]
            else:
                direction.iloc[i] = -1
                supertrend.iloc[i] = final_upper.iloc[i]

    df['supertrend'] = supertrend
    df['st_direction'] = direction
    return df
