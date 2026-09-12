"""1.0／1.5／2.0 長K門檻並行統計：同一段行情、同一組規則，只切換門檻。

使用者 2026-09-13 核准：用數據決定長K門檻（CHANNEL_LIVE_BREAKOUT_BODY_ATR /
CHANNEL_LONG_BODY_ENTRY_ATR）要用哪一個，而不是憑感覺。

輸出：每個門檻的進場筆數、勝率、單筆平均、合計淨損益、最差單筆，
並附上現行階梯出口的對照。
"""
from __future__ import annotations

import json
import pathlib
import time
import urllib.request

from tools.channel_exit_policy_replay import (
    DATA_DIR, load_frame, make_policies, simulate, summarise,
)
from tools.channel_long_body_replay import long_body_signals
from tools.channel_prototype_replay import atr_bracket

SYMBOLS = ("龙虾USDT", "LABUSDT")
MULTS = (1.0, 1.5, 2.0)
DAYS = 30
BASE = "https://fapi.binance.com"
ATR_EXIT = (1.5, 3.0, None)      # 現行：1.5 ATR 停損 / 3 ATR 目標


def ensure_cache(symbol: str, days: int = DAYS) -> None:
    """沒有快取就抓幣安 1m K 線（與 channel_exit_comparison 相同格式）。"""
    if list(DATA_DIR.glob(f"{symbol}_*.json")):
        return
    end = int(time.time() * 1000)
    start = end - days * 24 * 3600 * 1000
    rows = []
    cursor = start
    while cursor < end:
        url = (f"{BASE}/fapi/v1/klines?symbol={symbol}&interval=1m"
               f"&limit=1500&startTime={cursor}")
        data = json.load(urllib.request.urlopen(url, timeout=30))
        if not data:
            break
        rows.extend(data)
        cursor = int(data[-1][0]) + 60_000
        if len(data) < 1500:
            break
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    path = DATA_DIR / f"{symbol}_{start}_{cursor}.json"
    path.write_text(json.dumps({"klines": rows}), encoding="utf-8")


def report(symbol: str) -> None:
    ensure_cache(symbol)
    df = load_frame(symbol)
    policy = next(p for p in make_policies() if p.name.startswith("A 現行"))
    print(f"\n=== {symbol}｜最近 {DAYS} 天 1m｜有效 {len(df)} 根 ===")
    header = (f"{'門檻':<6}{'筆數':>7}{'勝率':>8}{'單筆平均':>11}"
              f"{'合計':>11}{'最差單筆':>11}   ｜現行階梯出口")
    print(header)
    for mult in MULTS:
        signals = [(r["index"], r["side"]) for r in long_body_signals(df, mult)]
        trades = atr_bracket(df, signals, *ATR_EXIT)
        stats = summarise(trades)
        per = stats["net"] / stats["n"] if stats["n"] else 0.0
        worst = min(trades) if trades else 0.0
        ladder_trades = simulate(df, signals, policy)
        ladder = summarise(ladder_trades)
        ladder_per = ladder["net"] / ladder["n"] if ladder["n"] else 0.0
        print(f"{mult:<6}{stats['n']:>7}{stats['win']:>7.0f}%{per:>+11.2f}"
              f"{stats['net']:>+11.1f}{worst:>+11.1f}"
              f"   ｜{ladder['net']:+.1f}U / 單筆 {ladder_per:+.2f}U"
              f"（勝率 {ladder['win']:.0f}%、{ladder['n']} 筆）")


def main() -> None:
    for symbol in SYMBOLS:
        report(symbol)


if __name__ == "__main__":
    main()
