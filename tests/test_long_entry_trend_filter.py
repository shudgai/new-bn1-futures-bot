"""Long reversal confirmation at both signal and refreshed account boundaries."""
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pandas as pd
import pytest
from test_entry_e2e_20260928 import candles
from core.services.strategies.unified_entry_strategy import evaluate_closed_entry, long_entry_trend_problem
from core.services.entry_firewall import validate_entry_frame, validate_account_entry


def bearish_bounce():
    f = candles(body=.55)
    f['kc_middle'] = [100.6, 100.5, 100.4, 100.3, 100.2, 100.1]
    f['kc_upper'] = f.kc_middle + .3
    f['kc_lower'] = f.kc_middle - .3
    f['ma15'] = [100.35, 100.3, 100.25, 100.2, 100.15, 100.1]
    return f


def test_real_lobster_ignition_is_now_rejected():
    trade = json.loads(Path('reports/long_trend_filter_20260928/lobster_trade.json').read_text())
    assert trade['reason'] == 'Closed1M CLOSED_IGNITION_LONG'
    f = pd.DataFrame(trade['entry_snapshot']['evidence']['candles'])
    f['is_closed'] = True
    assert f.iloc[-1].close > f.iloc[-1].kc_upper
    assert f.iloc[-1].ma15 > f.iloc[-2].ma15
    assert evaluate_closed_entry(f, 'LONG')[1] == 'BLOCKED_LONG_REVERSAL_UNCONFIRMED'
    with pytest.raises(ValueError, match='BLOCKED_LONG_REVERSAL_UNCONFIRMED'):
        validate_entry_frame(f, 'LONG', 'CLOSED_IGNITION_LONG')


@pytest.mark.parametrize('body', [.2, .55])
def test_bearish_bounce_cannot_bypass_with_strong_rule(body):
    f = bearish_bounce()
    f.loc[5, 'open'] = f.loc[5, 'close'] - body
    assert evaluate_closed_entry(f, 'LONG')[1] == 'BLOCKED_LONG_REVERSAL_UNCONFIRMED'
    for rule in ('IGNITION', 'TREND_BREAKOUT', 'TREND_CRAWLING'):
        with pytest.raises(ValueError, match='BLOCKED_LONG_REVERSAL_UNCONFIRMED'):
            validate_entry_frame(f, 'LONG', f'CLOSED_{rule}_LONG')


@pytest.mark.parametrize('confirmation', ['ma15', 'two_closes', 'ck'])
def test_each_confirmation_is_independently_sufficient(confirmation):
    f = bearish_bounce()
    if confirmation == 'ma15':
        f.loc[3:5, 'ma15'] = [100., 100.05, 100.1]
    elif confirmation == 'two_closes':
        f.loc[4, 'close'] = 100.25
    else:
        for key, base in [('kc_middle', 99.9), ('kc_upper', 100.2), ('kc_lower', 99.6)]:
            f.loc[3:5, key] = [base, base+.05, base+.1]
        f.loc[4, 'close'] = 99.9
    assert long_entry_trend_problem(f) is None


@pytest.mark.parametrize('offset', [0., -.01])
def test_inside_or_touching_upper_rejected_even_with_rising_ma15(offset):
    f = candles(body=.55)
    f.loc[3:5, 'ma15'] = [99.8, 99.9, 100.]
    f.loc[5, 'close'] = f.loc[5, 'kc_upper'] + offset
    assert evaluate_closed_entry(f, 'LONG')[1] == 'BLOCKED_LONG_CLOSE_NOT_ABOVE_UPPER'
    with pytest.raises(ValueError, match='BLOCKED_LONG_CLOSE_NOT_ABOVE_UPPER'):
        validate_entry_frame(f, 'LONG', 'CLOSED_IGNITION_LONG')


def test_forming_bar_cannot_confirm_reversal():
    f = bearish_bounce()
    live = f.iloc[-1].copy()
    live['timestamp'] += 60000
    live['is_closed'] = False
    live['close'] = 105.
    live['ma15'] = 104.
    frame = pd.concat([f, pd.DataFrame([live])], ignore_index=True)
    assert evaluate_closed_entry(frame, 'LONG')[1] == 'BLOCKED_LONG_REVERSAL_UNCONFIRMED'


def test_cached_signal_is_rejected_by_latest_account_snapshot():
    old = candles(body=.55)
    assert evaluate_closed_entry(old, 'LONG')[0]
    latest = bearish_bounce()
    latest['timestamp'] = old['timestamp']
    account = SimpleNamespace(entry_frame_provider=AsyncMock(return_value=latest), last_closed_at={})
    context = dict(entry_signal_code='CLOSED_IGNITION_LONG', channel_confirmation_bar_id=float(old.iloc[-1].timestamp))
    with pytest.raises(ValueError, match='BLOCKED_LONG_REVERSAL_UNCONFIRMED'):
        asyncio.run(validate_account_entry(account, 'TEST', 'LONG', context))


def test_short_entry_unchanged():
    f = candles('SHORT', body=.55)
    assert evaluate_closed_entry(f, 'SHORT')[1] == 'CLOSED_IGNITION_SHORT'
    assert validate_entry_frame(f, 'SHORT', 'CLOSED_IGNITION_SHORT')['action'] == 'ENTER'
