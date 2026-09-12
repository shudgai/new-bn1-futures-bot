"""多窗 walk-forward：把 30 天切成 5 個 6 天窗口，檢查每個組合是否穩定。

目的：確認「進場過濾 × 出口幾何」的改善不是單一期間的曲線擬合。
通過標準（暫定）：5 窗中有 ≥4 窗不虧，且總計為正。
"""
from __future__ import annotations

from typing import List, Sequence, Tuple

from tools.channel_prototype_replay import ENTRIES, EXITS, atr_bracket, collect, one_hour_direction
from tools.channel_exit_policy_replay import (
    WARMUP, load_frame, make_policies, simulate,
)

SYMBOLS = ("1000PEPEUSDT", "龙虾USDT")
WINDOWS = 5


def windows(length: int) -> List[Tuple[int, int]]:
    span = (length - WARMUP) // WINDOWS
    return [(WARMUP + i * span, WARMUP + (i + 1) * span if i < WINDOWS - 1 else length)
            for i in range(WINDOWS)]


def main() -> None:
    policy = next(p for p in make_policies() if p.name.startswith("A 現行"))
    for symbol in SYMBOLS:
        df = load_frame(symbol)
        direction = one_hour_direction(df)
        records = collect(df, direction)
        bounds = windows(len(df))
        print(f"\n=== {symbol}｜基礎訊號 {len(records)} 筆｜每窗約 {(bounds[0][1]-bounds[0][0])//1440} 天 ===")
        for entry_name, test in ENTRIES.items():
            picked = [r for r in records if test(r)]
            print(f"\n-- {entry_name} --")
            header = f"{'出口幾何':<24}" + "".join(f"{'W'+str(i+1):>12}" for i in range(WINDOWS)) + f"{'合計':>10}{'不虧窗':>8}"
            print(header)
            for exit_name, geometry in EXITS.items():
                cells, total, positive = [], 0.0, 0
                for low, high in bounds:
                    signals = [(r["index"], r["side"]) for r in picked if low <= r["index"] < high]
                    if geometry == "policy":
                        trades = simulate(df, signals, policy)
                    else:
                        stop_atr, target_atr, trail = geometry
                        trades = atr_bracket(df, signals, stop_atr, target_atr, trail)
                    net = round(sum(trades), 1)
                    total += net
                    if net >= 0:
                        positive += 1
                    cells.append(f"{net:+.1f}({len(trades)})")
                print(f"{exit_name:<24}" + "".join(f"{c:>12}" for c in cells)
                      + f"{round(total,1):>10}{f'{positive}/{WINDOWS}':>8}")


if __name__ == "__main__":
    main()
