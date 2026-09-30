import asyncio
import copy
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pandas as pd
import pytest

from core.engine import TradingEngine
from core.services.exits.realtime_profit_exit import cached_tick_indicators
from core.services.strategies.pure_trend_v2 import PureTrendStrategyV2
from core.services.symbol_runner import process_single_symbol_runner
from test_intraday_instant_exit import pos, observe


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_small_peak_is_protected_in_same_bar(side):
    p = pos(side)
    sign = 1 if side == 'LONG' else -1
    assert observe(p, 100 + sign * .4, 61000) is None
    assert observe(p, 100 + sign * .301, 61001) is None
    assert observe(p, 100 + sign * .299, 61002) == 'EXIT_PEAK_DRAWDOWN_25PCT'
    assert p['peak_price'] == pytest.approx(100 + sign * .4)


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_atr_retrace_fixed_scale_and_boundary(side):
    p = pos(side)
    sign = 1 if side == 'LONG' else -1
    assert observe(p, 100 + sign * 10, atr=1.) is None
    assert observe(p, 100 + sign * 9.201, 62000, atr=100.) is None
    assert observe(p, 100 + sign * 9.2, 62001, atr=100.) == 'EXIT_PEAK_RETRACE_08ATR'
    assert p['instant_exit_state']['trail_atr'] == 1.


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_outer_band_then_live_ma3_cross_without_large_drawdown(side):
    p = pos(side)
    sign = 1 if side == 'LONG' else -1
    strategy = PureTrendStrategyV2()
    snap = dict(quote_ms=61000, live_ma3=100+sign*9.5,
                kc_upper=105., kc_lower=95.)
    assert strategy.check_intraday_instant_exit(p, 100+sign*10, snap, 10.) is None
    snap.update(quote_ms=61001, live_ma3=100+sign*9.9)
    assert strategy.check_intraday_instant_exit(p, 100+sign*9.8, snap, 10.) == 'EXIT_OUTER_MA3_REVERSAL'


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_no_outer_cross_without_observed_outer_tick(side):
    p = pos(side)
    sign = 1 if side == 'LONG' else -1
    strategy = PureTrendStrategyV2()
    for price, stamp in [(100+sign*10, 61000), (100+sign*9.8, 61001)]:
        snap = dict(quote_ms=stamp, live_ma3=100+sign*9.9, kc_upper=120., kc_lower=80.)
        assert strategy.check_intraday_instant_exit(p, price, snap, 10.) is None


def engine_for(side):
    now = time.time()
    p = pos(side)
    p.update(open_timestamp=now-120, entry_atr=10.)
    account = SimpleNamespace(positions={'X':p}, position_meta={}, save_state=Mock(),
                              close_position=AsyncMock(return_value=False), log=Mock())
    engine = object.__new__(TradingEngine)
    engine.account = account
    engine.is_running = True
    engine._channel_exit_frames = {}
    engine.fetch_klines = AsyncMock(side_effect=AssertionError('Exit must not fetch'))
    return engine, p, now


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_ticks_bypass_lock_missing_frame_restart_and_reordered_quotes(side):
    async def run():
        e, p, now = engine_for(side)
        sign = 1 if side == 'LONG' else -1
        lock = asyncio.Lock()
        await lock.acquire()
        e._channel_symbol_locks = {'X':lock}
        assert not await e._instant_quote_exit('X', 100+sign, now*1000)
        assert not await e._instant_quote_exit('X', 100., now*1000-1)
        persisted = copy.deepcopy(e.account.position_meta)
        e.account.positions['X'] = dict(pos(side), open_timestamp=p['open_timestamp'])
        e.account.position_meta = persisted
        assert await asyncio.wait_for(e._instant_quote_exit('X', 100+sign*.74, now*1000), .5)
        assert e.account.close_position.await_count == 1
        assert await e._instant_quote_exit('X', 100+sign*2, now*1000)
        assert e.account.close_position.await_count == 2
        assert e.account.close_position.await_args.args[1] == 100+sign*2
        e.fetch_klines.assert_not_called()
        lock.release()
    asyncio.run(run())


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_stale_quotes_and_replacement_position_do_not_inherit_peak(side):
    async def run():
        e, p, now = engine_for(side)
        sign = 1 if side == 'LONG' else -1
        assert not await e._instant_quote_exit('X', 100+sign, (now-10)*1000)
        assert 'instant_exit_state' not in p
        assert not await e._instant_quote_exit('X', 100+sign, now*1000)
        e.account.positions['X'] = dict(pos(side), open_timestamp=now-1)
        assert not await e._instant_quote_exit('X', 100., now*1000)
        assert e.account.positions['X']['peak_pnl_usd'] == 0.
        e.account.close_position.assert_not_awaited()
    asyncio.run(run())


def test_ma3_uses_two_confirmed_closes_and_quote_not_cached_live_close():
    f = pd.DataFrame([dict(timestamp=60000,is_closed=True,close=101.),
                      dict(timestamp=120000,is_closed=True,close=102.,atr=2.),
                      dict(timestamp=180000,is_closed=False,close=999.,atr=999.)])
    snapshot, atr = cached_tick_indicators(f, 103., 181000)
    assert snapshot['live_ma3'] == 102.
    assert atr == 2.
    assert cached_tick_indicators(f, 103., 241000) == ({'quote_ms':241000}, 0.)


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_runner_qty_only_position_uses_same_tick_exit(side):
    async def run():
        e, p, now = engine_for(side)
        e._take_over_manual_position = Mock()
        sign = 1 if side == 'LONG' else -1
        bar = int(now//60)*60000
        mid = 90. if side == 'LONG' else 110.
        f = pd.DataFrame([dict(timestamp=bar-offset*60000, is_closed=True,
                              open=100.,close=100.,high=101.,low=99.,ma3=100.,ma15=100.,
                              kc_middle=mid,kc_upper=mid+15.,kc_lower=mid-15.,atr=10.)
                          for offset in (3,2,1)] +
                         [dict(timestamp=bar,is_closed=False,close=100.)])
        await process_single_symbol_runner(e,'X',now,None,False,exit_frame=f,exit_quote=100+sign)
        assert p['peak_pnl_usd'] == 1.
        await process_single_symbol_runner(e,'X',now,None,False,exit_frame=f,exit_quote=100+sign*.74)
        assert e.account.close_position.await_count == 1
        assert e.account.close_position.await_args.args[2] == 'EXIT_PEAK_DRAWDOWN_25PCT'
    asyncio.run(run())


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
@pytest.mark.parametrize('mode', ['paper', 'testnet'])
def test_real_account_reload_and_concurrent_tick_close(side, mode, tmp_path, monkeypatch):
    async def run():
        import core.paper_account as pm
        import core.testnet_account as tm
        from test_testnet_account import FakeTestnetExchange
        from test_close_deduplication import close_orders, closes
        symbol = 'DOGE/USDT'
        exchange = FakeTestnetExchange()
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
        assert await account.open_position(symbol, side, 100., 25., 0., 0., 'MANUAL', leverage=1, atr=10.,
                   entry_context={'entry_mode':'CHANNEL_SWING', 'manual_entry':True, 'entry_atr':10.}), account.logs[-3:]
        e = object.__new__(TradingEngine)
        e.is_running = True
        e.account = account
        e._channel_exit_frames = {}
        sign = 1 if side == 'LONG' else -1
        assert not await e._instant_quote_exit(symbol, 100+sign, time.time()*1000)
        peak = account.positions[symbol]['peak_pnl_usd']
        if mode == 'paper':
            account = pm.PaperAccount()
        else:
            account = tm.BinanceTestnetAccount(exchange)
            await account.initialize()
        e.account = account
        assert account.positions[symbol]['peak_pnl_usd'] == peak
        await asyncio.gather(*(e._instant_quote_exit(symbol,100+sign*.7,time.time()*1000) for _ in range(10)))
        assert symbol not in account.positions
        assert len(closes(account)) == 1
        if mode == 'testnet':
            assert len(close_orders(exchange)) == 1
            assert close_orders(exchange)[0]['type'] == 'market'
            assert close_orders(exchange)[0]['params']['reduceOnly']
    asyncio.run(run())


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_initial_atr_stop_retries_after_metadata_reload(side):
    async def run():
        e, p, now = engine_for(side)
        sign = 1 if side == 'LONG' else -1
        p['sl'] = 100-sign*.5
        assert await e._instant_quote_exit('X',100-sign*.5,now*1000)
        assert e.account.close_position.await_args.args[2] == 'EXIT_INITIAL_ATR_HARD_STOP'
        restored = dict(pos(side),open_timestamp=p['open_timestamp'],sl=p['sl'])
        e.account.positions['X'] = restored
        assert await e._instant_quote_exit('X',100.,now*1000)
        assert e.account.close_position.await_args.args[2] == 'EXIT_INITIAL_ATR_HARD_STOP'
    asyncio.run(run())
