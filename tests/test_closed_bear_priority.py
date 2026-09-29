"""Priority impulses cannot bypass low-price risk or matched-fill checks."""
from types import SimpleNamespace
import pandas as pd
import pytest
from core.services.closed_bear_entry import CODE, impulse, short_problem, arm_reversal, reversal_authorized
from core.services.closed_breakout_entry import evaluate_channel_entry
from core.services.entry_service import check_entry_signals


def market():
    rows = [dict(timestamp=60000*(i+1), open=100., close=100.1, high=101., low=99.,
                 kc_lower=99., kc_upper=102., kc_middle=100., ma3=100., ma15=100.,
                 atr=1., rsi=45., is_closed=i<6) for i in range(7)]
    rows[5].update(open=100.2, close=98.8, high=100.3, low=98.7)
    rows[6].update(open=98.8, close=98.7, low=98.6)
    return pd.DataFrame(rows)


def test_closed_impulse_has_priority_without_ma_cross():
    f=market()
    assert check_entry_signals(f,'SHORT',0)['reason']==CODE
    assert check_entry_signals(f,'LONG',0)['reason']=='WAIT_CLOSED_BEAR_PRIORITY'
    assert impulse(f,100.2) is None
    f.loc[5,'is_closed']=False
    assert impulse(f,98.7) is None


@pytest.mark.parametrize('mode', ['extension','rsi','six','invalid'])
def test_low_short_guards_precede_cross_and_priority(mode):
    f=market()
    if mode=='extension': price=96.
    else: price=98.7
    if mode=='rsi': f.loc[5,'rsi']=24.
    if mode=='six': f.loc[:4,'open']=100.2
    if mode=='invalid': price=float('nan')
    assert short_problem(f,price)
    assert not impulse(f,price)
    assert check_entry_signals(f,'SHORT',0,live_price=price)['action']=='WAIT'


def test_latest_quote_revalidated_for_reentry():
    f=market()
    assert evaluate_channel_entry(f,98.7,'SHORT')[0]
    assert not evaluate_channel_entry(f,96.,'SHORT')[0]
    closed_at=('421',)
    assert not evaluate_channel_entry(f,98.7,'SHORT',closed_at=closed_at)[0]
    assert evaluate_channel_entry(f,98.7,'SHORT',closed_at=closed_at,reverse_authorized=True)[0]
    assert not evaluate_channel_entry(f,96.,'SHORT',closed_at=closed_at,reverse_authorized=True)[0]


def test_reversal_requires_matching_close_and_expires_or_consumes():
    account=SimpleNamespace(positions={},trades=[])
    engine=SimpleNamespace(account=account)
    signal={'side':'SHORT','signal_code':CODE}
    assert not arm_reversal(engine,'X','DualTrackExit TEST',420000)
    fill=dict(symbol='X',action='CLOSE_LONG',id=421000,reason='DualTrackExit TEST')
    account.trades.append(fill)
    assert arm_reversal(engine,'X','DualTrackExit TEST',420000)
    assert reversal_authorized(engine,'X',signal,now=422)
    assert not reversal_authorized(engine,'X',signal,now=480)
    assert not reversal_authorized(engine,'X',dict(side='LONG',signal_code=CODE),now=422)
    fill['reason']='manual'
    assert not reversal_authorized(engine,'X',signal,now=422)
    fill['reason']='DualTrackExit TEST'
    account.trades.append(dict(symbol='X',action='OPEN_SHORT',id=422000))
    assert not reversal_authorized(engine,'X',signal,now=423)


@pytest.mark.parametrize('outcome', ['success','failed','missing_fill','partial','halt','hard_stop','no_exit'])
def test_runner_close_before_reverse(monkeypatch, outcome):
    import asyncio
    import time
    from unittest.mock import Mock, AsyncMock
    from core.services import symbol_runner as runner
    from core.engine import TradingEngine
    import core.services.exits.staged_risk_service as staged
    account=SimpleNamespace(positions={'X':dict(side='LONG',entry_price=100.,qty=1.)},
                            position_meta={'X': {'old_exit_state': True}},trades=[], channel_profit_reentries={},
                            save_state=Mock(),log=Mock())
    engine=SimpleNamespace(account=account,tickers={'X':98.7},
                           _take_over_manual_position=Mock(),get_velocity_drop_ratio=lambda _:0.,
                           _channel_candidate_bar_id=lambda f:float(f.iloc[-1]['timestamp']))
    async def close(symbol, price, reason, **kwargs):
        if outcome=='failed': return False
        account.positions.pop(symbol)
        if outcome!='missing_fill':
            account.trades.append(dict(symbol=symbol,action='CLOSE_LONG',id=int(time.time()*1000),reason=reason))
        return True
    account.close_position=AsyncMock(side_effect=close)
    account.partial_close_position=AsyncMock(return_value=True)
    async def execute(*args, **kwargs):
        assert not account.positions
        assert 'X' not in account.position_meta
        assert reversal_authorized(engine,'X',dict(side='SHORT',signal_code=CODE))
        if outcome=='halt':
            return await TradingEngine._execute_confirmed_channel_break(engine,*args,**kwargs)
        account.positions['X']=dict(side='SHORT')
        account.position_meta['X']=dict(new_position=True)
        return True
    engine._execute_confirmed_channel_break=AsyncMock(side_effect=execute)
    monkeypatch.setattr(staged,'staged_enabled',lambda *_:False)
    monkeypatch.setattr(runner,'enforce_hard_stop',AsyncMock(return_value=outcome=='hard_stop'))
    monkeypatch.setattr(runner.ProfitProtectionExitStrategy,'evaluate_exit',
                        lambda *_a,**_k:None if outcome=='no_exit' else ('PARTIAL_TAKE_PROFIT' if outcome=='partial' else 'TEST_EXIT'))
    frame = market()
    if outcome == 'partial': frame.loc[5, 'close'] = 100.1
    asyncio.run(runner.process_single_symbol_runner(engine,'X',time.time(),None,outcome=='halt',
                exit_frame=frame,exit_quote=98.7))
    if outcome in ('success','halt','no_exit'):
        engine._execute_confirmed_channel_break.assert_awaited_once()
    else:
        engine._execute_confirmed_channel_break.assert_not_awaited()
    if outcome=='success':
        assert account.positions['X']['side']=='SHORT'
        assert account.position_meta['X']['new_position']
    if outcome=='halt': assert not account.positions
    assert not any('處理失敗' in call.args[0] for call in account.log.call_args_list)


def test_order_lock_requires_matched_ticket_even_with_legacy_bypass():
    import asyncio
    import time
    from unittest.mock import Mock, AsyncMock
    from core.engine import TradingEngine
    account=SimpleNamespace(positions={},trades=[],log=Mock())
    engine=SimpleNamespace(account=account,_channel_candle_entry_blocked=lambda _:True,
                           _ck_reverse_order_authorized=lambda *_:False,
                           _place_structured_entry_locked=AsyncMock(return_value=True))
    signal=dict(side='SHORT',signal_code=CODE,bypass_cooldown=True)
    assert not asyncio.run(TradingEngine._place_structured_entry(engine,'X',signal,98.7))
    account.trades.append(dict(symbol='X',action='CLOSE_LONG',id=int(time.time()*1000),reason='DualTrackExit TEST'))
    assert arm_reversal(engine,'X','DualTrackExit TEST',0)
    assert asyncio.run(TradingEngine._place_structured_entry(engine,'X',signal,98.7))
    engine._place_structured_entry_locked.assert_awaited_once()


@pytest.mark.parametrize('body,expected', [(0.8,False),(0.81,True)])
def test_first_red_inside_channel_strict_threshold(body,expected):
    f=market()
    # Binary-exact threshold: prior ATR=1.25, required body=1.0.
    f.loc[4,'atr']=1.25
    f.loc[5,['open','close','high','low']]=[101.,101.-body*1.25,101.1,99.]
    assert bool(impulse(f,100.)) == expected
    if expected:
        assert check_entry_signals(f,'SHORT',0,live_price=100.)['reason']==CODE
    f.loc[4,'close']=f.loc[4,'open']
    assert impulse(f,100.) is None


@pytest.mark.parametrize('side', ['LONG','SHORT'])
def test_late_direction_cannot_bypass_with_ma_cross(side):
    from test_entry_cleanup_regression import frame
    f=frame(side,'cross')
    if side=='LONG':
        f.loc[3,'close']=f.loc[3,'kc_upper']+.1
        expected='BLOCKED_LATE_LONG_C0_OUTSIDE'
    else:
        f.loc[4,['open','close']]=[100.4,100.2]
        expected='BLOCKED_LATE_SHORT_NEAR_MIDDLE'
    assert check_entry_signals(f,side,0,live_price=100.)['reason']==expected
    assert not evaluate_channel_entry(f,100.,side)[0]


def test_long_c0_touch_allowed_but_outside_rejected():
    from test_entry_cleanup_regression import frame
    f=frame('LONG','continuation')
    f.loc[3,'close']=f.loc[3,'kc_upper']
    result=check_entry_signals(f,'LONG',0)
    assert result['reason']=='ENTER_FIRST_BREAKOUT_LONG'
    f.loc[3,'close']+=.01
    assert check_entry_signals(f,'LONG',0)['reason']=='BLOCKED_LATE_LONG_C0_OUTSIDE'


@pytest.mark.parametrize('fault', [None,'quote_rebound','late_short','duplicate','halt'])
def test_real_execution_of_inside_channel_impulse(tmp_path,monkeypatch,fault):
    import asyncio
    from unittest.mock import AsyncMock
    from core.engine import TradingEngine
    import core.testnet_account as tm
    from test_testnet_account import FakeTestnetExchange
    monkeypatch.setattr(tm,'STATE_FILE',str(tmp_path/'account.json'))
    monkeypatch.setattr(tm,'DATA_DIR',str(tmp_path))
    monkeypatch.setattr(tm,'notify_email',lambda *a,**k:None)
    monkeypatch.setattr(tm.BinanceTestnetAccount,'credentials_configured',staticmethod(lambda:True))
    monkeypatch.setattr('core.engine.DEFAULT_SYMBOLS',['DOGE/USDT'])
    async def run():
        exchange=FakeTestnetExchange()
        account=tm.BinanceTestnetAccount(exchange)
        await account.initialize()
        f=market()
        f.loc[5,['open','close','high','low']]=[101.2,100.2,101.3,100.1]
        f.loc[6,['open','close','high','low']]=[100.2,100.1,100.3,100.]
        assert impulse(f,100.1)
        engine=object.__new__(TradingEngine)
        engine.account=account
        engine.symbol_rotation=SimpleNamespace(get_stop_cooldown_remaining=lambda *a:0.,get_dynamic_leverage=lambda *a:1)
        engine.btc_1h_st_direction=0
        engine._same_side_entry_allowed=lambda *a:True
        engine._ck_reverse_order_authorized=lambda *a:False
        engine._execution_price_is_safe=AsyncMock(return_value=True)
        engine.fetch_klines=AsyncMock(return_value=f)
        engine.strategy=SimpleNamespace(compute_indicators=lambda f:f)
        engine.tickers={'DOGE/USDT':100.1}
        if fault=='quote_rebound': engine.tickers['DOGE/USDT']=101.2
        if fault=='late_short': f.loc[4,['open','close']]=[101.,100.5]
        if fault=='duplicate': engine._channel_used_confirmation={'DOGE/USDT':('SHORT',420000.)}
        opened=await engine._execute_confirmed_channel_break('DOGE/USDT',f,100.1,'SHORT',fault=='halt',v8_reason=CODE)
        if fault:
            assert not opened
            assert not exchange.orders
        else:
            assert opened
            assert exchange.orders[0]['type']=='market'
            assert exchange.orders[0]['side']=='sell'
            assert account.positions['DOGE/USDT']['side']=='SHORT'
            assert account.position_meta['DOGE/USDT']['entry_snapshot']['signal_code']==CODE
    asyncio.run(run())
