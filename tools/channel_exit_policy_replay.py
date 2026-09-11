"""Offline exit-policy comparison on the stored 30-day 1m market data.

The live engine evaluates exits on every quote. This tool can only see 1m OHLC,
so it uses the pessimistic ordering inside each bar: the adverse extreme is
checked against the stop before the favourable extreme is allowed to raise the
peak. Results are therefore a lower bound for trailing policies and an upper
bound for policies that need intrabar peak precision.

Usage:
    .venv/bin/python tools/channel_exit_policy_replay.py
"""
from __future__ import annotations

import argparse
import json
import math
import pathlib
from dataclasses import dataclass
from typing import Callable, Dict, List, Sequence, Tuple

import pandas as pd

from core import config
from core.services.strategies import outer_strategy
from core.services.strategies.outer_strategy import aligned_entry
from core.strategy import SuperTrendKeltnerStrategy

DATA_DIR = pathlib.Path("reports/channel_exit_comparison/market_data")
SYMBOLS = ("1000PEPEUSDT", "龙虾USDT")
MARGIN_USDT = 75.0
LEVERAGE = config.LEVERAGE
NOTIONAL = MARGIN_USDT * LEVERAGE
WARMUP = 600
MAX_HOLD_BARS = 1440


@dataclass
class Policy:
    name: str
    stop_price: Callable[[float, float, float], float | None]  # peak_net, entry, atr -> stop price
    hard_stop_pct: float


FEE_ENTRY = config.TAKER_FEE_RATE
FEE_EXIT = config.TAKER_FEE_RATE
SLIP_ENTRY = config.SLIPPAGE_PCT
SLIP_EXIT = config.SLIPPAGE_PCT


def net_pnl(side: str, entry: float, price: float, qty: float) -> float:
    sign = 1 if side == "LONG" else -1
    entry_exec = entry * (1 + sign * SLIP_ENTRY)
    exit_exec = price * (1 - sign * SLIP_EXIT)
    return (sign * (exit_exec - entry_exec) * qty
            - (entry_exec + exit_exec) * qty * FEE_EXIT
            - entry_exec * qty * FEE_ENTRY)


def price_for_net(side: str, entry: float, qty: float, target: float) -> float:
    """Price at which the given net P&L is realised (inverse of net_pnl)."""
    sign = 1 if side == "LONG" else -1
    entry_exec = entry * (1 + sign * SLIP_ENTRY)
    if sign > 0:
        exit_exec = (target / qty + entry_exec * (1 + FEE_EXIT + FEE_ENTRY)) / (1 - FEE_EXIT)
    else:
        exit_exec = (entry_exec * (1 - FEE_EXIT - FEE_ENTRY) - target / qty) / (1 + FEE_EXIT)
    return exit_exec / (1 - sign * SLIP_EXIT)


def make_policies() -> List[Policy]:
    def ladder(arm: float, offset: float):
        def stop(peak: float, entry: float, qty: float) -> float | None:
            if peak < arm:
                return None
            return price_for_net("LONG", entry, qty, peak - offset)
        return stop

    def ratio(arm: float, retrace: float):
        def stop(peak: float, entry: float, qty: float) -> float | None:
            if peak < arm:
                return None
            return price_for_net("LONG", entry, qty, peak * (1 - retrace))
        return stop

    def floor(base: Callable, arm: float, floor_usdt: float):
        def stop(peak: float, entry: float, qty: float) -> float | None:
            level = base(peak, entry, qty)
            if peak >= arm:
                floored = price_for_net("LONG", entry, qty, floor_usdt)
                level = floored if level is None else max(level, floored)
            return level
        return stop

    return [
        Policy("A 現行 4U/-2U 硬止損2%", ladder(4.0, 2.0), 0.02),
        Policy("B 現行 + 硬止損1%", ladder(4.0, 2.0), 0.01),
        Policy("C 2U/-1U 硬止損1%", ladder(2.0, 1.0), 0.01),
        Policy("D 0.5U/回吐20% 硬止損2%", ratio(0.5, 0.20), 0.02),
        Policy("E 現行 + 保底2U->+0.3U", floor(ladder(4.0, 2.0), 2.0, 0.3), 0.02),
        Policy("F 2U/-1U 硬止損2%", ladder(2.0, 1.0), 0.02),
    ]


def load_frame(symbol: str) -> pd.DataFrame:
    path = next(DATA_DIR.glob(f"{symbol}_*.json"))
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = payload["klines"]
    df = pd.DataFrame(rows, columns=[
        "timestamp", "open", "high", "low", "close", "volume", "close_time",
        "quote_volume", "trades", "taker_base", "taker_quote", "ignore",
    ])
    for column in ("open", "high", "low", "close", "volume"):
        df[column] = df[column].astype(float)
    df["timestamp"] = df["timestamp"].astype("int64")
    return SuperTrendKeltnerStrategy().compute_indicators(df)


def collect_signal_records(df: pd.DataFrame) -> List[Tuple[int, str, str, float, float, bool]]:
    """訊號 + 事前可得的品質特徵（走平比、離軌距離、MA3/MA15 排列）。"""
    previous_ratio = outer_strategy.CHANNEL_FLAT_MIDDLE_RATIO
    outer_strategy.CHANNEL_FLAT_MIDDLE_RATIO = 0.0
    records = []
    try:
        for index in range(WARMUP, len(df) - 2):
            frame = df.iloc[: index + 1]
            price = float(frame["close"].iloc[-1])
            decision = aligned_entry(frame, price)
            if decision.get("action") != "ENTER":
                continue
            side = decision["side"]
            key = "kc_middle" if "kc_middle" in frame.columns else "ema_20"
            mid_previous, mid_latest = float(frame[key].iloc[-3]), float(frame[key].iloc[-2])
            width = float(frame["kc_upper"].iloc[-2]) - float(frame["kc_lower"].iloc[-2])
            sign = 1 if side == "LONG" else -1
            rail = float(frame["kc_upper"].iloc[-1] if side == "LONG" else frame["kc_lower"].iloc[-1])
            atr = float(frame["atr"].iloc[-2])
            depth = (sign * (price - rail) / atr) if atr > 0 else 0.0
            ma3, ma15 = float(frame["ma3"].iloc[-1]), float(frame["ma15"].iloc[-1])
            aligned = ma3 > ma15 if side == "LONG" else ma3 < ma15
            records.append((index, side, str(decision.get("reason") or ""),
                            abs(mid_latest - mid_previous) / width if width > 0 else 0.0, depth, aligned))
    finally:
        outer_strategy.CHANNEL_FLAT_MIDDLE_RATIO = previous_ratio
    return records


def collect_signals(df: pd.DataFrame, flat_ratio: float) -> List[Tuple[int, str]]:
    previous = outer_strategy.CHANNEL_FLAT_MIDDLE_RATIO
    outer_strategy.CHANNEL_FLAT_MIDDLE_RATIO = flat_ratio
    signals: List[Tuple[int, str]] = []
    try:
        for index in range(WARMUP, len(df) - 2):
            frame = df.iloc[: index + 1]
            decision = aligned_entry(frame, float(frame["close"].iloc[-1]))
            if decision.get("action") == "ENTER":
                signals.append((index, decision["side"]))
    finally:
        outer_strategy.CHANNEL_FLAT_MIDDLE_RATIO = previous
    return signals


def signal_ratios(df: pd.DataFrame) -> List[Tuple[int, str, float]]:
    """Signal list with the closed-middle displacement ratio of each signal."""
    ratios: List[Tuple[int, str, float]] = []
    for index, side in collect_signals(df, 0.0):
        frame = df.iloc[: index + 1]
        key = "kc_middle" if "kc_middle" in frame.columns else "ema_20"
        previous, latest = (float(v) for v in frame[key].iloc[-3:-1])
        width = float(frame["kc_upper"].iloc[-2]) - float(frame["kc_lower"].iloc[-2])
        ratios.append((index, side, abs(latest - previous) / width if width > 0 else 0.0))
    return ratios


def simulate(df: pd.DataFrame, signals: Sequence[Tuple[int, str]], policy: Policy) -> List[float]:
    trades: List[float] = []
    cursor = 0
    while cursor < len(signals):
        index, side = signals[cursor]
        entry = float(df["close"].iloc[index])
        qty = NOTIONAL / entry
        atr = float(df["atr"].iloc[index]) if "atr" in df else 0.0
        sign = 1 if side == "LONG" else -1
        peak = 0.0
        closed = None
        for step in range(1, MAX_HOLD_BARS):
            j = index + step
            if j >= len(df):
                break
            row = df.iloc[j]
            adverse = float(row["low"]) if sign > 0 else float(row["high"])
            favourable = float(row["high"]) if sign > 0 else float(row["low"])
            hard = entry * (1 - sign * policy.hard_stop_pct)
            trail = policy.stop_price(peak, entry, qty)
            stop = hard
            if trail is not None:
                trail = trail if sign > 0 else 2 * entry - trail  # mirror for SHORT
                stop = max(hard, trail) if sign > 0 else min(hard, trail)
            if (sign > 0 and adverse <= stop) or (sign < 0 and adverse >= stop):
                closed = (j, min(stop, adverse) if sign > 0 else max(stop, adverse))
                break
            peak = max(peak, net_pnl(side, entry, favourable, qty))
        if closed is None:
            j = min(index + MAX_HOLD_BARS, len(df) - 1)
            closed = (j, float(df["close"].iloc[j]))
        trades.append(round(net_pnl(side, entry, closed[1], qty), 4))
        cursor += 1
        while cursor < len(signals) and signals[cursor][0] <= closed[0]:
            cursor += 1
    return trades


def summarise(trades: Sequence[float]) -> Dict[str, float]:
    if not trades:
        return {"n": 0, "net": 0.0, "win": 0.0, "pf": 0.0, "dd": 0.0}
    equity = 0.0
    peak = 0.0
    dd = 0.0
    for value in trades:
        equity += value
        peak = max(peak, equity)
        dd = min(dd, equity - peak)
    wins = [v for v in trades if v > 0]
    losses = [v for v in trades if v < 0]
    gross_win = sum(wins)
    gross_loss = abs(sum(losses)) or 1e-9
    return {
        "n": len(trades),
        "net": round(sum(trades), 2),
        "win": round(len(wins) / len(trades) * 100, 1),
        "pf": round(gross_win / gross_loss, 3),
        "dd": round(dd, 2),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--flat-ratio", type=float, default=config.CHANNEL_FLAT_MIDDLE_RATIO)
    parser.add_argument("--sweep", action="store_true", help="只掃走平門檻，使用政策 A")
    parser.add_argument("--maker-entry", action="store_true",
                        help="進場用限價 maker（0.02%%，無滑價），出場維持 taker")
    args = parser.parse_args()
    if args.maker_entry:
        global FEE_ENTRY, SLIP_ENTRY
        FEE_ENTRY, SLIP_ENTRY = 0.0002, 0.0
        print("※ 情境：進場限價 maker 0.02%%、無進場滑價；出場維持 taker 0.05%% + 滑價")

    policies = make_policies()
    if args.sweep:
        for symbol in SYMBOLS:
            df = load_frame(symbol)
            scored = signal_ratios(df)
            print(f"\n=== {symbol}｜走平門檻掃描（政策 A：4U/-2U + 硬止損2%）｜全部訊號 {len(scored)} 筆 ===")
            print(f"{'門檻':<10}{'保留訊號':>10}{'筆數':>7}{'淨損益U':>12}{'勝率%':>8}{'獲利因子':>10}")
            for threshold in (0.0, 0.03, 0.05, 0.08, 0.10, 0.15, 0.20, 0.30):
                kept = [(i, s) for i, s, r in scored if r >= threshold]
                stats = summarise(simulate(df, kept, policies[0]))
                label = "不擋" if threshold == 0.0 else f"< {threshold:g}"
                print(f"{label:<10}{len(kept):>10}{stats['n']:>7}{stats['net']:>12}{stats['win']:>8}{stats['pf']:>10}")
        return
    for symbol in SYMBOLS:
        df = load_frame(symbol)
        plain = collect_signals(df, 0.0)
        filtered = collect_signals(df, args.flat_ratio)
        print(f"\n=== {symbol}｜1m K 線 {len(df)} 根｜"
              f"訊號：不擋走平 {len(plain)} 筆 → 擋走平(<{args.flat_ratio:g}) {len(filtered)} 筆 ===")
        header = f"{'政策':<26}{'筆數':>6}{'淨損益U':>12}{'勝率%':>8}{'獲利因子':>10}{'最大回撤U':>11}"
        print(header)
        for policy in policies:
            stats = summarise(simulate(df, filtered, policy))
            print(f"{policy.name:<26}{stats['n']:>6}{stats['net']:>12}{stats['win']:>8}"
                  f"{stats['pf']:>10}{stats['dd']:>11}")


if __name__ == "__main__":
    main()
