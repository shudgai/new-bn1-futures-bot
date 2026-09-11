"""比較兩種進場：MA3/MA15 同向 vs 破軌（含「通道內連續同色後破軌」特例）。

- 訊號來源與正式程式共用 aligned_entry（破軌入口暫時開啟以收集兩類訊號）。
- 每個訊號事後標記它同時符合哪些型態，再用現行出口政策（階梯 4U/-2U＋硬止損2%）模擬。
- 全部只用已收線K判斷型態，不看未來。
"""
from __future__ import annotations

from typing import Dict, List, Sequence, Tuple

import pandas as pd

from tools.channel_exit_policy_replay import (
    WARMUP, collect_signal_records, load_frame, make_policies, simulate, summarise,
)
from core.services.strategies import outer_strategy

SYMBOLS = ("1000PEPEUSDT", "龙虾USDT")
BODY_RATIO = 0.20


def two_body_breakout(frame: pd.DataFrame, side: str) -> bool:
    """最後兩根已收線同色、實體 ≥ 全長 20%、且都收在持倉側外軌外。"""
    rail_key = "kc_upper" if side == "LONG" else "kc_lower"
    try:
        rows = list(frame.iloc[-3:-1].iterrows())
    except IndexError:
        return False
    if len(rows) != 2:
        return False
    for _, row in rows:
        opened, close = float(row["open"]), float(row["close"])
        high, low = float(row["high"]), float(row["low"])
        body, span = abs(close - opened), high - low
        if span <= 0 or body < BODY_RATIO * span or rail_key not in row:
            return False
        if side == "LONG" and not (close > opened and close > float(row[rail_key])):
            return False
        if side == "SHORT" and not (close < opened and close < float(row[rail_key])):
            return False
    return True


def inside_run_then_break(frame: pd.DataFrame, side: str) -> bool:
    """倒數第二根在軌內同色，最後一根同色收在軌外（通道內連續同色後破軌）。"""
    rail_key = "kc_upper" if side == "LONG" else "kc_lower"
    try:
        previous, latest = frame.iloc[-3], frame.iloc[-2]
    except IndexError:
        return False
    try:
        prev_open, prev_close, prev_rail = float(previous["open"]), float(previous["close"]), float(previous[rail_key])
        last_open, last_close, last_rail = float(latest["open"]), float(latest["close"]), float(latest[rail_key])
    except (KeyError, TypeError, ValueError):
        return False
    if side == "LONG":
        return (prev_close > prev_open and prev_close <= prev_rail
                and last_close > last_open and last_close > last_rail)
    return (prev_close < prev_open and prev_close >= prev_rail
            and last_close < last_open and last_close < last_rail)


def classify(records, df: pd.DataFrame) -> Dict[str, List[Tuple[int, str]]]:
    groups: Dict[str, List[Tuple[int, str]]] = {
        "全部訊號": [], "MA3/MA15 同向": [], "破軌：即時長K": [],
        "破軌：兩根同色收線": [], "特例：軌內同色後破軌": [],
    }
    for index, side, reason, _ratio, _depth, aligned in records:
        frame = df.iloc[max(0, index - 40): index + 1]
        pair = (index, side)
        groups["全部訊號"].append(pair)
        if aligned:
            groups["MA3/MA15 同向"].append(pair)
        if str(reason).startswith("KC_LIVE_BODY_BREAKOUT"):
            groups["破軌：即時長K"].append(pair)
        if two_body_breakout(frame, side):
            groups["破軌：兩根同色收線"].append(pair)
        if inside_run_then_break(frame, side):
            groups["特例：軌內同色後破軌"].append(pair)
    return groups


def main() -> None:
    policy = next(p for p in make_policies() if p.name.startswith("A 現行"))
    previous = outer_strategy.CHANNEL_LIVE_BODY_BREAKOUT_ENABLED
    outer_strategy.CHANNEL_LIVE_BODY_BREAKOUT_ENABLED = True
    try:
        for symbol in SYMBOLS:
            df = load_frame(symbol)
            records = collect_signal_records(df)
            groups = classify(records, df)
            print(f"\n=== {symbol}｜{policy.name} ===")
            print(f"{'進場型態':<24}{'筆數':>7}{'淨損益U':>11}{'單筆U':>9}{'勝率%':>7}{'獲利因子':>9}")
            for name, signals in groups.items():
                trades = simulate(df, signals, policy)
                stats = summarise(trades)
                per = round(stats["net"] / stats["n"], 2) if stats["n"] else 0.0
                print(f"{name:<24}{stats['n']:>7}{stats['net']:>11}{per:>9}{stats['win']:>7}{stats['pf']:>9}")
    finally:
        outer_strategy.CHANNEL_LIVE_BODY_BREAKOUT_ENABLED = previous


if __name__ == "__main__":
    main()
