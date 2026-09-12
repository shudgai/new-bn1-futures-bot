"""幣種篩選器：用現行規則找「跟龙蝦同性質」的幣。

規則與實跑一致：走平 0.05、20 根方向效率 0.30、順 1h SuperTrend、需要淨利空間 ≥0.15%；
出口 ATR 1.5 停損，趨勢進場目標 3 ATR、長K進場目標 1 ATR。
輸出：開發(前70%)／驗證(後30%)的單筆期望值、PF、筆數與波動度，用來挑第二個交易幣。
"""
from __future__ import annotations

import argparse
import json
import pathlib
import time
from typing import Dict, List, Tuple

import pandas as pd

from core.config import NET_PROFIT_GUARANTEE_BUFFER, SLIPPAGE_PCT, TAKER_FEE_RATE
from core.services.entry_room_service import entry_room
from core.services.strategies.outer_strategy import (
    aligned_entry, live_body_breakout_side, long_body_side,
)
from core.strategy import SuperTrendKeltnerStrategy
from tools.channel_prototype_replay import atr_bracket, one_hour_direction
from tools.channel_exit_policy_replay import WARMUP, summarise

CACHE = pathlib.Path("reports/symbol_screen/market_data")
MINUTE = 60_000
STOP_ATR = 1.5
TREND_TARGET_ATR = 3.0
LONG_BODY_TARGET_ATR = 1.0


def fetch_1m(ccxt_symbol: str, days: int) -> pd.DataFrame:
    import ccxt

    CACHE.mkdir(parents=True, exist_ok=True)
    end = int(time.time() // 60 * MINUTE)
    start = end - days * 1440 * MINUTE
    path = CACHE / f"{ccxt_symbol.replace('/', '_')}_{days}d.json"
    if path.exists():
        rows = json.loads(path.read_text())
    else:
        exchange = ccxt.binanceusdm({"enableRateLimit": True})
        rows, cursor = [], start
        while cursor < end:
            batch = exchange.fetch_ohlcv(ccxt_symbol, "1m", since=cursor, limit=1500)
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


def collect(df: pd.DataFrame) -> List[Dict]:
    records: List[Dict] = []
    for index in range(WARMUP, len(df) - 2):
        frame = df.iloc[: index + 1]
        price = float(frame["close"].iloc[-1])
        decision = aligned_entry(frame, price)
        if decision.get("action") != "ENTER":
            continue
        side = decision["side"]
        body_driven = bool(live_body_breakout_side(frame, price)) or long_body_side(frame, 2.0) == side
        room = entry_room(df.iloc[max(0, index - 79): index + 1], price, side,
                          TAKER_FEE_RATE, SLIPPAGE_PCT, NET_PROFIT_GUARANTEE_BUFFER)
        records.append({"index": index, "side": side, "body": body_driven, "room": bool(room["allowed"])})
    return records


def run(symbol: str, days: int) -> Dict:
    df = fetch_1m(symbol, days)
    if len(df) < WARMUP + 500:
        return {"symbol": symbol, "trades": 0, "note": f"資料不足（{len(df)} 根）"}
    trend = one_hour_direction(df)
    records = collect(df)
    split = WARMUP + int((len(df) - WARMUP) * 0.7)
    dev_pnls: List[float] = []
    val_pnls: List[float] = []
    bucket = {("trend", False): [], ("trend", True): [], ("body", False): [], ("body", True): []}
    for record in records:
        index, side = record["index"], record["side"]
        aligned = ((side == "LONG" and trend[index] == 1) or (side == "SHORT" and trend[index] == -1))
        if not aligned or not record["room"]:
            continue
        target = LONG_BODY_TARGET_ATR if record["body"] else TREND_TARGET_ATR
        pnl = atr_bracket(df, [(index, side)], STOP_ATR, target)
        bucket[("body" if record["body"] else "trend", index >= split)].extend(pnl)
        (val_pnls if index >= split else dev_pnls).extend(pnl)
    all_pnls = dev_pnls + val_pnls
    atr_pct = float((df["atr"] / df["close"]).iloc[-500:].mean() * 100)
    return {
        "symbol": symbol, "bars": len(df),
        "atr_pct": round(atr_pct, 3),
        "trades": len(all_pnls), "dev_n": len(dev_pnls), "val_n": len(val_pnls),
        "dev": round(sum(dev_pnls), 1), "val": round(sum(val_pnls), 1),
        "per": round(sum(all_pnls) / len(all_pnls), 2) if all_pnls else 0.0,
        "win": round(100 * sum(1 for p in all_pnls if p > 0) / len(all_pnls), 1) if all_pnls else 0.0,
        "pf": round(sum(p for p in all_pnls if p > 0) / (abs(sum(p for p in all_pnls if p < 0)) or 1e-9), 2) if all_pnls else 0.0,
        "per_dev": round(sum(dev_pnls) / len(dev_pnls), 2) if dev_pnls else 0.0,
        "per_val": round(sum(val_pnls) / len(val_pnls), 2) if val_pnls else 0.0,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--symbols", nargs="+", default=[
        "龙虾/USDT:USDT", "币安人生/USDT:USDT", "我踏马来了/USDT:USDT", "牛来/USDT:USDT",
        "哈基米/USDT:USDT", "SOL/USDT:USDT", "DOGE/USDT:USDT", "SUI/USDT:USDT",
    ])
    args = parser.parse_args()
    results = [run(symbol, args.days) for symbol in args.symbols]
    results.sort(key=lambda r: r.get("per_val", 0.0), reverse=True)
    header = f"{'幣種':<22}{'天數':>6}{'ATR%':>7}{'筆數':>7}{'開發淨U':>10}{'驗證淨U':>10}{'單筆U':>8}{'開發單筆':>9}{'驗證單筆':>9}{'勝率%':>7}{'PF':>7}"
    print(header)
    for r in results:
        if not r.get("trades"):
            print(f"{r['symbol']:<22}{'':>6}{'':>7}{0:>7}  {r.get('note','')}")
            continue
        print(f"{r['symbol']:<22}{r['bars']//1440:>6}{r['atr_pct']:>7}{r['trades']:>7}{r['dev']:>10}"
              f"{r['val']:>10}{r['per']:>8}{r['per_dev']:>9}{r['per_val']:>9}{r['win']:>7}{r['pf']:>7}")


if __name__ == "__main__":
    main()
