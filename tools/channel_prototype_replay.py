"""離線原型：提高賠率（ATR 停損／目標）與提高勝率（更嚴趨勢過濾）。

- 開發期 = 前 70%，驗證期 = 後 30%。只有驗證期不虧才算通過。
- 進場特徵全部只用進場前已知的已收線資料：走平比、20 根方向效率、1h SuperTrend 方向。
- 出口幾何：現行階梯 4U/-2U＋2% 硬止損，以及 1 ATR 停損/2 ATR 目標、1.5/3、1 ATR 移動停損。
"""
from __future__ import annotations

from typing import Dict, List, Sequence, Tuple

import pandas as pd

from core.services.strategies import outer_strategy
from core.services.strategies.outer_strategy import aligned_entry
from core.strategy import SuperTrendKeltnerStrategy
from tools.channel_exit_policy_replay import (
    MAX_HOLD_BARS, NOTIONAL, WARMUP, load_frame, make_policies, net_pnl, simulate, summarise,
)

SYMBOLS = ("1000PEPEUSDT", "龙虾USDT")


def one_hour_direction(df: pd.DataFrame) -> List[int]:
    """1h SuperTrend 方向，對應到每根 1m K（只用已完成的 1h K）。"""
    frame = df.copy()
    frame.index = pd.to_datetime(frame["timestamp"], unit="ms")
    agg = frame.resample("1h").agg(open=("open", "first"), high=("high", "max"),
                                   low=("low", "min"), close=("close", "last"),
                                   volume=("volume", "sum")).dropna()
    computed = SuperTrendKeltnerStrategy().compute_indicators(agg.reset_index(drop=True))
    direction = computed["st_direction"].tolist()
    stamps = [int(ts.value // 10 ** 6) for ts in agg.index]
    series: List[int] = []
    pointer = -1
    for ts in df["timestamp"].tolist():
        while pointer + 1 < len(stamps) and stamps[pointer + 1] <= ts:
            pointer += 1
        series.append(int(direction[pointer]) if pointer >= 0 else 0)
    return series


def collect(df: pd.DataFrame, hour_direction: Sequence[int]) -> List[Dict]:
    records: List[Dict] = []
    previous = outer_strategy.CHANNEL_FLAT_MIDDLE_RATIO
    outer_strategy.CHANNEL_FLAT_MIDDLE_RATIO = 0.0
    try:
        for index in range(WARMUP, len(df) - 2):
            frame = df.iloc[: index + 1]
            price = float(frame["close"].iloc[-1])
            decision = aligned_entry(frame, price)
            if decision.get("action") != "ENTER":
                continue
            side = decision["side"]
            key = "kc_middle" if "kc_middle" in frame.columns else "ema_20"
            width = float(frame["kc_upper"].iloc[-2]) - float(frame["kc_lower"].iloc[-2])
            previous_mid, latest_mid = float(frame[key].iloc[-3]), float(frame[key].iloc[-2])
            closes = [float(v) for v in frame["close"].iloc[-21:-1]]
            path = sum(abs(b - a) for a, b in zip(closes, closes[1:]))
            efficiency = abs(closes[-1] - closes[0]) / path if path > 0 else 0.0
            trend_1h = hour_direction[index] if index < len(hour_direction) else 0
            records.append({
                "index": index, "side": side,
                "ratio": abs(latest_mid - previous_mid) / width if width > 0 else 0.0,
                "efficiency": efficiency, "trend_1h": trend_1h,
            })
    finally:
        outer_strategy.CHANNEL_FLAT_MIDDLE_RATIO = previous
    return records


def atr_bracket(df: pd.DataFrame, signals, stop_atr: float, target_atr: float,
                trail_atr: float | None = None) -> List[float]:
    trades: List[float] = []
    cursor = 0
    while cursor < len(signals):
        index, side = signals[cursor]
        entry = float(df["close"].iloc[index])
        atr = float(df["atr"].iloc[index]) if "atr" in df.columns else 0.0
        qty = NOTIONAL / entry
        sign = 1 if side == "LONG" else -1
        if not atr or atr <= 0:
            cursor += 1
            continue
        stop = entry - sign * stop_atr * atr
        target = entry + sign * target_atr * atr
        best = entry
        closed = None
        for step in range(1, MAX_HOLD_BARS):
            j = index + step
            if j >= len(df):
                break
            row = df.iloc[j]
            adverse = float(row["low"]) if sign > 0 else float(row["high"])
            favourable = float(row["high"]) if sign > 0 else float(row["low"])
            if (sign > 0 and adverse <= stop) or (sign < 0 and adverse >= stop):
                closed = (j, min(stop, adverse) if sign > 0 else max(stop, adverse))
                break
            if not trail_atr:
                if (sign > 0 and favourable >= target) or (sign < 0 and favourable <= target):
                    closed = (j, max(target, favourable) if sign > 0 else min(target, favourable))
                    break
            else:
                if sign * (favourable - best) > 0:
                    best = favourable
                moved = sign * (best - entry)
                if moved >= trail_atr * atr:
                    trailed = best - sign * trail_atr * atr
                    stop = max(stop, trailed) if sign > 0 else min(stop, trailed)
        if closed is None:
            j = min(index + MAX_HOLD_BARS, len(df) - 1)
            closed = (j, float(df["close"].iloc[j]))
        trades.append(round(net_pnl(side, entry, closed[1], qty), 4))
        cursor += 1
        while cursor < len(signals) and signals[cursor][0] <= closed[0]:
            cursor += 1
    return trades


ENTRIES = {
    "E0 現行（走平0.05）": lambda r: r["ratio"] >= 0.05,
    "E1 ＋效率≥0.30": lambda r: r["ratio"] >= 0.05 and r["efficiency"] >= 0.30,
    "E2 ＋1h 同向": lambda r: r["ratio"] >= 0.05 and ((r["side"] == "LONG" and r["trend_1h"] == 1)
                                                     or (r["side"] == "SHORT" and r["trend_1h"] == -1)),
    "E3 ＋效率0.30＋1h同向": lambda r: (r["ratio"] >= 0.05 and r["efficiency"] >= 0.30
                                      and ((r["side"] == "LONG" and r["trend_1h"] == 1)
                                           or (r["side"] == "SHORT" and r["trend_1h"] == -1))),
}

EXITS = {
    "現行 4U/-2U＋2%": "policy",
    "1ATR 停損／2ATR 目標": (1.0, 2.0, None),
    "1.5ATR 停損／3ATR 目標": (1.5, 3.0, None),
    "1ATR 停損／1ATR 移動停損": (1.5, 999.0, 1.0),
}

SPLIT = 0.7


def report(name: str, trades: List[float]) -> str:
    stats = summarise(trades)
    per = round(stats["net"] / stats["n"], 2) if stats["n"] else 0.0
    return f"{name}：{stats['n']} 筆 {stats['net']:+.1f}U（單筆 {per:+.2f}、勝率 {stats['win']:.0f}%、PF {stats['pf']}）"


def main() -> None:
    policy = next(p for p in make_policies() if p.name.startswith("A 現行"))
    for symbol in SYMBOLS:
        df = load_frame(symbol)
        direction = one_hour_direction(df)
        records = collect(df, direction)
        split = WARMUP + int((len(df) - WARMUP) * SPLIT)
        print(f"\n=== {symbol}｜基礎訊號 {len(records)} 筆｜開發/驗證切點 {split} ===")
        for entry_name, test in ENTRIES.items():
            picked = [r for r in records if test(r)]
            dev = [(r["index"], r["side"]) for r in picked if r["index"] < split]
            val = [(r["index"], r["side"]) for r in picked if r["index"] >= split]
            print(f"\n-- 進場 {entry_name}（開發 {len(dev)}／驗證 {len(val)}）--")
            for exit_name, geometry in EXITS.items():
                if geometry == "policy":
                    dev_trades = simulate(df, dev, policy)
                    val_trades = simulate(df, val, policy)
                else:
                    stop_atr, target_atr, trail = geometry
                    dev_trades = atr_bracket(df, dev, stop_atr, target_atr, trail)
                    val_trades = atr_bracket(df, val, stop_atr, target_atr, trail)
                print(f"   {exit_name:<22}｜開發 {report('', dev_trades).strip('：')}"
                      f"｜驗證 {report('', val_trades).strip('：')}")


if __name__ == "__main__":
    main()
