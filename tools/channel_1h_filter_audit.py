"""量化 1h 過濾：順 1h / 逆 1h 的進場，各自表現如何？

用現行進場條件（走平 ≥0.05、20 根方向效率 ≥0.30）收集訊號後，
依 1h SuperTrend 方向分成「順」「逆」「未知」三組，各組用 ATR 1.5/3 幾何模擬。
"""
from __future__ import annotations

from typing import Dict, List, Tuple

from tools.channel_prototype_replay import atr_bracket, collect, one_hour_direction
from tools.channel_exit_policy_replay import WARMUP, load_frame, summarise

SYMBOLS = ("1000PEPEUSDT", "龙虾USDT")
STOP_ATR, TARGET_ATR = 1.5, 3.0


def main() -> None:
    for symbol in SYMBOLS:
        df = load_frame(symbol)
        records = collect(df, one_hour_direction(df))
        groups: Dict[str, List[Tuple[int, str]]] = {"順 1h": [], "逆 1h": [], "1h 未知": []}
        for record in records:
            if record["ratio"] < 0.05 or record["efficiency"] < 0.30:
                continue
            side, trend = record["side"], record["trend_1h"]
            if trend == 0:
                key = "1h 未知"
            elif (side == "LONG" and trend == 1) or (side == "SHORT" and trend == -1):
                key = "順 1h"
            else:
                key = "逆 1h"
            groups[key].append((record["index"], side))
        length = len(df)
        split = WARMUP + int((length - WARMUP) * 0.7)
        print(f"\n=== {symbol}｜ATR {STOP_ATR}/{TARGET_ATR} ===")
        print(f"{'分組':<10}{'全部筆數':>8}{'開發淨U':>10}{'驗證淨U':>10}{'單筆U':>9}{'勝率%':>7}{'PF':>7}")
        for name, signals in groups.items():
            trades = atr_bracket(df, signals, STOP_ATR, TARGET_ATR)
            dev = atr_bracket(df, [(i, s) for i, s in signals if i < split], STOP_ATR, TARGET_ATR)
            val = atr_bracket(df, [(i, s) for i, s in signals if i >= split], STOP_ATR, TARGET_ATR)
            stats = summarise(trades)
            per = round(stats["net"] / stats["n"], 2) if stats["n"] else 0.0
            print(f"{name:<10}{stats['n']:>8}{round(sum(dev),1):>10}{round(sum(val),1):>10}"
                  f"{per:>9}{stats['win']:>7}{stats['pf']:>7}")


if __name__ == "__main__":
    main()
