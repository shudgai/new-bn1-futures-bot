"""Abnormal-body-only exits, migration, and shared scan/tick execution."""
import asyncio
import copy
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pandas as pd
import pytest

from core.services.exits.peak_trailing_exit import (
    ABNORMAL_REASON, HARD_REASON, POLICY, STATE_KEY, evaluate_peak_trailing,
    migrate_peak_state,
)
from core.services.exits.realtime_profit_exit import cached_tick_indicators, enforce_realtime_profit_exit
from core.services.symbol_runner import process_single_symbol_runner


def position(side='LONG', opened=60.):
    sign = 1 if side == 'LONG' else -1
    return dict(side=side, entry_price=100., qty=1., margin=100., leverage=1.,
                open_timestamp=opened, entry_mode='CHANNEL_SWING', entry_atr=10.,
                initial_sl=100-sign*15., sl=100-sign*15.)


def snapshot(side='LONG', stamp=181000):
    bar = int(stamp//60000)*60000
    return dict(quote_ms=stamp, live_bar_ms=bar, closed_bar_ms=bar-60000,
                live_open=110. if side == 'LONG' else 90., atr=1.,
                kc_middle=120. if side == 'LONG' else 80., ma5=120., ma15=100.,
                kc_upper=125., kc_lower=75., close=100., open=101., high=101., low=99.)


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
@pytest.mark.parametrize('body', [1.199, 1.2, 1.201])
def test_abnormal_boundary_both_sides(side, body):
    p = position(side); snap = snapshot(side)
    sign = 1 if side == 'LONG' else -1
    result = evaluate_peak_trailing(p, snap['live_open']-sign*body, snap)
    assert bool(result) == (body >= 1.2)
    if result:
        assert result['type'] == ABNORMAL_REASON
        assert result['action'] == 'FULL_CLOSE'
        assert p[STATE_KEY]['trigger_atr'] == 1.  # Not entry ATR=10.


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_normal_pullback_middle_ma5_doji_and_peak_giveback_hold(side):
    p = position(side); snap = snapshot(side)
    sign = 1 if side == 'LONG' else -1
    for gain in (12., 10., 7., 4., 1.):
        price = 100+sign*gain
        snap.update(live_open=price+sign*.2, quote_ms=snap['quote_ms']+1,
                    live_high=150., live_low=50.)
        assert evaluate_peak_trailing(p, price, snap) is None
    assert p['peak_pnl_usd'] == 12.


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
@pytest.mark.parametrize('fault', ['no_open', 'nan_atr', 'zero_atr', 'old_live', 'old_closed', 'missing_frame'])
def test_missing_or_mismatched_candles_cannot_create_exit(side, fault):
    snap = snapshot(side); p = position(side)
    if fault == 'no_open': snap.pop('live_open')
    if fault == 'nan_atr': snap['atr'] = float('nan')
    if fault == 'zero_atr': snap['atr'] = 0.
    if fault == 'old_live': snap['live_bar_ms'] -= 60000
    if fault == 'old_closed': snap['closed_bar_ms'] -= 60000
    if fault == 'missing_frame': snap = 181000
    assert evaluate_peak_trailing(p, 108. if side == 'LONG' else 92., snap) is None


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_original_open_same_entry_bar_and_no_favorable_body_exit(side):
    p = position(side, opened=180.5); snap = snapshot(side)
    sign = 1 if side == 'LONG' else -1
    assert evaluate_peak_trailing(p, snap['live_open']+sign*3., snap) is None
    result = evaluate_peak_trailing(p, snap['live_open']-sign*1.2, snap)
    assert result['type'] == ABNORMAL_REASON


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
@pytest.mark.parametrize('pending', ['EXIT_REALTIME_PEAK_TRAILING', HARD_REASON])
def test_migrate_preserves_verified_peak_and_only_hard_pending(side, pending):
    p = position(side); sign = 1 if side == 'LONG' else -1
    p[STATE_KEY] = dict(policy='realtime_peak_trailing_v1',
                       identity=[side,60.,100.,1.], peak_price=100+sign*10.,
                       peak_net_pnl=9., atr=10., pending=pending, trigger='CLOSED_BELOW_MA5')
    p['sl'] = 100+sign*5.
    meta = copy.deepcopy(p)
    meta.update(limit_tp1_price=110., breakeven_trigger_price=108.)
    state = migrate_peak_state(p, meta)
    assert state['policy'] == POLICY
    assert state['peak_price'] == 100+sign*10.
    assert state.get('pending') == (HARD_REASON if pending == HARD_REASON else None)
    assert p['sl'] == p['initial_sl']
    assert 'limit_tp1_price' not in meta and 'breakeven_trigger_price' not in meta
    result = evaluate_peak_trailing(p, 100., 181000)
    assert bool(result) == (pending == HARD_REASON)


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_pending_retry_new_identity_and_initial_stop(side):
    p = position(side); snap = snapshot(side)
    price = 108. if side == 'LONG' else 92.
    assert evaluate_peak_trailing(p, price, snap)['type'] == ABNORMAL_REASON
    restored = copy.deepcopy(p)
    assert evaluate_peak_trailing(restored, 100., 241000)['type'] == ABNORMAL_REASON
    restored['open_timestamp'] = 240.
    assert evaluate_peak_trailing(restored, 100., 241001) is None
    assert evaluate_peak_trailing(restored, restored['initial_sl'], 241002)['type'] == HARD_REASON


def frame_for(side, now):
    bar = int(now//60)*60000
    opening = 110. if side == 'LONG' else 90.
    return pd.DataFrame([
        dict(timestamp=bar-60000, is_closed=True, open=100., close=100., high=101., low=99., atr=1.),
        dict(timestamp=bar, is_closed=False, open=opening, close=opening,
             high=opening, low=opening, atr=999.),
    ])


def test_cached_snapshot_requires_actual_live_bar_and_supports_one_closed_bar():
    now = time.time(); f = frame_for('LONG', now)
    snap, atr = cached_tick_indicators(f, 108., now*1000)
    assert atr == 1. and snap['live_open'] == 110.
    assert cached_tick_indicators(f.iloc[:-1], 108., now*1000) == ({'quote_ms': now*1000}, 0.)
    assert cached_tick_indicators(f, 108., now*1000+60000) == ({'quote_ms': now*1000+60000}, 0.)


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_scan_tick_retry_no_rest_and_no_position(side):
    async def run():
        now=time.time(); p=position(side, now-120); f=frame_for(side, now)
        account=SimpleNamespace(positions={'X':p}, position_meta={}, save_state=Mock(), log=Mock(),
                                close_position=AsyncMock(return_value=False))
        engine=SimpleNamespace(account=account,is_running=True,_channel_exit_frames={'X':f},
                               fetch_klines=AsyncMock(side_effect=AssertionError('No REST')))
        price = 108. if side == 'LONG' else 92.
        await process_single_symbol_runner(engine,'X',now,None,True,exit_frame=f,exit_quote=price)
        assert account.close_position.await_count == 1
        assert ABNORMAL_REASON in account.close_position.await_args.args[2]
        engine._channel_exit_frames = {}
        assert await enforce_realtime_profit_exit(engine,'X',100.,time.time()*1000)
        assert account.close_position.await_count == 2
        saved=copy.deepcopy(p)
        assert not await enforce_realtime_profit_exit(engine,'X',100.,(now-10)*1000)
        assert p==saved
        account.positions.clear()
        assert not await enforce_realtime_profit_exit(engine,'X',100.,time.time()*1000)
        engine.fetch_klines.assert_not_awaited()
    asyncio.run(run())


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
@pytest.mark.parametrize('mode', ['paper', 'testnet'])
def test_real_account_reload_and_single_flight_abnormal_close(side, mode, tmp_path, monkeypatch):
    async def run():
        import core.paper_account as pm
        import core.testnet_account as tm
        from test_testnet_account import FakeTestnetExchange
        from test_close_deduplication import close_orders, closes
        ex=FakeTestnetExchange()
        ex.market=lambda symbol: dict(linear=True,contractSize=1.,info={'filters':[
            dict(filterType=name,stepSize='0.001',minQty='0.001',maxQty='1000000')
            for name in ('LOT_SIZE','MARKET_LOT_SIZE')]})
        ex.amount_to_precision=lambda symbol, amount: str(round(float(amount),3))
        monkeypatch.setattr(pm,'STATE_FILE',str(tmp_path/'paper.json'))
        monkeypatch.setattr(tm,'STATE_FILE',str(tmp_path/'testnet.json'))
        monkeypatch.setattr(tm,'DATA_DIR',str(tmp_path))
        monkeypatch.setattr(tm,'notify_email',lambda *a,**k:None)
        monkeypatch.setattr(tm.BinanceTestnetAccount,'credentials_configured',staticmethod(lambda:True))
        account=pm.PaperAccount() if mode=='paper' else tm.BinanceTestnetAccount(ex)
        if mode=='testnet': await account.initialize()
        assert await account.open_position('DOGE/USDT',side,100.,25.,0.,0.,'MANUAL',leverage=1,atr=10.,
            entry_context={'entry_mode':'CHANNEL_SWING','manual_entry':True})
        p=account.positions['DOGE/USDT']
        assert 'limit_tp1_price' not in account.position_meta['DOGE/USDT']
        now=time.time();f=frame_for(side,now)
        engine=SimpleNamespace(account=account,is_running=True,_channel_exit_frames={'DOGE/USDT':f})
        assert not await enforce_realtime_profit_exit(engine,'DOGE/USDT',110. if side=='LONG' else 90.,now*1000)
        account=pm.PaperAccount() if mode=='paper' else tm.BinanceTestnetAccount(ex)
        if mode=='testnet': await account.initialize()
        engine.account=account
        assert account.positions['DOGE/USDT'][STATE_KEY]['policy']==POLICY
        price=108. if side=='LONG' else 92.
        await asyncio.gather(*(enforce_realtime_profit_exit(engine,'DOGE/USDT',price,time.time()*1000) for _ in range(10)))
        assert 'DOGE/USDT' not in account.positions
        assert len(closes(account))==1
        if mode=='testnet':
            assert len(close_orders(ex))==1
            assert close_orders(ex)[0]['params']['reduceOnly']
    asyncio.run(run())


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_paper_account_updates_keep_initial_and_account_hard_stops(side, tmp_path, monkeypatch):
    async def run():
        import core.paper_account as pm
        monkeypatch.setattr(pm,'STATE_FILE',str(tmp_path/'paper.json'))
        account=pm.PaperAccount()
        assert await account.open_position('X',side,100.,25.,0.,0.,'MANUAL',atr=1.,leverage=1,
            entry_context={'entry_mode':'CHANNEL_SWING','manual_entry':True})
        stop=account.positions['X']['initial_sl']
        await account.update_positions({'X': stop})
        assert 'X' not in account.positions
        assert 'HARD_STOP' in account.trades[0]['reason']
    asyncio.run(run())


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_old_state_machine_cannot_exit_on_ma_middle_or_doji(side):
    from core.services.strategies.strict_state_machine import StrictStateMachineStrategy, PositionState
    strategy=StrictStateMachineStrategy()
    strategy.set_state('X', PositionState[side], {'entry_price':100., 'max_pnl':100.})
    frame=pd.DataFrame([dict(timestamp=60000*i,open=100.,close=99.,high=110.,low=90.,
                            kc_middle=100.,kc_upper=105.,kc_lower=95.,ma3=101.,ma15=102.,atr=1.)
                        for i in (1,2,3)])
    assert strategy.evaluate_tick('X',frame,99. if side=='LONG' else 101.,0.)['action']=='WAIT'
