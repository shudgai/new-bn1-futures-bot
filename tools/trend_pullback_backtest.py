"""Five-minute pullback experiment with minute-resolution protective stops."""
from pathlib import Path
import json
import sys
from collections import Counter
import pandas as pd

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from core.trend_pullback import evaluate, DEFAULT_RULES, confirmed_swings
from core.strategy import SuperTrendKeltnerStrategy
from core.config import TAKER_FEE_RATE, SLIPPAGE_PCT
from tools.channel_exit_comparison import fetch_data, MINUTE


def prepare(raw):
    indexed = raw.set_index(pd.to_datetime(raw.timestamp,unit='ms',utc=True))
    five = indexed.resample('5min',closed='left',label='left').agg(
        timestamp=('timestamp','first'),open=('open','first'),high=('high','max'),
        low=('low','min'),close=('close','last'),volume=('volume','sum'),count=('close','size'))
    five = five[five['count']==5].reset_index(drop=True)
    five = SuperTrendKeltnerStrategy().compute_indicators(five)
    decisions, reasons = {}, Counter()
    for i in range(20,len(five)):
        frame = five.iloc[max(0,i-199):i+1]
        price = float(frame.iloc[-1].open)
        action = evaluate(frame,price,fee_rate=TAKER_FEE_RATE,slippage=SLIPPAGE_PCT)
        reasons[action['reason']] += 1
        recent = frame.iloc[-6:-1]
        trails = {}
        for j,kind,level in confirmed_swings(recent):
            trails['LONG' if kind=='LOW' else 'SHORT'] = (
                level + (-1 if kind=='LOW' else 1)*DEFAULT_RULES.stop_buffer_atr*float(recent.iloc[j].atr),
                int(recent.iloc[j].timestamp))
        decisions[int(five.iloc[i].timestamp)] = (action,trails)
    return decisions,dict(reasons)


def simulate(raw,funding,decisions,start,end,mult=1.,notional=1000.):
    fee,slip = TAKER_FEE_RATE*mult,SLIPPAGE_PCT*mult
    selected = raw[(raw.timestamp>=start)&(raw.timestamp<end)]
    events = {}
    for event in funding:
        events.setdefault(int(event['fundingTime'])//MINUTE*MINUTE,[]).append(event)
    held, trades = None, []
    pnl, peak, drawdown, fees, funding_net = 0.,0.,0.,0.,0.
    last_exit_bucket = None
    def close(price,stamp,reason):
        nonlocal held,pnl,fees,last_exit_bucket
        execution = price*(1-held['sign']*slip)
        exit_fee = held['qty']*execution*fee
        gross = held['sign']*held['qty']*(execution-held['price'])
        pnl += gross-exit_fee
        fees += exit_fee
        trades.append(dict(entry_time=held['time'],exit_time=int(stamp),side=held['side'],
            entry_price=held['price'],exit_price=execution,reason=reason,
            net_pnl=gross-exit_fee-held['fee']+held['funding']))
        held = None
        last_exit_bucket = int(stamp)//(5*MINUTE)
    for row in selected.itertuples():
        if held:
            for event in events.get(int(row.timestamp),[]):
                payment = -held['sign']*held['qty']*float(event.get('markPrice') or row.open)*float(event['fundingRate'])
                pnl += payment
                held['funding'] += payment
                funding_net += payment
        if held and ((held['sign']==1 and row.open<=held['sl']) or (held['sign']==-1 and row.open>=held['sl'])):
            close(row.open,row.timestamp,'gap_stop')
        decision,trails = decisions.get(int(row.timestamp),({},{}))
        if held:
            candidate,swing_time = trails.get(held['side'],(held['sl'],0))
            if swing_time>held['time']:
                held['sl'] = max(held['sl'],candidate) if held['sign']==1 else min(held['sl'],candidate)
        elif decision.get('action')=='ENTER' and last_exit_bucket!=int(row.timestamp)//(5*MINUTE):
            sign = 1 if decision['side']=='LONG' else -1
            stop = decision['stop_loss']
            risk = sign*(row.open-stop)
            cost = row.open*2*(fee+slip)
            reward = sign*(decision['reference_target']-row.open)
            if (reward-cost)/(risk+cost)>=DEFAULT_RULES.min_net_rr:
                execution = row.open*(1+sign*slip)
                held = dict(side=decision['side'],sign=sign,price=execution,qty=notional/execution,
                    fee=notional*fee,sl=stop,time=int(row.timestamp),funding=0.)
                fees += held['fee']
                pnl -= held['fee']
        if held and ((held['sign']==1 and row.low<=held['sl']) or (held['sign']==-1 and row.high>=held['sl'])):
            trigger = min(row.open,held['sl']) if held['sign']==1 else max(row.open,held['sl'])
            close(trigger,row.timestamp,'structural_stop')
        marked = pnl+(held['sign']*held['qty']*(row.close-held['price']) if held else 0.)
        peak = max(peak,marked)
        drawdown = max(drawdown,peak-marked)
    if held:
        close(float(selected.iloc[-1].close),int(selected.iloc[-1].timestamp)+MINUTE-1,'sample_end')
        drawdown = max(drawdown,peak-pnl)
    return dict(trades=len(trades),net_pnl=pnl,max_drawdown=drawdown,fees=fees,funding=funding_net),trades


def main():
    source=Path('reports/channel_exit_comparison')
    out=Path('reports/trend_pullback_5m');out.mkdir(parents=True,exist_ok=True)
    metadata=json.loads((source/'results.json').read_text())
    results=[]
    for item in metadata['datasets']:
        symbol=item['symbol']
        raw,funding,digest=fetch_data(symbol,metadata['start']-200*MINUTE,metadata['end'],source/'market_data')
        decisions,reasons=prepare(raw)
        print(symbol,'signals',reasons,flush=True)
        for period,start,end in [('development',metadata['start'],metadata['split']),('validation',metadata['split'],metadata['end']),('full',metadata['start'],metadata['end'])]:
            for mult in (1.,2.):
                result,trades=simulate(raw,funding,decisions,start,end,mult)
                results.append(dict(symbol=symbol,period=period,cost_mult=mult,**result))
                pd.DataFrame(trades).to_csv(out/f'{symbol}_{period}_cost{mult:g}_trades.csv',index=False)
        (out/f'{symbol}_signal_reasons.json').write_text(json.dumps(reasons,indent=2))
    pd.DataFrame(results).to_csv(out/'summary.csv',index=False)
    (out/'results.json').write_text(json.dumps(dict(source_period=metadata,rules=DEFAULT_RULES.__dict__,results=results),ensure_ascii=False,indent=2))
    print(pd.DataFrame(results).round(2).to_string(index=False))
if __name__=='__main__': main()
