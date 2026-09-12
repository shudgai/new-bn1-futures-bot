"""長K進場的目標倍數要多少？1 ATR（入袋）vs 2/3 ATR（抱波段）。

訊號＝長K型進場：即時長K破軌（逐報價、實體 ≥1 ATR）或長K特例（收線實體 ≥2 ATR）。
出口固定停損 1.5 ATR，比較目標 1.0 / 2.0 / 3.0 ATR。
"""
from __future__ import annotations

from typing import List, Tuple

from core.config import NET_PROFIT_GUARANTEE_BUFFER, SLIPPAGE_PCT, TAKER_FEE_RATE
from core.services.entry_room_service import entry_room
from core.services.strategies.outer_strategy import live_body_breakout_side, long_body_side
from tools.channel_prototype_replay import one_hour_direction
from tools.channel_prototype_replay import atr_bracket
from tools.channel_exit_policy_replay import WARMUP, load_frame, summarise

SYMBOLS = ("1000PEPEUSDT", "龙虾USDT")
STOP_ATR = 1.5
TARGETS = (1.0, 2.0, 3.0)


def long_body_signals(df) -> List[Tuple[int, str]]:
    signals: List[Tuple[int, str]] = []
    for index in range(WARMUP, len(df) - 2):
        frame = df.iloc[: index + 1]
        price = float(frame["close"].iloc[-1])
        side = live_body_breakout_side(frame, price) or long_body_side(frame, 2.0)
        if side:
            signals.append((index, side))
    return signals


def filtered_signals(df) -> List[Tuple[int, str]]:
    """再加上實跑會套用的兩關：順 1h ＋ 有淨利空間。"""
    trend = one_hour_direction(df)
    kept: List[Tuple[int, str]] = []
    for index, side in long_body_signals(df):
        direction = trend[index] if index < len(trend) else 0
        aligned = ((side == "LONG" and direction == 1) or (side == "SHORT" and direction == -1))
        if not aligned:
            continue
        frame = df.iloc[max(0, index - 79): index + 1]
        room = entry_room(frame, float(df["close"].iloc[index]), side,
                          TAKER_FEE_RATE, SLIPPAGE_PCT, NET_PROFIT_GUARANTEE_BUFFER)
        if room["allowed"]:
            kept.append((index, side))
    return kept


def main() -> None:
    for symbol in SYMBOLS:
        df = load_frame(symbol)
        signals = filtered_signals(df)
        split = WARMUP + int((len(df) - WARMUP) * 0.7)
        print(f"\n=== {symbol}｜長K且順1h且有淨利空間的訊號 {len(signals)} 筆｜停損固定 {STOP_ATR} ATR ===")
        print(f"{'目標':<8}{'筆數':>7}{'開發淨U':>10}{'驗證淨U':>10}{'單筆U':>9}{'勝率%':>7}{'PF':>7}{'平均獲利':>9}{'平均虧損':>9}")
        for target in TARGETS:
            trades = atr_bracket(df, signals, STOP_ATR, target)
            dev = atr_bracket(df, [(i, s) for i, s in signals if i < split], STOP_ATR, target)
            val = atr_bracket(df, [(i, s) for i, s in signals if i >= split], STOP_ATR, target)
            wins = [t for t in trades if t > 0]
            losses = [t for t in trades if t < 0]
            stats = summarise(trades)
            per = round(stats["net"] / stats["n"], 2) if stats["n"] else 0.0
            aw = round(sum(wins) / len(wins), 2) if wins else 0.0
            al = round(sum(losses) / len(losses), 2) if losses else 0.0
            print(f"{target:<8.1f}{stats['n']:>7}{round(sum(dev),1):>10}{round(sum(val),1):>10}"
                  f"{per:>9}{stats['win']:>7}{stats['pf']:>7}{aw:>9}{al:>9}")


if __name__ == "__main__":
    main()
