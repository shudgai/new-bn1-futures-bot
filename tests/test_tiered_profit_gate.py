import asyncio
import copy
import time
from types import SimpleNamespace
import pandas as pd
import pytest
from core.services.exits.peak_trailing_exit import evaluate_peak_trailing,STATE_KEY
from core.services.post_profit_lock_gate import post_profit_lock_reason,profit_exit_fields
from test_strict_entry_gates_live import frame_for


def position(side):
    return dict(symbol='CAP/USDT',side=side,entry_price=100.,qty=1.,margin=10.,
                leverage=10.,open_timestamp=60.,entry_mode='CHANNEL_SWING')


def tick(p,roi,stamp):
    sign=1 if p['side']=='LONG' else -1
    price=100+sign*roi*100
    return evaluate_peak_trailing(p,price,dict(quote_ms=stamp,ma5=price+sign,
        last_ma5=price+2*sign,live_open=price+sign,last_close=price+sign),fee=0.,slippage=0.)


def event_account(side,now,peak=110.):
    return SimpleNamespace(positions={},trades=[dict(symbol='X',action='CLOSE_'+side,status='CLOSED',
        last_profit_exit_timestamp=now,last_profit_exit_side=side,last_profit_exit_peak_price=peak)])


@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_gate_cooldown_extreme_and_expiry(side):
    f=frame_for(side);closed=f.iloc[:-1];now=time.time()*1000
    peak=110. if side=='LONG' else 90.
    a=event_account(side,now,peak)
    assert post_profit_lock_reason(a,'X',closed,side,now+239999)=='BLOCKED_BY_POST_PROFIT_COOLDOWN'
    reason='BLOCKED_BY_PEAK_EXHAUSTION_GATE' if side=='LONG' else 'BLOCKED_BY_TROUGH_EXHAUSTION_GATE'
    assert post_profit_lock_reason(a,'X',closed,side,now+240000)==reason
    assert post_profit_lock_reason(a,'X',closed,side,now+900000) is None
    closed=closed.copy();closed.loc[closed.index[-1],'close']=peak
    assert post_profit_lock_reason(a,'X',closed,side,now+240000)==reason
    closed.loc[closed.index[-1],'close']=peak+(1 if side=='LONG' else -1)
    assert post_profit_lock_reason(a,'X',closed,side,now+240000) is None
    assert post_profit_lock_reason(a,'X',closed,'SHORT' if side=='LONG' else 'LONG',now+1) is None


@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_midline_reset_requires_post_exit_cross(side):
    now=1000000.;a=event_account(side,now,110. if side=='LONG' else 90.)
    sign=1 if side=='LONG' else -1
    closed=pd.DataFrame([dict(timestamp=now-60000,close=100+sign,kc_middle=100),
                         dict(timestamp=now+60000,close=100-sign,kc_middle=100)])
    assert post_profit_lock_reason(a,'X',closed,side,now+240000) is None
    closed['timestamp']-=180000
    assert post_profit_lock_reason(a,'X',closed,side,now+240000) is not None


def verified_lock(position):
    position.update(
        breakeven_locked=True,
        breakeven_price=101.0 if position['side'] == 'LONG' else 99.0,
    )
    return 'EXIT_BY_RATCHET_PROFIT_LOCK'


@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_actual_paper_profit_lock_close_persists_event_and_reload(tmp_path,monkeypatch,side):
    import core.paper_account as pm
    monkeypatch.setattr(pm,'STATE_FILE',str(tmp_path/'paper.json'))
    a=pm.PaperAccount();p=position(side);tick(p,.1,61000);tick(p,.075,62000)
    a.positions['CAP/USDT']=p;a.position_meta['CAP/USDT']={STATE_KEY:copy.deepcopy(p[STATE_KEY])}
    reason = verified_lock(p)
    assert asyncio.run(a.close_position(
        'CAP/USDT',100.75 if side=='LONG' else 99.25,reason,is_manual=True,
    ))
    b=pm.PaperAccount()
    event=b.trades[0]
    assert event['last_profit_exit_side']==side
    assert event['last_profit_exit_peak_price']==110. if side=='LONG' else event['last_profit_exit_peak_price']==90.
    assert post_profit_lock_reason(b,'CAP/USDT',frame_for(side).iloc[:-1],side)=='BLOCKED_BY_POST_PROFIT_COOLDOWN'


@pytest.mark.parametrize('order,expected',[
    ({'status':'closed','filled':1},True),
    ({'info':{'status':'FILLED','executedQty':'1'}},True),
    ({'status':'open','filled':.5},False),
    ({'status':'closed','filled':.5},False),
    ({'id':'unknown'},False),
])
def test_unconfirmed_or_partial_close_cannot_record_event(order,expected):
    from core.services.post_profit_lock_gate import confirmed_full_close
    assert confirmed_full_close(order,1)==expected


@pytest.mark.parametrize('filled',[True,False])
def test_real_testnet_close_only_records_confirmed_event(tmp_path,monkeypatch,filled):
    from unittest.mock import AsyncMock
    import core.testnet_account as tm
    from core.gates.trend_riding_strategy import AntiSlippageOrderExecutor
    monkeypatch.setattr(tm,'notify_email',lambda *a,**k:None)
    from test_testnet_account import FakeTestnetExchange
    a=tm.BinanceTestnetAccount(FakeTestnetExchange(),state_file=str(tmp_path/'testnet.json'))
    p=position('LONG');tick(p,.1,61000);tick(p,.075,62000)
    reason = verified_lock(p)
    a.positions['CAP/USDT']=p
    a.position_meta['CAP/USDT']={STATE_KEY:copy.deepcopy(p[STATE_KEY])}
    a._cancel_all_orders=AsyncMock()
    a._send_order=AsyncMock(return_value=dict(id='test',status='closed' if filled else 'open',
                                            filled=1. if filled else .5,average=100.75))
    order = dict(
        id='test',
        status='closed' if filled else 'open',
        filled=1. if filled else .5,
        average=100.75,
    )
    monkeypatch.setattr(
        AntiSlippageOrderExecutor,
        'execute_confirmed_limit_close',
        AsyncMock(return_value=(filled, order)),
    )
    a.refresh=AsyncMock()
    success=asyncio.run(a.close_position(
        'CAP/USDT',100.75,f'Channel Swing {reason}',is_manual=True,
    ))
    assert success is filled
    if filled:
        assert a.trades[0]['last_profit_exit_peak_price']==110.
        data=__import__('json').load(open(tmp_path/'testnet.json'))
        assert data['trades'][0]['last_profit_exit_side']=='LONG'
    else:
        assert 'CAP/USDT' in a.positions
        assert not a.trades
