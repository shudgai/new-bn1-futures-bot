"""kc_bb_squeeze_trailing.py

此模組實作結合 Keltner Channel (KC)、Bollinger Bands (BB) Squeeze 濾網、
KC 中軌斜率過濾、順勢突破開倉 (Trend Breakout Entry，支援多空雙向)
以及移動止盈 / 中軌跌破離場 (Trailing Stop & Midline Exit) 的完整量化策略。

使用方式示例（在 Jupyter / Python 腳本中）：
```python
import pandas as pd
from core.services.strategies.kc_bb_squeeze_trailing import (
    calculate_squeeze_kc_breakout_strategy,
)

df = pd.read_csv('data.csv')  # 必須包含 high, low, close
result = calculate_squeeze_kc_breakout_strategy(df)
print(
    result[
        [
            'close',
            'kc_middle',
            'kc_upper',
            'kc_lower',
            'bb_upper',
            'bb_lower',
            'is_sqz',
            'kc_slope',
            'signal',
        ]
    ].tail()
)
```

`signal` 欄位說明：
-  1 : 順勢買多 (Open Long)
- -1 : 順勢賣空 (Open Short)
-  2 : 平多離場 (Close Long)
- -2 : 平空離場 (Close Short)
-  0 : 無操作 (Hold / Wait)
"""

from typing import Optional

import numpy as np
import pandas as pd

try:
    import pandas_ta as ta  # type: ignore
except ImportError:
    ta = None


def _compute_atr(
    high: pd.Series, low: pd.Series, close: pd.Series, length: int = 20
) -> pd.Series:
    """計算 ATR (Average True Range)。
    若環境已安裝 `pandas_ta` 則優先呼叫 `ta.atr`，否則使用原生 pandas 計算以确保零外部套件依賴故障。
    """
    if ta is not None:
        atr_series = ta.atr(high, low, close, length=length)
        if atr_series is not None:
            return atr_series

    prev_close = close.shift(1)
    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    # 使用 Wilder's RMA (alpha = 1 / length) 與 pandas_ta 預設行為對齊，首段使用 rolling mean 避免全 NaN
    return tr.ewm(alpha=1.0 / length, min_periods=length, adjust=False).mean().bfill()


def calculate_squeeze_kc_breakout_strategy(
    df: pd.DataFrame,
    kc_length: int = 20,
    kc_mult: float = 2.0,
    bb_length: int = 20,
    bb_std: float = 2.0,
    min_kc_slope: float = 0.0003,
    min_ma_dist: float = 0.001,
    pivot_lookback: int = 10,
    min_pivot_rebound_pct: float = 0.0025,
    trailing_stop_pct: float = 0.015,
    overbought_trailing_pct: Optional[float] = None,
    waterfall_drop_pct: float = 0.012,
    fast_ma_length: int = 3,
    slow_ma_length: int = 15,
) -> pd.DataFrame:
    """
    KC + BB Squeeze + 多空方向鎖 + MA3 距離過濾 + 峰谷確認 + 防洗盤與大瀑布暴脹止盈平倉策略

    平倉與暴脹止盈核心設計：
    1. 區分「一般回檔」與「大瀑布暴跌」：
       - 一般回檔（不輕易平倉）：當多單處於獲利或常態波動，且收盤未跌破 KC 中軌 (`close >= kc_middle`)、
         單根 K 棒跌幅未達瀑布極限 (`bar_drop_pct <= waterfall_drop_pct`，預設 1.2%) 時，繼續抱緊持倉讓利潤奔跑，
         不因盤中微小拉回或普通快線抖動被洗出場。
       - 大瀑布觸發（立刻平倉）：當單根 K 棒跌幅超過 `1.2%` (`(open - close)/open > 0.012` 或 `(prev_close - close)/prev_close > 0.012`)
         或收盤強勢貫穿 KC 下軌 (`close < kc_lower`) 時，判定為真大瀑布，立即執行平倉 (`WATERFALL_EXIT`)。
    2. 高點動態止盈微調 (Adaptive Trailing Stop)：
       - 只有當持倉期間最高價曾突破 KC 上軌（`reached_overbought = True`，進入超買暴脹區），
         且自高點回檔超過 `active_trailing_pct`（預設 `0.008` 即 `0.8%`；若呼叫端顯式傳入自訂 `trailing_stop_pct` 則予以尊重）時，
         才觸發高點動態防守止盈 (`OVERBOUGHT_TRAILING_STOP`)；若仍在 KC 通道內部常態波動，則給予足夠震盪空間不輕易平倉。
    3. 保底平倉條件：
       - 以「收盤價跌破 KC 中軌 (`close < kc_middle`)」作為基礎趨勢改變的防守平倉線 (`MIDLINE_BREAK_EXIT`)。
    """
    df = df.copy()
    # 若呼叫端未特別覆寫 trailing_stop_pct (維持預設 0.015)，超買區動態止盈預設採用 0.8% (0.008)
    active_trailing_pct = (
        overbought_trailing_pct
        if overbought_trailing_pct is not None
        else (0.008 if trailing_stop_pct == 0.015 else trailing_stop_pct)
    )

    # 1. 計算 KC (肯特納通道) 與 快慢均線 (MA3 / MA15)
    df["kc_middle"] = df["close"].ewm(span=kc_length, adjust=False).mean()
    df["atr"] = _compute_atr(df["high"], df["low"], df["close"], length=kc_length)
    df["kc_upper"] = df["kc_middle"] + (kc_mult * df["atr"])
    df["kc_lower"] = df["kc_middle"] - (kc_mult * df["atr"])

    df["ma3"] = df["close"].rolling(window=fast_ma_length, min_periods=1).mean()
    df["ma15"] = df["close"].rolling(window=slow_ma_length, min_periods=1).mean()
    df["ma_fast"] = df["ma3"]
    df["ma_slow"] = df["ma15"]
    # 拉遠視角大週期均線 (EMA60) 與其 10 棒斜率，防止在空頭下降趨勢中因短線 3 棒反彈而逆勢開多
    df["ema60"] = df["close"].ewm(span=60, adjust=False).mean()
    df["ema60_slope"] = df["ema60"] - df["ema60"].shift(10).bfill()
    df["is_macro_bear"] = (df["ema60_slope"] < 0) & (df["close"] < df["ema60"])
    df["is_macro_bull"] = (df["ema60_slope"] > 0) & (df["close"] > df["ema60"])

    # 2. 計算 布林通道 (BB)
    df["bb_middle"] = df["close"].rolling(window=bb_length, min_periods=1).mean()
    df["bb_std"] = df["close"].rolling(window=bb_length, min_periods=1).std().fillna(0.0)
    df["bb_upper"] = df["bb_middle"] + (bb_std * df["bb_std"])
    df["bb_lower"] = df["bb_middle"] - (bb_std * df["bb_std"])

    # 3. 計算過去 10 根 K 棒的最高點 (peak) 與最低點 (trough) 供峰谷確認使用
    df["peak"] = df["high"].rolling(window=pivot_lookback, min_periods=1).max()
    df["trough"] = df["low"].rolling(window=pivot_lookback, min_periods=1).min()

    # 谷底反彈與頂峰回落幅度是否達標 (預設 0.25%)
    df["valid_trough_rebound"] = df["close"] > (df["trough"] * (1.0 + min_pivot_rebound_pct))
    df["valid_peak_pullback"] = df["close"] < (df["peak"] * (1.0 - min_pivot_rebound_pct))

    # 4. 濾網條件：Squeeze + 中軌斜率 + MA3 與中軌距離過濾 (MA Distance Filter)
    # A. 擠壓狀態: BB 完全包在 KC 內部 -> 判定盤整，禁止開倉
    df["is_sqz"] = (df["bb_upper"] < df["kc_upper"]) & (df["bb_lower"] > df["kc_lower"])

    # B. MA3 與 KC 中軌的相對距離 (%)
    safe_kc_mid = df["kc_middle"].replace(0, np.nan)
    df["ma_dist"] = (df["ma3"] - df["kc_middle"]).abs() / safe_kc_mid
    df["is_ma_detached"] = df["ma_dist"] > min_ma_dist

    # 基礎交易許可: 必須非 Squeeze 狀態 且 MA3 與中軌拉開足夠距離 (非黏著盤整)
    df["can_trade"] = (~df["is_sqz"]) & df["is_ma_detached"]
    df["can_open"] = df["can_trade"]

    # C. KC 中軌斜率 (近 3 棒變動率) 與當根單棒即時斜率
    shifted_mid = df["kc_middle"].shift(3)
    df["kc_slope"] = (df["kc_middle"] - shifted_mid) / shifted_mid.replace(0, np.nan)
    kc_step_up = df["kc_middle"] > df["kc_middle"].shift(1)
    kc_step_down = df["kc_middle"] < df["kc_middle"].shift(1)

    # 【多頭與空頭開倉四合一核心條件 can_buy / can_sell】
    # 多頭開倉必須同時滿足以下所有條件：
    # 1. kc_slope > 0.0003 (KC 中軌必須明確向上，解決 KC向下 開多問題)
    # 2. close > kc_middle (收盤價必須在 KC 中軌上方)
    # 3. ma_dist > 0.001   (MA3 與中軌離差 > 0.1%，解決貼著中軌走的問題)
    # 4. ~is_sqz           (非布林擠壓狀態)
    df["can_buy"] = (
        (df["kc_slope"] > min_kc_slope)
        & kc_step_up
        & (df["close"] > df["kc_middle"])
        & (df["ma_dist"] > min_ma_dist)
        & (~df["is_sqz"])
        & (~df["is_macro_bear"])
    )

    # 對稱空頭開倉必須同時滿足以下所有條件：
    # 1. kc_slope < -0.0003 (KC 中軌必須明確向下，解決 KC向上 開空問題)
    # 2. close < kc_middle  (收盤價必須在 KC 中軌下方)
    # 3. ma_dist > 0.001    (MA3 與中軌離差 > 0.1%，解決貼著中軌走的問題)
    # 4. ~is_sqz            (非布林擠壓狀態)
    # 5. ~is_macro_bull     (拉遠看非多頭趨勢，防止多頭回檔開空)
    df["can_sell"] = (
        (df["kc_slope"] < -min_kc_slope)
        & kc_step_down
        & (df["close"] < df["kc_middle"])
        & (df["ma_dist"] > min_ma_dist)
        & (~df["is_sqz"])
        & (~df["is_macro_bull"])
    )

    df["allow_long"] = (
        df["can_buy"]
        & (df["ma3"] > df["kc_middle"])
        & df["valid_trough_rebound"]
    )
    df["can_open_long"] = df["allow_long"]

    df["allow_short"] = (
        df["can_sell"]
        & (df["ma3"] < df["kc_middle"])
        & df["valid_peak_pullback"]
    )
    df["can_open_short"] = df["allow_short"]

    # -------------------------------------------------------------------------
    # 5. 順勢突破與「峰谷確認後之 MA3/MA15 交叉」訊號判定
    # -------------------------------------------------------------------------
    # 原始 MA3 / MA15 金叉 (raw_gold_cross) 與 死叉 (raw_death_cross)
    raw_gold_cross = (df["ma3"].shift(1) <= df["ma15"].shift(1)) & (df["ma3"] > df["ma15"])
    raw_death_cross = (df["ma3"].shift(1) >= df["ma15"].shift(1)) & (df["ma3"] < df["ma15"])
    df["raw_gold_cross"] = raw_gold_cross
    df["raw_death_cross"] = raw_death_cross
    ma_cross_up = raw_gold_cross
    ma_cross_down = raw_death_cross

    # 經過【峰谷確認】與【can_buy / can_sell】把關的有效金叉 / 有效死叉
    df["valid_trough_cross"] = (
        raw_gold_cross
        & df["can_buy"]
        & df["valid_trough_rebound"]
    )
    df["valid_peak_cross"] = (
        raw_death_cross
        & df["can_sell"]
        & df["valid_peak_pullback"]
    )

    # 軌道順勢突破：突破 KC 上軌 / 跌破 KC 下軌
    breakout_upper = (df["close"].shift(1) <= df["kc_upper"].shift(1)) & (
        df["close"] > df["kc_upper"]
    )
    breakout_lower = (df["close"].shift(1) >= df["kc_lower"].shift(1)) & (
        df["close"] < df["kc_lower"]
    )

    # 順勢做多觸發：必須通過【多頭方向鎖、MA3距離與谷底反彈確認 allow_long】且 (突破上軌 或 谷底有效金叉)
    raw_long_entry = df["allow_long"] & (breakout_upper | df["valid_trough_cross"])

    # 順勢做空觸發：必須通過【空頭方向鎖、MA3距離與頂峰回落確認 allow_short】且 (跌破下軌 或 頂峰有效死叉)
    raw_short_entry = df["allow_short"] & (breakout_lower | df["valid_peak_cross"])

    df["signal"] = 0  # 1: 買多, -1: 賣空, 2: 平多, -2: 平空
    df["exit_reason"] = None  # 紀錄平倉原因供回測診斷

    position = 0  # 0: 無持倉, 1: 持有多單, -1: 持有空單
    entry_price = 0.0
    highest_price = 0.0
    lowest_price = 0.0
    reached_overbought = False  # 多單持倉期間最高價是否曾突破 KC 上軌 (進入超買區)
    reached_oversold = False    # 空單持倉期間最低價是否曾跌破 KC 下軌 (進入超賣區)

    for i in range(1, len(df)):
        current_open = float(df["open"].iloc[i]) if "open" in df.columns else float(df["close"].iloc[i - 1])
        current_high = float(df["high"].iloc[i])
        current_low = float(df["low"].iloc[i])
        current_close = float(df["close"].iloc[i])
        prev_close = float(df["close"].iloc[i - 1])

        kc_mid = float(df["kc_middle"].iloc[i])
        kc_up = float(df["kc_upper"].iloc[i])
        kc_low = float(df["kc_lower"].iloc[i])

        kc_slope_val = float(df["kc_slope"].iloc[i]) if pd.notna(df["kc_slope"].iloc[i]) else 0.0
        ma_dist_val = float(df["ma_dist"].iloc[i]) if pd.notna(df["ma_dist"].iloc[i]) else 0.0
        allow_long = bool(df["allow_long"].iloc[i])
        allow_short = bool(df["allow_short"].iloc[i])

        # 【空倉狀態 - 嚴格依照多空方向鎖與 MA3 距離過濾尋找順勢進場點】
        if position == 0:
            # 若 MA3 貼著中軌走 (ma_dist <= min_ma_dist)，強制禁止開倉
            if ma_dist_val <= min_ma_dist:
                continue

            # 做多：嚴格要求 allow_long (含 ma_dist > min_ma_dist, kc_slope > min_kc_slope, close > kc_middle)
            if allow_long and kc_slope_val > min_kc_slope and current_close > kc_mid:
                if bool(raw_long_entry.iloc[i]):
                    df.loc[df.index[i], "signal"] = 1  # 順勢做多
                    position = 1
                    entry_price = current_close
                    highest_price = max(current_high, current_close)
                    reached_overbought = highest_price > kc_up

            # 做空：嚴格要求 allow_short (含 ma_dist > min_ma_dist, kc_slope < -min_kc_slope, close < kc_middle)
            elif allow_short and kc_slope_val < -min_kc_slope and current_close < kc_mid:
                if bool(raw_short_entry.iloc[i]):
                    df.loc[df.index[i], "signal"] = -1  # 順勢做空
                    position = -1
                    entry_price = current_close
                    lowest_price = min(current_low, current_close)
                    reached_oversold = lowest_price < kc_low

        # 【持有多單 - 大週期不變一單到底，只到真峰頂、鎖利或破下軌才平多】
        elif position == 1:
            bar_peak = max(current_high, current_close)
            highest_price = max(highest_price, bar_peak)
            if bar_peak > kc_up or highest_price > kc_up:
                reached_overbought = True

            # 1. 核心破軌防線（結構破壞）：實體陰線收盤正式跌破 KC 下軌 (close < kc_lower)
            is_lower_rail_trend_exit = (current_close < kc_low) and (current_close < current_open)

            # 2. 真峰頂回撤平多：盈利達標後 (最高價 > 成本 * 1.015)，自最高價回撤超過 1.8% 且收陰線並跌破 KC 中軌
            is_peak_pullback_exit = (
                (highest_price > entry_price * 1.015)
                and (current_close < highest_price * 0.982)
                and (current_close < current_open)
                and (current_close < kc_mid)
            )

            # 3. 取消固定點位硬止損：依使用者指示「不要硬止損，要真峰頂或結構破壞才能平倉」
            is_hard_sl_long = False

            if is_lower_rail_trend_exit:
                df.loc[df.index[i], "signal"] = 2
                df.loc[df.index[i], "exit_reason"] = "LOWER_RAIL_TREND_EXIT"
                position = 0
                reached_overbought = False
            elif is_peak_pullback_exit:
                df.loc[df.index[i], "signal"] = 2
                df.loc[df.index[i], "exit_reason"] = "PEAK_PULLBACK_1_8_PCT_EXIT"
                position = 0
                reached_overbought = False
            # 否則：大週期向上期間回踩中軌或小幅震盪一律強制續抱讓利潤奔跑！

        # 【持有空單 - 大波段谷底持倉模式 (1000PEPE & 龍蝦一路抱到底，中間不平空)】
        elif position == -1:
            bar_trough = min(current_low, current_close)
            lowest_price = min(lowest_price, bar_trough)

            # 1. 大趨勢反轉 / 結構破壞平倉：必須等待 K 棒實體收盤價強勢貫穿突破 KC 上軌 (close > kc_upper)
            is_upper_rail_trend_exit = (current_close > kc_up) and (current_close > current_open)

            # 2. 真谷底強勢反彈平空：曾獲利 >= 1.5% 後，自最低谷底價格 (lowest_price) 反彈超過 1.8% 且收陽線並站上 KC 中軌
            is_valley_rebound_exit = (
                (lowest_price > 0)
                and (entry_price > 0)
                and ((entry_price - lowest_price) / entry_price >= 0.015)
                and (current_close > lowest_price * 1.018)
                and (current_close > current_open)
                and (current_close > kc_mid)
            )

            # 3. 取消固定點位硬止損：依使用者指示「一路開空中間不平倉，要真谷底或結構破壞才能平倉」
            is_hard_sl_short = False

            if is_upper_rail_trend_exit:
                df.loc[df.index[i], "signal"] = -2  # 強勢貫穿 KC 上軌，大趨勢反轉平空
                df.loc[df.index[i], "exit_reason"] = "UPPER_RAIL_TREND_EXIT"
                position = 0
                lowest_price = 0.0
            elif is_valley_rebound_exit:
                df.loc[df.index[i], "signal"] = -2  # 真谷底反彈突破中軌止盈
                df.loc[df.index[i], "exit_reason"] = "VALLEY_REBOUND_2_0_PCT_EXIT"
                position = 0
                lowest_price = 0.0
            # 否則：KC向下過程中的任何微幅反彈陽線，一律視為洗盤雜訊，強制續抱吃滿主跌段！

    return df


def calculate_full_kc_bb_strategy(
    df: pd.DataFrame,
    kc_length: int = 20,
    kc_mult: float = 2.0,
    bb_length: int = 20,
    bb_std: float = 2.0,
    min_kc_slope: float = 0.0003,
    min_ma_dist: float = 0.001,
    trailing_stop_pct: float = 0.018,
    max_mid_dist_pct: Optional[float] = None,
) -> pd.DataFrame:
    """
    KC + BB Squeeze 雙向 (多頭/空頭) 大波段跟蹤策略 (Trend Riding & Parabolic Lock)

    參數設定：
    - kc_length: KC 通道週期 (預設 20)
    - kc_mult: KC ATR 乘數 (預設 2.0)
    - bb_length / bb_std: 布林通道週期與標準差
    - min_kc_slope: KC 中軌最小斜率 (過濾平坦走勢)
    - min_ma_dist: MA3 與中軌最小距離 (%)，過濾貼著走黏著盤
    - trailing_stop_pct: 大波段移動止盈回檔門檻 (預設 1.8%)
    """
    df = df.copy()

    # -------------------------------------------------------------------------
    # 1. 基礎指標計算 (KC, BB, MA3, MA15)
    # -------------------------------------------------------------------------
    df["ma3"] = df["close"].ewm(span=3, adjust=False).mean()
    df["ma15"] = df["close"].ewm(span=15, adjust=False).mean()

    # KC 通道
    df["kc_middle"] = df["close"].ewm(span=kc_length, adjust=False).mean()
    df["atr"] = _compute_atr(df["high"], df["low"], df["close"], length=kc_length)
    df["kc_upper"] = df["kc_middle"] + (kc_mult * df["atr"])
    df["kc_lower"] = df["kc_middle"] - (kc_mult * df["atr"])

    # 布林通道 (BB)
    df["bb_middle"] = df["close"].rolling(window=bb_length, min_periods=1).mean()
    df["bb_std"] = df["close"].rolling(window=bb_length, min_periods=1).std().fillna(0.0)
    df["bb_upper"] = df["bb_middle"] + (bb_std * df["bb_std"])
    df["bb_lower"] = df["bb_middle"] - (bb_std * df["bb_std"])

    # -------------------------------------------------------------------------
    # 2. 濾網條件判斷
    # -------------------------------------------------------------------------
    # A. Squeeze 擠壓狀態 (BB 縮進 KC 內部 -> 極度盤整，禁止開倉)
    df["is_sqz"] = (df["bb_upper"] < df["kc_upper"]) & (df["bb_lower"] > df["kc_lower"])

    # B. KC 中軌斜率 (近 3 棒變動率)
    shifted_mid = df["kc_middle"].shift(3)
    df["kc_slope"] = (df["kc_middle"] - shifted_mid) / shifted_mid.replace(0, np.nan)

    # C. MA3 與 KC 中軌距離 (防黏著離差)
    safe_kc_mid = df["kc_middle"].replace(0, np.nan)
    df["ma_dist"] = (df["ma3"] - df["kc_middle"]).abs() / safe_kc_mid

    # 基礎許可條件
    df["can_trade"] = (~df["is_sqz"]) & (df["ma_dist"] > min_ma_dist)

    # -------------------------------------------------------------------------
    # 3. 核心多/空開平倉邏輯迴圈
    # -------------------------------------------------------------------------
    df["signal"] = 0  # 1: 買多, -1: 賣空, 2: 平多, -2: 平空
    df["exit_reason"] = None

    position = 0  # 0: 空倉, 1: 持有多單, -1: 持有空單
    entry_price = 0.0
    highest_price = 0.0
    lowest_price = 0.0

    for i in range(1, len(df)):
        current_close = float(df["close"].iloc[i])
        current_open = float(df["open"].iloc[i]) if "open" in df.columns else float(df["close"].iloc[i - 1])
        current_high = float(df["high"].iloc[i]) if "high" in df.columns else current_close
        current_low = float(df["low"].iloc[i]) if "low" in df.columns else current_close
        kc_mid = float(df["kc_middle"].iloc[i])
        kc_up = float(df["kc_upper"].iloc[i])
        kc_low = float(df["kc_lower"].iloc[i])
        slope = float(df["kc_slope"].iloc[i]) if pd.notna(df["kc_slope"].iloc[i]) else 0.0
        can_trade = bool(df["can_trade"].iloc[i])

        # 前一棒突破條件
        prev_close = float(df["close"].iloc[i - 1])
        prev_kc_up = float(df["kc_upper"].iloc[i - 1])
        prev_kc_low = float(df["kc_lower"].iloc[i - 1])

        ma3_curr = float(df["ma3"].iloc[i])
        ma3_prev = float(df["ma3"].iloc[i - 1])
        ma15_curr = float(df["ma15"].iloc[i])
        ma15_prev = float(df["ma15"].iloc[i - 1])

        # =====================================================================
        # 🛡️ 核心重構：大趨勢過濾器 (Trend Filter) & 封鎖追高追低
        # =====================================================================
        # 1. 價格與中軌離差率 (Distance from KC Middle)
        dist_from_mid = abs(current_close - kc_mid) / kc_mid if kc_mid > 0 else 0.0
        is_overextended = (max_mid_dist_pct is not None) and (dist_from_mid > max_mid_dist_pct)  # 離差過大認定為延伸末端封鎖追單

        # 2. 趨勢基本許可：
        #    買多：KC向上 (slope > min_kc_slope) 且 收盤站穩中軌之上
        #    賣空：KC向下 (slope < -min_kc_slope) 且 收盤壓制中軌之下
        allow_long = (slope > min_kc_slope) and (current_close > kc_mid) and (not is_overextended)
        allow_short = (slope < -min_kc_slope) and (current_close < kc_mid) and (not is_overextended)

        # 3. 過濾頻繁翻轉與無效重複開倉 (Disable Churning & Early Flip)：
        #    剛平空後 3 棒內：若未強勢突破上軌，嚴禁在中軌附近反手買多！
        #    剛平多後若仍在主升段 (slope > 0 且收紅站穩 MA3 與中軌之上)，允許延續開倉，而非都不動作！
        just_closed_short = any(df["signal"].iloc[max(0, i - 3):i] == -2) if i >= 1 else False
        just_closed_long_exit = any(df["signal"].iloc[max(0, i - 3):i] == 2) if i >= 1 else False

        if just_closed_short and (current_close <= kc_up):
            allow_long = False  # 剛平空，封鎖反手追多，防止中軌雜訊震盪多空雙巴！
        if just_closed_long_exit and (current_close >= kc_low):
            allow_short = False  # 剛平多，封鎖反手追空！

        # =====================================================================
        # A. 【無持倉狀態 (position == 0) - 開倉邏輯 & 順勢延續開倉】
        # =====================================================================
        if position == 0:
            # 判斷是否為「剛平多後的順勢延續開倉 / 破軌追進」：
            just_closed_long = any(df["signal"].iloc[max(0, i - 5):i] == 2) if i >= 1 else False
            is_breakout_reentry = (
                just_closed_long
                and (slope > 0)
                and (current_close > kc_mid)
                and (current_close >= ma3_curr)
                and (ma3_curr > ma3_prev)
                and (current_close > current_open)  # 順勢陽線延續
            )

            if is_breakout_reentry:
                # 🚀 順勢延續開倉機制 (Continuation Re-entry)：
                # 若先前平多，但趨勢仍在 KC向上 主升段且收紅站穩 MA3 與中軌之上，立即延續買多，絕不發呆空手！
                df.loc[df.index[i], "signal"] = 1
                df.loc[df.index[i], "exit_reason"] = "CONTINUATION_REENTRY_LONG"
                position = 1
                entry_price = current_close
                highest_price = current_close
            elif can_trade:
                # 【買多邏輯】: 必須 KC向上 + 收盤在中軌上方 + 斜率足夠 + 未過度延伸 (allow_long 硬性過濾)
                if allow_long:
                    # 條件1: 順勢突破/延續 KC 上軌 或 條件2: MA3 金叉向上拉開 或 條件3: 主升段站穩 MA3 與中軌之上順勢延續
                    is_trend_continuation_long = (
                        current_close > kc_mid
                        and ma3_curr > kc_mid
                        and current_close >= ma3_curr
                        and ma3_curr > ma3_prev
                        and current_close > current_open
                    )
                    if (
                        (current_close > kc_up and current_close > current_open)
                        or (ma3_curr > ma15_curr and ma3_prev <= ma15_prev)
                        or is_trend_continuation_long
                    ):
                        df.loc[df.index[i], "signal"] = 1  # 買多
                        position = 1
                        entry_price = current_close
                        highest_price = current_close

                # 【賣空邏輯】: 必須 KC向下 + 收盤在中軌下方 + 斜率負值 + 未過度延伸 (allow_short 硬性過濾)
                # 封鎖追空：離中軌 > 1.2% 嚴禁開空！
                elif allow_short:
                    # 條件1: 順勢跌破/延續 KC 下軌 或 條件2: MA3 死叉向下拉開 或 條件3: 主跌段受壓 MA3 與中軌之下順勢延續
                    is_trend_continuation_short = (
                        current_close < kc_mid
                        and ma3_curr < kc_mid
                        and current_close <= ma3_curr
                        and ma3_curr < ma3_prev
                        and current_close < current_open
                    )
                    if (
                        (current_close < kc_low and current_close < current_open)
                        or (ma3_curr < ma15_curr and ma3_prev >= ma15_prev)
                        or is_trend_continuation_short
                    ):
                        df.loc[df.index[i], "signal"] = -1  # 賣空
                        position = -1
                        entry_price = current_close
                        lowest_price = current_close

        # =====================================================================
        # B. 【持有多單 (position == 1) - 從第一個開多到最後都不要平倉 (一單到底鎖定)】
        # =====================================================================
        elif position == 1:
            highest_price = max(highest_price, current_close)

            # 1. 只有當實體陰線收盤正式跌破 KC 下軌時，才視為大趨勢徹底反轉平多（拒絕下影線洗盤）！
            trend_reversal = (current_close < kc_low) and (current_close < current_open)

            # 2. 移動止盈: 從最高點回撤超過 2.0% 且收盤跌破中軌時才平多
            trailing_exit = (
                (highest_price > entry_price * 1.02)
                and (current_close < highest_price * (1.0 - trailing_stop_pct))
                and (current_close < kc_mid)
            )

            # 3. 初始硬性風控止損 (跌破成本價 1.8% 且位於中軌下方)
            hard_sl_long = (entry_price > 0) and (current_close <= entry_price * 0.982) and (current_close < kc_mid)

            # -----------------------------------------------------------------
            # 【核心硬防守】：從第一個開多一路抱到底！絕不因回踩中軌、快線下彎或單棒斜率微幅抖動而平多！
            # -----------------------------------------------------------------
            if trend_reversal:
                df.loc[df.index[i], "signal"] = 2  # 實體陰線跌破下軌，大趨勢徹底反轉平多
                df.loc[df.index[i], "exit_reason"] = "LOWER_RAIL_TREND_EXIT"
                position = 0
                highest_price = 0.0
            elif trailing_exit:
                df.loc[df.index[i], "signal"] = 2  # 衝高回撤超過 2% 且跌破中軌移動止盈
                df.loc[df.index[i], "exit_reason"] = "TRAILING_STOP_EXIT"
                position = 0
                highest_price = 0.0
            elif hard_sl_long:
                df.loc[df.index[i], "signal"] = 2
                df.loc[df.index[i], "exit_reason"] = "HARD_SL_LONG_EXIT"
                position = 0
                highest_price = 0.0
            # 否則：中間所有回踩中軌、下影線洗盤或快線下彎，100% 視為正常呼吸，強制續抱一單到底！

        # =====================================================================
        # C. 【持有空單 (position == -1) - 大波段谷底持倉模式 (1000PEPE & 龍蝦一單到底)】
        # =====================================================================
        elif position == -1:
            lowest_price = min(lowest_price, current_close)

            # 1. 大波段移動止盈: 曾獲利 >= 1.5% 後，自最低谷底反彈超過 1.8% (0.018) 且收陽線必須鎖利
            trailing_stop_threshold = 0.018
            trailing_exit = (
                (lowest_price > 0)
                and (entry_price > 0)
                and ((entry_price - lowest_price) / entry_price >= 0.015)
                and (current_close > lowest_price * (1.0 + trailing_stop_threshold))
                and (current_close > current_open)
            )

            # 2. 【核心破軌防線】: 只要實體收盤價突破 KC 通道上軌 (破軌)，無條件強制平空！
            # (無論當前大週期 slope 是正還是負，破軌就必須跑！)
            hard_breakout_exit = (current_close > kc_up) and (current_close > current_open)

            # 3. 初始硬性風控止損 (暴拉急煞超過成本價 1.8% 且突破中軌)
            hard_sl_short = (entry_price > 0) and (current_close >= entry_price * 1.018) and (current_close > kc_mid)

            if hard_breakout_exit:
                df.loc[df.index[i], "signal"] = -2  # 強勢突破 KC 上軌破軌，無條件強制平空
                df.loc[df.index[i], "exit_reason"] = "UPPER_RAIL_TREND_EXIT"
                position = 0
                lowest_price = 0.0
            elif trailing_exit:
                df.loc[df.index[i], "signal"] = -2  # 自谷底最低價反彈超過 1.8% 移動止盈落袋
                df.loc[df.index[i], "exit_reason"] = "VALLEY_REBOUND_1_8_PCT_EXIT"
                position = 0
                lowest_price = 0.0
            elif hard_sl_short:
                df.loc[df.index[i], "signal"] = -2
                df.loc[df.index[i], "exit_reason"] = "HARD_SL_SHORT_EXIT"
                position = 0
                lowest_price = 0.0
            # 否則：KC向下過程中的任何微幅反彈陽線，一律視為洗盤雜訊，強制續抱吃滿主跌段！

    return df


# 保留舊函式名稱別名，確保向後相容
calculate_squeeze_kc_strategy = calculate_squeeze_kc_breakout_strategy


