import matplotlib
matplotlib.use("Agg")  # Ensure headless/server environment compatibility
import matplotlib.pyplot as plt
import mplfinance as mpf
import numpy as np
import pandas as pd
from typing import Optional


# -------------------------------------------------------------------------
# 1. 指標計算函數 (1m 級別)
# -------------------------------------------------------------------------
def prepare_indicators_1m(df: pd.DataFrame) -> pd.DataFrame:
    """計算 1m 級別指標：EMA 15, EMA 40, KC 通道"""
    df = df.copy()

    # 確保時間序列索引正確
    if not isinstance(df.index, pd.DatetimeIndex):
        if "timestamp" in df.columns:
            ts = df["timestamp"]
            if pd.api.types.is_numeric_dtype(ts):
                unit = "ms" if (len(ts) > 0 and ts.iloc[0] > 1e11) else "s"
                df.index = pd.to_datetime(ts, unit=unit)
            else:
                df.index = pd.to_datetime(ts)
        elif "time" in df.columns:
            df.index = pd.to_datetime(df["time"])
        else:
            df.index = pd.date_range(end=pd.Timestamp.now(), periods=len(df), freq="1min")

    # 均線計算
    df["ema_fast"] = df["close"].ewm(span=15, adjust=False).mean()
    df["ema_slow"] = df["close"].ewm(span=40, adjust=False).mean()
    df["fast_slope"] = df["ema_fast"] - df["ema_fast"].shift(3)

    # 均線間距比例（用於過濾黏合重疊）
    df["ma_spread_pct"] = (
        (df["ema_fast"] - df["ema_slow"]).abs() / df["ema_slow"]
    ) * 100

    # Keltner Channel (KC 週期 20, ATR 乘數 1.5)
    tr = np.maximum(
        df["high"] - df["low"],
        np.maximum(
            (df["high"] - df["close"].shift(1)).abs(),
            (df["low"] - df["close"].shift(1)).abs(),
        ),
    )
    df["atr"] = tr.rolling(window=20).mean()
    # 填補 ATR 前期 NaN
    if df["atr"].isna().any():
        df["atr"] = df["atr"].bfill().fillna(df["close"] * 0.01)

    df["kc_basis"] = df["close"].ewm(span=20, adjust=False).mean()
    df["kc_upper"] = df["kc_basis"] + (df["atr"] * 1.5)
    df["kc_lower"] = df["kc_basis"] - (df["atr"] * 1.5)

    return df


# -------------------------------------------------------------------------
# 2. 信號標記生成 (用於圖表 Marker)
# -------------------------------------------------------------------------
def generate_chart_signals(
    df: pd.DataFrame, min_spread_pct: float = 0.08
) -> pd.DataFrame:
    """掃描歷史 Bar，標記開倉點與回踩點"""
    df = df.copy()
    df["sig_buy"] = np.nan
    df["sig_sell"] = np.nan
    df["sig_pullback"] = np.nan

    for i in range(3, len(df)):
        curr = df.iloc[i]
        prev1 = df.iloc[i - 1]

        # 黏合過濾
        if curr.get("ma_spread_pct", 0) < min_spread_pct:
            continue

        # 開多信號：EMA15 > EMA40 向上發散，連續 2 根收在 KC 上軌外
        long_breakout = (
            curr["close"] > curr["kc_upper"]
            and prev1["close"] > prev1["kc_upper"]
        )
        if (
            curr["ema_fast"] > curr["ema_slow"]
            and curr["fast_slope"] > 0
            and long_breakout
        ):
            df.iloc[i, df.columns.get_loc("sig_buy")] = curr["low"] * 0.998

        # 開空信號：EMA15 < EMA40 向下發散，連續 2 根收在 KC 下軌外
        short_breakout = (
            curr["close"] < curr["kc_lower"]
            and prev1["close"] < prev1["kc_lower"]
        )
        if (
            curr["ema_fast"] < curr["ema_slow"]
            and curr["fast_slope"] < 0
            and short_breakout
        ):
            df.iloc[i, df.columns.get_loc("sig_sell")] = curr["high"] * 1.002

        # 多頭回踩識別：在慢線之上、觸及快線但未跌破慢線（同趨勢蓄勢不平倉）
        if (
            curr["ema_fast"] > curr["ema_slow"]
            and curr["close"] <= curr["ema_fast"]
            and curr["close"] >= curr["ema_slow"]
        ):
            df.iloc[i, df.columns.get_loc("sig_pullback")] = curr["low"] * 0.999

    return df


# -------------------------------------------------------------------------
# 3. 整合可視化繪圖
# -------------------------------------------------------------------------
def plot_1m_strategy_chart(
    df: pd.DataFrame,
    title: str = "1M Scalping Strategy (EMA 15/40 + KC)",
    save_path: Optional[str] = None,
):
    """繪製整合 1m K 線圖、均線、通道與信號標記"""
    # 準備額外圖層 (Addplot)
    apds = [
        # 核心均線
        mpf.make_addplot(
            df["ema_fast"],
            color="#00E5FF",
            width=1.5,
            panel=0,
            label="EMA 15 (Fast)",
        ),
        mpf.make_addplot(
            df["ema_slow"],
            color="#FF9100",
            width=1.8,
            panel=0,
            label="EMA 40 (Slow)",
        ),
        # KC 通道
        mpf.make_addplot(
            df["kc_upper"],
            color="#787B86",
            linestyle="--",
            width=1.0,
            panel=0,
        ),
        mpf.make_addplot(
            df["kc_lower"],
            color="#787B86",
            linestyle="--",
            width=1.0,
            panel=0,
        ),
        mpf.make_addplot(
            df["kc_basis"], color="#434651", linestyle=":", width=0.8, panel=0
        ),
    ]

    # 若有開多點則繪製標記
    if df["sig_buy"].notna().any():
        apds.append(
            mpf.make_addplot(
                df["sig_buy"],
                type="scatter",
                markersize=120,
                marker="^",
                color="#00E676",
                panel=0,
            )
        )
    # 若有開空點則繪製標記
    if df["sig_sell"].notna().any():
        apds.append(
            mpf.make_addplot(
                df["sig_sell"],
                type="scatter",
                markersize=120,
                marker="v",
                color="#FF1744",
                panel=0,
            )
        )
    # 若有回踩點則繪製標記
    if df["sig_pullback"].notna().any():
        apds.append(
            mpf.make_addplot(
                df["sig_pullback"],
                type="scatter",
                markersize=45,
                marker="o",
                color="#FFEA00",
                panel=0,
            )
        )

    # 自訂暗色系配色
    mc = mpf.make_marketcolors(
        up="#26a69a",
        down="#ef5350",
        edge="inherit",
        wick="inherit",
        volume="in",
    )
    style = mpf.make_mpf_style(
        base_mpf_style="nightclouds",
        marketcolors=mc,
        gridstyle=":",
        gridcolor="#2A2E39",
        facecolor="#131722",
    )

    plot_kwargs = dict(
        type="candle",
        addplot=apds,
        style=style,
        title=title,
        ylabel="Price (USDT)",
        volume=False,
        figsize=(14, 7),
        tight_layout=True,
    )
    if save_path:
        plot_kwargs["savefig"] = save_path

    # 繪圖輸出
    mpf.plot(df, **plot_kwargs)
    return save_path


# -------------------------------------------------------------------------
# 4. 實盤信號評估 (供交易引擎與 SymbolRunner 調用)
# -------------------------------------------------------------------------
def evaluate_scalping_1m_signal(
    df: pd.DataFrame,
    side: str,
    min_spread_pct: float = 0.08,
) -> tuple[bool, str, dict]:
    """評估當前最新 1m K 棒是否符合 1M Scalping Strategy (EMA 15/40 + KC) 開倉條件"""
    side = str(side or "").upper()
    if side not in ("LONG", "SHORT"):
        return False, "INVALID_SIDE", {}
    if df is None or len(df) < 4:
        return False, "INSUFFICIENT_BARS", {}

    df_calc = prepare_indicators_1m(df)
    curr = df_calc.iloc[-1]
    prev1 = df_calc.iloc[-2]

    ma_spread = float(curr.get("ma_spread_pct", 0.0) or 0.0)
    if ma_spread < min_spread_pct:
        return False, f"MA_SPREAD_TOO_SMALL({ma_spread:.4f}% < {min_spread_pct}%)", {}

    ema_fast = float(curr["ema_fast"])
    ema_slow = float(curr["ema_slow"])
    fast_slope = float(curr["fast_slope"])
    curr_close = float(curr["close"])
    curr_upper = float(curr["kc_upper"])
    curr_lower = float(curr["kc_lower"])
    prev_close = float(prev1["close"])
    prev_upper = float(prev1["kc_upper"])
    prev_lower = float(prev1["kc_lower"])

    details = {
        "ema_fast": ema_fast,
        "ema_slow": ema_slow,
        "fast_slope": fast_slope,
        "ma_spread_pct": ma_spread,
        "kc_upper": curr_upper,
        "kc_lower": curr_lower,
        "kc_basis": float(curr["kc_basis"]),
        "atr": float(curr["atr"]),
    }

    if side == "LONG":
        # 開多信號：EMA15 > EMA40 向上發散，連續 2 根收在 KC 上軌外
        long_breakout = (curr_close > curr_upper) and (prev_close > prev_upper)
        if ema_fast > ema_slow and fast_slope > 0 and long_breakout:
            return True, "SCALPING_1M_LONG_BREAKOUT", details
        else:
            reasons = []
            if ema_fast <= ema_slow:
                reasons.append("EMA_FAST_NOT_ABOVE_SLOW")
            if fast_slope <= 0:
                reasons.append("FAST_SLOPE_NOT_POSITIVE")
            if not long_breakout:
                reasons.append("NOT_2BAR_ABOVE_UPPER")
            return False, "|".join(reasons), details
    else:
        # 開空信號：EMA15 < EMA40 向下發散，連續 2 根收在 KC 下軌外
        short_breakout = (curr_close < curr_lower) and (prev_close < prev_lower)
        if ema_fast < ema_slow and fast_slope < 0 and short_breakout:
            return True, "SCALPING_1M_SHORT_BREAKOUT", details
        else:
            reasons = []
            if ema_fast >= ema_slow:
                reasons.append("EMA_FAST_NOT_BELOW_SLOW")
            if fast_slope >= 0:
                reasons.append("FAST_SLOPE_NOT_NEGATIVE")
            if not short_breakout:
                reasons.append("NOT_2BAR_BELOW_LOWER")
            return False, "|".join(reasons), details


def is_scalping_1m_pullback_holding(df: pd.DataFrame, side: str = "LONG") -> bool:
    """判斷多頭回踩保護（在慢線之上、觸及快線但未跌破慢線，提示保持持倉勿平倉）"""
    if side != "LONG" or df is None or len(df) < 2:
        return False
    df_calc = prepare_indicators_1m(df)
    curr = df_calc.iloc[-1]
    ema_fast = float(curr["ema_fast"])
    ema_slow = float(curr["ema_slow"])
    close = float(curr["close"])
    return (ema_fast > ema_slow) and (close <= ema_fast) and (close >= ema_slow)


if __name__ == "__main__":
    # 快速自測
    sample_data = {
        "timestamp": pd.date_range("2026-10-21 00:00", periods=50, freq="1min"),
        "open": np.linspace(100, 110, 50) + np.random.randn(50) * 0.2,
        "high": np.linspace(100.5, 110.8, 50) + np.random.randn(50) * 0.2,
        "low": np.linspace(99.5, 109.2, 50) + np.random.randn(50) * 0.2,
        "close": np.linspace(100.2, 110.5, 50) + np.random.randn(50) * 0.2,
        "volume": np.random.randint(100, 1000, 50),
    }
    raw_df = pd.DataFrame(sample_data)
    ind_df = prepare_indicators_1m(raw_df)
    sig_df = generate_chart_signals(ind_df)
    plot_1m_strategy_chart(sig_df, title="Self-Test 1M Strategy Chart", save_path="data/test_1m_chart.png")
    print("Self-test chart saved successfully.")
