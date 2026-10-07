import asyncio
import copy
import json
import math
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from core.services.exits.entry_atr_protection import enforce_atr_protection
from core.services.exits.peak_trailing_exit import STATE_KEY, evaluate_peak_trailing, migrate_peak_state
from core.services.exits.realtime_profit_exit import enforce_realtime_profit_exit
from core.services.exits.structural_holding_exit import HARD, POLICY, WATERFALL
from core.services.exits.trade_pressure_exit import (
    REASON, TradePressureFeed, confirmed_trade_pressure,
)

SYMBOLS = ['龙虾/USDT', 'CAP/USDT']
SIDES = ['LONG', 'SHORT']


def position(side='LONG', symbol='龙虾/USDT'):
    return dict(side=side, symbol=symbol, entry_price=100., qty=1., entry_atr=9.,
                entry_mode='CHANNEL_SWING', open_timestamp=290., margin=100.,
                leverage=1., initial_sl=90. if side == 'LONG' else 110.)


def snapshot(stamp=310000.):
    return dict(quote_ms=stamp, live_bar_ms=300000., closed_bar_ms=240000.,
                reason=None, live_open=100., atr=1.)


def trade(symbol, stamp, ident, side, price=100., amount=1.):
    return dict(symbol=symbol+':USDT', timestamp=stamp, id=str(ident),
                side=side, price=price, amount=amount,
                info=dict(e='aggTrade', a=ident, T=stamp, m=side == 'sell',
                          p=str(price), q=str(amount)))


def populate(feed, p, count=11, adverse_count=8, start=300000., price=100.):
    adverse = 'sell' if p['side'] == 'LONG' else 'buy'
    favorable = 'buy' if adverse == 'sell' else 'sell'
    for index in range(count):
        stamp = start+index*1000
        feed.observe(p['symbol'], p, trade(p['symbol'], stamp, index+1,
                     adverse if index < adverse_count else favorable, price), stamp)


def observe(feed, p, s):
    state = migrate_peak_state(p)
    s['trade_pressure'] = feed.evidence(p['symbol'], p, state, s, s['quote_ms'])
    return state


@pytest.mark.parametrize('symbol', SYMBOLS)
@pytest.mark.parametrize('side', SIDES)
def test_four_paths_confirm_exact_price_boundary_without_ma_or_pivot(symbol, side):
    p = position(side, symbol)
    feed = TradePressureFeed(Mock())
    populate(feed, p)
    s = snapshot()
    state = observe(feed, p, s)
    price = 99.85 if side == 'LONG' else 100.15
    evidence = confirmed_trade_pressure(p, state, s, price)
    assert evidence
    assert evaluate_peak_trailing(p, price, s) is None
    assert evidence['count'] == 11
    assert evidence['atr'] == 1.  # Not the entry ATR of 9.
    assert evidence['extreme'] == 100.
    assert evidence['reversal'] == '0.15'
    assert not state.get('armed')


@pytest.mark.parametrize('side', SIDES)
@pytest.mark.parametrize('ratio,passes', [('0.699999', False), ('0.70', True), ('0.700001', True)])
def test_notional_imbalance_boundaries(side, ratio, passes):
    p = position(side)
    feed = TradePressureFeed(Mock())
    populate(feed, p)
    s = snapshot()
    state = observe(feed, p, s)
    s['trade_pressure'].update(buy_notional=str(1-float(ratio)) if side == 'LONG' else ratio,
                              sell_notional=ratio if side == 'LONG' else str(1-float(ratio)))
    # Use exact complementary decimal strings at the inclusive boundary.
    if ratio == '0.70':
        s['trade_pressure'].update(buy_notional='0.30' if side == 'LONG' else '0.70',
                                  sell_notional='0.70' if side == 'LONG' else '0.30')
    result = confirmed_trade_pressure(p, state, s, 99.85 if side == 'LONG' else 100.15)
    assert bool(result) is passes


@pytest.mark.parametrize('side', SIDES)
@pytest.mark.parametrize('offset,passes', [(0.149999, False), (0.15, True), (0.150001, True)])
def test_reversal_boundaries(side, offset, passes):
    p = position(side)
    feed = TradePressureFeed(Mock())
    populate(feed, p)
    s = snapshot()
    observe(feed, p, s)
    price = 100-offset if side == 'LONG' else 100+offset
    assert bool(confirmed_trade_pressure(p, p[STATE_KEY], s, price)) is passes


@pytest.mark.parametrize('symbol', SYMBOLS)
@pytest.mark.parametrize('side', SIDES)
def test_warmup_sample_count_and_duplicate_cannot_authorize(symbol, side):
    p = position(side, symbol)
    feed = TradePressureFeed(Mock())
    populate(feed, p, count=10, adverse_count=10)
    s = snapshot(309000.)
    observe(feed, p, s)
    assert evaluate_peak_trailing(p, 99.8 if side == 'LONG' else 100.2, s) is None
    t = trade(symbol, 309000., 10, 'sell' if side == 'LONG' else 'buy')
    for _ in range(30):
        feed.observe(symbol, p, t, 310000.)
    s = snapshot()
    observe(feed, p, s)
    assert s['trade_pressure']['count'] == 10
    assert confirmed_trade_pressure(p, p[STATE_KEY], s, 99.8 if side == 'LONG' else 100.2)
    s['trade_pressure']['count'] = 9
    assert evaluate_peak_trailing(p, 99.8 if side == 'LONG' else 100.2, s) is None


@pytest.mark.parametrize('fault', ['missing_maker', 'wrong_side', 'nan', 'zero_amount',
                                  'bad_id', 'stale', 'future', 'preentry', 'id_gap',
                                  'out_of_order', 'stream_error'])
def test_bad_data_and_gaps_suspend_window(fault):
    p = position()
    feed = TradePressureFeed(Mock())
    populate(feed, p)
    t = trade(p['symbol'], 311000., 12, 'sell')
    now = 311000.
    if fault == 'missing_maker': t['info'].pop('m')
    elif fault == 'wrong_side': t['side'] = 'buy'
    elif fault == 'nan': t['price'] = float('nan')
    elif fault == 'zero_amount': t['amount'] = 0.
    elif fault == 'bad_id': t['id'] = '999'
    elif fault == 'stale': now += 5001.
    elif fault == 'future': now -= 1.
    elif fault == 'preentry':
        t['timestamp'] = t['info']['T'] = 289000.
        now = 289000.
    elif fault == 'id_gap':
        t['id'] = '13'
        t['info']['a'] = 13
    elif fault == 'out_of_order':
        t['id'] = '9'
        t['info']['a'] = 9
    if fault == 'stream_error':
        feed.suspend_all('STREAM_ERROR')
    else:
        feed.observe(p['symbol'], p, t, now)
    s = snapshot(now)
    state = observe(feed, p, s)
    assert confirmed_trade_pressure(p, state, s, 99.8) is None
    feed.log.assert_called()


@pytest.mark.parametrize('symbol', SYMBOLS)
@pytest.mark.parametrize('side', SIDES)
def test_retired_pressure_pending_does_not_survive_and_window_does_not(symbol, side):
    p = position(side, symbol)
    feed = TradePressureFeed(Mock())
    populate(feed, p)
    s = snapshot()
    observe(feed, p, s)
    evidence = confirmed_trade_pressure(p, p[STATE_KEY], s, 99.85 if side == 'LONG' else 100.15)
    p[STATE_KEY].update(pending=REASON, trigger=REASON,
                        holding_exit_policy='aggressive_trade_pressure_v5', trade_pressure_exit=evidence)
    p = json.loads(json.dumps(p))
    state = migrate_peak_state(p)
    new_feed = TradePressureFeed(Mock())
    assert new_feed.evidence(symbol, p, state, snapshot(), 310000.) is None
    assert evaluate_peak_trailing(p, 100., {'quote_ms': 311000., 'reason': 'NO_DATA'}) is None
    account = SimpleNamespace(positions={symbol: p}, position_meta={}, save_state=Mock(),
                              log=Mock(), close_position=AsyncMock(return_value=False))
    assert not asyncio.run(enforce_atr_protection(account, symbol, 100.))
    assert not account.position_meta[symbol][STATE_KEY].get('pending')
    account.close_position.return_value = True
    assert not asyncio.run(enforce_atr_protection(account, symbol, 100.))
    account.close_position.assert_not_awaited()


@pytest.mark.parametrize('side', SIDES)
def test_retired_pressure_observations_are_not_migrated_into_new_exit(side):
    p = position(side)
    feed = TradePressureFeed(Mock())
    populate(feed, p)
    observe(feed, p, snapshot())
    restarted = json.loads(json.dumps(p))
    restarted['entry_price'] = p['entry_price']
    feed = TradePressureFeed(Mock())
    populate(feed, restarted, start=311000., price=99.9 if side == 'LONG' else 100.1)
    s = snapshot(321000.)
    s['atr'] = 50.
    observe(feed, restarted, s)
    assert s['trade_pressure']['atr'] == 50.
    assert s['trade_pressure']['extreme'] == (99.9 if side == 'LONG' else 100.1)
    assert not confirmed_trade_pressure(restarted, restarted[STATE_KEY], s, 99.85 if side == 'LONG' else 100.15)
    assert evaluate_peak_trailing(restarted, 99.85 if side == 'LONG' else 100.15, s) is None


@pytest.mark.parametrize('side', SIDES)
def test_replacement_and_symbol_isolation(side):
    p = position(side)
    feed = TradePressureFeed(Mock())
    populate(feed, p)
    s = snapshot()
    observe(feed, p, s)
    cap = position(side, 'CAP/USDT')
    assert feed.evidence('CAP/USDT', cap, migrate_peak_state(cap), s, 310000.) is None
    replacement = dict(p, open_timestamp=310.)
    assert feed.evidence(p['symbol'], replacement, migrate_peak_state(replacement), s, 310000.) is None
    assert not replacement[STATE_KEY].get('trade_pressure_observation')


@pytest.mark.parametrize('reason', ['EXIT_CLOSED_PRICE_PIVOT', 'EXIT_CLOSED_PRICE_PIVOT_MA5_REVERSE',
                                    'EXIT_CLOSED_MA5_OUTER_PIVOT'])
def test_old_pivot_pending_retired_in_both_stores(reason):
    p = position()
    state = migrate_peak_state(p)
    state.update(pending=reason, trigger=reason, holding_exit_policy='closed_price_pivot_only_v4',
                 ma5_outer_pivot=dict(rule_version=3))
    meta = copy.deepcopy(p)
    migrate_peak_state(p, meta)
    assert not p[STATE_KEY].get('pending')
    assert not meta[STATE_KEY].get('pending')


@pytest.mark.parametrize('side', SIDES)
@pytest.mark.parametrize('risk', ['hard', 'waterfall'])
def test_risk_priority_is_unchanged(side, risk):
    p = position(side)
    feed = TradePressureFeed(Mock())
    populate(feed, p)
    s = snapshot()
    observe(feed, p, s)
    price = 99.85 if side == 'LONG' else 100.15
    if risk == 'hard':
        p['margin'] = 1.
    else:
        s['live_open'] = 102. if side == 'LONG' else 98.
    assert evaluate_peak_trailing(p, price, s)['reason'] == (HARD if risk == 'hard' else WATERFALL)


@pytest.mark.parametrize('symbol', SYMBOLS)
@pytest.mark.parametrize('side', SIDES)
def test_retired_pressure_evidence_cannot_close_realtime(symbol, side, monkeypatch):
    p = position(side, symbol)
    account = SimpleNamespace(positions={symbol: p}, position_meta={}, save_state=Mock(),
                              log=Mock(), close_position=AsyncMock(return_value=False))
    feed = TradePressureFeed(account.log)
    populate(feed, p)
    engine = SimpleNamespace(account=account, is_running=True, _channel_exit_frames={},
                             _trade_pressure_feed=feed)
    s = snapshot()
    monkeypatch.setattr('time.time', lambda: 310.)
    monkeypatch.setattr('core.services.exits.realtime_profit_exit.cached_tick_indicators', lambda *a: (s.copy(), 1.))
    monkeypatch.setattr('core.services.exits.trend_hold_evaluator.evaluate_trend_hold',
                        lambda *a, **k: ('HOLD', 'TEST'))
    price = 99.85 if side == 'LONG' else 100.15
    assert not asyncio.run(enforce_realtime_profit_exit(engine, symbol, price, 310000.))
    account.close_position.assert_not_awaited()
    assert 'exit_protection_snapshot' not in account.position_meta[symbol]
    feed.suspend_all('STREAM_ERROR')
    account.close_position.return_value = True
    assert not asyncio.run(enforce_realtime_profit_exit(engine, symbol, price, 310000.))


def test_stale_evidence_and_small_ma_bend_do_not_create_pressure_exit():
    p = position()
    feed = TradePressureFeed(Mock())
    populate(feed, p)
    s = snapshot(316000.)
    observe(feed, p, s)
    assert s['trade_pressure'] is None
    assert evaluate_peak_trailing(p, 99.8, s) is None
    s = snapshot()
    s.update(ma5=99., last_ma5=100., pivot_exit_history=[
        dict(timestamp=120000., open=100., high=101., low=99., close=100.),
        dict(timestamp=180000., open=100., high=102., low=99., close=100.),
        dict(timestamp=240000., open=100., high=101., low=99., close=100.)])
    assert evaluate_peak_trailing(position(), 99.8, s) is None


@pytest.mark.parametrize('symbol', SYMBOLS)
@pytest.mark.parametrize('side', SIDES)
@pytest.mark.parametrize('mode', ['paper', 'testnet'])
def test_actual_account_pressure_feed_alone_no_longer_closes(symbol, side, mode, tmp_path, monkeypatch):
    async def run():
        import core.paper_account as pm
        import core.testnet_account as tm
        from test_testnet_account import FakeTestnetExchange
        from test_close_deduplication import close_orders, closes

        exchange = FakeTestnetExchange()
        exchange.amount_to_precision = lambda symbol, amount: str(round(float(amount), 3))
        exchange.market = lambda symbol: dict(linear=True, contractSize=1., info={'filters':[
            dict(filterType=name, stepSize='0.001', minQty='0.001', maxQty='1000000')
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
        assert await account.open_position(symbol, side, 100., 25., 0., 0., 'MANUAL', leverage=1, atr=1.,
                    entry_context={'entry_mode': 'CHANNEL_SWING', 'manual_entry': True}), account.logs[-3:]
        p = account.positions[symbol]
        p['symbol'] = symbol
        start = math.ceil(p['open_timestamp']*1000)+1.
        feed = TradePressureFeed(account.log)
        populate(feed, p, start=start)
        stamp = start+10000.
        s = snapshot(stamp)
        bar = math.floor(stamp/60000)*60000
        s.update(live_bar_ms=bar, closed_bar_ms=bar-60000)
        monkeypatch.setattr('time.time', lambda: stamp/1000)
        monkeypatch.setattr('core.services.exits.realtime_profit_exit.cached_tick_indicators',
                            lambda *a: (s.copy(), 1.))
        engine = SimpleNamespace(account=account, is_running=True, _channel_exit_frames={},
                                 _trade_pressure_feed=feed)
        price = 99.85 if side == 'LONG' else 100.15
        await asyncio.gather(*(enforce_realtime_profit_exit(engine, symbol, price, stamp) for _ in range(10)))
        assert symbol in account.positions
        assert not closes(account)
        if mode == 'testnet':
            assert not close_orders(exchange)
    asyncio.run(run())


def test_engine_no_longer_accumulates_pressure_authority(monkeypatch):
    from core.engine import TradingEngine

    async def run():
        p = position(symbol='CAP/USDT')
        batch = [trade('CAP/USDT', 300000.+i*100, i+1, 'buy') for i in range(11)]
        engine = object.__new__(TradingEngine)
        engine.is_running = True
        engine.account = SimpleNamespace(positions={'CAP/USDT': p}, log=Mock())
        calls = 0

        async def watch(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                engine.is_running = False
            return batch

        async def exit_tick(symbol, price, stamp):
            return False

        engine.ws_exchange = SimpleNamespace(watch_trades_for_symbols=watch)
        engine._instant_quote_exit = exit_tick
        monkeypatch.setattr('time.time', lambda: 301.)
        await engine._instant_exit_trade_loop()
        assert not hasattr(engine, '_trade_pressure_feed')
        assert calls == 2
    asyncio.run(run())


@pytest.mark.parametrize('symbol', SYMBOLS)
@pytest.mark.parametrize('side', SIDES)
def test_notional_not_trade_count_and_window_rolloff(symbol, side):
    p = position(side, symbol)
    feed = TradePressureFeed(Mock())
    adverse = 'sell' if side == 'LONG' else 'buy'
    favorable = 'buy' if side == 'LONG' else 'sell'
    for index in range(11):
        stamp = 300000.+index*1000
        feed.observe(symbol, p, trade(symbol, stamp, index+1, adverse if index == 0 else favorable,
                                      amount=100. if index == 0 else 1.), stamp)
    s = snapshot()
    state = observe(feed, p, s)
    price = 99.85 if side == 'LONG' else 100.15
    assert confirmed_trade_pressure(p, state, s, price)
    feed.observe(symbol, p, trade(symbol, 311000., 12, favorable), 311000.)
    s = snapshot(311000.)
    observe(feed, p, s)
    assert s['trade_pressure']['count'] == 11
    assert not confirmed_trade_pressure(p, state, s, price)
    assert state['trade_pressure_status'] == 'WAIT_ADVERSE_IMBALANCE'


def test_received_time_not_backdated_trade_time_controls_warmup():
    p = position()
    feed = TradePressureFeed(Mock())
    for index in range(11):
        stamp = 300000.+index*500
        feed.observe(p['symbol'], p, trade(p['symbol'], stamp, index+1, 'sell'), stamp+4000.)
    s = snapshot(310000.)
    observe(feed, p, s)
    assert s['trade_pressure']['coverage_ms'] == 6000.
    assert evaluate_peak_trailing(p, 99.8, s) is None


@pytest.mark.parametrize('scale', [0., -1., float('nan'), float('inf'), None])
def test_invalid_atr_does_not_freeze_entry_atr_as_fallback(scale):
    p = position()
    feed = TradePressureFeed(Mock())
    populate(feed, p)
    s = snapshot()
    s['atr'] = scale
    observe(feed, p, s)
    assert s['trade_pressure'] is None
    assert evaluate_peak_trailing(p, 99.8, s) is None
    assert 'trade_pressure_observation' not in p[STATE_KEY]
