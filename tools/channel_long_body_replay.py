"""長K特例：不管 CK 中軌方向，只要出現夠大的順向長實體且收在軌外就進場。

使用者觀察：龍虾常有長綠K／長紅K，但多數發生在 CK 不明（中軌持平）時，
現行「CK 中軌必須有方向」的入口會全部錯過。

- 進場：最後一根已收線實體 ≥ X × 前一根 ATR，且收在持倉側外軌外。
- 另外標記「CK 不明」（中軌持平／方向相反）的子集合，單獨統計。
- 出口沿用兩套：現行 4U/-2U＋2% 與 1.5ATR 停損/3ATR 目標。
"""
from __future__ import annotations

from typing import Dict, List, Sequence, Tuple

from core.services.strategies.outer_strategy import ck_direction
from tools.channel_prototype_replay import atr_bracket
from tools.channel_exit_policy_replay import (
    WARMUP, load_frame, make_policies, net_pnl, simulate, summarise,
)

SYMBOLS = ("1000PEPEUSDT", "龙虾USDT")
MULTS = (1.0, 1.5, 2.0)
WINDOWS = 5


def long_body_signals(df, mult: float) -> List[dict]:
    out: List[dict] = []
    for i in range(WARMUP, len(df) - 2):
        try:
            previous = df.iloc[i - 1]
            atr = float(df.iloc[i - 2]["atr"])
            opened, close = float(previous["open"]), float(previous["close"])
            upper, lower_kc = float(previous["kc_upper"]), float(previous["kc_lower"])
        except (KeyError, IndexError, TypeError, ValueError):
            continue
        if not (atr > 0):
            continue
        body = abs(close - opened)
        if body < atr * mult:
            continue
        side = None
        if close > opened and close > upper:
            side = "LONG"
        elif close < opened and close < lower_kc:
            side = "SHORT"
        if not side:
            continue
        direction = ck_direction(df.iloc[:i - 1])
        if direction is None:
            ck_state = "CK 不明（持平／無效）"
        elif direction == side:
            ck_state = "CK 同向"
        else:
            ck_state = "CK 反向"
        out.append({"index": i, "side": side, "ck_state": ck_state,
                    "body_atr": round(body / atr, 2)})
    return out


def windows(length: int):
    span = (length - WARMUP) // WINDOWS
    return [(WARMUP + i * span, WARMUP + (i + 1) * span if i < WINDOWS - 1 else length)
            for i in range(WINDOWS)]


def main() -> None:
    policy = next(p for p in make_policies() if p.name.startswith("A 現行"))
    for symbol in SYMBOLS:
        df = load_frame(symbol)
        bounds = windows(len(df))
        print(f"\n=== {symbol}｜長K特例 ===")
        for mult in MULTS:
            records = long_body_signals(df, mult)
            if not records:
                continue
            states: Dict[str, List[dict]] = {}
            for record in records:
                states.setdefault(record["ck_state"], []).append(record)
            print(f"\n-- 長實體 ≥ {mult} ATR（共 {len(records)} 筆）--")
            for state, group in sorted(states.items()):
                signals = [(r["index"], r["side"]) for r in group]
                line = f"   {state:<20}{len(signals):>6} 筆"
                for name, geometry in (("現行出口", "policy"), ("1.5ATR/3ATR", (1.5, 3.0, None))):
                    if geometry == "policy":
                        trades = simulate(df, signals, policy)
                    else:
                        trades = atr_bracket(df, signals, *geometry)
                    stats = summarise(trades)
                    per = round(stats["net"] / stats["n"], 2) if stats["n"] else 0.0
                    line += (f"｜{name} {stats['net']:+.0f}U（單筆 {per:+.2f}、勝率 {stats['win']:.0f}%）")
                print(line)


if __name__ == "__main__":
    main()
