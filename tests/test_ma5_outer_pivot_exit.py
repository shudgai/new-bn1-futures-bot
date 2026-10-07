import asyncio
import copy
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pandas as pd
import pytest

from core.services.exits.ma5_outer_pivot_exit import REASON
from core.services.exits.peak_trailing_exit import STATE_KEY, evaluate_peak_trailing, migrate_peak_state
from core.services.exits.structural_holding_exit import POLICY, HARD, WATERFALL
from core.services.exits.realtime_profit_exit import cached_tick_indicators, enforce_realtime_profit_exit
from core.services.exits.entry_atr_protection import enforce_atr_protection


def sample(side='LONG', symbol='龙虾/USDT'):
    sign = 1 if side == 'LONG' else -1
    rows = [dict(timestamp=stamp, open=100., high=104., low=96., close=100.,
                 ma5=value, kc_upper=101., kc_lower=99., is_closed=True, atr=1.)
            for stamp, value in zip((120000., 180000., 240000.), (101., 102., 101.7))]
    rows[1]['high' if side == 'LONG' else 'low'] = 105. if side == 'LONG' else 95.
    if sign == -1:
        for row in rows:
            row['ma5'] = 200. - row['ma5']
    p = dict(side=side, symbol=symbol, entry_price=100., qty=1., entry_atr=1.,
             entry_mode='CHANNEL_SWING', open_timestamp=60., initial_sl=100-sign*10.,
             margin=100., leverage=1.)
    snapshot = dict(quote_ms=301000., live_bar_ms=300000., closed_bar_ms=240000.,
                    reason=None, live_open=100., atr=1., ma5_pivot_history=rows)
    return p, snapshot, sign


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
@pytest.mark.parametrize('symbol', ['龙虾/USDT', 'CAP/USDT'])
@pytest.mark.parametrize('location', ['inside', 'touch', 'outside'])
def test_retired_price_pivot_has_no_authority_at_any_location(side, symbol, location):
    p, s, sign = sample(side, symbol)
    for row, offset in zip(s['ma5_pivot_history'], (0., .2, .1)):
        row['ma5'] = 100+sign*offset
    if location == 'touch':
        s['ma5_pivot_history'][1]['kc_upper' if sign == 1 else 'kc_lower'] = 100+sign*.2
    elif location == 'outside':
        for row, offset in zip(s['ma5_pivot_history'], (2., 1.8, 1.4)):
            row['ma5'] = 100+sign*offset
    result = evaluate_peak_trailing(p, 100., s)
    assert result is None
    assert 'ma5_outer_pivot' not in p[STATE_KEY]


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
@pytest.mark.parametrize('symbol', ['龙虾/USDT', 'CAP/USDT'])
def test_ma5_small_bend_without_price_extreme_cannot_close(side, symbol):
    p, s, sign = sample(side, symbol)
    for row in s['ma5_pivot_history']:
        row['high'], row['low'] = 104., 96.
    assert evaluate_peak_trailing(p, 100., s) is None
    assert p[STATE_KEY]['live_ma5_v_status'] == 'BLOCKED_LIVE_MA5_V_DATA'


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
@pytest.mark.parametrize('symbol', ['龙虾/USDT', 'CAP/USDT'])
def test_initial_atr_cross_holds_and_old_pending_is_retired(side, symbol):
    p, s, sign = sample(side, symbol)
    p['initial_sl'] = 100-sign*.5
    no_frame = dict(quote_ms=301000., reason='NO_DATA')
    assert evaluate_peak_trailing(p, 100-sign*.6, no_frame) is None
    p[STATE_KEY].update(pending='EXIT_INITIAL_ATR_HARD_STOP', trigger='INITIAL_ATR')
    p['channel_hard_stop_pending'] = 'INITIAL_ATR'
    p['closed_exit_state'] = dict(pending=True, reason='EXIT_INITIAL_ATR_HARD_STOP')
    meta = copy.deepcopy(p)
    restarted = json.loads(json.dumps(p))
    migrate_peak_state(restarted, meta)
    for source in (restarted, meta):
        assert 'channel_hard_stop_pending' not in source
        assert 'closed_exit_state' not in source
        assert source[STATE_KEY].get('pending') is None
    assert evaluate_peak_trailing(restarted, 100-sign*.6, no_frame) is None
    assert evaluate_peak_trailing(restarted, 100-sign*.6, s) is None


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
@pytest.mark.parametrize('symbol', ['龙虾/USDT', 'CAP/USDT'])
@pytest.mark.parametrize('profitable', [True, False])
def test_first_closed_reversal_without_trade_pressure_cannot_exit(side, symbol, profitable):
    p, snapshot, sign = sample(side, symbol)
    price = 100 + sign*(.3 if profitable else -.1)
    snapshot.update(ma5=100+sign*2., last_ma5=100+sign*1.,
                    kc_closed_history=[dict(timestamp=180000., middle=100.),
                                       dict(timestamp=240000., middle=100+sign*.1)])
    snapshot['swing_structure_'+side.lower()] = dict(side=side, intact=True)
    result = evaluate_peak_trailing(p, price, snapshot)
    assert result is None
    assert sign*(snapshot['ma5_pivot_history'][-1]['ma5']-(101. if sign == 1 else 99.)) > 0
    assert 'ma5_outer_pivot' not in p[STATE_KEY]
    assert 'profit_stop_price' not in p[STATE_KEY]


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
@pytest.mark.parametrize('fault', ['no_price_pivot', 'equal_price', 'nan', 'ohlc',
    'gap', 'duplicate', 'preentry', 'stale', 'future', 'fallback'])
def test_unconfirmed_noise_and_bad_data_cannot_close(side, fault):
    p, s, sign = sample(side)
    rows = s['ma5_pivot_history']
    if fault == 'no_price_pivot':
        rows[2]['high' if sign == 1 else 'low'] = 106. if sign == 1 else 94.
    elif fault == 'equal_price':
        rows[2]['high' if sign == 1 else 'low'] = rows[1]['high' if sign == 1 else 'low']
    elif fault == 'nan': rows[1]['low'] = float('nan')
    elif fault == 'ohlc': rows[1]['close'] = 106.
    elif fault == 'gap': rows[0]['timestamp'] = 60000.
    elif fault == 'duplicate': rows[0]['timestamp'] = rows[1]['timestamp']
    elif fault == 'preentry': p['open_timestamp'] = 121.
    elif fault == 'stale': s['closed_bar_ms'] = 180000.
    elif fault == 'future': s['quote_ms'] = 299999.
    else: s['fallback_used'] = True
    assert evaluate_peak_trailing(p, 100., s) is None


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_retired_closed_price_pivot_cannot_exit_despite_closed_ma5_still_advancing(side):
    p, s, sign = sample(side)
    s['ma5_pivot_history'][-1]['ma5'] = 100+sign*2.1
    s.update(ma5=100+sign*.5, last_ma5=100+sign*2.)
    assert evaluate_peak_trailing(p, 100., s) is None


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
@pytest.mark.parametrize('symbol', ['龙虾/USDT', 'CAP/USDT'])
@pytest.mark.parametrize('ma_state', ['flat', 'favorable', 'missing', 'invalid'])
def test_price_only_history_no_longer_authorizes_exit(side, symbol, ma_state):
    p, s, sign = sample(side, symbol)
    s['pivot_exit_history'] = copy.deepcopy(s.pop('ma5_pivot_history'))
    for row in s['pivot_exit_history']:
        row.pop('kc_lower')
        row.pop('kc_upper')
        if ma_state == 'missing':
            row.pop('ma5')
        else:
            row['ma5'] = (float('nan') if ma_state == 'invalid' else
                          100. if ma_state == 'flat' else 100+sign*row['timestamp']/60000)
    result = evaluate_peak_trailing(p, 100., s)
    assert result is None
    assert 'ma5_outer_pivot' not in p[STATE_KEY]


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
@pytest.mark.parametrize('symbol', ['龙虾/USDT', 'CAP/USDT'])
def test_right_candle_closure_does_not_restore_retired_pivot_authority(side, symbol):
    p, s, sign = sample(side, symbol)
    s.update(quote_ms=241000., live_bar_ms=240000., closed_bar_ms=180000.)
    assert evaluate_peak_trailing(p, 100., s) is None
    s.update(quote_ms=301000., live_bar_ms=300000., closed_bar_ms=240000.)
    assert evaluate_peak_trailing(p, 100., s) is None


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_old_price_ma5_retry_is_retired_without_new_market_data(side):
    p, s, _ = sample(side)
    evaluate_peak_trailing(p, 100., s)
    state = p[STATE_KEY]
    state.update(pending='EXIT_CLOSED_PRICE_PIVOT_MA5_REVERSE',
                 trigger='EXIT_CLOSED_PRICE_PIVOT_MA5_REVERSE',
                 holding_exit_policy='closed_price_pivot_ma5_reverse_v3')
    state['ma5_outer_pivot'] = dict(rule_version=2)
    meta = copy.deepcopy(p)
    restarted = json.loads(json.dumps(p))
    migrate_peak_state(restarted, meta)
    assert not restarted[STATE_KEY].get('pending')
    assert not meta[STATE_KEY].get('pending')
    assert evaluate_peak_trailing(restarted, 100., {'quote_ms': 302000., 'reason': 'NO_DATA'}) is None


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_snapshot_uses_exact_closed_ma5_and_pivot_rail_not_live(side):
    _, s, _ = sample(side)
    f = pd.DataFrame(s['ma5_pivot_history']+[dict(timestamp=300000., open=100.,
        high=104., low=96., close=100., ma5=777., kc_upper=999., kc_lower=1.,
        is_closed=False, atr=1.)])
    f.attrs['timeframe_ms'] = 60000
    snap, _ = cached_tick_indicators(f, 100., 301000.)
    assert [r['ma5'] for r in snap['ma5_pivot_history']] == list(f.iloc[:3].ma5)
    assert snap['ma5_pivot_history'][1]['kc_upper'] == 101.
    f.loc[2, 'is_closed'] = False
    snap, _ = cached_tick_indicators(f, 100., 301000.)
    assert len(snap['ma5_pivot_history']) == 2


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
@pytest.mark.parametrize('legacy', ['EXIT_CONFIRMED_SWING_STRUCTURE', 'EXIT_CONFIRMED_PIVOT_TURN',
                                  'EXIT_EARLY_SWING_REVERSAL', 'EXIT_TERMINAL_DOJI_PRESSURE',
                                  'EXIT_CLOSED_MA5_OUTER_PIVOT'])
def test_old_authority_cannot_survive_migration(side, legacy):
    p, _, _ = sample(side)
    state = migrate_peak_state(p)
    state.update(pending=legacy, trigger=legacy, holding_exit_policy='pre0600_structure_no_profit_lock_v1')
    meta = {'entry_mode': 'CHANNEL_SWING', STATE_KEY: copy.deepcopy(state)}
    p['entry_mode'] = ''
    migrate_peak_state(p, meta)
    assert 'pending' not in p[STATE_KEY]
    assert 'pending' not in meta[STATE_KEY]
    assert p[STATE_KEY]['peak_price'] == 100.


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_retired_price_pivot_pending_cannot_retry_after_restart(side, monkeypatch):
    p, s, _ = sample(side)
    evaluate_peak_trailing(p, 100., s)
    p[STATE_KEY].update(pending=REASON, trigger=REASON,
                        holding_exit_policy='closed_price_pivot_only_v4',
                        ma5_outer_pivot=dict(rule_version=3))
    p = json.loads(json.dumps(p))
    account = SimpleNamespace(positions={'X': p}, position_meta={},
                              save_state=Mock(), log=Mock(), close_position=AsyncMock(return_value=False))
    monkeypatch.setattr('time.time', lambda: 302.)
    assert not asyncio.run(enforce_atr_protection(account, 'X', 100.))
    assert not account.position_meta['X'][STATE_KEY].get('pending')
    account.close_position.return_value = True
    assert not asyncio.run(enforce_atr_protection(account, 'X', 100.))
    account.close_position.assert_not_awaited()


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
@pytest.mark.parametrize('risk', ['margin', 'waterfall'])
def test_retained_risk_overrides_ma5(side, risk):
    p, s, sign = sample(side)
    price = 100.
    if risk == 'margin':
        p['margin'] = 1.
        price = 100-sign*.1
    else:
        s['live_open'] = 100+sign*2.
    result = evaluate_peak_trailing(p, price, s)
    assert result['reason'] == (WATERFALL if risk == 'waterfall' else HARD)


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_retired_price_pivot_cannot_close_through_realtime_adapter(side, monkeypatch):
    p, s, _ = sample(side)
    account = SimpleNamespace(positions={'X': p}, position_meta={},
        save_state=Mock(), log=Mock(), close_position=AsyncMock(return_value=False))
    engine = SimpleNamespace(account=account, is_running=True, _channel_exit_frames={},
                             _entry_boundary_frame=AsyncMock(side_effect=AssertionError('No entry checks')))
    monkeypatch.setattr('time.time', lambda: 301.)
    monkeypatch.setattr('core.services.exits.realtime_profit_exit.cached_tick_indicators', lambda *a: (s, 1.))
    monkeypatch.setattr('core.services.exits.trend_hold_evaluator.evaluate_trend_hold',
                        lambda *a, **k: ('HOLD', 'TEST'))
    assert not asyncio.run(enforce_realtime_profit_exit(engine, 'X', 100., 301000.))
    assert account.positions['X'][STATE_KEY]['holding_exit_policy'] == POLICY
    assert 'exit_protection_snapshot' not in account.position_meta['X']
    account.close_position.assert_not_awaited()
    engine._entry_boundary_frame.assert_not_awaited()


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_replaced_position_never_inherits_old_ma5_pending(side):
    p, s, _ = sample(side)
    evaluate_peak_trailing(p, 100., s)
    replacement = dict(p, open_timestamp=301.)
    replacement[STATE_KEY] = copy.deepcopy(p[STATE_KEY])
    assert evaluate_peak_trailing(replacement, 100., 302000.) is None
    assert 'pending' not in replacement[STATE_KEY]


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_new_pivot_exit_is_channel_only(side):
    p, s, _ = sample(side)
    p['entry_mode'] = 'MANUAL'
    assert evaluate_peak_trailing(p, 100., s) is None
