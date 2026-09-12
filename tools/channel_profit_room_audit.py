"""淨利空間檢查的稽核：在趨勢盤（一波疊一波）會不會把單全部擋掉？

把現行進場訊號逐一送進 entry_room，依結果分組（允許／空間不足／沒有目標），
再用 ATR 1.5/3 幾何模擬，看被擋掉的單到底是賺還是賠。
"""
from __future__ import annotations

import collections
from typing import Dict, List, Tuple

from core.config import NET_PROFIT_GUARANTEE_BUFFER, SLIPPAGE_PCT, TAKER_FEE_RATE
from core.services.entry_room_service import entry_room
from tools.channel_prototype_replay import atr_bracket, collect, one_hour_direction
from tools.channel_exit_policy_replay import WARMUP, load_frame, summarise

SYMBOLS = ("1000PEPEUSDT", "龙虾USDT")
STOP_ATR, TARGET_ATR = 1.5, 3.0


def main() -> None:
    for symbol in SYMBOLS:
        df = load_frame(symbol)
        records = collect(df, one_hour_direction(df))
        groups: Dict[str, List[Tuple[int, str]]] = collections.defaultdict(list)
        reason_counts = collections.Counter()
        for record in records:
            if record["ratio"] < 0.05 or record["efficiency"] < 0.30:
                continue
            index, side = record["index"], record["side"]
            frame = df.iloc[max(0, index - 79): index + 1]
            price = float(df["close"].iloc[index])
            room = entry_room(frame, price, side, TAKER_FEE_RATE, SLIPPAGE_PCT, NET_PROFIT_GUARANTEE_BUFFER)
            reason_counts[room["reason"]] += 1
            allowed = bool(room["allowed"])
            trend = record["trend_1h"]
            aligned = ((side == "LONG" and trend == 1) or (side == "SHORT" and trend == -1))
            if trend == 0:
                state = "1h 未知"
            else:
                state = "順 1h" if aligned else "逆 1h"
            groups[state + ("＋有空間" if allowed else "＋無空間")].append((index, side))
            key = "允許" if allowed else (
                "沒有可用目標" if room["reason"] == "KC_PROFIT_TARGET_UNAVAILABLE"
                else "空間不足" if room["reason"] == "KC_PROFIT_ROOM_INSUFFICIENT"
                else "資料無效")
            groups[key].append((index, side))
        split = WARMUP + int((len(df) - WARMUP) * 0.7)
        print(f"\n=== {symbol}｜ATR {STOP_ATR}/{TARGET_ATR}｜門檻 {NET_PROFIT_GUARANTEE_BUFFER*100:g}% ===")
        print("  原因分布：", dict(reason_counts))
        print(f"{'分組':<14}{'筆數':>7}{'開發淨U':>10}{'驗證淨U':>10}{'單筆U':>9}{'勝率%':>7}{'PF':>7}")
        for name in ("順 1h＋有空間", "順 1h＋無空間", "逆 1h＋有空間", "逆 1h＋無空間",
                     "允許", "沒有可用目標", "空間不足"):
            signals = groups.get(name) or []
            if not signals:
                continue
            trades = atr_bracket(df, signals, STOP_ATR, TARGET_ATR)
            dev = atr_bracket(df, [(i, s) for i, s in signals if i < split], STOP_ATR, TARGET_ATR)
            val = atr_bracket(df, [(i, s) for i, s in signals if i >= split], STOP_ATR, TARGET_ATR)
            stats = summarise(trades)
            per = round(stats["net"] / stats["n"], 2) if stats["n"] else 0.0
            print(f"{name:<14}{stats['n']:>7}{round(sum(dev),1):>10}{round(sum(val),1):>10}"
                  f"{per:>9}{stats['win']:>7}{stats['pf']:>7}")


if __name__ == "__main__":
    main()
