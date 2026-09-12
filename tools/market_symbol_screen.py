"""全市場篩選：先用 1m ATR% 找「跟龙蝦同性質」的高波動幣，再跑 30 天完整稽核。

第一階段（便宜）：每個幣抓 500 根 1m，算 ATR ÷ 價格 × 100，排序。
第二階段（貴）  ：對 ATR% 前 N 名（排除龙蝦）跑與實跑一致的稽核。
"""
from __future__ import annotations

import argparse
import json
import pathlib
import time
from typing import Dict, List

import ccxt

from tools.symbol_screener import run as audit_symbol

CACHE = pathlib.Path("reports/symbol_screen/market_data")
MINUTE = 60_000


def atr_pct(exchange, symbol: str, bars: int = 500) -> float:
    try:
        rows = exchange.fetch_ohlcv(symbol, "1m", limit=bars)
    except Exception:
        return 0.0
    if len(rows) < 50:
        return 0.0
    trs, prev_close = [], None
    for _, o, h, l, c, _v in rows:
        if prev_close is None:
            trs.append(h - l)
        else:
            trs.append(max(h - l, abs(h - prev_close), abs(l - prev_close)))
        prev_close = c
    if not trs:
        return 0.0
    atr = sum(trs[-200:]) / len(trs[-200:])
    price = rows[-1][4]
    return atr / price * 100 if price else 0.0


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--top", type=int, default=10)
    parser.add_argument("--min-atr-pct", type=float, default=0.5)
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--audit", action="store_true")
    args = parser.parse_args()

    exchange = ccxt.binanceusdm({"enableRateLimit": True})
    markets = exchange.load_markets()
    symbols = [s for s, m in markets.items()
               if m.get("swap") and m.get("quote") == "USDT" and m.get("active")]
    print(f"掃描 {len(symbols)} 個 USDT 永續合約…")
    scored: List[Dict] = []
    for index, symbol in enumerate(symbols, 1):
        value = atr_pct(exchange, symbol)
        if value > 0:
            scored.append({"symbol": symbol, "atr_pct": round(value, 3)})
        if index % 100 == 0:
            print(f"  …已掃 {index}/{len(symbols)}")
    scored.sort(key=lambda r: r["atr_pct"], reverse=True)
    CACHE.mkdir(parents=True, exist_ok=True)
    (CACHE / "volatility_ranking.json").write_text(json.dumps(scored, indent=1))
    print(f"\n=== 1m ATR% 排名前 25（門檻 {args.min_atr_pct}%）===")
    for row in scored[:25]:
        mark = " ← 龙蝦" if "龙虾" in row["symbol"] else (" ✅" if row["atr_pct"] >= args.min_atr_pct else "")
        print(f"  {row['symbol']:<26}{row['atr_pct']:>7.3f}%{mark}")

    if not args.audit:
        return
    candidates = [r["symbol"] for r in scored
                  if r["atr_pct"] >= args.min_atr_pct and "龙虾" not in r["symbol"]][: args.top]
    print(f"\n=== 30 天稽核（前 {len(candidates)} 名）===")
    print(f"{'幣種':<26}{'ATR%':>7}{'筆數':>7}{'開發單筆':>9}{'驗證單筆':>9}{'合計單筆':>9}{'勝率%':>7}{'PF':>7}")
    for symbol in candidates:
        stats = audit_symbol(symbol, args.days)
        if not stats.get("trades"):
            print(f"{symbol:<26}{'':>7}{0:>7}  {stats.get('note', '無訊號')}")
            continue
        print(f"{symbol:<26}{stats['atr_pct']:>7}{stats['trades']:>7}{stats['per_dev']:>9}"
              f"{stats['per_val']:>9}{stats['per']:>9}{stats['win']:>7}{stats['pf']:>7}")


if __name__ == "__main__":
    main()
