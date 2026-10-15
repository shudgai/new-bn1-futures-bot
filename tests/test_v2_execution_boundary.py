"""V2 signal -> engine -> real paper account, with no external orders."""
import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pandas as pd
import pytest

from core.services.strategies.pure_trend_v2 import evaluate_v2_frame
from core.services.entry_firewall import validate_account_entry, validate_entry_frame


def candles(side='LONG', live=True):
    stamp = int(time.time() // 60) * 60000
    # Build a valid LONG trend once, then mirror every price/indicator for SHORT.
    sign = 1
    rows = []
    for i in range(6):
        middle = 100. + sign * 0.04 * i
        ma3 = middle + sign * 0.8
        ma15 = middle + 0.3
        rows.append(dict(
            timestamp=stamp-(5-i)*60000, open=middle, close=middle+sign*0.2,
            high=middle+0.4, low=middle-0.2,
            ma3=ma3, ma5=ma3, ma15=ma15, atr=1.,
            kc_upper=middle+1.0, kc_middle=middle, kc_lower=middle-1.0,
            is_closed=True,
        ))
    rows[-3].update(open=100.8, close=101.3, high=101.6, low=100.7)
    rows[-2].update(open=101.5, close=101.8, high=101.9, low=101.4)
    rows[-1].update(open=101.5, close=101.8, high=101.9, low=101.4, is_closed=not live)
    if not live:
        for row in rows: row['timestamp'] -= 60000
    f = pd.DataFrame(rows)
    if side == 'SHORT':
        original = f.copy()
        for a,b in [('open','open'),('close','close'),('high','low'),('low','high'),
                    ('ma3','ma3'),('ma5','ma5'),('ma15','ma15'),('kc_upper','kc_lower'),
                    ('kc_lower','kc_upper'),('kc_middle','kc_middle')]:
            f[a] = 200-original[b]
    f.attrs['timeframe_ms'] = 60000
    return f


def context(f, side):
    d = evaluate_v2_frame(f)
    return dict(entry_mode='CHANNEL_SWING', entry_signal_code=d['type'],
                channel_confirmation_bar_id=d['confirmation_bar_id'])




@pytest.mark.parametrize('side', ['LONG','SHORT'])
@pytest.mark.parametrize('fault', ['inside','atr','wrong_side','unknown','expired','changed'])
def test_account_boundary_rejects_invalid(side, fault):
    f = candles(side); ctx = context(f,side)
    if fault == 'inside': f.loc[f.index[-1], 'close'] = 100.
    if fault == 'atr': f.loc[f.index[-2], 'atr'] = float('nan')
    if fault == 'wrong_side': side = 'SHORT' if side == 'LONG' else 'LONG'
    if fault == 'unknown': ctx['entry_signal_code'] = 'UNKNOWN'
    if fault == 'expired':
        f['timestamp'] -= 300000; ctx['channel_confirmation_bar_id'] -= 300000
    if fault == 'changed': ctx['channel_confirmation_bar_id'] -= 60000
    account = SimpleNamespace(entry_frame_provider=AsyncMock(return_value=f), last_closed_at={})
    with pytest.raises(ValueError):
        asyncio.run(validate_account_entry(account,'TEST',side,ctx))




@pytest.mark.parametrize('side', ['LONG','SHORT'])
def test_obsolete_entry_codes_rejected(side):
    f = candles(side)
    for prefix in ('THIRD_BAR_INTRA_', 'THIRD_BAR_TRACK_RIDING_', 'THREE_BAR_BREAKOUT_', 'CONTINUATION_'):
        with pytest.raises(ValueError):
            validate_entry_frame(f,side,prefix+side)


