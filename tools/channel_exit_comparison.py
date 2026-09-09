"""Offline KC exit comparison. Public market-data GETs only; never opens accounts."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import sys
import time

import numpy as np
import pandas as pd
import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core.config import TAKER_FEE_RATE, SLIPPAGE_PCT, KELTNER_ATR_MULTIPLIER
from core.strategy import SuperTrendKeltnerStrategy

BASE = 'https://fapi.binance.com'
MINUTE = 60_000
VARIANTS = ('opposite_break', 'peak_exit_trend_entry', 'peak_reverse')


def fetch_data(symbol, start, end, directory):
    path = directory / f'{symbol}_{start}_{end}.json'
    if path.exists():
        payload = json.loads(path.read_text())
    else:
        session = requests.Session()
        rows, funding = [], []
        cursor = start
        while cursor < end:
            response = session.get(BASE + '/fapi/v1/klines', params=dict(
                symbol=symbol, interval='1m', startTime=cursor, endTime=end-1, limit=1500), timeout=30)
            response.raise_for_status()
            batch = response.json()
            if not batch:
                break
            rows.extend(batch)
            following = int(batch[-1][0]) + MINUTE
            if following <= cursor:
                raise ValueError('Kline pagination did not advance')
            cursor = following
        cursor = start
        while cursor < end:
            response = session.get(BASE + '/fapi/v1/fundingRate', params=dict(
                symbol=symbol, startTime=cursor, endTime=end-1, limit=1000), timeout=30)
            response.raise_for_status()
            batch = response.json()
            if not batch:
                break
            funding.extend(batch)
            following = int(batch[-1]['fundingTime']) + 1
            if following <= cursor:
                raise ValueError('Funding pagination did not advance')
            cursor = following
        payload = dict(klines=rows, funding=funding)
    frame = pd.DataFrame(payload['klines'], columns=[
        'timestamp','open','high','low','close','volume','close_time',
        'quote_volume','trades','taker_base','taker_quote','ignore'])
    for key in ('open','high','low','close','volume'):
        frame[key] = pd.to_numeric(frame[key], errors='raise')
    frame = frame.drop_duplicates('timestamp').sort_values('timestamp').reset_index(drop=True)
    frame = frame[(frame.timestamp >= start) & (frame.close_time < end)].reset_index(drop=True)
    if frame.empty or not frame.timestamp.diff().dropna().eq(MINUTE).all():
        raise ValueError(f'{symbol}: empty or discontinuous klines')
    if frame.timestamp.iloc[0] != start or frame.timestamp.iloc[-1] != end - MINUTE:
        raise ValueError(f'{symbol}: incomplete requested history; do not silently shorten sample')
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False))
    return frame, payload['funding'], hashlib.sha256(path.read_bytes()).hexdigest()


def signals(frame):
    """All output rows are decisions available at that row's OPEN, never its close."""
    f = SuperTrendKeltnerStrategy().compute_indicators(frame)
    bo, cf = f.shift(2), f.shift(1)
    up = (bo.open <= bo.kc_upper) & (bo.close > bo.kc_upper) & (cf.close > cf.open) & (cf.close > cf.kc_upper)
    down = (bo.open >= bo.kc_lower) & (bo.close < bo.kc_lower) & (cf.close < cf.open) & (cf.close < cf.kc_lower)
    widths = np.minimum(bo.kc_upper-bo.kc_lower, cf.kc_upper-cf.kc_lower)
    spike = np.maximum(bo.high-bo.low, cf.high-cf.low) > widths * 1.25
    # Same available-history MA15 direction as the engine (up to 60 closed values).
    trend = cf.ma15 - f.ma15.shift(60)
    fallback = f.ma15.where(f.ma15.notna().cumsum().eq(1)).ffill().shift(1)
    trend = trend.fillna(cf.ma15 - fallback)
    f['entry'] = np.select([up & ~spike & (trend > 0), down & ~spike & (trend < 0)], [1,-1], default=0)
    f['break_side'] = np.select([up,down], [1,-1], default=0)
    # A local MA3 pivot at [-2] is only known after [-1] closes. Require
    # an opposite body to break the pivot candle's extreme as well.
    peak = (bo.ma3 > f.ma3.shift(3)) & (bo.ma3 > cf.ma3) & (bo.high >= bo.kc_upper) & (cf.close < cf.open) & (cf.close < bo.low)
    trough = (bo.ma3 < f.ma3.shift(3)) & (bo.ma3 < cf.ma3) & (bo.low <= bo.kc_lower) & (cf.close > cf.open) & (cf.close > bo.high)
    f['peak_exit'] = np.select([peak,trough], [1,-1], default=0)
    return f


def simulate(f, funding, variant, start, end, cost_mult=1., notional=1000.):
    if variant not in VARIANTS:
        raise ValueError(variant)
    fee_rate, slip = TAKER_FEE_RATE*cost_mult, SLIPPAGE_PCT*cost_mult
    rows = f[(f.timestamp >= start) & (f.timestamp < end)]
    events = {}
    for event in funding:
        # Funding belongs to the position held at settlement, before new orders.
        minute = int(event['fundingTime']) // MINUTE * MINUTE
        events.setdefault(minute, []).append(event)
    cash, peak_equity, max_dd = 0., 0., 0.
    held, trades, curve = None, [], []
    fees_total, funding_total = 0., 0.

    def close(price, stamp, reason):
        nonlocal held, cash, fees_total
        execution = price * (1-held['side']*slip)
        fee = held['qty']*execution*fee_rate
        pnl = held['side']*held['qty']*(execution-held['price'])
        cash += pnl-fee
        fees_total += fee
        trades.append(dict(entry_time=held['time'], exit_time=int(stamp), side=held['side'],
            entry_price=held['price'], exit_price=execution, reason=reason,
            net_pnl=pnl-fee-held['fee']+held['funding'], funding=held['funding']))
        held = None

    def mark(price, stamp):
        nonlocal peak_equity, max_dd
        equity = cash + (held['side']*held['qty']*(price-held['price']) if held else 0.)
        peak_equity = max(peak_equity, equity)
        max_dd = max(max_dd, peak_equity-equity)
        curve.append((int(stamp),equity))

    for row in rows.itertuples():
        if held:
            for event in events.get(int(row.timestamp), []):
                mark_price = float(event.get('markPrice') or row.open)
                payment = -held['side']*held['qty']*mark_price*float(event['fundingRate'])
                cash += payment
                held['funding'] += payment
                funding_total += payment
        target = 0
        if held:
            opposite = row.break_side == -held['side']
            pivot_exit = variant != 'opposite_break' and row.peak_exit == held['side']
            if opposite or pivot_exit:
                old = held['side']
                close(row.open, row.timestamp, 'opposite_break' if opposite else 'confirmed_pivot')
                if variant == 'opposite_break' or variant == 'peak_reverse':
                    target = -old
                elif opposite and row.entry == -old:
                    target = -old
        else:
            target = row.entry
        if target and not held:
            execution = row.open*(1+target*slip)
            qty = notional/execution
            fee = notional*fee_rate
            cash -= fee
            fees_total += fee
            held = dict(side=int(target), price=execution, qty=qty, fee=fee,
                        funding=0., time=int(row.timestamp))
        mark(row.close, row.timestamp)
    if held:
        close(float(rows.iloc[-1].close), int(rows.iloc[-1].timestamp)+MINUTE-1, 'sample_end')
        mark(float(rows.iloc[-1].close), int(rows.iloc[-1].timestamp)+MINUTE-1)
    wins = sum(t['net_pnl']>0 for t in trades)
    profits = sum(max(0.,t['net_pnl']) for t in trades)
    losses = -sum(min(0.,t['net_pnl']) for t in trades)
    summary = dict(variant=variant,cost_mult=cost_mult,trades=len(trades),net_pnl=cash,
        fees=fees_total,funding=funding_total,win_rate=100*wins/len(trades) if trades else 0.,
        profit_factor=profits/losses if losses else None,max_drawdown=max_dd)
    return summary, trades, curve


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--end', default=None, help='Exclusive UTC end, e.g. 2026-09-08T22:00:00Z')
    parser.add_argument('--days', type=int, default=30)
    parser.add_argument('--symbols', nargs='+', default=['1000PEPEUSDT','龙虾USDT'])
    parser.add_argument('--output', type=Path, default=Path('reports/channel_exit_comparison'))
    args = parser.parse_args()
    if args.days < 4:
        parser.error('at least four days required for chronological split')
    end = int(pd.Timestamp(args.end).timestamp()*1000) if args.end else int(time.time()*1000)//MINUTE*MINUTE
    if end % MINUTE:
        parser.error('end must be a minute boundary')
    start = end-args.days*86400_000
    split = start+int(args.days*.7)*86400_000
    args.output.mkdir(parents=True, exist_ok=True)
    summaries, metadata = [], []
    for symbol in args.symbols:
        raw, funding, digest = fetch_data(symbol,start-200*MINUTE,end,args.output/'market_data')
        prepared = signals(raw)
        metadata.append(dict(symbol=symbol,bars=len(raw),funding_events=len(funding),sha256=digest))
        print(symbol, len(raw), 'bars loaded', flush=True)
        for period, begin, finish in [('development',start,split),('validation',split,end),('full',start,end)]:
            for mult in (1.,2.):
                for variant in VARIANTS:
                    summary, trades, curve = simulate(prepared,funding,variant,begin,finish,mult)
                    summaries.append(dict(symbol=symbol,period=period,**summary))
                    prefix = args.output/f'{symbol}_{period}_{variant}_cost{mult:g}'
                    pd.DataFrame(trades).to_csv(str(prefix)+'_trades.csv',index=False)
                    pd.DataFrame(curve,columns=['timestamp','cumulative_pnl']).to_csv(str(prefix)+'_equity.csv',index=False)
    pd.DataFrame(summaries).to_csv(args.output/'summary.csv',index=False)
    metadata = dict(start=start,split=split,end=end,notional=1000,fee_rate=TAKER_FEE_RATE,
        slippage=SLIPPAGE_PCT,kc_atr_multiplier=KELTNER_ATR_MULTIPLIER,atr_period=10,
        execution='next open plus adverse slippage',drawdown='minute-close cumulative PnL',
        model='constant notional, no compounding or wallet/liquidation simulation',
        datasets=metadata,results=summaries)
    (args.output/'results.json').write_text(json.dumps(metadata,ensure_ascii=False,indent=2,allow_nan=False))
    print(pd.DataFrame(summaries).query("period == 'validation' and cost_mult == 1").to_string(index=False))

if __name__ == '__main__':
    main()
