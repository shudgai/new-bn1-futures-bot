"""峰谷開倉＋峰谷平倉 vs 現行進場，同一批 30 天資料。

- 峰谷開倉：最後一根已收線是最近 L 根的最低點且收盤轉升（多單）／最高點且轉跌（空單）。
- 峰谷平倉：出現對向峰谷即平倉（多單遇峰、空單遇谷），另有 2% 硬止損。
- 全部只用已收線K判斷，不看未來。
"""
from __future__ import annotations

from typing import Dict, List, Sequence, Tuple

from tools.channel_exit_policy_replay import (
    MAX_HOLD_BARS, NOTIONAL, WARMUP, collect_signal_records, load_frame,
    make_policies, net_pnl,
)

SYMBOLS = ("1000PEPEUSDT", "龙虾USDT")
LOOKBACK = 3


def pivots(df) -> Dict[str, List[int]]:
    low = df["low"].to_numpy()
    high = df["high"].to_numpy()
    close = df["close"].to_numpy()
    troughs, peaks = [], []
    for i in range(WARMUP, len(df) - 2):
        window = slice(i - LOOKBACK, i)          # 最後 LOOKBACK 根已收線
        if low[i - 1] <= low[window].min() and close[i - 1] > close[i - 2]:
            troughs.append(i)
        if high[i - 1] >= high[window].max() and close[i - 1] < close[i - 2]:
            peaks.append(i)
    return {"trough": troughs, "peak": peaks}


def simulate_pivot(df, entries: Sequence[Tuple[int, str]], exits: Dict[str, List[int]],
                   side_of: Dict[int, str], hard_stop_pct: float = 0.02) -> List[float]:
    trade_pnls: List[float] = []
    exit_points = sorted(exits)
    cursor = 0
    for index, side in entries:
        entry = float(df["close"].iloc[index])
        qty = NOTIONAL / entry
        sign = 1 if side == "LONG" else -1
        hard = entry * (1 - sign * hard_stop_pct)
        closed = None
        target_kind = "peak" if side == "LONG" else "trough"
        for step in range(1, MAX_HOLD_BARS):
            j = index + step
            if j >= len(df):
                break
            row = df.iloc[j]
            adverse = float(row["low"]) if sign > 0 else float(row["high"])
            if (sign > 0 and adverse <= hard) or (sign < 0 and adverse >= hard):
                closed = (j, min(hard, adverse) if sign > 0 else max(hard, adverse))
                break
            if j in exits.get(target_kind, ()):
                closed = (j, float(row["close"]))
                break
        if closed is None:
            j = min(index + MAX_HOLD_BARS, len(df) - 1)
            closed = (j, float(df["close"].iloc[j]))
        trade_pnls.append(round(net_pnl(side, entry, closed[1], qty), 4))
    return trade_pnls


def stats(trades: List[float]) -> Dict[str, float]:
    if not trades:
        return {"n": 0, "net": 0.0, "win": 0.0, "pf": 0.0, "avg_win": 0.0, "avg_loss": 0.0}
    wins = [v for v in trades if v > 0]
    losses = [v for v in trades if v < 0]
    return {
        "n": len(trades),
        "net": round(sum(trades), 2),
        "win": round(len(wins) / len(trades) * 100, 1),
        "pf": round(sum(wins) / (abs(sum(losses)) or 1e-9), 3),
        "avg_win": round(sum(wins) / len(wins), 2) if wins else 0.0,
        "avg_loss": round(sum(losses) / len(losses), 2) if losses else 0.0,
    }


def main() -> None:
    policy = next(p for p in make_policies() if p.name.startswith("A 現行"))
    for symbol in SYMBOLS:
        df = load_frame(symbol)
        pv = pivots(df)
        trough_entries = [(i, "LONG") for i in pv["trough"]]
        peak_entries = [(i, "SHORT") for i in pv["peak"]]
        pivot_entries = sorted(trough_entries + peak_entries)
        records = collect_signal_records(df)
        trend_signals = [(i, side) for i, side, _r, ratio, _d, _a in records
                         if ratio >= 0.05]
        print(f"\n=== {symbol}｜30 天 ===")
        print(f"{'進場/出口':<34}{'筆數':>7}{'淨損益U':>11}{'勝率%':>7}{'PF':>7}{'平均獲利':>9}{'平均虧損':>9}")
        rows = [
            ("現行進場＋現行出口", None, trend_signals, "policy"),
            ("峰谷開倉＋峰谷平倉", None, pivot_entries, "pivot"),
            ("峰谷開倉＋現行出口", None, pivot_entries, "policy"),
        ]
        for name, _x, signals, mode in rows:
            if mode == "policy":
                from tools.channel_exit_policy_replay import simulate
                trades = simulate(df, signals, policy)
            else:
                trades = simulate_pivot(df, signals, pv, {})
            st = stats(list(trades))
            print(f"{name:<34}{st['n']:>7}{st['net']:>11}{st['win']:>7}{st['pf']:>7}"
                  f"{st['avg_win']:>9}{st['avg_loss']:>9}")


if __name__ == "__main__":
    main()
