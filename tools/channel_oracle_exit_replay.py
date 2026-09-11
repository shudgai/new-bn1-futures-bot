"""上限測試：每次進場後「賣在期間最高點」能賺多少？

- 逐筆計算最大有利幅度（MFE）、最大不利幅度（MAE）、放到期滿的損益。
- oracle 上限 = 用 MFE 價位平倉（事後才知道，實務做不到，所以是上限）。
- 若 oracle 也是負的 → 問題在進場；若 oracle 是正的 → 問題在平倉。
"""
from __future__ import annotations

from typing import List, Sequence, Tuple

from tools.channel_exit_policy_replay import (
    NOTIONAL, WARMUP, collect_signal_records, load_frame, net_pnl,
)

SYMBOLS = ("1000PEPEUSDT", "龙虾USDT")
HORIZONS = (60, 240, 720)


def analyse(df, signals: Sequence[Tuple[int, str]], horizon: int) -> dict:
    rows: List[dict] = []
    for index, side in signals:
        end = min(index + horizon, len(df) - 1)
        if end <= index:
            continue
        entry = float(df["close"].iloc[index])
        qty = NOTIONAL / entry
        sign = 1 if side == "LONG" else -1
        window = df.iloc[index + 1: end + 1]
        if window.empty:
            continue
        if sign > 0:
            best = float(window["high"].max())
            worst = float(window["low"].min())
        else:
            best = float(window["low"].min())
            worst = float(window["high"].max())
        rows.append({
            "oracle": net_pnl(side, entry, best, qty),
            "worst": net_pnl(side, entry, worst, qty),
            "hold": net_pnl(side, entry, float(df["close"].iloc[end]), qty),
        })
    if not rows:
        return {}
    count = len(rows)
    return {
        "n": count,
        "oracle": round(sum(r["oracle"] for r in rows) / count, 2),
        "worst": round(sum(r["worst"] for r in rows) / count, 2),
        "hold": round(sum(r["hold"] for r in rows) / count, 2),
        "profitable_share": round(100 * sum(1 for r in rows if r["oracle"] > 0) / count, 1),
    }


def main() -> None:
    for symbol in SYMBOLS:
        df = load_frame(symbol)
        records = collect_signal_records(df)
        signals = [(i, side) for i, side, _r, ratio, _d, _a in records if ratio >= 0.05]
        print(f"\n=== {symbol}｜訊號 {len(signals)} 筆（單位：USDT／筆）===")
        print(f"{'持有上限':<10}{'筆數':>7}{'賣在最高(上限)':>15}{'賣在最低':>11}{'到期平倉':>11}{'上限為正比例':>13}")
        for horizon in HORIZONS:
            stats = analyse(df, signals, horizon)
            if not stats:
                continue
            print(f"{str(horizon) + ' 根':<10}{stats['n']:>7}{stats['oracle']:>15}{stats['worst']:>11}"
                  f"{stats['hold']:>11}{str(stats['profitable_share']) + '%':>13}")


if __name__ == "__main__":
    main()
