"""Research-only validation of next-candle adverse bodies; never imports an account."""
import json
import hashlib
from pathlib import Path
import sys
import numpy as np
import pandas as pd
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.channel_entry_risk_research import (
    load_data, make_samples, evaluate, predict, filter_metrics, day_bootstrap, FEATURES,
)


def main():
    root = Path('reports/channel_entry_risk_current')
    samples = pd.read_csv(root/'samples.csv')
    previous = json.loads((root/'results.json').read_text())
    # Model selection uses only the original training/calibration periods.
    # Primary target is an adverse closing body >= existing 0.5 ATR threshold.
    samples['bad_1m'] = samples['bad_close_1m']
    summary, model, predictions = evaluate(samples, previous['cuts']['start'], previous['cuts']['end'])
    summary['target'] = 'next_candle_adverse_closed_body'
    fresh = []
    sources = []
    for symbol in ('1000PEPEUSDT', '龙虾USDT'):
        old = load_data(f'reports/channel_exit_comparison/market_data/{symbol}_1786299600000_1788903600000.json')
        path = root/'recent_market_data'/f'{symbol}.json'
        new = load_data(path)
        payload = json.loads(path.read_text())
        if int(new.close_time.iloc[-1]) >= int(payload['downloaded_at_ms']):
            raise ValueError('Recent history includes an unfinished candle at download time')
        sources.append(dict(symbol=symbol, path=str(path), bars=len(new),
            first=int(new.timestamp.iloc[0]), end=int(new.timestamp.iloc[-1])+60000,
            sha256=hashlib.sha256(path.read_bytes()).hexdigest(), source=payload['source']))
        if int(new.timestamp.iloc[0]) != int(old.timestamp.iloc[-1])+60000:
            raise ValueError('History boundary is not continuous')
        raw = pd.concat([old,new],ignore_index=True)
        frame = make_samples(raw,symbol)
        frame = frame[frame.timestamp >= int(new.timestamp.iloc[0])].copy()
        frame['intrabar_adverse'] = frame['bad_1m']
        frame['bad_1m'] = frame['bad_close_1m']
        fresh.append(frame)
    fresh = pd.concat(fresh,ignore_index=True)
    selected = summary['selected_filter']
    cutoff = summary['selection'][selected]['cutoff']
    scores = (predict(model,fresh[FEATURES].to_numpy()) if selected == 'logistic' else
              fresh['extension_atr' if selected == 'extension' else 'rejection_wick_ratio'].to_numpy())
    fresh['risk_score'] = scores
    fresh['blocked'] = scores >= cutoff if cutoff is not None else False
    summary['recent'] = []
    for symbol,side,g in [('ALL','ALL',fresh),*[(sym,side,g) for (sym,side),g in fresh.groupby(['symbol','side'])]]:
        summary['recent'].append(dict(symbol=symbol,side=side,**filter_metrics(g,g.blocked),ci95=day_bootstrap(g,g.blocked)))
    summary['live_enabled'] = False
    summary['datasets'] = sources
    summary['parameters'] = previous['parameters']
    summary['frozen_model'] = model
    summary['recent_label_counts'] = dict(candidates=len(fresh),
        intrabar_adverse=int(fresh.intrabar_adverse.sum()),
        adverse_closed_body=int(fresh.bad_close_1m.sum()))
    summary['limitations'] = ['minute-open candidates, not actual intrabar fills',
        'no order-book or tick history', 'recent period is short',
        'historical source data was previously used for other strategy research']
    (root/'adverse_body_results.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2,allow_nan=False))
    fresh.to_csv(root/'recent_body_predictions.csv',index=False)
    predictions.to_csv(root/'body_holdout_predictions.csv',index=False)
    print(json.dumps(dict(selected=selected,holdout=summary['selected_test'],ci95=summary['selected_risk_difference_ci95'],recent=summary['recent']),ensure_ascii=False),flush=True)

if __name__ == '__main__':
    main()
