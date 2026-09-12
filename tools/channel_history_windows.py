"""龙蝦的歷史分窗檢驗：獲利是不是只出現在某一段（例如新幣蜜月期）？

把可用歷史切成每 30 天一個窗口，每個窗口用同一套規則跑一遍，
輸出每窗筆數與單筆期望值，用來判斷 edge 是否持續存在。
"""
from __future__ import annotations

import argparse
import json
import pathlib
import time
from typing import List

import pandas as pd

from core.config import NET_PROFIT_GUARANTEE_BUFFER, SLIPPAGE_PCT, TAKER_FEE_RATE
from core.services.entry_room_service import entry_room
from core.services.strategies.outer_strategy import (
    aligned_entry, live_body_breakout_side, long_body_side,
)
from core.strategy import SuperTrendKeltnerStrategy
from tools.channel_prototype_replay import atr_bracket, one_hour_direction
from tools.channel_exit_policy_replay import WARMUP

CACHE = pathlib.Path("reports/symbol_screen/market_data")
MINUTE = 60_000


def fetch(symbol: str, days: int) -> pd.DataFrame:
    import ccxt

    CACHE.mkdir(parents=True, exist_ok=True)
    path = CACHE / f"{symbol.replace('/', '_')}_{days}d_full.json"
    if path.exists():
        rows = json.loads(path.read_text())
    else:
        exchange = ccxt.binanceusdm({"enableRateLimit": True})
        end = int(time.time() // 60 * MINUTE)
        cursor = end - days * 1440 * MINUTE
        rows = []
        while cursor < end:
            batch = exchange.fetch_ohlcv(symbol, "1m", since=cursor, limit=1500)
            if not batch:
                break
            rows.extend(batch)
            following = int(batch[-1][0]) + MINUTE
            if following <= cursor:
                break
            cursor = following
        path.write_text(json.dumps(rows))
    frame = pd.DataFrame(rows, columns=["timestamp", "open", "high", "low", "close", "volume"])
    return SuperTrendKeltnerStrategy().compute_indicators(frame)


def window_stats(df: pd.DataFrame, start: int, end: int) -> dict:
    sub = df.iloc[start:end].reset_index(drop=True)
    if len(sub) < WARMUP + 200:
        return {"n": 0}
    trend = one_hour_direction(sub)
    pnls: List[float] = []
    side_pnls = {"LONG": [], "SHORT": []}
    for index in range(WARMUP, len(sub) - 2):
        frame = sub.iloc[: index + 1]
        price = float(frame["close"].iloc[-1])
        decision = aligned_entry(frame, price)
        if decision.get("action") != "ENTER":
            continue
        side = decision["side"]
        aligned = ((side == "LONG" and trend[index] == 1) or (side == "SHORT" and trend[index] == -1))
        if not aligned:
            continue
        room = entry_room(sub.iloc[max(0, index - 79): index + 1], price, side,
                          TAKER_FEE_RATE, SLIPPAGE_PCT, NET_PROFIT_GUARANTEE_BUFFER)
        if not room["allowed"]:
            continue
        body = bool(live_body_breakout_side(frame, price)) or long_body_side(frame, 2.0) == side
        outcome = atr_bracket(sub, [(index, side)], 1.5, 1.0 if body else 3.0)
        pnls.extend(outcome)
        side_pnls[side].extend(outcome)
    if not pnls:
        return {"n": 0, "per": 0.0, "net": 0.0, "win": 0.0}
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    def side_stat(values):
        if not values:
            return {"n": 0, "per": 0.0}
        wins = [v for v in values if v > 0]
        losses = [v for v in values if v < 0]
        return {"n": len(values), "per": round(sum(values) / len(values), 2),
                "pf": round(sum(wins) / (abs(sum(losses)) or 1e-9), 2)}
    return {
        "long": side_stat(side_pnls["LONG"]), "short": side_stat(side_pnls["SHORT"]),
        "n": len(pnls), "net": round(sum(pnls), 1), "per": round(sum(pnls) / len(pnls), 2),
        "win": round(100 * len(wins) / len(pnls), 1),
        "pf": round(sum(wins) / (abs(sum(losses)) or 1e-9), 2),
        "atr_pct": round(float((sub["atr"] / sub["close"]).mean() * 100), 3),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", default="龙虾/USDT:USDT")
    parser.add_argument("--days", type=int, default=180)
    parser.add_argument("--window", type=int, default=30)
    args = parser.parse_args()
    df = fetch(args.symbol, args.days)
    span = args.window * 1440
    print(f"{args.symbol}｜共 {len(df)} 根 1m（約 {len(df)//1440} 天）｜每窗 {args.window} 天")
    print(f"{'窗口':<9}{'合計筆數':>8}{'合計單筆':>9}{'多單筆數':>9}{'多單單筆':>10}{'多單PF':>8}"
          f"{'空單筆數':>9}{'空單單筆':>10}{'空單PF':>8}{'ATR%':>7}")
    for i, start in enumerate(range(0, len(df), span)):
        stats = window_stats(df, start, start + span)
        if not stats.get("n"):
            continue
        label = time.strftime("%m-%d", time.gmtime(df["timestamp"].iloc[start] / 1000))
        long_, short_ = stats["long"], stats["short"]
        print(f"{label:<9}{stats['n']:>8}{stats['per']:>9}{long_['n']:>9}{long_['per']:>10}{long_.get('pf', 0):>8}"
              f"{short_['n']:>9}{short_['per']:>10}{short_.get('pf', 0):>8}{stats['atr_pct']:>7}")


if __name__ == "__main__":
    main()
