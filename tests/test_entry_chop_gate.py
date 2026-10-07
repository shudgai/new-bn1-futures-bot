"""Measurable chop limits, independent authorities and fresh account validation."""
import asyncio
import copy
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pandas as pd
import pytest

from core.services.entry_chop_gate import evaluate_entry_chop
from core.services.entry_contract import evaluate_entry_contract
from core.services.entry_firewall import validate_account_entry
from core.services.ma5_outer_pivot_entry import PHASE
from core.services.ma5_outer_pivot_entry import evaluate_ma5_outer_pivot_entry
from test_lobster_cap_gates import frame
from test_two_breakout_restore import ordinary
from test_ma5_outer_pivot_entry import pivot_frame


def eligible_frame(kind, side):
    if kind == 'pivot':
        f = pivot_frame(side)
        # Keep the six-run peak and its adverse confirmation, but avoid a large
        # round trip and overlapping setup bodies in this qualified example.
        sign = 1 if side == 'SHORT' else -1
        f.loc[3, 'close'] = 100+sign*2.
        f.loc[3, 'open'] = 100+sign*1.9
        f.loc[4, 'close'] = 100+sign*1.9
        f.loc[4, 'open'] = 100+sign*2.
        return f
    f = frame() if kind == 'live' else ordinary()
    f['timestamp'] += 60000
    prefix = dict(f.iloc[0], timestamp=60000., open=100.87, close=100.9,
                  high=100.95, low=100.85)
    f = pd.concat([pd.DataFrame([prefix], index=[-1]), f])
    closes = [101.+i*.1 for i in range(5)]
    if kind == 'pair':
        closes[-2:] = [101.3, 101.48]
        f.loc[4, 'close'] = 101.48
        f.loc[f.index[-1], 'close'] = 101.49
    for index, close in enumerate(closes):
        if kind == 'pair' and index >= 3:
            continue
        f.loc[index, ['open', 'close', 'high', 'low']] = [
            close-.03, close, close+.05, close-.05]
    if kind == 'live':
        f.loc[2, 'ma5'] = f.loc[4, 'ma5']
        f.loc[3, 'ma5'] = f.loc[4, 'ma5']-.1*float(f.loc[4, 'atr'])
    if side == 'SHORT':
        old = f.copy()
        for key, source in [('open', 'open'), ('close', 'close'), ('high', 'low'),
                            ('low', 'high'), ('ma5', 'ma5'), ('ma3', 'ma3'),
                            ('ma15', 'ma15'), ('kc_upper', 'kc_lower'),
                            ('kc_lower', 'kc_upper'), ('kc_middle', 'kc_middle')]:
            f[key] = 200.-old[source]
    return f


def authority_code(kind, side):
    return ('KC_LIVE_BODY_BREAKOUT_' if kind == 'live' else
            'KC_2BAR_CONFIRM_' if kind == 'pair' else PHASE+'_')+side


@pytest.mark.parametrize('symbol', ['龙虾/USDT', 'CAP/USDT'])
@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
@pytest.mark.parametrize('kind', ['live', 'pair', 'pivot'])
def test_all_authorities_pass_nonchoppy_history(symbol, side, kind):
    f = eligible_frame(kind, side)
    status, evidence = evaluate_entry_chop(f)
    assert status == 'PASS'
    result = evaluate_entry_contract(f, symbol=symbol, code=authority_code(kind, side))
    assert result
    assert result['chop_bars'] == 6
    assert result['chop_efficiency'] >= .70
    assert result['chop_mean_overlap'] <= .50
    assert result['chop_middle_crosses'] <= 1
    assert evidence['chop_end_ms'] == float(f.iloc[-2].timestamp)


def overlapping_history(f):
    for index in f.index[:-1]:
        f.loc[index, 'open'] = 100.
        f.loc[index, 'high'] = max(float(f.loc[index, 'high']), 100.1)
        f.loc[index, 'low'] = min(float(f.loc[index, 'low']), 99.9)


@pytest.mark.parametrize('symbol', ['龙虾/USDT', 'CAP/USDT'])
@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
@pytest.mark.parametrize('kind', ['live', 'pair', 'pivot'])
def test_overlapping_history_exempts_only_valid_breakouts(symbol, side, kind):
    f = eligible_frame(kind, side)
    overlapping_history(f)
    diagnostics = {}
    result = evaluate_entry_contract(f, symbol=symbol, code=authority_code(kind, side),
                                    diagnostics=diagnostics)
    if kind == 'pivot':
        assert result is None
        assert diagnostics['reason'] == 'BLOCKED_CHOP_BODY_OVERLAP'
    else:
        assert result and result['chop_limits_exempt'] is True
        assert result['chop_mean_overlap'] > .50


def metric_frame(closes):
    rows = [dict(timestamp=(i+1)*60000., open=c-.01, close=c, high=c+.1,
                 low=c-.1, kc_middle=90., is_closed=True) for i, c in enumerate(closes)]
    rows.append(dict(rows[-1], timestamp=420000., is_closed=False))
    return pd.DataFrame(rows)


@pytest.mark.parametrize('last,passed', [(105., True), (103.5, True),
                                        (103.499999, False), (103.500001, True)])
def test_efficiency_inclusive_seventy_percent(last, passed):
    f = metric_frame([100., 101., 102., 103., 104.25, last])
    status, result = evaluate_entry_chop(f)
    assert bool(result) is passed
    if not passed:
        assert status == 'BLOCKED_CHOP_LOW_EFFICIENCY'


@pytest.mark.parametrize('step,passed', [(.5, True), (.500001, True), (.499999, False)])
def test_overlap_inclusive_fifty_percent(step, passed):
    f = metric_frame([100.+i*step for i in range(6)])
    f.loc[f.index[:-1], 'open'] = f.close.iloc[:-1]-1.
    f.loc[f.index[:-1], 'low'] = f.open.iloc[:-1]-.1
    status, result = evaluate_entry_chop(f)
    assert bool(result) is passed
    if not passed:
        assert status == 'BLOCKED_CHOP_BODY_OVERLAP'


@pytest.mark.parametrize('crosses', [0, 1, 2])
def test_middle_cross_count_and_touch_bridge(crosses):
    f = metric_frame([100., 101., 102., 103., 104., 105.])
    offsets = [-.1]*6 if crosses == 0 else [-.1, 0., .1, .1, .1, .1] if crosses == 1 else [-.1, 0., .1, 0., -.1, -.1]
    f.loc[f.index[:-1], 'kc_middle'] = [c+o for c, o in zip(f.close.iloc[:-1], offsets)]
    status, result = evaluate_entry_chop(f)
    assert bool(result) is (crosses <= 1)
    if result:
        assert result['chop_middle_crosses'] == crosses


@pytest.mark.parametrize('fault', ['flat', 'wave', 'missing', 'nan', 'ohlc',
                                  'gap', 'future', 'short', 'doji'])
def test_bad_or_choppy_history_fails_closed(fault):
    f = metric_frame([100., 101., 102., 103., 104., 105.])
    if fault in ('flat', 'wave'):
        closes = [100.]*6 if fault == 'flat' else [100., 102., 100., 102., 100., 101.]
        for i, c in enumerate(closes):
            f.loc[i, ['open', 'close', 'high', 'low']] = [c-.01, c, c+.1, c-.1]
    elif fault == 'missing':
        f = f.drop(columns=['kc_middle'])
    elif fault == 'nan':
        f.loc[2, 'close'] = float('nan')
    elif fault == 'ohlc':
        f.loc[2, 'low'] = 200.
    elif fault == 'gap':
        f.loc[1, 'timestamp'] = 60000.
    elif fault == 'future':
        f.loc[5, 'timestamp'] = 480000.
    elif fault == 'short':
        f = f.iloc[1:]
    else:
        f.loc[f.index[:-1], 'open'] = f.close.iloc[:-1]
    assert evaluate_entry_chop(f)[1] is None


@pytest.mark.parametrize('symbol', ['龙虾/USDT', 'CAP/USDT'])
@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
@pytest.mark.parametrize('count', [6, 9, 10, 11])
def test_outer_run_minimum_counts_only_completed_outside_candles(symbol, side, count):
    f = pivot_frame(side)
    if count < 10:
        f = f.iloc[10-count:].copy()
    elif count > 10:
        f['timestamp'] += 60000.
        row = dict(f.iloc[0])
        sign = 1 if side == 'SHORT' else -1
        row.update(timestamp=60000., open=100+sign*1.001,
                   close=100+sign*1.005, high=101.02 if sign == 1 else 99.01,
                   low=100.99 if sign == 1 else 98.98)
        f = pd.concat([pd.DataFrame([row], index=[-7]), f])
    result = evaluate_ma5_outer_pivot_entry(f, float(f.iloc[-1].close), symbol)
    assert bool(result) is (count >= 10)
    if result:
        assert result['outer_run_bars'] == 10
        assert len(result['outer_run_candles']) == 10
        for row in result['outer_run_candles']:
            assert row['close'] > row['kc_upper'] if side == 'SHORT' else row['close'] < row['kc_lower']


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
@pytest.mark.parametrize('symbol', ['龙虾/USDT', 'CAP/USDT'])
@pytest.mark.parametrize('kind', ['live', 'pair', 'pivot'])
def test_firewall_revokes_cached_entry_on_new_chop_history(side, symbol, kind, monkeypatch):
    f = eligible_frame(kind, side)
    now = float(f.iloc[-1].timestamp)+1000
    monkeypatch.setattr('time.time', lambda: now/1000)
    f.attrs.update(entry_finality_verified=True, entry_finality_server_ms=now)
    account = SimpleNamespace(positions={}, trades=[], position_meta={},
                              last_closed_at={}, log=Mock(), save_state=Mock(),
                              entry_frame_provider=AsyncMock(return_value=f))
    context = dict(entry_signal_code=authority_code(kind, side),
                   channel_confirmation_bar_id=float(f.iloc[-1].timestamp))
    asyncio.run(validate_account_entry(account, symbol, side, context))
    cached = copy.deepcopy(context)
    overlapping_history(f)
    if kind == 'pivot':
        with pytest.raises(ValueError, match='BLOCKED_CHOP_BODY_OVERLAP'):
            asyncio.run(validate_account_entry(account, symbol, side, cached))
    else:
        asyncio.run(validate_account_entry(account, symbol, side, cached))
        assert cached['entry_snapshot']['chop_limits_exempt'] is True
