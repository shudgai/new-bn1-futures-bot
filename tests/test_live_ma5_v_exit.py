import asyncio
import copy
import json
import math
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pandas as pd
import pytest

from core.services.exits.peak_trailing_exit import STATE_KEY, evaluate_peak_trailing, migrate_peak_state
from core.services.exits.live_ma5_v_exit import REASON, observe_live_ma5_v
from core.services.exits.realtime_profit_exit import cached_tick_indicators, enforce_realtime_profit_exit
from core.services.exits.entry_atr_protection import enforce_atr_protection
from core.services.exits.structural_holding_exit import HARD, WATERFALL, POLICY

SYMBOLS = ['龙虾/USDT', 'CAP/USDT']
SIDES = ['LONG', 'SHORT']


def position(side='LONG', symbol='龙虾/USDT'):
    return dict(side=side, symbol=symbol, entry_price=100., qty=1.,
                open_timestamp=100., entry_atr=9., margin=100., leverage=1.,
                entry_mode='CHANNEL_SWING', initial_sl=90. if side == 'LONG' else 110.)


def snapshot(ma5=100., stamp=301000., atr=1.):
    bar = math.floor(stamp/60000)*60000
    sign = 1 if ma5 >= 100 else -1
    history = [dict(timestamp=bar-(3-i)*60000, open=100., close=100.,
                    high=101.+(1 if i == 1 and sign == 1 else 0.),
                    low=99.-(1 if i == 1 and sign == -1 else 0.),
                    ma5=100.+sign*(.2 if i == 1 else .1))
               for i in range(3)]
    return dict(quote_ms=stamp, live_bar_ms=bar, closed_bar_ms=bar-60000,
                atr=atr, ma5=ma5, live_ma5_verified=True, live_open=100., reason=None,
                kc_lower=99.8, kc_upper=100.2, ma5_pivot_history=history,
                kc_closed_history=[dict(timestamp=bar-120000., middle=100.+sign*.1),
                                   dict(timestamp=bar-60000., middle=100.)])


def progress(p):
    sign = 1 if p['side'] == 'LONG' else -1
    state = migrate_peak_state(p)
    assert observe_live_ma5_v(p, state, snapshot(), 100.) is None
    assert observe_live_ma5_v(p, state, snapshot(100+sign*.2, 302000.), 100+sign*.5) is None
    return sign


def observe_retired_v(p, price, data):
    state = p.get(STATE_KEY)
    if state is None:
        state = migrate_peak_state(p)
    return observe_live_ma5_v(p, state, data, price)


@pytest.mark.parametrize('symbol', SYMBOLS)
@pytest.mark.parametrize('side', SIDES)
def test_retired_observer_threshold_is_not_production_close_authority(symbol, side):
    p = position(side, symbol)
    sign = progress(p)
    evidence = observe_retired_v(p, 100+sign*.35, snapshot(100+sign*.1, 303000.))
    assert evidence['atr'] == 1.
    assert evidence['ma5_reversal'] == '0.1'
    assert evidence['price_reversal'] == '0.15'
    assert evaluate_peak_trailing(p, 100+sign*.35, snapshot(100+sign*.1, 303000.)) is None
    assert p[STATE_KEY]['holding_exit_policy'] == POLICY
    assert not p[STATE_KEY]['armed']


@pytest.mark.parametrize('side', SIDES)
@pytest.mark.parametrize('ma_retreat,price_retreat,allowed', [
    (.099999, .15, False), (.1, .149999, False), (.1, .15, True),
    (.100001, .150001, True), (.2, .01, False), (.01, .2, False)])
def test_independent_inclusive_amplitudes(side, ma_retreat, price_retreat, allowed):
    p = position(side)
    sign = progress(p)
    s = snapshot(100+sign*(.2-ma_retreat), 303000.)
    assert bool(observe_retired_v(p, 100+sign*(.5-price_retreat), s)) is allowed


@pytest.mark.parametrize('side', SIDES)
def test_no_preentry_turn_or_immediate_reverse_without_favorable_observation(side):
    p = position(side)
    sign = 1 if side == 'LONG' else -1
    assert observe_retired_v(p, 100., snapshot()) is None
    assert observe_retired_v(p, 100-sign*.5, snapshot(100-sign*.2, 302000.)) is None
    assert p[STATE_KEY]['live_ma5_v_status'] == 'WAIT_POST_ENTRY_MA5_PROGRESSION'


@pytest.mark.parametrize('side', SIDES)
def test_flat_and_favorable_latest_slope_cannot_close_despite_old_retreat(side):
    p = position(side)
    sign = progress(p)
    # MA retreats but price has not yet confirmed.
    assert observe_retired_v(p, 100+sign*.49, snapshot(100+sign*.05, 303000.)) is None
    assert observe_retired_v(p, 100+sign*.3, snapshot(100+sign*.05, 304000.)) is None
    assert p[STATE_KEY]['live_ma5_v_status'] == 'WAIT_ACTUAL_MA5_REVERSE_SLOPE'
    assert observe_retired_v(p, 100+sign*.3, snapshot(100+sign*.06, 305000.)) is None


@pytest.mark.parametrize('fault', ['no_data', 'fallback', 'closed_ma5', 'stale_bar', 'nan',
                                  'invalid_atr', 'preentry', 'same_stamp', 'older_stamp'])
def test_invalid_or_unobserved_market_data_cannot_close(fault):
    p = position()
    progress(p)
    s = snapshot(100.1, 303000.)
    if fault == 'no_data': s['reason'] = 'NO_DATA'
    elif fault == 'fallback': s['fallback_used'] = True
    elif fault == 'closed_ma5': s['live_ma5_verified'] = False
    elif fault == 'stale_bar': s['closed_bar_ms'] = 180000.
    elif fault == 'nan': s['ma5'] = float('nan')
    elif fault == 'invalid_atr': s['atr'] = 0.
    elif fault == 'preentry': s['quote_ms'] = 99000.
    elif fault == 'same_stamp': s['quote_ms'] = 302000.
    else: s['quote_ms'] = 301999.
    assert observe_retired_v(p, 100.35, s) is None


@pytest.mark.parametrize('side', SIDES)
def test_gap_and_restart_keep_fixed_atr_but_require_fresh_progression(side, monkeypatch):
    import core.services.exits.live_ma5_v_exit as module
    p = position(side)
    sign = progress(p)
    p = json.loads(json.dumps(p))
    monkeypatch.setattr(module, 'SESSION', 'new-process')
    assert observe_retired_v(p, 100+sign*.35, snapshot(100+sign*.1, 303000., 10.)) is None
    assert p[STATE_KEY]['live_ma5_v_observation']['atr'] == 1.
    assert not p[STATE_KEY]['live_ma5_v_observation']['favorable']
    assert observe_retired_v(p, 100+sign*.7, snapshot(100+sign*.3, 304000., 10.)) is None
    assert observe_retired_v(p, 100+sign*.55, snapshot(100+sign*.2, 305000., 10.))
    p = position(side)
    progress(p)
    assert observe_retired_v(p, 100+sign*.35, snapshot(100+sign*.1, 308001.)) is None
    assert not p[STATE_KEY]['live_ma5_v_observation']['favorable']


@pytest.mark.parametrize('symbol', SYMBOLS)
@pytest.mark.parametrize('side', SIDES)
def test_retired_confirmed_retry_never_closes_through_account_adapter(symbol, side):
    p = position(side, symbol)
    sign = progress(p)
    evidence = observe_retired_v(p, 100+sign*.35, snapshot(100+sign*.1, 303000.))
    p[STATE_KEY].update(pending='EXIT_LIVE_MA5_V_REVERSAL', trigger=REASON, live_ma5_v_exit=evidence,
                       holding_exit_policy='live_ma5_v_reversal_v6')
    p = json.loads(json.dumps(p))
    account = SimpleNamespace(positions={symbol: p}, position_meta={}, save_state=Mock(),
                              log=Mock(), close_position=AsyncMock(return_value=False))
    assert not asyncio.run(enforce_atr_protection(account, symbol, 100.))
    assert not account.position_meta[symbol][STATE_KEY].get('pending')
    account.close_position.return_value = True
    assert not asyncio.run(enforce_atr_protection(account, symbol, 100.))
    account.close_position.assert_not_awaited()


@pytest.mark.parametrize('side', SIDES)
def test_position_replacement_never_inherits_observation_or_pending(side):
    p = position(side)
    sign = progress(p)
    evaluate_peak_trailing(p, 100+sign*.35, snapshot(100+sign*.1, 303000.))
    p['open_timestamp'] = 303.
    assert evaluate_peak_trailing(p, 100., snapshot(100., 304000.)) is None
    assert not p[STATE_KEY].get('pending')
    assert not p[STATE_KEY]['live_ma5_v_observation']['favorable']


@pytest.mark.parametrize('side', SIDES)
@pytest.mark.parametrize('risk', ['hard', 'waterfall'])
def test_risk_priority_preserved(side, risk):
    p = position(side)
    sign = progress(p)
    s = snapshot(100+sign*.1, 303000.)
    if risk == 'hard': p['margin'] = 1.
    else: s['live_open'] = 102. if side == 'LONG' else 98.
    result = evaluate_peak_trailing(p, 100+sign*.35 if risk != 'hard' else 100-sign*.1, s)
    assert result['reason'] == (HARD if risk == 'hard' else WATERFALL)


@pytest.mark.parametrize('legacy', ['EXIT_AGGRESSIVE_TRADE_PRESSURE', 'EXIT_CLOSED_PRICE_PIVOT', 'EXIT_LIVE_MA5_V_REVERSAL'])
def test_replaced_exit_authority_retired_in_both_stores(legacy):
    p = position()
    state = migrate_peak_state(p)
    state.update(pending=legacy, trigger=legacy, holding_exit_policy='aggressive_trade_pressure_v5',
                 trade_pressure_exit=dict(rule_version=1), trade_pressure_observation=dict(atr=9.))
    meta = copy.deepcopy(p)
    migrate_peak_state(p, meta)
    for source in (p, meta):
        assert not source[STATE_KEY].get('pending')
        assert 'trade_pressure_observation' not in source[STATE_KEY]
        assert 'trade_pressure_exit' not in source[STATE_KEY]


@pytest.mark.parametrize('symbol', SYMBOLS)
@pytest.mark.parametrize('side', SIDES)
def test_realtime_ma5_reverse_keeps_position_without_retry(symbol, side, monkeypatch):
    p = position(side, symbol)
    sign = progress(p)
    p[STATE_KEY]['live_ma5_v_observation']['outer_extreme'] = None
    account = SimpleNamespace(positions={symbol: p}, position_meta={}, save_state=Mock(),
                              log=Mock(), close_position=AsyncMock(return_value=False))
    engine = SimpleNamespace(account=account, is_running=True, _channel_exit_frames={})
    monkeypatch.setattr('time.time', lambda: 303.)
    monkeypatch.setattr('core.services.exits.realtime_profit_exit.cached_tick_indicators',
                        lambda *a: (snapshot(100+sign*.1, 303000.), 1.))
    monkeypatch.setattr('core.services.exits.trend_hold_evaluator.evaluate_trend_hold',
                        lambda *a, **k: ('HOLD', 'TEST'))
    assert not asyncio.run(enforce_realtime_profit_exit(engine, symbol, 100+sign*.35, 303000.))
    account.close_position.assert_not_awaited()
    assert 'exit_protection_snapshot' not in account.position_meta[symbol]
    account.close_position.return_value = True
    assert not asyncio.run(enforce_realtime_profit_exit(engine, symbol, 100+sign*.35, 303000.))
    account.close_position.assert_not_awaited()


def test_cached_ma5_uses_latest_quote_and_filtered_basis_not_live_indicator():
    rows = [dict(timestamp=(i+1)*60000., open=100., close=100., high=101., low=99.,
                 ma5=100., kc_middle=100., atr=1., close_price_spike_filtered=99.,
                 is_closed=True) for i in range(5)]
    rows.append(dict(rows[-1], timestamp=360000., is_closed=False, ma5=999.))
    f = pd.DataFrame(rows)
    s, _ = cached_tick_indicators(f, 101., 361000.)
    assert s['live_ma5_verified'] is True
    assert s['ma5'] == (99.*4+101.)/5


@pytest.mark.parametrize('key,value', [('atr', -1.), ('atr', float('nan')),
                                    ('ma5_extreme', float('inf')), ('price_extreme', 0.)])
def test_invalid_persisted_observation_never_authorizes_close(key, value):
    p = position()
    progress(p)
    p[STATE_KEY]['live_ma5_v_observation'][key] = value
    assert observe_retired_v(p, 100.35, snapshot(100.1, 303000.)) is None
    assert p[STATE_KEY]['live_ma5_v_status'] == 'BLOCKED_LIVE_MA5_V_STATE'


@pytest.mark.parametrize('symbol', SYMBOLS)
@pytest.mark.parametrize('side', SIDES)
@pytest.mark.parametrize('mode', ['paper', 'testnet'])
@pytest.mark.parametrize('outer', [False, True])
def test_actual_account_concurrent_v_quotes_require_outer_extreme(symbol, side, mode, outer, tmp_path, monkeypatch):
    async def run():
        import core.paper_account as pm
        import core.testnet_account as tm
        from test_testnet_account import FakeTestnetExchange
        from test_close_deduplication import close_orders, closes
        from decimal import Decimal, ROUND_DOWN
        exchange = FakeTestnetExchange()
        exchange.amount_to_precision = lambda symbol, amount: str(
            Decimal(str(amount)).quantize(Decimal('.001'), rounding=ROUND_DOWN))
        exchange.market = lambda symbol: dict(linear=True, contractSize=1., info={'filters': [
            dict(filterType=name, stepSize='.001', minQty='.001', maxQty='1000000')
            for name in ('LOT_SIZE', 'MARKET_LOT_SIZE')]})
        if mode == 'paper':
            monkeypatch.setattr(pm, 'STATE_FILE', str(tmp_path/'paper.json'))
            account = pm.PaperAccount()
        else:
            monkeypatch.setattr(tm, 'STATE_FILE', str(tmp_path/'testnet.json'))
            monkeypatch.setattr(tm, 'DATA_DIR', str(tmp_path))
            monkeypatch.setattr(tm, 'notify_email', lambda *a, **k: None)
            monkeypatch.setattr(tm.BinanceTestnetAccount, 'credentials_configured', staticmethod(lambda: True))
            account = tm.BinanceTestnetAccount(exchange)
            await account.initialize()
        assert await account.open_position(symbol, side, 100., 25., 0., 0., 'MANUAL', leverage=1,
                    atr=1., entry_context={'entry_mode': 'CHANNEL_SWING', 'manual_entry': True})
        p = account.positions[symbol]
        sign = 1 if side == 'LONG' else -1
        start = (math.floor(p['open_timestamp']/60)+4)*60000+1000.
        bar = math.floor(start/60000)*60000
        def snap(ma5, stamp):
            edge = .2 if outer else 1.
            return dict(snapshot(ma5, stamp), live_bar_ms=bar, closed_bar_ms=bar-60000,
                        kc_lower=100-edge, kc_upper=100+edge)
        assert evaluate_peak_trailing(p, 100., snap(100., start)) is None
        assert evaluate_peak_trailing(p, 100+sign*.5, snap(100+sign*.2, start+1000)) is None
        stamp = start+2000
        monkeypatch.setattr('time.time', lambda: stamp/1000)
        monkeypatch.setattr('core.services.exits.realtime_profit_exit.cached_tick_indicators',
                            lambda *a: (snap(100+sign*.1, stamp), 1.))
        engine = SimpleNamespace(account=account, is_running=True, _channel_exit_frames={})
        await asyncio.gather(*(enforce_realtime_profit_exit(engine, symbol, 100+sign*.35, stamp)
                               for _ in range(10)))
        assert (symbol not in account.positions) is outer
        assert len(closes(account)) == int(outer)
        if mode == 'testnet':
            assert len(close_orders(exchange)) == int(outer)
            if outer:
                assert close_orders(exchange)[0]['params']['reduceOnly']
    asyncio.run(run())


@pytest.mark.parametrize('symbol', SYMBOLS)
@pytest.mark.parametrize('side', SIDES)
def test_legacy_ma5_v_retry_is_retired_before_new_outer_observation(symbol, side):
    p = position(side, symbol)
    sign = progress(p)
    evidence = observe_retired_v(p, 100+sign*.35, snapshot(100+sign*.1, 303000.))
    p[STATE_KEY].update(pending='EXIT_LIVE_MA5_V_REVERSAL', trigger=REASON, live_ma5_v_exit=evidence,
                       holding_exit_policy='live_ma5_v_reversal_v6')
    p[STATE_KEY]['live_ma5_v_observation']['rule_version'] = 1
    meta = copy.deepcopy(p)
    migrate_peak_state(p, meta)
    for source in (p, meta):
        assert not source[STATE_KEY].get('pending')
        assert not any(key.startswith('live_ma5_v') for key in source[STATE_KEY])
    for bar in (300000., 360000., 420000.):
        s = dict(snapshot(100-sign*.5, bar+3000.), live_bar_ms=bar,
                 closed_bar_ms=bar-60000)
        assert evaluate_peak_trailing(p, 100-sign*.5, s) is None
        assert p[STATE_KEY]['holding_exit_policy'] == POLICY


@pytest.mark.parametrize('symbol', SYMBOLS)
@pytest.mark.parametrize('side', SIDES)
@pytest.mark.parametrize('location', ['inside', 'touch', 'outside'])
def test_outer_ma5_extreme_required_for_production_close(symbol, side, location):
    p = position(side, symbol)
    sign = 1 if side == 'LONG' else -1
    edge = .200001 if location == 'inside' else .2 if location == 'touch' else .199999
    def data(ma5, stamp):
        return dict(snapshot(ma5, stamp), kc_lower=100-edge, kc_upper=100+edge)
    assert evaluate_peak_trailing(p, 100., data(100., 301000.)) is None
    assert evaluate_peak_trailing(p, 100+sign*.5, data(100+sign*.2, 302000.)) is None
    result = evaluate_peak_trailing(p, 100+sign*.35, data(100+sign*.1, 303000.))
    assert bool(result) is (location != 'inside')
    if result:
        assert result['reason'] == REASON
        evidence = p[STATE_KEY]['live_ma5_v_exit']
        assert evidence['outer_extreme']['quote_ms'] == 302000.
        p = json.loads(json.dumps(p))
        account = SimpleNamespace(positions={symbol:p}, position_meta={},
                                  save_state=Mock(), close_position=AsyncMock(return_value=False))
        assert not asyncio.run(enforce_atr_protection(account, symbol, 100.))
        account.close_position.assert_awaited_once()
        assert p[STATE_KEY]['pending'] == REASON


@pytest.mark.parametrize('side', SIDES)
def test_moving_rail_cannot_retroactively_qualify_inner_extreme(side):
    p = position(side)
    sign = 1 if side == 'LONG' else -1
    assert evaluate_peak_trailing(p, 100., dict(snapshot(), kc_lower=99., kc_upper=101.)) is None
    assert evaluate_peak_trailing(p, 100+sign*.5,
                                 dict(snapshot(100+sign*.2, 302000.), kc_lower=99., kc_upper=101.)) is None
    assert evaluate_peak_trailing(p, 100+sign*.35,
                                 dict(snapshot(100+sign*.1, 303000.), kc_lower=99.95, kc_upper=100.05)) is None
    assert not p[STATE_KEY]['live_ma5_v_observation']['outer_extreme']


@pytest.mark.parametrize('side', SIDES)
def test_new_inner_extreme_replaces_old_outer_peak(side):
    p = position(side)
    sign = progress(p)
    assert p[STATE_KEY]['live_ma5_v_observation']['outer_extreme']
    assert evaluate_peak_trailing(p, 100+sign*.6,
                                 dict(snapshot(100+sign*.3, 303000.), kc_lower=99., kc_upper=101.)) is None
    assert evaluate_peak_trailing(p, 100+sign*.45,
                                 dict(snapshot(100+sign*.2, 304000.), kc_lower=99., kc_upper=101.)) is None
    assert not p[STATE_KEY]['live_ma5_v_observation']['outer_extreme']


@pytest.mark.parametrize('symbol', SYMBOLS)
@pytest.mark.parametrize('side', SIDES)
@pytest.mark.parametrize('fault', ['no_pivot', 'favorable_ma5', 'flat_ma5', 'missing',
                                  'unclosed', 'gap', 'invalid_ohlc', 'preentry', 'new_extreme'])
def test_live_rebound_never_replaces_closed_price_and_ma5_confirmation(symbol, side, fault):
    p = position(side, symbol)
    if fault == 'preentry':
        p['open_timestamp'] = 200.
    sign = progress(p)
    s = snapshot(100+sign*.1, 303000.)
    rows = s['ma5_pivot_history']
    if fault == 'no_pivot':
        key = 'high' if side == 'LONG' else 'low'
        rows[2][key] = rows[1][key]+sign*.1
    elif fault in ('favorable_ma5', 'flat_ma5'):
        rows[2]['ma5'] = rows[1]['ma5']+(sign*.1 if fault == 'favorable_ma5' else 0.)
    elif fault == 'missing':
        s.pop('ma5_pivot_history')
    elif fault == 'unclosed':
        rows[-1]['timestamp'] = s['live_bar_ms']
    elif fault == 'gap':
        rows[0]['timestamp'] -= 60000.
    elif fault == 'invalid_ohlc':
        rows[1]['low'] = 200.
    elif fault == 'new_extreme':
        key = 'high' if side == 'LONG' else 'low'
        for i, row in enumerate(rows):
            row[key] = 100+sign*(.3 if i == 1 else .2)
    assert evaluate_peak_trailing(p, 100+sign*.35, s) is None
    assert not p[STATE_KEY].get('pending')
    assert p[STATE_KEY]['live_ma5_v_status'] == {
        'no_pivot': 'WAIT_CLOSED_PRICE_PIVOT',
        'favorable_ma5': 'WAIT_CLOSED_MA5_REVERSE_SLOPE',
        'flat_ma5': 'WAIT_CLOSED_MA5_REVERSE_SLOPE',
        'missing': 'BLOCKED_CLOSED_PRICE_MA5_DATA',
        'unclosed': 'BLOCKED_CLOSED_PRICE_MA5_IDENTITY',
        'gap': 'BLOCKED_CLOSED_PRICE_MA5_IDENTITY',
        'invalid_ohlc': 'BLOCKED_CLOSED_PRICE_MA5_DATA',
        'preentry': 'WAIT_POST_ENTRY_CLOSED_PRICE_PIVOT',
        'new_extreme': 'WAIT_NEW_CLOSED_PRICE_PIVOT',
    }[fault]


@pytest.mark.parametrize('symbol', SYMBOLS)
@pytest.mark.parametrize('side', SIDES)
def test_old_outer_v_retry_without_closed_confirmation_is_revoked(symbol, side):
    p = position(side, symbol)
    sign = progress(p)
    evidence = observe_retired_v(p, 100+sign*.35, snapshot(100+sign*.1, 303000.))
    evidence['rule_version'] = 2
    evidence.pop('closed_confirmation')
    p[STATE_KEY].update(pending=REASON, trigger=REASON, live_ma5_v_exit=evidence)
    p[STATE_KEY]['live_ma5_v_observation']['rule_version'] = 2
    meta = copy.deepcopy(p)
    migrate_peak_state(p, meta)
    for source in (p, meta):
        assert not source[STATE_KEY].get('pending')
        assert 'live_ma5_v_exit' not in source[STATE_KEY]


@pytest.mark.parametrize('symbol', SYMBOLS)
@pytest.mark.parametrize('side', SIDES)
@pytest.mark.parametrize('kc', ['aligned', 'flat', 'opposite', 'missing', 'stale', 'nan'])
def test_closed_pivot_and_ma5_cannot_close_until_closed_kc_is_opposite(symbol, side, kc):
    p = position(side, symbol)
    sign = progress(p)
    s = snapshot(100+sign*.1, 303000.)
    if kc == 'aligned':
        s['kc_closed_history'][-1]['middle'] = 100+sign*.2
    elif kc == 'flat':
        s['kc_closed_history'][-1]['middle'] = 100+sign*.1
    elif kc == 'missing':
        s.pop('kc_closed_history')
    elif kc == 'stale':
        s['kc_closed_history'][-1]['timestamp'] -= 60000.
    elif kc == 'nan':
        s['kc_closed_history'][-1]['middle'] = float('nan')
    result = evaluate_peak_trailing(p, 100+sign*.35, s)
    assert bool(result) is (kc == 'opposite')
    if result:
        confirmation = p[STATE_KEY]['live_ma5_v_exit']['closed_confirmation']
        assert confirmation['kc_confirmation'] == 'CLOSED_OPPOSITE'
    else:
        assert not p[STATE_KEY].get('pending')


@pytest.mark.parametrize('symbol', SYMBOLS)
@pytest.mark.parametrize('side', SIDES)
def test_version_three_retry_without_kc_confirmation_is_revoked(symbol, side):
    p = position(side, symbol)
    sign = progress(p)
    evidence = observe_retired_v(p, 100+sign*.35, snapshot(100+sign*.1, 303000.))
    evidence['rule_version'] = 3
    evidence['closed_confirmation'].pop('kc_confirmation')
    p[STATE_KEY].update(pending=REASON, trigger=REASON, live_ma5_v_exit=evidence)
    meta = copy.deepcopy(p)
    migrate_peak_state(p, meta)
    assert not p[STATE_KEY].get('pending') and not meta[STATE_KEY].get('pending')
