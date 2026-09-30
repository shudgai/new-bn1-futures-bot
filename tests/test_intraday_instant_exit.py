import asyncio
import copy
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pandas as pd
import pytest

from core.engine import TradingEngine
from core.services.strategies.pure_trend_v2 import PureTrendStrategyV2
from core.services.exits.dual_track_exit_service import DualTrackExitStrategy


def pos(side='SHORT'):
    return dict(side=side, entry_price=100., qty=1., open_timestamp=60.,
                entry_mode='CHANNEL_SWING', margin=100., leverage=1.,
                entry_atr=100., sl=250. if side == 'SHORT' else 1.)


def observe(p, price, stamp=61000, mid=0., atr=0.):
    return PureTrendStrategyV2().check_intraday_instant_exit(
        p, price, dict(quote_ms=stamp, kc_middle=mid, low=1., high=1000.), atr)


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_ten_dollar_arming_and_strict_twenty_five_percent(side):
    p=pos(side); sign=1 if side=='LONG' else -1
    assert observe(p,100+sign*7.9) is None
    assert observe(p,100+sign*6.,62000) is None
    assert observe(p,100+sign*14.,63000) is None
    assert observe(p,100+sign*11.21,64000) is None
    assert observe(p,100+sign*10.5,65000) is None
    assert observe(p,100+sign*10.49,66000)=='EXIT_PEAK_DRAWDOWN_25PCT'
    assert DualTrackExitStrategy().evaluate_exit(p,current_price=100)=='EXIT_PEAK_DRAWDOWN_25PCT'


@pytest.mark.parametrize('side', ['LONG','SHORT'])
def test_mid_touch_without_closed_candle(side):
    p=pos(side)
    assert observe(p,99. if side=='SHORT' else 101.,mid=100.) is None
    assert observe(p,100.,62000,mid=100.) is None
    assert observe(p,100.01 if side=='SHORT' else 99.99,63000,mid=100.)=='EXIT_KC_MID_BREACH'


@pytest.mark.parametrize('side', ['LONG','SHORT'])
def test_spike_post_entry_ticks_exact_boundary_and_bar_reset(side):
    p=pos(side); sign=1 if side=='LONG' else -1
    assert observe(p,100.,atr=1.) is None  # ignore pre-entry/full-candle wicks
    assert observe(p,100.-sign*.79,62000,atr=1.) is None
    assert observe(p,100.-sign*.8,63000,atr=1.) is None
    p=pos(side)
    assert observe(p,100.,atr=1.) is None
    assert observe(p,100.-sign*.8,120000,atr=1.) is None


def test_invalid_stale_and_position_identity():
    p=pos()
    assert observe(p,float('nan')) is None
    assert observe(p,86.,59000) is None
    assert observe(p,86.,65000) is None
    assert observe(p,99.,64000) is None
    assert p['instant_exit_state']['peak']==14
    p['open_timestamp']=66.
    assert observe(p,99.,67000) is None
    assert p['instant_exit_state']['peak']==1


def test_restart_preserves_peak_and_pending():
    p=pos()
    observe(p,86.)
    restarted=copy.deepcopy(p)
    assert observe(restarted,89.51,62000)=='EXIT_PEAK_DRAWDOWN_25PCT'
    assert observe(copy.deepcopy(restarted),80.,63000)=='EXIT_PEAK_DRAWDOWN_25PCT'


def test_fast_path_ignores_scan_lock_and_rest_retries_after_restart():
    async def run():
        now=time.time(); bar=int(now//60)*60000
        p=pos();p['open_timestamp']=now-120
        account=SimpleNamespace(positions={'X':p},position_meta={},save_state=Mock(),
                                close_position=AsyncMock(return_value=False), log=Mock())
        engine=object.__new__(TradingEngine)
        engine.account=account;engine.is_running=True
        engine.fetch_klines=AsyncMock(side_effect=AssertionError('No REST in fast path'))
        lock=asyncio.Lock();await lock.acquire()
        engine._channel_symbol_locks={'X':lock}
        engine._channel_exit_frames={'X':pd.DataFrame([dict(
            timestamp=bar-60000,is_closed=True,kc_middle=100.,atr=1.)])}
        assert await engine._instant_quote_exit('X',100.01,now*1000)
        assert account.close_position.await_count==1
        assert account.position_meta['X']['closed_exit_state']['pending']
        # Restore persisted state into a reloaded position; no frame required for retry.
        account.positions['X']=dict(pos(),open_timestamp=now-120)
        engine._channel_exit_frames={}
        assert await engine._instant_quote_exit('X',99.,now*1000)
        assert account.close_position.await_count==2
        assert account.close_position.await_args.args[1]==99.
        engine.fetch_klines.assert_not_called()
        lock.release()
    asyncio.run(run())


def test_trade_stream_deduplicates_cache_and_observes_each_trade():
    async def run():
        engine=object.__new__(TradingEngine);engine.is_running=True
        engine.account=SimpleNamespace(positions={'X':pos()},log=Mock())
        batch=[dict(symbol='X',timestamp=61000,id=str(i),price=100-i) for i in range(3)]
        async def watch(*args,**kwargs):
            if engine.ws_exchange.watch_trades_for_symbols.await_count==2:
                engine.is_running=False
            return batch
        engine.ws_exchange=SimpleNamespace(watch_trades_for_symbols=AsyncMock(side_effect=watch))
        engine._instant_quote_exit=AsyncMock()
        await engine._instant_exit_trade_loop()
        assert engine._instant_quote_exit.await_count==3
    asyncio.run(run())
