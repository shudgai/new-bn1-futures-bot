"""Compare confirmed KC-outer entries with earlier first-touch entries.

This is a research-only backtest.  It does not import the trading engine,
connect an account, or submit orders.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import sys
import time

import pandas as pd
import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core.strategy import SuperTrendKeltnerStrategy


FUTURES_KLINES = "https://fapi.binance.com/fapi/v1/klines"
FEE_RATE = 0.0005  # taker fee charged on both entry and exit


def fetch_klines(symbol: str, days: int) -> pd.DataFrame:
    end_ms = int(time.time() * 1000)
    start_ms = end_ms - days * 24 * 60 * 60 * 1000
    rows: list[list[object]] = []
    session = requests.Session()
    while start_ms < end_ms:
        response = session.get(
            FUTURES_KLINES,
            params={
                "symbol": symbol,
                "interval": "1m",
                "startTime": start_ms,
                "endTime": end_ms,
                "limit": 1500,
            },
            timeout=30,
        )
        response.raise_for_status()
        batch = response.json()
        if not batch:
            break
        rows.extend(batch)
        start_ms = int(batch[-1][0]) + 60_000

    frame = pd.DataFrame(
        rows, columns=[
            "timestamp", "open", "high", "low", "close", "volume",
            "close_time", "quote_volume", "trades", "taker_base",
            "taker_quote", "ignore",
        ],
    )
    for column in ("open", "high", "low", "close", "volume"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    return frame.drop_duplicates("timestamp").sort_values("timestamp").reset_index(drop=True)


def add_indicators(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy()
    previous_close = result["close"].shift(1)
    true_range = pd.concat(
        [
            result["high"] - result["low"],
            (result["high"] - previous_close).abs(),
            (result["low"] - previous_close).abs(),
        ], axis=1,
    ).max(axis=1)
    result["atr"] = true_range.rolling(10).mean()
    result["ema20"] = result["close"].ewm(span=20, adjust=False).mean()
    result["ma3"] = result["close"].rolling(3).mean()
    result["ma15"] = result["close"].rolling(15).mean()
    result["vol_ma_20"] = result["volume"].rolling(20).mean()
    up_move = result["high"].diff()
    down_move = -result["low"].diff()
    plus_dm = pd.Series(0.0, index=result.index).where(
        ~((up_move > down_move) & (up_move > 0)), up_move,
    )
    minus_dm = pd.Series(0.0, index=result.index).where(
        ~((down_move > up_move) & (down_move > 0)), down_move,
    )
    smooth_tr = true_range.ewm(alpha=1 / 14, adjust=False).mean()
    plus_di = 100 * plus_dm.ewm(alpha=1 / 14, adjust=False).mean() / (smooth_tr + 1e-9)
    minus_di = 100 * minus_dm.ewm(alpha=1 / 14, adjust=False).mean() / (smooth_tr + 1e-9)
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di + 1e-9)
    result["adx"] = dx.ewm(alpha=1 / 14, adjust=False).mean()
    result["upper"] = result["ema20"] + result["atr"] * 1.5
    result["lower"] = result["ema20"] - result["atr"] * 1.5
    return result.dropna().reset_index(drop=True)


def add_market_modes(frame: pd.DataFrame) -> pd.DataFrame:
    """Reconstruct the live RANGE/BULL/BEAR classification without look-ahead."""
    result = frame.copy()
    spread_atr = (result["ma3"] - result["ma15"]).abs() / result["atr"]
    raw_range = (result["adx"] < 20) & (spread_atr < 0.35)
    raw_trend = (result["adx"] >= 25) & (spread_atr >= 0.50)
    regime = []
    current = "RANGE"
    for index in range(len(result)):
        if index >= 2 and raw_range.iloc[index - 2:index + 1].all():
            current = "RANGE"
        elif index >= 2 and raw_trend.iloc[index - 2:index + 1].all():
            current = "TREND"
        regime.append(current)
    result["regime"] = regime

    timestamps = pd.to_datetime(result["timestamp"], unit="ms", utc=True)
    hourly = result.assign(hour=timestamps.dt.floor("h")).groupby("hour", as_index=False).agg(
        open=("open", "first"), high=("high", "max"), low=("low", "min"),
        close=("close", "last"), volume=("volume", "sum"),
    )
    hourly = SuperTrendKeltnerStrategy().compute_indicators(hourly)
    hourly["available_at"] = hourly["hour"] + pd.Timedelta(hours=1)
    hourly = hourly[["available_at", "st_direction", "ema_50"]].dropna()
    result = pd.merge_asof(
        result.assign(available_at=timestamps).sort_values("available_at"),
        hourly.sort_values("available_at"), on="available_at", direction="backward",
    ).sort_index()
    result["market_mode"] = "RANGE"
    trend = result["regime"] == "TREND"
    result.loc[
        trend & (result["st_direction"] == 1) & (result["close"] >= result["ema_50"]),
        "market_mode",
    ] = "BULL"
    result.loc[
        trend & (result["st_direction"] == -1) & (result["close"] <= result["ema_50"]),
        "market_mode",
    ] = "BEAR"
    result.loc[trend & (result["market_mode"] == "RANGE"), "market_mode"] = "TREND_UNALIGNED"
    return result.reset_index(drop=True)


def strong_live_side(frame: pd.DataFrame, index: int) -> str | None:
    """Recreate the strong live KC break using only data known at this bar."""
    if index < 6:
        return None
    quality = frame.iloc[index - 6:index]
    row = frame.iloc[index]
    close = quality["close"].astype(float)
    ma3 = quality["ma3"].astype(float)
    ma15 = quality["ma15"].astype(float)
    middle = (quality["upper"].astype(float) + quality["lower"].astype(float)) / 2.0
    total_path = float(close.diff().abs().sum())
    efficiency = abs(float(close.iloc[-1] - close.iloc[0])) / total_path if total_path else 0.0
    volume_ratio = float(quality["volume"].iloc[-1]) / max(
        float(quality["vol_ma_20"].iloc[-1]), 1e-12,
    )
    if volume_ratio < 1.0 or efficiency < 0.65:
        return None
    if (
        float(row.high) >= float(row.upper)
        and (close.iloc[-3:] > middle.iloc[-3:]).all()
        and (ma3.iloc[-3:] > ma15.iloc[-3:]).all()
        and ma15.iloc[-1] > ma15.iloc[-3]
        and middle.iloc[-1] > middle.iloc[-3]
    ):
        return "LONG"
    if (
        float(row.low) <= float(row.lower)
        and (close.iloc[-3:] < middle.iloc[-3:]).all()
        and (ma3.iloc[-3:] < ma15.iloc[-3:]).all()
        and ma15.iloc[-1] < ma15.iloc[-3]
        and middle.iloc[-1] < middle.iloc[-3]
    ):
        return "SHORT"
    return None


def run(
    frame: pd.DataFrame,
    early_entry: bool = False,
    strong_live_entry: bool = False,
    min_volume_ratio: float = 0.0,
    breakout_atr_buffer: float = 0.0,
    max_entry_extension_atr: float | None = None,
) -> list[tuple[str, float]]:
    """Return net percentage PnL per closed trade.

    Confirmed entry: outer green/red candidate followed by an adjacent
    same-direction break.  Early entry: first closed outer touch.  Both use
    the next candle's open for execution.  RANGE exits on an outer KC MA3
    peak/valley; BULL longs and BEAR shorts exit only when an opposite candle
    returns inside the corresponding KC rail.
    """
    trades: list[tuple[str, float]] = []
    position: tuple[str, float, str] | None = None
    for index in range(2, len(frame) - 1):
        row = frame.iloc[index]
        previous = frame.iloc[index - 1]
        next_open = float(frame.iloc[index + 1]["open"])
        if position:
            side, entry, entry_mode = position
            peak = float(row.ma3) < float(previous.ma3)
            valley = float(row.ma3) > float(previous.ma3)
            mode = str(row.market_mode)
            long_reentry = (
                side == "LONG" and mode == "BULL"
                and (float(row.open) >= float(row.upper) or float(previous.close) >= float(previous.upper))
                and float(row.lower) <= float(row.close) < float(row.upper)
                and float(row.close) < float(row.open)
            )
            short_reentry = (
                side == "SHORT" and mode == "BEAR"
                and (float(row.open) <= float(row.lower) or float(previous.close) <= float(previous.lower))
                and float(row.lower) < float(row.close) <= float(row.upper)
                and float(row.close) > float(row.open)
            )
            exit_long = long_reentry or (
                side == "LONG" and mode != "BULL"
                and float(row.close) > float(row.upper) and peak
            )
            exit_short = short_reentry or (
                side == "SHORT" and mode != "BEAR"
                and float(row.close) < float(row.lower) and valley
            )
            if exit_long or exit_short:
                gross = next_open / entry - 1.0 if side == "LONG" else entry / next_open - 1.0
                trades.append((entry_mode, gross - 2 * FEE_RATE))
                position = None
            continue

        if strong_live_entry:
            side = strong_live_side(frame, index)
            if side:
                position = (side, next_open, str(row.market_mode))
            continue

        if early_entry:
            # B variant: the first completed candle that touches an outer rail
            # is enough.  The execution remains the next open, so the backtest
            # never assumes a fill at an intrabar high or low.
            if float(row.low) <= float(row.lower):
                position = ("LONG", next_open, str(row.market_mode))
            elif float(row.high) >= float(row.upper):
                position = ("SHORT", next_open, str(row.market_mode))
            continue

        # Signal candle is `previous`; `row` is its one-and-only adjacent confirmation.
        atr = max(float(row.atr), 1e-12)
        volume_ratio = float(row.volume) / max(float(row.vol_ma_20), 1e-12)
        long_break_level = float(previous.high) + breakout_atr_buffer * atr
        short_break_level = float(previous.low) - breakout_atr_buffer * atr
        long_extension_atr = (next_open - float(previous.low)) / atr
        short_extension_atr = (float(previous.high) - next_open) / atr
        volume_ready = volume_ratio >= min_volume_ratio
        long_not_overextended = (
            max_entry_extension_atr is None
            or long_extension_atr <= max_entry_extension_atr
        )
        short_not_overextended = (
            max_entry_extension_atr is None
            or short_extension_atr <= max_entry_extension_atr
        )
        long_confirmed = (
            float(previous.low) <= float(previous.lower)
            and float(previous.close) > float(previous.open)
            and float(row.close) > float(row.open)
            and float(row.high) >= long_break_level
            and volume_ready
            and long_not_overextended
        )
        short_confirmed = (
            float(previous.high) >= float(previous.upper)
            and float(previous.close) < float(previous.open)
            and float(row.close) < float(row.open)
            and float(row.low) <= short_break_level
            and volume_ready
            and short_not_overextended
        )
        if long_confirmed:
            position = ("LONG", next_open, str(row.market_mode))
        elif short_confirmed:
            position = ("SHORT", next_open, str(row.market_mode))
    return trades


def summary(trades: list[float]) -> str:
    if not trades:
        return "trades=0"
    series = pd.Series(trades)
    equity = (1 + series).cumprod()
    drawdown = equity / equity.cummax() - 1
    gains = series[series > 0].sum()
    losses = -series[series < 0].sum()
    profit_factor = gains / losses if losses else float("inf")
    return (
        f"trades={len(series)} win_rate={(series > 0).mean():.1%} "
        f"net={series.sum():.2%} avg={series.mean():.3%} "
        f"max_drawdown={drawdown.min():.2%} profit_factor={profit_factor:.2f}"
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--symbols", nargs="+", default=["BTCUSDT", "ETHUSDT", "SOLUSDT"])
    args = parser.parse_args()
    for symbol in args.symbols:
        frame = add_market_modes(add_indicators(fetch_klines(symbol, args.days)))
        print(f"{symbol} ({len(frame)} 1m candles, {args.days}d)")
        for label, options in (
            ("legacy_confirmed", {}),
            ("current_vol_1.2", {"min_volume_ratio": 1.2}),
            ("buffer_0.05_atr_max_1.5_atr", {"min_volume_ratio": 1.2, "breakout_atr_buffer": 0.05, "max_entry_extension_atr": 1.5}),
            ("buffer_0.10_atr_max_1.5_atr", {"min_volume_ratio": 1.2, "breakout_atr_buffer": 0.10, "max_entry_extension_atr": 1.5}),
            ("buffer_0.05_atr_max_1.2_atr", {"min_volume_ratio": 1.2, "breakout_atr_buffer": 0.05, "max_entry_extension_atr": 1.2}),
            ("buffer_0.10_atr_max_1.2_atr", {"min_volume_ratio": 1.2, "breakout_atr_buffer": 0.10, "max_entry_extension_atr": 1.2}),
            ("early", {"early_entry": True}),
            ("strong_live", {"strong_live_entry": True}),
        ):
            trades = run(frame, **options)
            values = [pnl for _, pnl in trades]
            print(f"  {label}: {summary(values)}")
            for mode in ("RANGE", "BULL", "BEAR", "TREND_UNALIGNED"):
                mode_values = [pnl for trade_mode, pnl in trades if trade_mode == mode]
                print(f"    {mode}: {summary(mode_values)}")


if __name__ == "__main__":
    main()
