"""Run with PYTHONPATH=. python3 scratch/replay-abnormal-direction.py.

Minute-open causal replay: never use the current candle's completed H/L/C.
This checks strategy eligibility, not historical quote freshness or account fills.
"""
import json
from pathlib import Path
from types import SimpleNamespace
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd
from core.strategy import SuperTrendKeltnerStrategy
from core.channel_abnormal_release import opposite_entry_releases
from core.channel_outer_entry import aligned_entry

for evidence in json.loads(Path(__file__).with_name('abnormal-direction-evidence.json').read_text()):
    symbol, close, rows = evidence['symbol'], evidence['close'], evidence['klines']
    # Historical request time was not saved here. For predicate replay only,
    # anchor the synthetic ticket at the known fill; do not claim ticket recovery.
    ticket = dict(phase='closed', side='LONG', mode='outer_cycle', requires_pullback=True,
                  close_reason=close['reason'], exit_bar_id=close['id']//60000*60000,
                  close_requested_at_ms=close['id'])
    account = SimpleNamespace(positions={}, channel_profit_reentries={symbol: ticket}, trades=[close])
    first_release = first_outer = None
    for i, row in enumerate(rows):
        if row['time']*1000 <= close['id'] or row['time']*1000 >= 1789075959019:
            continue
        frame = pd.DataFrame(rows[:i+1])
        frame['timestamp'] = frame['time']*1000
        # Direction/ATR/MA3/outside eligibility do not use volume.
        frame['volume'] = 1.
        price = float(row['open'])
        frame.loc[frame.index[-1], ['high', 'low', 'close']] = price
        frame = SuperTrendKeltnerStrategy().compute_indicators(frame)
        frame['kc_middle'] = frame['ema_20']
        ready = opposite_entry_releases(account, symbol, frame, price)
        stamp = datetime.fromtimestamp(row['time'], ZoneInfo('Asia/Taipei')).isoformat()
        if ready and first_release is None:
            first_release = (stamp, price)
        if ready and aligned_entry(frame, price).get('side') == 'SHORT' and first_outer is None:
            first_outer = (stamp, price)
    print(symbol, 'release:', first_release, 'outside:', first_outer)
