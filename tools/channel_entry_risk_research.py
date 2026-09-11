"""Offline next-minute adverse-move research. No account or order imports.

One-minute OHLCV cannot reconstruct the live pullback entry. Samples are
confirmed outer-breakout candidates observed at the following minute's open,
not executed trades. Training, cutoff selection and holdout use disjoint times.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core.channel_outer_entry import aligned_entry
from core.config import (KELTNER_ATR_MULTIPLIER, RAPID_PIVOT_IMMEDIATE_REVERSE_BODY_ATR,
                         TAKER_FEE_RATE, SLIPPAGE_PCT)

MINUTE = 60_000
FEATURES = [
    'return_1_atr', 'return_3_atr', 'return_6_atr', 'body_atr', 'body_ratio',
    'rejection_wick_ratio', 'extension_atr', 'ma3_slope_atr', 'ma3_acceleration_atr',
    'ma15_slope_atr', 'volume_shock', 'volume_change', 'atr_regime', 'opening_gap_atr',
    'short_side', 'pepe_asset',
]


def load_data(path):
    payload = json.loads(Path(path).read_text())
    f = pd.DataFrame(payload['klines'], columns=[
        'timestamp', 'open', 'high', 'low', 'close', 'volume', 'close_time',
        'quote_volume', 'trades', 'taker_base', 'taker_quote', 'ignore'])
    for key in ['timestamp', 'close_time', 'open', 'high', 'low', 'close', 'volume']:
        f[key] = pd.to_numeric(f[key], errors='raise')
    values = f[['open', 'high', 'low', 'close', 'volume']].to_numpy()
    if (len(f) < 300 or not np.isfinite(values).all()
            or not (f.timestamp.diff().dropna() == MINUTE).all()
            or not (f.timestamp % MINUTE == 0).all()
            or not (f.close_time == f.timestamp + MINUTE - 1).all()
            or not (f[['open', 'high', 'low', 'close']] > 0).all().all()
            or not (f.volume >= 0).all()
            or not (f.low <= f[['open', 'close']].min(axis=1)).all()
            or not (f.high >= f[['open', 'close']].max(axis=1)).all()):
        raise ValueError(f'Invalid, duplicated or discontinuous history: {path}')
    return f


def indicators(raw):
    """Exact causal MA/KC subset of strategy.compute_indicators (ATR period 10)."""
    f = raw.copy()
    prev = f.close.shift(1)
    f['tr'] = pd.concat([f.high-f.low, (f.high-prev).abs(), (f.low-prev).abs()], axis=1).max(axis=1)
    f['atr'] = f.tr.rolling(10).mean()
    f['ema_20'] = f.close.ewm(span=20, adjust=False).mean()
    f['ma3'] = f.close.rolling(3).mean()
    f['ma15'] = f.close.rolling(15).mean()
    f['kc_upper'] = f.ema_20 + f.atr*KELTNER_ATR_MULTIPLIER
    f['kc_lower'] = f.ema_20 - f.atr*KELTNER_ATR_MULTIPLIER
    return f


def snapshot_at_open(f, index):
    """Replace every unfinished candle field with information known at its open."""
    history = f.iloc[max(0, index-80):index+1].copy()
    stamp = history.index[-1]
    price = float(f.iloc[index].open)
    for key in ('open', 'high', 'low', 'close'):
        history.loc[stamp, key] = price
    for key in ('volume', 'quote_volume', 'trades', 'taker_base', 'taker_quote'):
        if key in history: history.loc[stamp, key] = 0.
    gap = abs(price - float(f.iloc[index-1].close))
    atr = (float(f.tr.iloc[index-9:index].sum()) + gap) / 10
    middle = float(f.iloc[index-1].ema_20)*(19/21) + price*(2/21)
    history.loc[stamp, 'atr'] = atr
    history.loc[stamp, 'tr'] = gap
    history.loc[stamp, 'ema_20'] = middle
    history.loc[stamp, 'ma3'] = (float(f.close.iloc[index-2:index].sum()) + price)/3
    history.loc[stamp, 'ma15'] = (float(f.close.iloc[index-14:index].sum()) + price)/15
    history.loc[stamp, 'kc_upper'] = middle + atr*KELTNER_ATR_MULTIPLIER
    history.loc[stamp, 'kc_lower'] = middle - atr*KELTNER_ATR_MULTIPLIER
    return history


def sample_features(f, index, side, symbol):
    """All features use closed rows or the next minute's observed opening price."""
    sign = 1 if side == 'LONG' else -1
    cf, bo, older = (f.iloc[index-j] for j in (1, 2, 3))
    atr, price = float(cf.atr), float(f.iloc[index].open)
    span = float(cf.high-cf.low)
    wick = float(cf.high-max(cf.open, cf.close)) if sign == 1 else float(min(cf.open, cf.close)-cf.low)
    avg_volume = float(f.volume.iloc[index-21:index-1].mean())
    if min(atr, span, avg_volume, float(bo.volume)) <= 0: return None
    values = dict(
        return_1_atr=sign*(cf.close-bo.close)/atr,
        return_3_atr=sign*(cf.close-f.iloc[index-4].close)/atr,
        return_6_atr=sign*(cf.close-f.iloc[index-7].close)/atr,
        body_atr=sign*(cf.close-cf.open)/atr, body_ratio=abs(cf.close-cf.open)/span,
        rejection_wick_ratio=wick/span, extension_atr=sign*(price-cf.ema_20)/atr,
        ma3_slope_atr=sign*(cf.ma3-bo.ma3)/atr,
        ma3_acceleration_atr=sign*(cf.ma3-2*bo.ma3+older.ma3)/atr,
        ma15_slope_atr=sign*(cf.ma15-bo.ma15)/atr,
        volume_shock=np.log1p(cf.volume/avg_volume),
        volume_change=np.log1p(cf.volume/bo.volume),
        atr_regime=atr/float(f.atr.iloc[index-60:index].mean()),
        opening_gap_atr=sign*(price-cf.close)/atr,
        short_side=float(side == 'SHORT'), pepe_asset=float(symbol == '1000PEPEUSDT'))
    return {k: float(v) for k, v in values.items()} if np.isfinite(list(values.values())).all() else None


def make_samples(raw, symbol):
    f = indicators(raw)
    bo, cf = f.shift(2), f.shift(1)
    # Cheap superset before running the actual shared production entry helper.
    valid = ((bo.open <= bo.kc_upper) & (bo.close > bo.kc_upper) & (cf.close > cf.open) & (cf.close > cf.kc_upper)) | (
        (bo.open >= bo.kc_lower) & (bo.close < bo.kc_lower) & (cf.close < cf.open) & (cf.close < cf.kc_lower))
    records = []
    for i in np.flatnonzero(valid.to_numpy()):
        if i < 200 or i+2 >= len(f): continue
        snapshot = snapshot_at_open(f, i)
        price = float(f.iloc[i].open)
        decision = aligned_entry(snapshot, price)
        if decision['action'] != 'ENTER': continue
        side = decision['side']
        features = sample_features(f, i, side, symbol)
        if features is None: continue
        sign = 1 if side == 'LONG' else -1
        atr, row = float(f.iloc[i-1].atr), f.iloc[i]
        future = f.iloc[i:i+3]
        adverse = (price-row.low if sign == 1 else row.high-price)/atr
        favorable = (row.high-price if sign == 1 else price-row.low)/atr
        adverse3 = (price-future.low.min() if sign == 1 else future.high.max()-price)/atr
        entry, exit_price = price*(1+sign*SLIPPAGE_PCT), float(row.close)*(1-sign*SLIPPAGE_PCT)
        net_bps = (sign*(exit_price-entry)-(entry+exit_price)*TAKER_FEE_RATE)/price*10_000
        records.append(dict(symbol=symbol, side=side, timestamp=int(row.timestamp),
            label_end=int(row.timestamp)+3*MINUTE, price=price, atr=atr,
            adverse_1m_atr=max(0., adverse), favorable_1m_atr=max(0., favorable),
            bad_1m=int(adverse >= RAPID_PIVOT_IMMEDIATE_REVERSE_BODY_ATR),
            bad_close_1m=int(sign*(price-row.close)/atr >= RAPID_PIVOT_IMMEDIATE_REVERSE_BODY_ATR),
            bad_3m=int(adverse3 >= RAPID_PIVOT_IMMEDIATE_REVERSE_BODY_ATR),
            net_1m_bps=net_bps, **features))
    return pd.DataFrame(records)


def sigmoid(x):
    return 1/(1+np.exp(-np.clip(x, -35, 35)))


def fit_logistic(x, y, penalty=.01):
    """Small L2 logistic model; fit scaling ONLY on the training partition."""
    x, y = np.asarray(x, float), np.asarray(y, float)
    if len(x) < 50 or len(np.unique(y)) != 2 or not np.isfinite(x).all():
        raise ValueError('Insufficient valid training samples/classes')
    mean, scale = x.mean(axis=0), x.std(axis=0)
    scale[scale < 1e-10] = 1.
    z = np.column_stack([np.ones(len(x)), np.clip((x-mean)/scale, -10, 10)])
    coef = np.zeros(z.shape[1]); coef[0] = np.log(y.mean()/(1-y.mean()))
    reg = np.eye(len(coef))*penalty; reg[0, 0] = 0.
    converged = False
    for iteration in range(100):
        p = sigmoid(z@coef)
        gradient = z.T@(p-y)/len(y) + reg@coef
        hessian = (z.T*(p*(1-p)))@z/len(y) + reg + np.eye(len(coef))*1e-10
        step = np.linalg.solve(hessian, gradient)
        old = np.mean(np.logaddexp(0, z@coef)-y*(z@coef)) + .5*coef@reg@coef
        rate = 1.
        while rate > 1e-8:
            candidate = coef-rate*step
            loss = np.mean(np.logaddexp(0,z@candidate)-y*(z@candidate)) + .5*candidate@reg@candidate
            if loss <= old+1e-12: break
            rate *= .5
        coef = candidate
        if np.max(np.abs(rate*step)) < 1e-7:
            converged = True; break
    if not converged: raise ValueError('Logistic fit did not converge')
    return dict(mean=mean.tolist(), scale=scale.tolist(), coef=coef.tolist(), penalty=penalty, iterations=iteration+1)


def predict(model, x):
    z = np.clip((np.asarray(x, float)-model['mean'])/model['scale'], -10, 10)
    return sigmoid(np.column_stack([np.ones(len(z)), z])@np.asarray(model['coef']))


def auc(y, scores):
    y = np.asarray(y, int); positive = int(y.sum()); negative = len(y)-positive
    if not positive or not negative: return None
    ranks = pd.Series(np.asarray(scores)).rank(method='average').to_numpy()
    return float((ranks[y==1].sum()-positive*(positive+1)/2)/(positive*negative))


def filter_metrics(frame, blocked):
    bad = frame.bad_1m.to_numpy(dtype=bool); blocked = np.asarray(blocked, bool)
    good_proxy = frame.net_1m_bps.to_numpy() > 0
    count, bad_n = len(frame), int(bad.sum())
    kept = ~blocked
    rate = lambda n, d: float(n/d) if d else None
    return dict(n=count, bad=bad_n, base_bad_rate=rate(bad_n, count),
        blocked=int(blocked.sum()), blocked_fraction=rate(blocked.sum(), count),
        bad_blocked=int((bad&blocked).sum()), bad_catch_rate=rate((bad&blocked).sum(), bad_n),
        nonbad_blocked=int((~bad&blocked).sum()), nonbad_miss_rate=rate((~bad&blocked).sum(), (~bad).sum()),
        blocked_bad_rate=rate((bad&blocked).sum(), blocked.sum()),
        kept_bad_rate=rate((bad&kept).sum(), kept.sum()),
        profitable_proxy_count=int(good_proxy.sum()), profitable_proxy_blocked=int((good_proxy&blocked).sum()),
        profitable_proxy_miss_rate=rate((good_proxy&blocked).sum(), good_proxy.sum()),
        baseline_mean_net_bps=float(frame.net_1m_bps.mean()) if count else None,
        kept_mean_net_bps=float(frame.loc[kept, 'net_1m_bps'].mean()) if kept.any() else None)


def choose_cutoff(calibration, scores):
    """Predeclared search: block at most 25%, maximize catch minus nonbad miss."""
    options = []
    for fraction in (.10, .15, .20, .25):
        cutoff = float(np.quantile(scores, 1-fraction))
        metrics = filter_metrics(calibration, scores >= cutoff)
        if metrics['blocked'] < 10 or metrics['blocked_fraction'] > .25+1e-9: continue
        objective = (metrics['bad_catch_rate'] or 0)-(metrics['nonbad_miss_rate'] or 0)
        options.append(dict(cutoff=cutoff, objective=objective, calibration=metrics))
    # A negative/zero result is a valid "do not filter" decision.
    return max(options, key=lambda item:item['objective']) if options and max(x['objective'] for x in options) > 0 else dict(
        cutoff=None, objective=0., calibration=filter_metrics(calibration, np.zeros(len(calibration),bool)))


def day_bootstrap(frame, blocked, draws=1000):
    """Paired day-cluster interval for kept minus unfiltered adverse-event rate."""
    days = (frame.timestamp//(1440*MINUTE)).to_numpy()
    bad = frame.bad_1m.to_numpy(); kept = ~np.asarray(blocked, bool)
    totals = np.asarray([[sum(days==d), bad[days==d].sum(), kept[days==d].sum(),
        (bad*kept)[days==d].sum()] for d in np.unique(days)],float)
    if len(totals) < 3: return None
    rng = np.random.default_rng(20260910)
    sums = totals[rng.integers(0,len(totals),size=(draws,len(totals)))].sum(axis=1)
    usable = sums[:,2] > 0
    diff = sums[usable,3]/sums[usable,2]-sums[usable,1]/sums[usable,0]
    return np.quantile(diff,[.025,.975]).tolist() if len(diff) else None


def partition(samples, start, end):
    train_end = start + ((end-start)//MINUTE*60//100)*MINUTE
    calibration_end = start + ((end-start)//MINUTE*80//100)*MINUTE
    train = samples[(samples.timestamp >= start)&(samples.label_end < train_end)].copy()
    cal = samples[(samples.timestamp >= train_end)&(samples.label_end < calibration_end)].copy()
    test = samples[(samples.timestamp >= calibration_end)&(samples.label_end <= end)].copy()
    return train, cal, test, dict(start=start, train_end=train_end, calibration_end=calibration_end, end=end)


def evaluate(samples, start, end):
    train, cal, test, cuts = partition(samples,start,end)
    if min(len(train),len(cal),len(test)) < 50 or min(cal.bad_1m.sum(),test.bad_1m.sum()) < 10:
        raise ValueError('Too few events for a credible chronological evaluation')
    x = lambda f:f[FEATURES].to_numpy(float)
    # Regularization chosen on calibration loss, never on the held-out tail.
    fits = [fit_logistic(x(train),train.bad_1m.to_numpy(),p) for p in (.001,.01,.1)]
    losses = [float(np.mean((predict(m,x(cal))-cal.bad_1m.to_numpy())**2)) for m in fits]
    model = fits[int(np.argmin(losses))]
    score_cal = dict(logistic=predict(model,x(cal)), extension=cal.extension_atr.to_numpy(),
                     rejection_wick=cal.rejection_wick_ratio.to_numpy())
    score_test = dict(logistic=predict(model,x(test)), extension=test.extension_atr.to_numpy(),
                      rejection_wick=test.rejection_wick_ratio.to_numpy())
    choices = {name:choose_cutoff(cal,score) for name,score in score_cal.items()}
    # Ties retain insertion order; no later replacement after viewing test results.
    selected = max(choices,key=lambda name:choices[name]['objective'])
    pooled_rate = float(train.bad_1m.mean())
    strata = train.groupby(['symbol','side']).bad_1m.mean().to_dict()
    naive = np.asarray([strata.get((r.symbol,r.side),pooled_rate) for r in test.itertuples()])
    results = []
    for name, score in score_test.items():
        cutoff = choices[name]['cutoff']
        blocked = score >= cutoff if cutoff is not None else np.zeros(len(score),bool)
        test[name+'_score'] = score; test[name+'_blocked'] = blocked
        groups = [('ALL','ALL',test)] + [(sym,'ALL',g) for sym,g in test.groupby('symbol')] + [
            (sym,side,g) for (sym,side),g in test.groupby(['symbol','side'])]
        for symbol,side,g in groups:
            mask = g[name+'_blocked'].to_numpy(bool)
            results.append(dict(filter=name,symbol=symbol,side=side,**filter_metrics(g,mask),
                auc=auc(g.bad_1m,g[name+'_score']), risk_difference_ci95=day_bootstrap(g,mask)))
    chosen = test[selected+'_blocked'].to_numpy(bool)
    brier = float(np.mean((test.logistic_score-test.bad_1m)**2))
    reference = float(np.mean((naive-test.bad_1m.to_numpy())**2))
    reliability = []
    for low,high in zip([0,.2,.4,.6,.8],[.2,.4,.6,.8,1.00001]):
        rows = test[(test.logistic_score>=low)&(test.logistic_score<high)]
        if len(rows): reliability.append(dict(low=low,high=min(high,1),n=len(rows),mean_predicted=float(rows.logistic_score.mean()),observed=float(rows.bad_1m.mean())))
    daily = []
    for day,g in test.groupby(test.timestamp//(1440*MINUTE)):
        daily.append(dict(day=int(day),**filter_metrics(g,g[selected+'_blocked'])))
    summary = dict(cuts=cuts,partitions={k:dict(n=len(f),bad=int(f.bad_1m.sum()),bad_rate=float(f.bad_1m.mean())) for k,f in [('train',train),('calibration',cal),('test',test)]},
        selected_filter=selected,selection=choices,calibration_brier=dict(zip(['.001','.01','.1'],losses)),
        test_logistic_brier=brier,test_stratum_baseline_brier=reference,
        test_logistic_auc=auc(test.bad_1m,test.logistic_score),
        selected_test=filter_metrics(test,chosen),selected_risk_difference_ci95=day_bootstrap(test,chosen),
        reliability=reliability,daily=daily,results=results,live_enabled=False)
    return summary,model,test


def percentage(value):
    return '—' if value is None else f'{value:.1%}'


def write_report(out,summary):
    m = summary['selected_test'];ci=summary['selected_risk_difference_ci95']
    dates = {k:pd.to_datetime(v,unit='ms',utc=True).isoformat() for k,v in summary['cuts'].items()}
    rows = ['| 幣種／方向 | 樣本 | 反向異常 | 擋下異常 | 誤擋非異常 | 保留後異常率 | 錯過正報酬代理 |',
            '|---|---:|---:|---:|---:|---:|---:|']
    for r in summary['results']:
        if r['filter'] != summary['selected_filter']: continue
        rows.append(f"| {r['symbol']}／{r['side']} | {r['n']} | {r['bad']} ({percentage(r['base_bad_rate'])}) | {r['bad_blocked']} ({percentage(r['bad_catch_rate'])}) | {r['nonbad_blocked']} ({percentage(r['nonbad_miss_rate'])}) | {percentage(r['kept_bad_rate'])} | {r['profitable_proxy_blocked']}/{r['profitable_proxy_count']} |")
    text=f'''# 開倉後反向急拉／急跌風險研究

本輪完成離線風險模型與比較。未接入實際下單，沒有修改或重啟交易服務。這是候選訊號研究，不是帳戶交易績效，也不能宣稱已預知下一根顏色。

## 驗證結果

依調整段事先選出的過濾器為 **{summary['selected_filter']}**。最後驗證段共 {m['n']} 個候選，其中 {m['bad']} 個在下一分鐘反向達異常門檻（{percentage(m['base_bad_rate'])}）。它擋掉 {m['blocked']} 個候選，其中 {m['bad_blocked']} 個確實反向異常、{m['nonbad_blocked']} 個沒有達異常門檻；保留候選的異常率是 {percentage(m['kept_bad_rate'])}。擋下異常不等於挽救虧損；被擋非異常也不一定是好交易。

{chr(10).join(rows)}

保留後減原始異常率的按 UTC 日分組 bootstrap 95% 區間：{ci}（比例，負值表示降低）。此為少量日期的探索性區間，不是部署保證。模型 Brier={summary['test_logistic_brier']:.4f}；按幣種／方向訓練頻率基準={summary['test_stratum_baseline_brier']:.4f}，越低越好。AUC={summary['test_logistic_auc']:.4f}，0.5 為隨機排序基準。不要把整體命中率當成稀少異常的偵測能力。

## 固定方法

- 兩幣現有公開 Binance USD-M 1m 快取，資料雜湊見 results.json；沒有連線交易帳戶。開始 {dates['start']}，結束 {dates['end']}（不含）。
- 前 60% 訓練，到 {dates['train_end']}；中間 20% 調整，到 {dates['calibration_end']}；後 20% 最後驗證。所有幣共用時間切點，標籤跨邊界者排除；沒有隨機打散。
- 使用現行外軌突破／兩根實體／已收線 MA3、MA15、CK 順向，及下一分鐘開盤重算的即時 MA3 與外軌，呼叫共用 aligned_entry。先暖機 200 根。
- 觀察點為確認完成後下一分鐘的開盤。當根 high/low/close/volume 絕不作特徵；只用已收線方向、實體、影線、軌外距離、動能變化、量能與波動，加已知開盤缺口。以分鐘開盤重建即時指標，並非使用該分鐘事後收盤指標。
- 主要標籤：接下來這一分鐘任一時點反向至少 {summary['parameters']['abnormal_atr']} 倍前根 ATR；空單看高點、多單看低點。即使最後收同色，也算盤中異常。另保存反色收盤大實體及三分鐘風險標籤，僅作後續診斷，不拿來反覆選本輪模型。
- 16 個固定特徵、L2 logistic regression（NumPy 實作），正則強度只用中間段 Brier 選擇。比較單純軌外距離與拒絕影線分數；門檻只取中間段前 10/15/20/25% 高風險候選，調整段擋單不得超過25%（固定門檻在驗證段可能超過），最大化「異常攔截率－非異常誤擋率」。若調整段無正值就不擋單。
- 「正報酬代理」只是假設開盤買／賣、同分鐘收盘平倉並扣雙邊費用滑點後為正。它不是現行 MA3／異常／20% 保護策略的獲利單，也不是資金曲線；沒有拿分鐘高低點虛構成交或移動保護路徑。

## 使用界線

只有 1m OHLCV，無法觀察同根 0.10 ATR 回調、0.05 ATR 轉向與真實成交先後；當根開盤本身為十字，故候選不是完整送單條件已成立的交易。未模擬回踩票據、持倉互斥、帳戶風控、每根限次或末端空間，不能把候選數當實際交易數。兩幣30天且這批資料曾用於其他策略研究，後段只是本次演算法的時間保留段，不是從未接觸過的全新市場樣本。

未驗證實際進場時點效益前，不啟用自動擋單、不更改出口。若日後評估即時使用，需另外保存送單前特徵與逐筆報價，做未來時段影子驗證；本次沒有啟用背景收集。

## 重跑

`OPENBLAS_NUM_THREADS=1 .venv/bin/python3 tools/channel_entry_risk_research.py`

- samples.csv：全部候選、時間、已知特徵與事後標籤。
- holdout_predictions.csv：最後驗證逐筆分數、各方案是否擋單。
- results.json：來源 SHA-256、參數、切點、各幣／方向／日結果與校準分箱。
- model.json：只用訓練段擬合的標準化、權重與特徵順序，不由交易服務載入。
'''
    (out/'README.md').write_text(text)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir',type=Path,default=Path('reports/channel_exit_comparison/market_data'))
    parser.add_argument('--output',type=Path,default=Path('reports/channel_entry_risk'))
    args=parser.parse_args();args.output.mkdir(parents=True,exist_ok=True)
    datasets=[];frames=[]
    for symbol in ('1000PEPEUSDT','龙虾USDT'):
        path=args.data_dir/f'{symbol}_1786299600000_1788903600000.json'
        raw=load_data(path)
        datasets.append(dict(symbol=symbol,path=str(path),sha256=hashlib.sha256(path.read_bytes()).hexdigest(),bars=len(raw),first=int(raw.timestamp.iloc[0]),end=int(raw.timestamp.iloc[-1])+MINUTE))
        samples=make_samples(raw,symbol);frames.append(samples)
        print(f'{symbol}: {len(raw)} bars, {len(samples)} candidates',flush=True)
    samples=pd.concat(frames,ignore_index=True).sort_values(['timestamp','symbol']).reset_index(drop=True)
    start=max(d['first'] for d in datasets)+200*MINUTE;end=min(d['end'] for d in datasets)
    summary,model,test=evaluate(samples,start,end)
    summary['datasets']=datasets
    summary['parameters']=dict(kc_atr_multiplier=KELTNER_ATR_MULTIPLIER,atr_period=10,abnormal_atr=RAPID_PIVOT_IMMEDIATE_REVERSE_BODY_ATR,fee=TAKER_FEE_RATE,slippage=SLIPPAGE_PCT,features=FEATURES)
    samples.to_csv(args.output/'samples.csv',index=False)
    test.to_csv(args.output/'holdout_predictions.csv',index=False)
    (args.output/'results.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2,allow_nan=False))
    (args.output/'model.json').write_text(json.dumps(dict(features=FEATURES,model=model,live_enabled=False),indent=2,allow_nan=False))
    write_report(args.output,summary)
    print(json.dumps(dict(selected=summary['selected_filter'],holdout=summary['selected_test'],ci95=summary['selected_risk_difference_ci95']),ensure_ascii=False),flush=True)


if __name__ == '__main__':
    main()
