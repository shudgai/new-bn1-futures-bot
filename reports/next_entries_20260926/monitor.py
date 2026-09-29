"""Read-only local API observer. Stop after the next opening fill per symbol."""
import json
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SYMBOLS = ('1000PEPE/USDT', '龙虾/USDT')
started = time.time()
found = {}
seen_logs = set()
baseline = None


def save_status(data):
    temporary = ROOT / 'status.tmp'
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n')
    temporary.replace(ROOT / 'status.json')


def record(event):
    with (ROOT / 'events.jsonl').open('a') as stream:
        stream.write(json.dumps(event, ensure_ascii=False) + '\n')


while len(found) < len(SYMBOLS):
    try:
        with urllib.request.urlopen('http://127.0.0.1:8006/api/status', timeout=5) as response:
            data = json.load(response)
        trades = data.get('trades', [])
        if baseline is None:
            baseline = max([float(t.get('id') or 0) for t in trades] + [started * 1000])
            record(dict(event='started', timestamp=started, baseline_id=baseline,
                        paper_trading=data.get('paper_trading'), symbols=SYMBOLS))
        for trade in sorted(trades, key=lambda t:float(t.get('id') or 0)):
            symbol = trade.get('symbol')
            if (symbol not in SYMBOLS or symbol in found
                    or float(trade.get('id') or 0) <= baseline
                    or trade.get('action') not in ('OPEN_LONG', 'OPEN_SHORT')):
                continue
            price, qty = float(trade.get('price') or 0), float(trade.get('qty') or 0)
            margin, leverage = float(trade.get('amount') or 0), float(trade.get('leverage') or 0)
            event = dict(event='next_open', observed_at=time.time(),
                         paper_trading=data.get('paper_trading'),
                         trade={k:trade.get(k) for k in ('id','time','symbol','action','price','qty',
                             'amount','leverage','reason','channel_confirmation_bar_id')},
                         fill_notional=price*qty, margin_times_leverage=margin*leverage,
                         notional_difference=price*qty-margin*leverage,
                         balance_after=data.get('balance'), available_after=data.get('available_balance'))
            found[symbol] = event
            record(event)
        for log in data.get('logs', []):
            stamp = float(log.get('timestamp') or 0)
            text = str(log.get('text') or '')
            key = (stamp, text)
            relevant = any(s.split('/')[0] in text for s in SYMBOLS) or 'FATAL_REJECT' in text
            if stamp >= started and key not in seen_logs and relevant:
                seen_logs.add(key)
                record(dict(event='signal_or_gate_log', **{k:log.get(k) for k in ('timestamp','text','level')}))
        if len(seen_logs) > 10000:
            seen_logs = set(sorted(seen_logs)[-5000:])
        save_status(dict(started_at=started, updated_at=time.time(),
                         state='complete' if len(found)==len(SYMBOLS) else 'monitoring',
                         engine_running=data.get('is_running'), paper_trading=data.get('paper_trading'),
                         waiting_for=[s for s in SYMBOLS if s not in found], results=found))
    except Exception as exc:
        save_status(dict(started_at=started, updated_at=time.time(), state='retrying_api',
                         error=type(exc).__name__ + ': ' + str(exc), results=found))
    if len(found) < len(SYMBOLS):
        time.sleep(5)
