"""比較兩幣的「走勢空間」：每筆進場後的最大有利幅度（MFE）與最大不利幅度（MAE）。"""
from __future__ import annotations

import json
import pathlib
from typing import Dict, List

import ccxt
import pandas as pd

from core.config import NET_PROFIT_GUARANTEE_BUFFER, SLIPPAGE_PCT, TAKER_FEE_RATE
from core.services.entry_room_service import entry_room
from core.services.strategies.outer_strategy import aligned_entry
from core.strategy import SuperTrendKeltnerStrategy
from tools.channel_prototype_replay import one_hour_direction
from tools.channel_exit_policy_replay import WARMUP

CACHE = pathlib.Path("reports/symbol_screen/market_data")
HORIZON = 240


def load(symbol: str) -> pd.DataFrame:
    path = CACHE / f"{symbol.replace('/', '_')}_30d.json"
    rows = json.loads(path.read_text())
    frame = pd.DataFrame(rows, columns=["timestamp", "open", "high", "low", "close", "volume"])
    return SuperTrendKeltnerStrategy().compute_indicators(frame)


def analyse(symbol: str) -> Dict:
    df = load(symbol)
    trend = one_hour_direction(df)
    mfe, mae = [], []
    for index in range(WARMUP, len(df) - 2):
        frame = df.iloc[: index + 1]
        price = float(frame["close"].iloc[-1])
        decision = aligned_entry(frame, price)
        if decision.get("action") != "ENTER":
            continue
        side = decision["side"]
        if not ((side == "LONG" and trend[index] == 1) or (side == "SHORT" and trend[index] == -1)):
            continue
        room = entry_room(df.iloc[max(0, index - 79): index + 1], price, side,
                          TAKER_FEE_RATE, SLIPPAGE_PCT, NET_PROFIT_GUARANTEE_BUFFER)
        if not room["allowed"]:
            continue
        end = min(index + HORIZON, len(df) - 1)
        window = df.iloc[index + 1: end + 1]
        if window.empty:
            continue
        atr = float(df.iloc[index - 1]["atr"])
        if side == "LONG":
            best, worst = float(window["high"].max()), float(window["low"].min())
        else:
            best, worst = float(window["low"].min()), float(window["high"].max())
        sign = 1 if side == "LONG" else -1
        mfe.append(sign * (best - price) / price * 100)
        mae.append(sign * (worst - price) / price * 100)
        mfe_atr = sign * (best - price) / atr if atr > 0 else 0.0
        mae.append(mae[-1])
    return {"symbol": symbol, "n": len(mfe), "mfe": mfe, "mae": mae}


def atr_percent(df: pd.DataFrame) -> float:
    return float((df["atr"] / df["close"]).mean() * 100)


def main() -> None:
    exchange = ccxt.binanceusdm({"enableRateLimit": True})
    since = exchange.parse8601("2026-01-01T00:00:00Z")
    print(f"{'幣種':<20}{'24h成交額(M)':>13}{'上市天數':>9}{'1m ATR%':>9}")
    for symbol in ("龙虾/USDT:USDT", "LAB/USDT:USDT"):
        ticker = exchange.fetch_ticker(symbol)
        first = exchange.fetch_ohlcv(symbol, "1m", since=since, limit=1)
        days = (exchange.milliseconds() - first[0][0]) / 86400000 if first else 0
        print(f"{symbol:<20}{(ticker.get('quoteVolume') or 0)/1e6:>13.1f}{days:>9.0f}{atr_percent(load(symbol)):>9.3f}")

    print(f"\n=== 走勢空間（進場後 {HORIZON} 根內）===")
    print(f"{'幣種':<20}{'筆數':>7}{'平均MFE%':>10}{'中位MFE%':>10}{'平均MAE%':>10}{'MFE≥3ATR比例':>14}")
    for symbol in ("龙虾/USDT:USDT", "LAB/USDT:USDT"):
        stats = analyse(symbol)
        mfe = stats["mfe"]
        if not mfe:
            print(f"{symbol:<20}{0:>7}")
            continue
        median = sorted(mfe)[len(mfe) // 2]
        df = load(symbol)
        atr_pct = atr_percent(df)
        share = 100 * sum(1 for v in mfe if v >= 3 * atr_pct) / len(mfe)
        print(f"{symbol:<20}{len(mfe):>7}{sum(mfe)/len(mfe):>10.2f}{median:>10.2f}"
              f"{sum(stats['mae'])/len(stats['mae']):>10.2f}{share:>13.0f}%")


if __name__ == "__main__":
    main()
