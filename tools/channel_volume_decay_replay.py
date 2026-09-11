"""量能衰退平倉的 30 天回放：它到底幫多少？參數要多嚴？

判定完全沿用正式程式的 core.strategy.has_real_volume_decay（真量能衰退），
每根 K 以「截至該根的資料」評估，未收線那一根不列入（與live一致）。
"""
from __future__ import annotations

from typing import Dict, List, Sequence, Tuple

import pandas as pd

from core.strategy import has_real_volume_decay
from tools.channel_exit_policy_replay import (
    MAX_HOLD_BARS, NOTIONAL, WARMUP, load_frame, make_policies, net_pnl,
    collect_signal_records, make_policies as _policies,
)

SYMBOLS = ("1000PEPEUSDT", "龙虾USDT")
LOOKBACK = 16


def decay_flags(df: pd.DataFrame, lookback: int, ratio: float):
    bottom = [False] * len(df)
    top = [False] * len(df)
    for index in range(WARMUP, len(df)):
        window = df.iloc[max(0, index - lookback - 1): index + 1]
        bottom[index] = has_real_volume_decay(window, 1, lookback, ratio)
        top[index] = has_real_volume_decay(window, -1, lookback, ratio)
    return bottom, top


def simulate(df: pd.DataFrame, signals, policy, bottom, top) -> Tuple[List[float], int]:
    ma3 = df["ma3"].to_numpy()
    trades: List[float] = []
    decay_exits = 0
    cursor = 0
    while cursor < len(signals):
        index, side = signals[cursor]
        entry = float(df["close"].iloc[index])
        qty = NOTIONAL / entry
        sign = 1 if side == "LONG" else -1
        peak = 0.0
        closed = None
        for step in range(1, MAK := MAX_HOLD_BARS):
            j = index + step
            if j >= len(df):
                break
            row = df.iloc[j]
            adverse = float(row["low"]) if sign > 0 else float(row["high"])
            favourable = float(row["high"]) if sign > 0 else float(row["low"])
            turned = (ma3[j - 1] < ma3[j - 2]) if sign > 0 else (ma3[j - 1] > ma3[j - 2])
            decay = top[j] if sign > 0 else bottom[j]
            if turned and decay:
                closed = (j, float(row["close"]))
                decay_exits += 1
                break
            hard = entry * (1 - sign * policy.hard_stop_pct)
            trail = policy.stop_price(peak, entry, qty)
            stop = hard
            if trail is not None:
                trail = trail if sign > 0 else 2 * entry - trail
                stop = max(hard, trail) if sign > 0 else min(hard, trail)
            if (sign > 0 and adverse <= stop) or (sign < 0 and adverse >= stop):
                closed = (j, min(stop, adverse) if sign > 0 else max(stop, adverse))
                break
            peak = max(peak, net_pnl(side, entry, favourable, qty))
        if closed is None:
            j = min(index + MAK, len(df) - 1)
            closed = (j, float(df["close"].iloc[j]))
        trades.append(round(net_pnl(side, entry, closed[1], qty), 4))
        cursor += 1
        while cursor < len(signals) and signals[cursor][0] <= closed[0]:
            cursor += 1
    return trades, decay_exits


def summarise(trades):
    if not trades:
        return {"n": 0, "net": 0.0, "win": 0.0, "pf": 0.0}
    wins = [v for v in trades if v > 0]
    losses = [v for v in trades if v < 0]
    return {
        "n": len(trades),
        "net": round(sum(trades), 2),
        "win": round(len(wins) / len(trades) * 100, 1),
        "pf": round(sum(wins) / (abs(sum(losses)) or 1e-9), 3),
    }


def main() -> None:
    policy = make_policies()[0]
    current = make_policies()[0]
    for symbol in SYMBOLS:
        df = load_frame(symbol)
        records = collect_signal_records(df)
        signals = [(i, side) for i, side, _r, ratio, _d, _a in records
                   if ratio >= 0.05 and i >= WARMUP]
        print(f"\n=== {symbol}｜訊號 {len(signals)} 筆｜出口政策 {policy.name} ===")
        print(f"{'版本':<28}{'筆數':>7}{'淨損益U':>11}{'勝率%':>7}{'獲利因子':>9}{'量衰出場':>9}")
        stats = summarise(simulate(df, signals, policy, [False] * len(df), [False] * len(df))[0])
        print(f"{'不開量能衰退平倉':<28}{stats['n']:>7}{stats['net']:>11}{stats['win']:>7}{stats['pf']:>9}{0:>9}")
        for lookback, ratio in ((16, 0.7), (24, 0.7)):
            bottom, top = decay_flags(df, lookback, ratio)
            trades, decay_exits = simulate(df, signals, policy, bottom, top)
            stats = summarise(trades)
            print(f"{f'衰退平倉 lookback={lookback} ratio={ratio}':<28}"
                  f"{stats['n']:>7}{stats['net']:>11}{stats['win']:>7}{stats['pf']:>9}{decay_exits:>9}")


if __name__ == "__main__":
    main()
