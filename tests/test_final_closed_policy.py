"""Final A-E specification: close-only decisions and physical execution gates."""
import asyncio
import copy
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
import pandas as pd
import pytest
from core.services.strategies.unified_entry_strategy import evaluate_closed_entry, confirmed
from core.services.exits.dual_track_exit_service import DualTrackExitStrategy


def candles(rule='C', side='LONG'):
    rows=[dict(timestamp=(i+1)*60000,open=100.,close=100.1,high=100.3,low=99.8,
               ma3=100.2,ma15=100.,kc_upper=102.,kc_middle=100.,kc_lower=98.,atr=1.,
               volume=100.,is_closed=i<22) for i in range(23)]
    if rule=='A':
        rows[20].update(open=100.5,close=100.2,high=100.6,low=100.1)
        rows[21].update(open=100.,close=101.,high=101.1,low=99.9)
    elif rule=='B':
        rows[21].update(open=100.1,close=101.5,high=101.6,low=100.)
    elif rule=='C':
        rows[20].update(ma3=99.9)
        rows[21].update(open=100.4,close=100.6,high=100.7,low=100.3)
    elif rule=='D':
        rows[19].update(close=102.,high=102.1)
        rows[20].update(open=102.,close=102.2,high=102.3,low=101.9,ma3=102.,ma15=101.)
        rows[21].update(open=102.2,close=102.4,high=102.5,low=102.1,ma3=102.,ma15=101.)
    elif rule=='E':
        for i in range(22):
            close=103.+i*.1
            rows[i].update(open=close-.6,close=close,high=close+.1,low=close-.7)
        rows[21].update(open=105.2,close=106.2,high=106.3,low=105.1)
    f=pd.DataFrame(rows)
    if side=='SHORT':
        original=f.copy()
        for a,b in [('open','open'),('close','close'),('high','low'),('low','high'),
                    ('ma3','ma3'),('ma15','ma15'),('kc_upper','kc_lower'),('kc_lower','kc_upper'),('kc_middle','kc_middle')]:
            f[a]=200-original[b]
    f.attrs['timeframe_ms']=60000
    return f


@pytest.mark.parametrize('side',['LONG','SHORT'])
@pytest.mark.parametrize('rule',list('ABCDE'))
def test_all_rules_and_flat_kc(side,rule):
    f=candles(rule,side)
    ok,code,d=evaluate_closed_entry(f,side,after_close=True)
    assert ok and code==f'CLOSED_{rule}_{side}'
    # Arbitrary forming-candle volatility cannot change a closed decision.
    f.loc[22,['open','high','low','close','ma3','ma15','kc_middle']]=[1.,10000.,.001,999.,1.,999.,1.]
    assert evaluate_closed_entry(f,side,after_close=True)==(ok,code,d)


@pytest.mark.parametrize('side',['LONG','SHORT'])
@pytest.mark.parametrize('fault',['unclosed','invalid','gap','color','bad_timeframe'])
def test_physical_failures(side,fault):
    f=candles('A',side)
    if fault=='unclosed': f.loc[21,'is_closed']=False
    if fault=='invalid': f.loc[21,'close']=float('nan')
    if fault=='gap': f.loc[20,'timestamp']-=1
    if fault=='color': f.loc[21,'close']=f.loc[21,'open']+(-.1 if side=='LONG' else .1)
    if fault=='bad_timeframe': f.attrs['timeframe_ms']=300000
    assert not evaluate_closed_entry(f,side)[0]


@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_A_can_use_second_previous_opposite_and_strict_threshold(side):
    f=candles('A',side);sign=1 if side=='LONG' else -1
    f.loc[19]=f.loc[20].copy(); f.loc[19,'timestamp']=1200000
    f.loc[20,['open','close']]=[100.,100.+sign*.1]
    f.loc[20,['low','high']]=[99.,101.]
    f.loc[20,'atr']=1.25
    f.loc[21,['open','close']]=[100.,100.+sign*1.]
    f.loc[21,['low','high']]=[98.,102.]
    assert not evaluate_closed_entry(f,side)[0]
    f.loc[21,'close']+=sign*.01
    assert evaluate_closed_entry(f,side)[1]==f'CLOSED_A_{side}'


@pytest.mark.parametrize('side',['LONG','SHORT'])
@pytest.mark.parametrize('fault',['small','wick','no_new_high','no_close'])
def test_E_never_allows_weak_late_chase(side,fault):
    f=candles('E',side);sign=1 if side=='LONG' else -1
    if fault=='small': f.loc[21,'open']=f.loc[21,'close']-sign*.1
    if fault=='wick': f.loc[21,'high' if side=='LONG' else 'low']=f.loc[21,'close']+sign*2.
    if fault=='no_new_high':
        f.loc[21,'close']-=sign*2.
        f.loc[21,'open']=f.loc[21,'close']-sign*1.
        f.loc[21,'low']=min(f.loc[21,'open'],f.loc[21,'close'])-.1
        f.loc[21,'high']=max(f.loc[21,'open'],f.loc[21,'close'])+.1
    assert not evaluate_closed_entry(f,side,after_close=fault!='no_close')[0]


@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_C_requires_cross_instant_and_D_requires_c0_inside(side):
    f=candles('C',side); f.loc[20,'ma3']=f.loc[21,'ma3']
    assert not evaluate_closed_entry(f,side)[0]
    f=candles('D',side);sign=1 if side=='LONG' else -1
    f.loc[19,'close']+=sign*.1
    assert not evaluate_closed_entry(f,side,after_close=True)[0]


def position(side):
    return dict(side=side,entry_price=95. if side=='LONG' else 105.,qty=1.,open_timestamp=1.)


@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_favorable_large_body_suppresses_every_exit_including_pending(side):
    f=candles('A',side);p=position(side);s=DualTrackExitStrategy()
    # Put the middle on the adverse side while maintaining valid rails.
    f.loc[21,'kc_middle']=101.5 if side=='LONG' else 98.5
    assert s.evaluate_exit(p,f,-100000.) is None
    p['closed_exit_state']['pending']='EXIT_ATR_TRAIL_CLOSED'
    assert s.evaluate_exit(p,f,100000.) is None


@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_profitable_outer_engulfing_and_retry_persistence(side):
    f=candles('C','LONG')
    f.loc[20,['open','close','high','low']]=[103.,104.,104.1,102.9]
    f.loc[21,['open','close','high','low']]=[104.,102.5,104.1,102.4]
    if side=='SHORT':
        original=f.copy()
        for a,b in [('open','open'),('close','close'),('high','low'),('low','high'),('ma3','ma3'),('ma15','ma15'),('kc_upper','kc_lower'),('kc_lower','kc_upper'),('kc_middle','kc_middle')]: f[a]=200-original[b]
    p=position(side);s=DualTrackExitStrategy()
    assert s.evaluate_exit(p,f,1.)=='EXIT_OUTER_ENGULFING_CLOSED'
    restored=copy.deepcopy(p)
    assert s.evaluate_exit(restored,f,10000.)=='EXIT_OUTER_ENGULFING_CLOSED'


@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_close_only_extremes_and_no_preentry_exit(side):
    p=position(side);f=candles('C',side);s=DualTrackExitStrategy()
    s.evaluate_exit(p,f,1.)
    snapshot=copy.deepcopy(p)
    s.evaluate_exit(p,f,10000.)
    assert p==snapshot
    p=position(side);p['open_timestamp']=float(f.loc[21,'timestamp']+60000)/1000
    assert s.evaluate_exit(p,f,1.) is None
    assert 'closed_exit_state' not in p


def engine_for(account,frame,quote):
    from core.engine import TradingEngine
    engine=object.__new__(TradingEngine)
    engine.account=account
    engine.symbol_rotation=SimpleNamespace(get_dynamic_leverage=lambda *a:1)
    engine._execution_price_is_safe=AsyncMock(return_value=True)
    engine.fetch_klines=AsyncMock(return_value=frame)
    engine.strategy=SimpleNamespace(compute_indicators=lambda f:f)
    engine.tickers={'DOGE/USDT':quote}
    return engine


@pytest.mark.parametrize('side',['LONG','SHORT'])
@pytest.mark.parametrize('rule',list('ABCDE'))
def test_real_account_order_boundary(side,rule,tmp_path,monkeypatch):
    import core.testnet_account as tm
    from test_testnet_account import FakeTestnetExchange
    monkeypatch.setattr(tm,'STATE_FILE',str(tmp_path/'account.json'))
    monkeypatch.setattr(tm,'DATA_DIR',str(tmp_path))
    monkeypatch.setattr(tm,'notify_email',lambda *a,**k:None)
    monkeypatch.setattr(tm.BinanceTestnetAccount,'credentials_configured',staticmethod(lambda:True))
    monkeypatch.setattr('core.engine.DEFAULT_SYMBOLS',['DOGE/USDT'])
    async def run():
        exchange=FakeTestnetExchange();account=tm.BinanceTestnetAccount(exchange)
        await account.initialize()
        f=candles(rule,side)
        account.trades.append(dict(symbol='DOGE/USDT',action='CLOSE_LONG',id=1000))
        account.last_closed_at['DOGE/USDT']=__import__('time').time() # same minute is not a veto
        engine=engine_for(account,f,100.)
        assert await engine._execute_confirmed_channel_break('DOGE/USDT',f,100.,side,v8_reason=f'CLOSED_{rule}_{side}')
        assert exchange.orders[0]['side']==('buy' if side=='LONG' else 'sell')
        assert len(exchange.orders)==1 # no native tick-triggered SL/TP
        assert account.positions['DOGE/USDT']['entry_signal_code']==f'CLOSED_{rule}_{side}'
        account.positions.clear()
        assert not await engine._execute_confirmed_channel_break('DOGE/USDT',f,100.,side,v8_reason=f'CLOSED_{rule}_{side}')
    asyncio.run(run())


@pytest.mark.parametrize('fault',['stale','wrong_side','unsupported','daily_halt'])
def test_final_gate_rejects_ghost_orders(fault,monkeypatch):
    from core.engine import TradingEngine
    monkeypatch.setattr('core.engine.DEFAULT_SYMBOLS',['DOGE/USDT'])
    f=candles('A');account=SimpleNamespace(positions={},pending_limit_orders={},trades=[],
        daily_loss_limit_hit=lambda:(fault=='daily_halt',0),open_position=AsyncMock())
    engine=engine_for(account,f,100.)
    initial=f.copy(deep=True)
    if fault=='stale': f.loc[21,'close']=100.
    code='CLOSED_A_LONG' if fault!='unsupported' else 'MA_CROSS_OR_ENGULFING_LONG'
    side='SHORT' if fault=='wrong_side' else 'LONG'
    assert not asyncio.run(engine._execute_confirmed_channel_break('DOGE/USDT',initial,100.,side,v8_reason=code))
    account.open_position.assert_not_awaited()
