"""Regression for the reported PEPE exit and Lobster entry on 2026-09-14."""
import json
from pathlib import Path
from unittest.mock import AsyncMock

import pandas as pd
import pytest

from core.engine import TradingEngine
from core.services.strategies.outer_strategy import aligned_entry, three_point_pivot_exit_ready
from core.services.strategies.pivot_strategy import pivot_entry
from test_channel_swing_execution import SYMBOL, _execution_engine


@pytest.fixture
def anyio_backend():
    return 'asyncio'


def breakout_market(side='LONG'):
    f = pd.DataFrame({
        'open': [100., 101.8, 102.5, 103.],
        'close': [100.3, 102.5, 103., 103.1],
        'high': [100.5, 102.7, 103.2, 103.15],
        'low': [99.8, 101.6, 102.3, 102.9],
        'ma3': [100., 100.5, 101., 101.1],
        'ma15': [99., 99.2, 99.3, 99.4],
        'kc_middle': [99.9, 100., 100.1, 100.2],
        'kc_upper': [102.] * 4, 'kc_lower': [98.] * 4, 'atr': [2.] * 4,
        'timestamp': [60000, 120000, 180000, 240000],
    })
    if side == 'SHORT':
        old = f.copy()
        for col in ('open', 'close', 'ma3', 'ma15', 'kc_middle'):
            f[col] = 200. - old[col]
        f['high'], f['low'] = 200. - old.low, 200. - old.high
    return f


def reported(name):
    data = json.loads((Path(__file__).parent / 'fixtures/pepe_lobster_20260914_1721.json').read_text())[name]
    return pd.DataFrame(data['rows']), data['price']


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
@pytest.mark.parametrize('case', ['valid', 'first_weak', 'second_weak', 'second_opposite', 'second_live', 'no_cross', 'inside', 'ma3_flat', 'kc_flat', 'kc_opposite'])
def test_only_two_closed_breakout_bodies_allow_normal_entry(side, case):
    f = breakout_market(side)
    sign = 1 if side == 'LONG' else -1
    if case in ('first_weak', 'second_weak'):
        f.loc[1 if case == 'first_weak' else 2, ['high', 'low']] = [110., 90.]
    elif case == 'second_opposite':
        f.loc[2, 'open'] = f.loc[2, 'close'] + .1 * sign
    elif case == 'second_live':
        f = f.iloc[:-1].copy()
    elif case == 'no_cross':
        f.loc[1, 'open'] = 102.2 if side == 'LONG' else 97.8
    elif case == 'ma3_flat':
        f.loc[2, 'ma3'] = f.loc[1, 'ma3']
    elif case in ('kc_flat', 'kc_opposite'):
        f.loc[2, 'kc_middle'] = f.loc[1, 'kc_middle'] - (sign if case == 'kc_opposite' else 0.)
    price = 100. if case == 'inside' else float(f.iloc[-1]['close'])
    assert pivot_entry(f, price)['action'] == 'WAIT'
    for flags in ({}, dict(require_second_body=False, special_k_exempt=True, continuation_exempt=True, legacy_fallback=True)):
        d = aligned_entry(f, price, **flags)
        assert (d['action'] == 'ENTER') is (case == 'valid'), d
    assert (TradingEngine._channel_swing_action(f, price)['action'] == 'ENTER') is (case == 'valid')


def test_reported_lobster_reversal_after_peak_requires_closed_bodies():
    f, price = reported('lobster')
    assert pivot_entry(f, price)['action'] == 'WAIT'
    assert aligned_entry(f, price)['reason'] == 'KC_SECOND_BODY_WAIT'
    assert TradingEngine._channel_swing_action(f, price)['action'] == 'WAIT'
    inside_price = float(f.iloc[-1]['open'])
    assert aligned_entry(f, inside_price)['reason'] == 'KC_CHOP_WAIT'


@pytest.mark.parametrize('mirror', [False, True])
def test_reported_pepe_small_bounce_is_not_an_exit_while_kc_still_trends(mirror):
    f, price = reported('pepe')
    side = 'SHORT'
    if mirror:
        old = f.copy()
        for col in ('open', 'close', 'ma3', 'ma15', 'kc_middle'):
            f[col] = .01 - old[col]
        f['high'], f['low'] = .01 - old.low, .01 - old.high
        f['kc_upper'], f['kc_lower'] = .01 - old.kc_lower, .01 - old.kc_upper
        price, side = .01 - price, 'LONG'
    assert not three_point_pivot_exit_ready(f, side, price)
    # Once KC stops opposing the closed price/MA3 turn, the exit is valid.
    f.loc[f.index[-2], 'kc_middle'] = f.iloc[-3]['kc_middle']
    assert three_point_pivot_exit_ready(f, side, price)
    broken_price = float(f.iloc[-3]['low' if side == 'SHORT' else 'high'])
    assert not three_point_pivot_exit_ready(f, side, broken_price)


@pytest.mark.anyio
@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
async def test_valid_two_body_breakout_reaches_account_once(side, monkeypatch):
    f = breakout_market(side)
    e = _execution_engine(f, side, True)
    e.account.positions.clear()
    e.account.save_state = lambda: None
    e.tickers[SYMBOL] = float(f.iloc[-1]['close'])
    e._abnormal_market_entry_allowed = lambda *a, **k: True
    e._channel_chop_state = lambda *_: {'detected': False}
    monkeypatch.setattr('core.engine.DEFAULT_SYMBOLS', [SYMBOL])
    await e._process_single_symbol(SYMBOL, 241., None, False)
    assert [(event[0], event[2]) for event in e.account.events] == [('open', side)], e.account.logs


@pytest.mark.anyio
async def test_reported_pepe_scan_holds_instead_of_small_valley_exit():
    f, price = reported('pepe')
    e = _execution_engine(f, 'SHORT', True)
    e.tickers[SYMBOL] = price
    e.account.save_state = lambda: None
    e.account.positions[SYMBOL].update(entry_price=.00343025694, qty=109321.25, open_timestamp=1789377090.68)
    await e._process_single_symbol(SYMBOL, 1789377663., None, False)
    assert not e.account.events, e.account.logs
    assert SYMBOL in e.account.positions


@pytest.mark.parametrize('mode', ['same_side_special_k', 'trend_same_side'])
def test_old_ticket_cannot_bypass_reversal_body_confirmation(mode):
    f, price = reported('lobster')
    e = _execution_engine(f, 'LONG', True)
    e.account.positions.clear()
    ticket = dict(phase='closed', side='LONG', mode=mode, exit_bar_id=float(f.iloc[-2]['timestamp']))
    assert not e._profit_reentry_ready(SYMBOL, ticket, f, price)
    assert not e._profit_reentry_ready(SYMBOL, ticket, f, float(f.iloc[-1]['open']))


@pytest.mark.anyio
@pytest.mark.parametrize('blocked', ['daily', 'balance', 'abnormal'])
async def test_general_breakout_retains_account_risk_checks(blocked, monkeypatch):
    f = breakout_market()
    e = _execution_engine(f, 'LONG', True)
    e.account.positions.clear()
    e.account.save_state = lambda: None
    e.tickers[SYMBOL] = float(f.iloc[-1]['close'])
    e._channel_chop_state = lambda *_: {'detected': False}
    e._abnormal_market_entry_allowed = lambda *a, **k: blocked != 'abnormal'
    if blocked == 'balance':
        e.account.get_available_balance = lambda: 0.
    monkeypatch.setattr('core.engine.DEFAULT_SYMBOLS', [SYMBOL])
    assert not await e._execute_confirmed_channel_break(SYMBOL, f, e.tickers[SYMBOL], 'LONG', daily_halt=blocked == 'daily')
    assert not e.account.events


@pytest.mark.anyio
@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
async def test_hard_stop_still_closes_without_a_peak_or_valley(side, monkeypatch):
    from core import config
    f = breakout_market(side)
    e = _execution_engine(f, side, True)
    e.account.save_state = lambda: None
    e.account.positions[SYMBOL].update(entry_price=100., qty=3.75, margin=75., leverage=5.)
    e.tickers[SYMBOL] = 103. if side == 'SHORT' else 97.
    monkeypatch.setattr(config, 'MAX_POSITION_MARGIN_LOSS_RATIO', .10)
    assert not three_point_pivot_exit_ready(f, side, e.tickers[SYMBOL])
    await e._process_single_symbol(SYMBOL, 241., None, False)
    assert len(e.account.events) == 1
    assert 'HARD_STOP' in e.account.events[0][3]


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_atr_stop_remains_independent_of_the_valley_gate(side, monkeypatch):
    from core import config
    from core.services.exits.profit_protection_service import protection
    f = breakout_market(side)
    monkeypatch.setattr(config, 'CHANNEL_ATR_EXIT_ENABLED', True)
    monkeypatch.setattr(config, 'CHANNEL_ATR_STOP_MULT', 1.5)
    p = dict(side=side, entry_price=100., qty=1., atr=1., open_timestamp=1.)
    price = 104. if side == 'SHORT' else 96.
    result = protection(p, price, 0., 0., frame=f)
    assert result and result['triggered'] and result['exit_kind'] == 'ATR_STOP'


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_new_confirmed_pivot_after_exit_can_use_one_candle(side):
    from test_channel_ma3_primary_entry import market
    f = market(side)
    price = float(f.iloc[-1]['close'])
    exit_info = dict(side=side, exit_bar_id=float(f.iloc[-3]['timestamp']), require_new_closed_break=True)
    assert not TradingEngine._channel_peak_exit_reentry_blocked('ENTER', False, side, f, exit_info, SYMBOL, live_price=price)
    exit_info['exit_bar_id'] = float(f.iloc[-2]['timestamp'])
    assert TradingEngine._channel_peak_exit_reentry_blocked('ENTER', False, side, f, exit_info, SYMBOL, live_price=price)


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_two_closed_breakout_retains_rejection_wick_guard(side):
    f = breakout_market(side)
    f.loc[3, 'high' if side == 'LONG' else 'low'] = 104. if side == 'LONG' else 96.
    assert aligned_entry(f, float(f.iloc[-1]['close']))['reason'] == 'KC_BREAKOUT_REJECTION_WAIT'
