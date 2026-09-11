"""只做趨勢盤：加趨勢盤偵測後，30 天樣本能不能轉正？

趨勢盤偵測全部只用進場前已知的已收線資料：
- ratio：最近兩根已收線中軌位移 ÷ 軌寬（現行走平門檻）
- eff：最近 20 根已收線收盤的「方向效率」= |淨位移| ÷ 總路徑
- expand：最近一根已收線軌寬 ÷ 前 20 根軌寬中位數（>1 代表波動擴張）
"""
from __future__ import annotations

from typing import Dict, List, Sequence, Tuple

from core.services.strategies import outer_strategy
from core.services.strategies.outer_strategy import aligned_entry
from tools.channel_exit_policy_replay import (
    WARMUP, Policy, load_frame, price_for_net, simulate, summarise,
)

SYMBOLS = ("1000PEPEUSDT", "龙虾USDT")


def collect(df) -> List[Tuple[int, str, float, float, float]]:
    previous = outer_strategy.CHANNEL_FLAT_MIDDLE_RATIO
    outer_strategy.CHANNEL_FLAT_MIDDLE_RATIO = 0.0
    records = []
    try:
        for index in range(WARMUP, len(df) - 2):
            frame = df.iloc[: index + 1]
            price = float(frame["close"].iloc[-1])
            decision = aligned_entry(frame, price)
            if decision.get("action") != "ENTER":
                continue
            key = "kc_middle" if "kc_middle" in frame.columns else "ema_20"
            width = float(frame["kc_upper"].iloc[-2]) - float(frame["kc_lower"].iloc[-2])
            previous_mid, latest_mid = float(frame[key].iloc[-3]), float(frame[key].iloc[-2])
            ratio = abs(latest_mid - previous_mid) / width if width > 0 else 0.0
            closes = [float(v) for v in frame["close"].iloc[-21:-1]]
            path = sum(abs(b - a) for a, b in zip(closes, closes[1:]))
            eff = abs(closes[-1] - closes[0]) / path if path > 0 else 0.0
            widths = (frame["kc_upper"] - frame["kc_lower"]).iloc[-22:-1]
            median_width = float(widths.median())
            expand = float(widths.iloc[-1]) / median_width if median_width > 0 else 0.0
            records.append((index, decision["side"], ratio, eff, expand))
    finally:
        outer_strategy.CHANNEL_FLAT_MIDDLE_RATIO = previous
    return records


VARIANTS: Sequence[Tuple[str, Dict]] = (
    ("R0 走平 0.10（現行）", dict(ratio=0.10)),
    ("R1 走平 0.05", dict(ratio=0.05)),
    ("R2 走平0.05＋效率≥0.30", dict(ratio=0.05, eff=0.30)),
    ("R3 走平0.05＋軌寬擴張≥1.10", dict(ratio=0.05, expand=1.10)),
    ("R4 走平0.05＋效率0.30＋擴張1.10", dict(ratio=0.05, eff=0.30, expand=1.10)),
    ("R5 效率≥0.40＋擴張≥1.10", dict(eff=0.40, expand=1.10)),
    ("R6 走平0.10＋效率≥0.30＋擴張1.10", dict(ratio=0.10, eff=0.30, expand=1.10)),
    ("R7 走平0.10＋效率≥0.40", dict(ratio=0.10, eff=0.40)),
)


def select(records, ratio=0.0, eff=0.0, expand=0.0, start=0, end=10 ** 12):
    return [(i, s) for i, s, r, e, x in records
            if r >= ratio and e >= eff and x >= expand and start <= i < end]


def main() -> None:
    def ladder_policy(arm, offset):
        def stop(peak, entry, qty):
            return None if peak < arm else price_for_net("LONG", entry, qty, peak - offset)
        return stop

    def ratio_policy(arm, retrace):
        def stop(peak, entry, qty):
            return None if peak < arm else price_for_net("LONG", entry, qty, peak * (1 - retrace))
        return stop

    exits = (
        ("A 4U/-2U", ladder_policy(4.0, 2.0), 0.02),
        ("B 8U/-3U", ladder_policy(8.0, 3.0), 0.02),
        ("C 2U/30%", ratio_policy(2.0, 0.30), 0.02),
        ("D 1U/25%", ratio_policy(1.0, 0.25), 0.02),
    )
    for symbol in SYMBOLS:
        df = load_frame(symbol)
        records = collect(df)
        split = WARMUP + int((len(df) - WARMUP) * 0.7)
        print(f"\n=== {symbol}｜基礎訊號 {len(records)} 筆｜"
              f"效率中位數 {sorted(r[3] for r in records)[len(records)//2]:.3f}｜"
              f"擴張中位數 {sorted(r[4] for r in records)[len(records)//2]:.3f} ===")
        for policy_name, stop_price, hard in exits:
            print(f"--- 出口 {policy_name} ---")
            print(f"{'版本':<34}{'開發筆數':>8}{'開發淨U':>10}{'驗證筆數':>8}{'驗證淨U':>10}{'驗證PF':>8}")
            for name, kw in VARIANTS:
                dev = select(records, start=0, end=split, **kw)
                val = select(records, start=split, **kw)
                policy = Policy(policy_name, stop_price, hard)
                d = summarise(simulate(df, dev, policy))
                v = summarise(simulate(df, val, policy))
                print(f"{name:<34}{d['n']:>8}{d['net']:>10}{v['n']:>8}{v['net']:>10}{v['pf']:>8}")


if __name__ == "__main__":
    main()
