"""Backtest of the swing core (entry gatekeeper + ExitGatekeeper) on 1m klines.

Approximation: decisions use CLOSED bars; entries/structure exits fill at the next open;
ratchet fills at the close +/- 0.25% limit offset; peak ROE uses prior bars' favorable
extremes and the current close. Costs: taker fee + slippage per side. No per-trade hard stop.
Usage: python3 tools/backtest_swing.py [SYMBOL] [DAYS]
"""
import sys
from collections import Counter

import ccxt
import pandas as pd

from core.config import LEVERAGE, SLIPPAGE_PCT, TAKER_FEE_RATE
from core.indicators.dual_timeframe_provider import DualTimeframeProvider
from core.swing import Position, SwingState, evaluate_dump_reversal, evaluate_entry, evaluate_exit, floor_roe
from core.swing.gatekeeper import COOLDOWN_BARS, YaoBiEntryGatekeeper
from core.swing.models import Bar
from core.swing.ratchet import limit_price

SYMBOL = sys.argv[1] if len(sys.argv) > 1 else "龙虾/USDT:USDT"
DAYS = float(sys.argv[2]) if len(sys.argv) > 2 else 5
COST = 2 * (TAKER_FEE_RATE + SLIPPAGE_PCT)
COLS = ("open", "high", "low", "close", "volume", "ma5", "ma15", "kc_upper", "kc_lower", "atr")


def fetch(symbol, days):
    ex = ccxt.binanceusdm()
    need, rows, since = int(days * 1440), [], None
    end = ex.milliseconds()
    since = end - need * 60_000
    while since < end:
        batch = ex.fetch_ohlcv(symbol, "1m", since=since, limit=1500)
        if not batch:
            break
        rows += batch
        since = batch[-1][0] + 60_000
    df = pd.DataFrame(rows, columns=["timestamp", "open", "high", "low", "close", "volume"])
    return df.drop_duplicates("timestamp").reset_index(drop=True)


def roe(side, entry, px):
    move = (px - entry) / entry if side == "LONG" else (entry - px) / entry
    return (move - COST) * LEVERAGE * 100


def run(df):
    df = DualTimeframeProvider.compute_indicators_df(df)
    bars = [Bar(*[float(r[c]) for c in COLS], is_closed=True) for _, r in df.iterrows()]
    trades, blocked, pos = [], Counter(), None
    state = SwingState()
    for i in range(30, len(bars) - 1):
        hist, nxt = bars[max(0, i - 29):i + 1], bars[i + 1]
        if state.bars_since_close is not None:
            state.bars_since_close += 1
            if state.bars_since_close > COOLDOWN_BARS + 5:
                state.bars_since_close = None
        if pos:
            b = bars[i]
            favorable = b.high if pos["side"] == "LONG" else b.low
            cur = roe(pos["side"], pos["entry"], b.close)
            pos["peak"] = max(pos["peak"], pos["ext"], cur)
            pos["ext"] = max(pos["ext"], roe(pos["side"], pos["entry"], favorable))
            held = i - pos["i"]
            flip = evaluate_dump_reversal(pos["side"], hist) if held >= 1 else None
            d = None if flip else evaluate_exit(Position(pos["side"], held, pos["peak"], cur), b, b.close,
                                                prior_bars=hist[:-1])
            if flip or d:
                px = limit_price(pos["side"], b.close) if d and d.order_type == "LIMIT" else nxt.open
                r = roe(pos["side"], pos["entry"], px)
                trades.append(dict(side=pos["side"], mode=pos["mode"], held=held, peak=pos["peak"], roe=r,
                                   exit=d.reason if d else "DUMP_FLUSH_FLIP",
                                   locked=bool(d and d.reason == "RATCHET_PROFIT_LOCK")))
                state.last_close_side, state.bars_since_close = pos["side"], 0
                pos = None
                if flip:
                    pos = dict(side=flip.side, entry=nxt.open, i=i + 1, peak=0.0, ext=0.0, mode="DUMP_FLUSH_REVERSAL")
            continue
        sig = evaluate_entry(hist, state)
        if sig:
            pos = dict(side=sig.side, entry=nxt.open, i=i + 1, peak=0.0, ext=0.0, mode=sig.mode)
        else:
            for side in ("LONG", "SHORT"):
                ok, why = YaoBiEntryGatekeeper.can_enter(hist, side, state)
                if not ok:
                    blocked[why] += 1
    return trades, blocked


def report(trades, blocked, nbars):
    t = pd.DataFrame(trades)
    print(f"K棒數 {nbars}，總交易次數 {len(t)}")
    if t.empty:
        print("無交易")
    else:
        w, l = t[t.roe > 0], t[t.roe <= 0]
        pf = w.roe.sum() / abs(l.roe.sum()) if len(l) and l.roe.sum() else float("inf")
        print(f"勝率 {len(w) / len(t):.1%} | 盈虧比(平均賺/平均賠) "
              f"{(w.roe.mean() / abs(l.roe.mean())) if len(w) and len(l) else float('nan'):.2f} | Profit Factor {pf:.2f}")
        print(f"總 ROE {t.roe.sum():.1f}% | 平均 {t.roe.mean():.2f}% | 平均持倉 {t.held.mean():.1f} 根")
        lk = t[t.locked]
        print(f"階梯鎖利觸發 {len(lk)} 次 | 鎖利平均 ROE {lk.roe.mean() if len(lk) else 0:.2f}% "
              f"| 鎖利單峰值平均 {lk.peak.mean() if len(lk) else 0:.2f}%")
        print("進場模式:", dict(t["mode"].value_counts()))
        print("出場原因:", dict(t["exit"].value_counts()))
    print("守門員攔截(多空各算一次):", dict(blocked.most_common()))


if __name__ == "__main__":
    data = fetch(SYMBOL, DAYS)
    tr, bl = run(data)
    report(tr, bl, len(data))
