"""Valid closed-body breakout data shared by execution regressions."""
import pandas as pd


def closed_outer_entry_frame(side="LONG", rows=20):
    f = pd.DataFrame({
        "open": [99.6] * rows, "close": [100.] * rows,
        "high": [100.1] * rows, "low": [99.9] * rows,
        "kc_upper": [102.] * rows, "kc_lower": [98.] * rows,
        "kc_middle": [100.] * rows, "ema_20": [100.] * rows,
        "ma15": [100.] * rows, "atr": [1.] * rows,
        "volume": [150.] * rows, "vol_ma_20": [100.] * rows,
    })
    f.loc[rows-3, ["open", "close"]] = [101.5, 102.5]
    f.loc[rows-2, ["open", "close"]] = [102.5, 103.]
    f.loc[rows-1, ["open", "close"]] = [103., 103.2]
    f.loc[rows-2:, "kc_upper"] = 102.1
    # 2026-09-11：共用進場框架必須是有斜率的通道，否則會被 KC_FLAT_MIDDLE_WAIT 正確擋下。
    f.loc[rows-4:rows-1, "kc_middle"] = [98.9, 99.3, 99.65, 100.]
    f.loc[rows-4:rows-1, "ma15"] = [99.7, 99.8, 99.9, 100.]
    f["high"] = f[["open", "close"]].max(axis=1) + .1
    f["low"] = f[["open", "close"]].min(axis=1) - .1
    f["ma3"] = f["close"].rolling(3).mean().fillna(100.)
    if side == "SHORT":
        for key in ("open", "close", "ma3", "ma15", "kc_middle", "ema_20"):
            f[key] = 200 - f[key]
        f["high"], f["low"] = 200 - f["low"].copy(), 200 - f["high"].copy()
        f["kc_upper"], f["kc_lower"] = 200 - f["kc_lower"].copy(), 200 - f["kc_upper"].copy()
    return f
