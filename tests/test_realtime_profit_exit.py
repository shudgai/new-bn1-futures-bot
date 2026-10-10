import asyncio
import copy
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pandas as pd
import pytest

from core.engine import TradingEngine
from core.services.exits.realtime_profit_exit import cached_tick_indicators
from core.services.exits.peak_trailing_exit import (
    LIVE_FLASH_DUMP_TRIGGER,
    LIVE_MA5_BREAKDOWN_TRIGGER,
    NET_ROE_LOCK_TRIGGER,
    evaluate_peak_trailing,
)
from core.services.strategies.pure_trend_v2 import PureTrendStrategyV2
from core.services.symbol_runner import process_single_symbol_runner
from test_intraday_instant_exit import pos, observe

@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_unarmed_small_peak_does_not_exit(side):
    p = pos(side)
    p['entry_atr'] = 1.
    sign = 1 if side == 'LONG' else -1
    assert observe(p, 100 + sign * .4, 61000) is None
    assert observe(p, 100 + sign * .301, 61001) is None
    assert observe(p, 100 + sign * .299, 61002) is None
    assert p['peak_price'] == pytest.approx(100 + sign * .4)


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_retired_outer_ma3_cross_has_no_exit_authority(side):
    p = pos(side)
    p['entry_atr'] = 1.
    sign = 1 if side == 'LONG' else -1
    strategy = PureTrendStrategyV2()
    snap = dict(quote_ms=61000, live_ma3=100+sign*9.5,
                kc_upper=105., kc_lower=95.)
    assert strategy.check_intraday_instant_exit(p, 100+sign*10, snap, 10.) is None
    snap.update(quote_ms=61001, live_ma3=100+sign*9.9)
    assert strategy.check_intraday_instant_exit(p, 100+sign*9.8, snap, 10.) is None


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_no_outer_cross_without_observed_outer_tick(side):
    p = pos(side)
    p['entry_atr'] = 1.
    sign = 1 if side == 'LONG' else -1
    strategy = PureTrendStrategyV2()
    for price, stamp in [(100+sign*10, 61000), (100+sign*9.8, 61001)]:
        snap = dict(quote_ms=stamp, live_ma3=100+sign*9.9, kc_upper=120., kc_lower=80.)
        assert strategy.check_intraday_instant_exit(p, price, snap, 10.) is None


def engine_for(side):
    now = time.time()
    p = pos(side)
    p.update(open_timestamp=now-120, entry_atr=.5)
    account = SimpleNamespace(positions={'X':p}, position_meta={}, save_state=Mock(),
                              close_position=AsyncMock(return_value=False), log=Mock())
    engine = object.__new__(TradingEngine)
    engine.account = account
    engine.is_running = True
    engine._channel_exit_frames = {}
    engine.fetch_klines = AsyncMock(side_effect=AssertionError('Exit must not fetch'))
    return engine, p, now


def live_exit_frame(*, history_close, opening, high, low):
    bar_ms = int(time.time() // 60) * 60_000
    rows = [
        dict(
            timestamp=bar_ms - (5 - index) * 60_000,
            is_closed=True,
            open=history_close,
            high=history_close + 0.1,
            low=history_close - 0.1,
            close=history_close,
            atr=1.0,
            ma5=history_close,
            ma15=history_close,
            kc_upper=history_close + 1.0,
            kc_middle=history_close,
            kc_lower=history_close - 1.0,
        )
        for index in range(5)
    ]
    rows.append(dict(
        timestamp=bar_ms,
        is_closed=False,
        open=opening,
        high=high,
        low=low,
        close=opening,
        atr=1000.0,
        ma5=opening,
        ma15=history_close,
        kc_upper=history_close + 1.0,
        kc_middle=history_close,
        kc_lower=history_close - 1.0,
    ))
    frame = pd.DataFrame(rows)
    frame.attrs['timeframe_ms'] = 60_000
    return frame


@pytest.mark.parametrize(
    ('history_close', 'opening', 'high', 'low', 'price', 'expected_trigger'),
    [
        (100.0, 100.0, 100.1, 99.5, 99.5, LIVE_MA5_BREAKDOWN_TRIGGER),
        (98.0, 100.0, 101.0, 99.3, 99.3, LIVE_FLASH_DUMP_TRIGGER),
    ],
)
def test_live_long_sell_pressure_closes_inside_unclosed_bar(
    history_close, opening, high, low, price, expected_trigger, monkeypatch,
):
    monkeypatch.setattr(
        'core.services.exits.trend_hold_evaluator.evaluate_trend_hold',
        lambda *a, **k: ('RELEASED', 'TEST'),
    )
    monkeypatch.setattr('core.services.entry_contract.ck_direction', lambda _frame: None)

    async def run():
        engine, _, _ = engine_for('LONG')
        engine._channel_exit_frames = {
            'X': live_exit_frame(
                history_close=history_close, opening=opening, high=high, low=low,
            ),
        }

        assert await engine._instant_quote_exit('X', price, time.time() * 1000)
        engine.account.close_position.assert_awaited_once()
        assert expected_trigger in engine.account.close_position.await_args.args[2]

    asyncio.run(run())


def test_small_live_bearish_candle_does_not_trigger_intraday_sell_exit(monkeypatch):
    monkeypatch.setattr(
        'core.services.exits.trend_hold_evaluator.evaluate_trend_hold',
        lambda *a, **k: ('RELEASED', 'TEST'),
    )
    monkeypatch.setattr('core.services.entry_contract.ck_direction', lambda _frame: None)

    async def run():
        engine, _, _ = engine_for('LONG')
        engine._channel_exit_frames = {
            'X': live_exit_frame(
                history_close=100.0, opening=100.0, high=100.1, low=99.9,
            ),
        }

        assert not await engine._instant_quote_exit('X', 99.95, time.time() * 1000)
        engine.account.close_position.assert_not_awaited()

    asyncio.run(run())


def test_net_roe_lock_floor_overrides_kc_outer_and_lower_shadow_holds(monkeypatch):
    monkeypatch.setattr(
        'core.services.exits.trend_hold_evaluator.evaluate_trend_hold',
        lambda *a, **k: ('RELEASED', 'TEST'),
    )
    position = pos('LONG')
    stamp = int(time.time() // 60) * 60_000 + 1_000
    snapshot = {
        'quote_ms': stamp,
        'live_bar_id': stamp // 60_000 * 60_000,
        'live_bar_ms': stamp // 60_000 * 60_000,
        'closed_bar_ms': stamp // 60_000 * 60_000 - 60_000,
        'live_open': 103.6,
        'live_high': 107.0,
        'live_low': 99.0,
        'live_kc_middle': 100.0,
        'ma15': 100.0,
        'atr': 1.0,
        'history_5': [{
            'ms': stamp // 60_000 * 60_000 - 60_000,
            'o': 101.0, 'h': 103.0, 'l': 100.5, 'c': 102.0,
            'kc_upper': 101.0, 'kc_lower': 99.0, 'kc_middle': 100.0,
        }],
    }

    def quote_for_net_roe(net_roe_pct):
        fee, slippage = 0.0005, 0.0001
        target_pnl = net_roe_pct
        return (target_pnl + 100. + 100. * fee) / (1. - fee - slippage)

    evaluate_peak_trailing(
        position, quote_for_net_roe(5.5), snapshot, fee=0.0005, slippage=0.0001,
    )
    decision = evaluate_peak_trailing(
        position, quote_for_net_roe(3.5), snapshot, fee=0.0005, slippage=0.0001,
    )

    assert decision is not None
    assert decision['trigger'] == NET_ROE_LOCK_TRIGGER


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_net_roe_staged_giveback_closes_through_realtime_path(side, monkeypatch):
    monkeypatch.setattr(
        'core.services.exits.trend_hold_evaluator.evaluate_trend_hold',
        lambda *a, **k: ('RELEASED', 'TEST'),
    )

    def quote_for_net_roe(net_roe_pct):
        sign = 1 if side == 'LONG' else -1
        target_pnl = net_roe_pct * 100. / 100.
        fee, slippage = .0005, .0001
        return (target_pnl + sign * 100. + 100. * fee) / (sign - fee - slippage)

    async def run():
        e, p, now = engine_for(side)
        assert not await e._instant_quote_exit(
            'X', quote_for_net_roe(5.5), time.time() * 1000
        )
        assert p['peak_trailing_state']['net_roe_lock_floor_pct'] == pytest.approx(3.5)

        persisted_meta = copy.deepcopy(e.account.position_meta)
        replacement = dict(pos(side), open_timestamp=p['open_timestamp'])
        e.account.positions['X'] = replacement
        e.account.position_meta = persisted_meta

        assert await e._instant_quote_exit(
            'X', quote_for_net_roe(3.5), time.time() * 1000
        )
        e.account.close_position.assert_awaited_once()
        args = e.account.close_position.await_args.args
        assert 'NET_ROE_STAGED_GIVEBACK' in args[2]

    asyncio.run(run())


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_tiered_profit_pullback_requests_close_after_reload(side, monkeypatch):
    monkeypatch.setattr('core.services.exits.trend_hold_evaluator.evaluate_trend_hold', lambda *a, **k: ('RELEASED', 'TEST'))
    async def run():
        e, p, now = engine_for(side)
        sign = 1 if side == 'LONG' else -1
        lock = asyncio.Lock()
        await lock.acquire()
        e._channel_symbol_locks = {'X':lock}
        assert not await e._instant_quote_exit('X', 100+sign*6.0, now*1000)
        assert not await e._instant_quote_exit('X', 100., now*1000-1)
        persisted = copy.deepcopy(e.account.position_meta)
        e.account.positions['X'] = dict(pos(side), open_timestamp=p['open_timestamp'])
        e.account.position_meta = persisted
        assert await asyncio.wait_for(e._instant_quote_exit('X', 100+sign*1.0, now*1000), .5)
        e.account.close_position.assert_awaited_once()
        assert p.get('entry_mode') == 'CHANNEL_SWING'
        assert 'X' in e.account.positions
        e.fetch_klines.assert_not_called()
        lock.release()
    asyncio.run(run())


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_stale_quotes_and_replacement_position_do_not_inherit_peak(side):
    async def run():
        e, p, now = engine_for(side)
        sign = 1 if side == 'LONG' else -1
        assert not await e._instant_quote_exit('X', 100+sign, (now-10)*1000)
        assert 'peak_trailing_state' not in p
        assert not await e._instant_quote_exit('X', 100+sign, now*1000)
        e.account.positions['X'] = dict(pos(side), open_timestamp=now-1)
        assert not await e._instant_quote_exit('X', 100., now*1000)
        assert e.account.positions['X']['peak_pnl_usd'] == 0.
        e.account.close_position.assert_not_awaited()
    asyncio.run(run())


def test_only_atr_fallback_uses_confirmed_history_not_live_candle():
    f = pd.DataFrame([dict(timestamp=60000,is_closed=True,close=101.),
                      dict(timestamp=120000,is_closed=True,close=102.,atr=2.),
                      dict(timestamp=180000,is_closed=False,close=999.,atr=999.)])
    snapshot, atr = cached_tick_indicators(f, 103., 181000)
    assert snapshot['quote_ms'] == 181000
    assert atr == 2.
    snap2, atr2 = cached_tick_indicators(f, 103., 241000)
    assert snap2['quote_ms'] == 241000
    assert atr2 == 2.


def test_cached_tick_indicators_exposes_only_three_closed_ma_values():
    rows = [
        dict(timestamp=60000, is_closed=True, open=99., high=101., low=98., close=100.,
             atr=1., ma5=98., ma15=95., kc_upper=105., kc_lower=90.),
        dict(timestamp=120000, is_closed=True, open=100., high=102., low=99., close=101.,
             atr=1., ma5=99., ma15=96., kc_upper=106., kc_lower=91.),
        dict(timestamp=180000, is_closed=True, open=101., high=103., low=100., close=102.,
             atr=1., ma5=100., ma15=97., kc_upper=107., kc_lower=92.),
        dict(timestamp=240000, is_closed=False, open=102., high=104., low=101., close=103.,
             atr=50., ma5=200., ma15=200., kc_upper=200., kc_lower=1.),
    ]
    frame = pd.DataFrame(rows)
    frame.attrs['timeframe_ms'] = 60000

    snapshot, _ = cached_tick_indicators(frame, 103., 241000)

    assert snapshot['ma5_history'] == [98., 99., 100.]
    assert snapshot['ma15_history'] == [95., 96., 97.]


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
@pytest.mark.parametrize('mode', ['paper', 'testnet'])
def test_real_account_reload_closes_tiered_profit_once(side, mode, tmp_path, monkeypatch):
    monkeypatch.setattr('core.services.exits.trend_hold_evaluator.evaluate_trend_hold', lambda *a, **k: ('RELEASED', 'TEST'))
    async def run():
        import core.paper_account as pm
        import core.testnet_account as tm
        from test_testnet_account import FakeTestnetExchange
        from test_close_deduplication import close_orders, closes
        symbol = 'DOGE/USDT'
        exchange = FakeTestnetExchange()
        original_create = exchange.create_order
        async def filled_create(*args, **kwargs):
            order = await original_create(*args, **kwargs)
            if str(args[1]).lower() == 'market':
                order.update(status='closed', filled=float(args[3]))
            return order
        exchange.create_order = filled_create
        exchange.market = lambda symbol: dict(linear=True,contractSize=1.,info={'filters':[
            dict(filterType=name,stepSize='0.001',minQty='0.001',maxQty='1000000')
            for name in ('LOT_SIZE','MARKET_LOT_SIZE')]})
        exchange.amount_to_precision = lambda symbol, amount: str(round(float(amount), 3))
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
        await account.open_position(symbol, side, 100., 100., 0., 0., 'MANUAL', leverage=1, atr=.5,
                                    entry_context={'entry_mode':'CHANNEL_SWING', 'manual_entry':True, 'entry_atr':10.})
        assert symbol in account.positions, account.logs[-3:]
        e = object.__new__(TradingEngine)
        e.is_running = True
        e.account = account
        e._channel_exit_frames = {}
        sign = 1 if side == 'LONG' else -1
        assert not await e._instant_quote_exit(symbol, 100+sign*6.0, time.time()*1000) # drive to 6U peak
        peak = account.positions[symbol]['peak_pnl_usd']
        if mode == 'paper':
            account = pm.PaperAccount()
        else:
            account = tm.BinanceTestnetAccount(exchange)
            await account.initialize()
        e.account = account
        assert account.positions[symbol]['peak_pnl_usd'] == peak
        await asyncio.gather(*(e._instant_quote_exit(symbol,100+sign*1.0,time.time()*1000) for _ in range(10)))
        assert symbol not in account.positions
        assert len(closes(account))==1
        if mode == 'testnet':
            assert len(close_orders(exchange))==1
    asyncio.run(run())


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_channel_initial_atr_stop_cannot_retry_after_metadata_reload(side):
    async def run():
        e, p, now = engine_for(side)
        sign = 1 if side == 'LONG' else -1
        p['initial_sl'] = p['sl'] = 100-sign*.5
        assert not await e._instant_quote_exit('X',100-sign*.5,now*1000)
        restored = dict(pos(side),open_timestamp=p['open_timestamp'],sl=p['sl'])
        e.account.positions['X'] = restored
        assert not await e._instant_quote_exit('X',100.,now*1000)
        assert restored['sl'] == 0.
        e.account.close_position.assert_not_awaited()
    asyncio.run(run())
