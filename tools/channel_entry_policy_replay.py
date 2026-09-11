"""Entry-variant replay on the stored 30-day 1m data.

Question: can any *entry* filter turn the sample positive, given the exit policy
was already shown to be the best of six? Signals are collected once per
symbol/timeframe with the flat filter disabled, then filtered post hoc so every
variant sees the same base signals.

Development window = first 70% of the sample, validation = last 30%. A variant
is only worth deploying if the validation window is not worse than the current
rule.

Usage:
    PYTHONPATH=. .venv/bin/python tools/channel_entry_policy_replay.py
"""
from __future__ import annotations

from typing import Dict, List, Sequence, Tuple

import pandas as pd

from core.strategy import SuperTrendKeltnerStrategy
from tools.channel_exit_policy_replay import (
    WARMUP, collect_signal_records, load_frame, make_policies, simulate, summarise,
)


def to_timeframe(df: pd.DataFrame, minutes: int) -> pd.DataFrame:
    if minutes == 1:
        return df
    frame = df.copy().set_index(pd.to_datetime(df["timestamp"], unit="ms")).drop(columns=["timestamp"])
    agg = frame.resample(f"{minutes}min").agg(
        open=("open", "first"), high=("high", "max"), low=("low", "min"),
        close=("close", "last"), volume=("volume", "sum"),
    ).dropna().rename_axis("timestamp").reset_index()
    agg["timestamp"] = agg["timestamp"].astype("int64") // 10 ** 6
    return SuperTrendKeltnerStrategy().compute_indicators(agg)


def select(records, ratio_min=0.0, aligned=False, depth=0.0, breakout=False):
    picked = []
    for index, side, reason, ratio, depth_atr, is_aligned in records:
        if ratio < ratio_min or depth_atr < depth:
            continue
        if aligned and not is_aligned:
            continue
        if breakout and not reason.startswith("KC_LIVE_BODY_BREAKOUT"):
            continue
        picked.append((index, side))
    return picked


def split_index(df: pd.DataFrame) -> int:
    return WARMUP + int((len(df) - WARMUP) * 0.7)


def evaluate(df: pd.DataFrame, records, subset, **kwargs) -> Tuple[Dict, Dict]:
    split = split_index(df)
    signals = select(records, **kwargs)
    policy = make_policies()[0]
    dev = [(i, s) for i, s in signals if i < split and i >= subset[0]]
    val = [(i, s) for i, s in signals if i >= split and i < subset[1]]
    return summarise(simulate(df, dev, policy)), summarise(simulate(df, val, policy))


VARIANTS: Sequence[Tuple[str, int, Dict]] = (
    ("V0 現行：走平 0.10", 1, {"ratio_min": 0.10}),
    ("V1 走平0.10＋MA3/MA15同向", 1, {"ratio_min": 0.10, "aligned": True}),
    ("V2 走平0.10＋離軌≥0.2ATR", 1, {"ratio_min": 0.10, "depth": 0.2}),
    ("V3 走平0.10＋僅即時破軌", 1, {"ratio_min": 0.10, "breakout": True}),
    ("V4 走平0.10＋同向＋離軌0.2", 1, {"ratio_min": 0.10, "aligned": True, "depth": 0.2}),
    ("V5 5 分鐘：走平 0.10", 5, {"ratio_min": 0.10}),
    ("V6 5 分鐘：走平0.10＋同向", 5, {"ratio_min": 0.10, "aligned": True}),
)


def main() -> None:
    for symbol in ("1000PEPEUSDT", "龙虾USDT"):
        frames = {tf: to_timeframe(load_frame(symbol), tf) for tf in (1, 5)}
        records = {tf: collect_signal_records(frames[tf]) for tf in (1, 5)}
        print(f"\n=== {symbol}｜1m {len(frames[1])} 根／5m {len(frames[5])} 根｜"
              f"基礎訊號 1m {len(records[1])} 筆、5m {len(records[5])} 筆 ===")
        print(f"{'版本':<30}{'開發筆數':>8}{'開發淨U':>10}{'驗證筆數':>8}{'驗證淨U':>10}")
        for name, timeframe, kwargs in VARIANTS:
            frame = frames[timeframe]
            dev, val = evaluate(frame, records[timeframe], (0, len(frame)), **kwargs)
            print(f"{name:<30}{dev['n']:>8}{dev['net']:>10}{val['n']:>8}{val['net']:>10}")


if __name__ == "__main__":
    main()
