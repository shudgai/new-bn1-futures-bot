import asyncio
import copy
import time
from types import SimpleNamespace
from unittest.mock import Mock, AsyncMock
import pytest
from core.services.auto_reverse import try_auto_reverse, matched_ticket, reverse_quantity, KEY, CLOSE_PREFIX


def reverse_history(side):
    import pandas as pd
    sign=1 if side=='LONG' else -1
    rows=[]
    for i in range(20):
        c=100+sign*i*.1
        rows.append(dict(timestamp=(i+1)*60000,open=c-sign*.03,close=c,high=c+.15,low=c-.15,
                         atr=.2,ma5=c-sign*.2,ma15=c-sign*.7,kc_middle=c,
                         kc_upper=c+1,kc_lower=c-1,is_closed=i<19))
    return pd.DataFrame(rows)


@pytest.fixture
def reverse_setup(monkeypatch):
    now=int(time.time()//60)*60000
    monkeypatch.setattr(time,'time',lambda:(now+10000)/1000)
    def make(symbol, old_side):
        side='SHORT' if old_side=='LONG' else 'LONG'
        f=reverse_history(side);f['timestamp']+=now-1200000
        # Reversal now needs a real live rail breakout, never an MA-only entry.
        i=f.index[-1]
        if side=='SHORT':
            f.loc[i,['open','high','low','kc_upper','kc_middle','kc_lower']]=[98.3,98.31,98.09,100.2,99.2,98.2]
        else:
            f.loc[i,['open','high','low','kc_upper','kc_middle','kc_lower']]=[101.7,101.91,101.69,101.8,100.8,99.8]
        f.attrs['entry_finality_verified']=True
        q=float(f.iloc[-1].close)
        p=dict(side=old_side,entry_price=q,qty=.01,leverage=2,margin=.5,
               open_timestamp=(now-60000)/1000,entry_mode='CHANNEL_SWING',entry_atr=.2)
        a=SimpleNamespace(positions={symbol:p},position_meta={},pending_limit_orders={},closing_lock=set(),
                          trades=[],last_closed_at={},save_state=Mock(),log=Mock(),realized_pnl=0.,balance=99.,
                          get_wallet_balance=lambda:100.,get_available_balance=lambda:99.)
        async def close(sym,price,reason):
            held=a.positions.pop(sym)
            a.trades.insert(0,dict(id=now+10000,symbol=sym,action='CLOSE_'+held['side'],
                                  status='CLOSED',reason=reason,qty=held['qty']))
            a.last_closed_at[sym]=(now+10000)/1000
            return True
        a.close_position=AsyncMock(side_effect=close)
        e=SimpleNamespace(account=a,is_running=True,_entry_boundary_frame=AsyncMock(return_value=f),
                          _execution_price_is_safe=AsyncMock(return_value=True),
                          _half_wallet_entry_margin=lambda *args:50.)
        async def place(sym,signal,price):
            ticket=matched_ticket(a,sym,signal['side'])
            assert ticket and signal['auto_reverse_token']==ticket['token']
            qty=reverse_quantity(a,sym,signal['side'],signal,1.)
            a.positions[sym]=dict(side=signal['side'],qty=qty,leverage=ticket['leverage'])
            a.trades.insert(0,dict(id=now+10001,symbol=sym,action='OPEN_'+signal['side']))
            return True
        e._place_structured_entry=AsyncMock(side_effect=place)
        a._structure_close_engine=e
        a.entry_frame_provider=e._entry_boundary_frame
        from core.paper_account import PaperAccount
        async def reverse(*args):
            return await PaperAccount.reverse_position(a,*args)
        a.reverse_position=AsyncMock(side_effect=reverse)
        return e,f,q,p
    return make


@pytest.mark.parametrize('symbol', ['龙虾/USDT', 'CAP/USDT'])
@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_one_operation_reverses_without_close_or_open(reverse_setup, symbol, side):
    e, f, q, old = reverse_setup(symbol, side)
    before = e.account.balance
    assert asyncio.run(try_auto_reverse(e, symbol, f, q))
    a = e.account
    new = a.positions[symbol]
    assert new['side'] != side
    assert new['qty'] == old['qty']
    assert new['leverage'] == old['leverage']
    assert new['entry_atr'] == .2
    assert 'peak_trailing_state' not in new
    a.close_position.assert_not_awaited()
    e._place_structured_entry.assert_not_awaited()
    assert len(a.trades) == 2
    assert a.trades[0]['reverse_id'] == a.trades[1]['reverse_id']
    from core.config import TAKER_FEE_RATE
    price = new['entry_price']
    raw = (1 if side == 'LONG' else -1)*(price-old['entry_price'])*old['qty']
    expected = before+old['margin']+raw-2*price*old['qty']*TAKER_FEE_RATE-new['margin']
    assert a.balance == pytest.approx(expected)
    assert not asyncio.run(try_auto_reverse(e, symbol, f, q))
    assert len(a.trades) == 2


@pytest.mark.parametrize('failure', ['same_side', 'no_data', 'conditions_changed', 'unsafe', 'budget', 'halted'])
def test_denial_keeps_original_position(reverse_setup, failure):
    e, f, q, old = reverse_setup('CAP/USDT', 'SHORT')
    if failure == 'same_side':old['side'] = 'LONG'
    elif failure == 'no_data':e._entry_boundary_frame.return_value = None
    elif failure == 'conditions_changed':
        changed = f.copy();changed.loc[19,'close'] = float(changed.loc[19,'open'])
        e._entry_boundary_frame.return_value = changed
    elif failure == 'unsafe':e._execution_price_is_safe.return_value = False
    elif failure == 'budget':e._half_wallet_entry_margin = lambda *args:.001
    else:e.is_running = False
    assert not asyncio.run(try_auto_reverse(e, 'CAP/USDT', f, q))
    assert e.account.positions['CAP/USDT'] is old
    assert not e.account.trades
    e.account.close_position.assert_not_awaited()


def test_concurrent_quotes_settle_once(reverse_setup):
    e, f, q, old = reverse_setup('CAP/USDT', 'LONG')
    async def run():
        return await asyncio.gather(*(try_auto_reverse(e,'CAP/USDT',f,q) for _ in range(3)))
    asyncio.run(run())
    assert e.account.reverse_position.await_count == 1
    assert len(e.account.trades) == 2


@pytest.fixture
def testnet_reverse(reverse_setup):
    def make(side='LONG'):
        from core.testnet_account import BinanceTestnetAccount
        e, f, q, old = reverse_setup('CAP/USDT', side)
        a = e.account
        signed = old['qty']*(1 if side=='LONG' else -1)
        a.exchange = SimpleNamespace(
            fapiPrivateGetPositionSideDual=AsyncMock(return_value={'dualSidePosition':False}),
            fapiPrivateV2GetPositionRisk=AsyncMock(return_value=[dict(symbol='CAPUSDT',positionSide='BOTH',positionAmt=signed)]),
            amount_to_precision=lambda symbol,qty:str(qty),
            fapiPrivateGetOrder=AsyncMock(),
            create_order=AsyncMock(return_value=dict(id='order-1',status='closed',filled=2*old['qty'],average=q)))
        a._raw_symbol=lambda symbol:symbol.replace('/','')
        a._cancel_all_orders=AsyncMock()
        a._send_order=lambda *args,**kw:BinanceTestnetAccount._send_order(a,*args,**kw)
        a._close_generation={}
        async def reverse(*args):return await BinanceTestnetAccount.reverse_position(a,*args)
        a.reverse_position=AsyncMock(side_effect=reverse)
        return e,f,q,old
    return make


@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_testnet_submits_one_double_size_market_order(testnet_reverse,side):
    e,f,q,old=testnet_reverse(side)
    assert asyncio.run(try_auto_reverse(e,'CAP/USDT',f,q))
    args=e.account.exchange.create_order.await_args.args
    assert args[:4]==('CAP/USDT','market','sell' if side=='LONG' else 'buy',2*old['qty'])
    assert args[5]['positionSide']=='BOTH'
    assert args[5]['reduceOnly'] is False
    assert args[5]['newClientOrderId'].startswith('rev_')
    assert e.account.exchange.create_order.await_count==1
    assert e.account.positions['CAP/USDT']['side']!=side
    e.account.close_position.assert_not_awaited()


@pytest.mark.parametrize('fault',['hedge','exchange_qty','precision','firewall'])
def test_testnet_rejects_before_submit(testnet_reverse,fault):
    e,f,q,old=testnet_reverse()
    a=e.account
    if fault=='hedge':a.exchange.fapiPrivateGetPositionSideDual.return_value={'dualSidePosition':True}
    elif fault=='exchange_qty':a.exchange.fapiPrivateV2GetPositionRisk.return_value=[]
    elif fault=='precision':a.exchange.amount_to_precision=lambda *args:'0.01'
    else:
        bad=f.copy();bad.attrs['entry_finality_verified']=False
        a.entry_frame_provider=AsyncMock(return_value=bad)
    assert not asyncio.run(try_auto_reverse(e,'CAP/USDT',f,q))
    assert a.positions['CAP/USDT'] is old
    a.exchange.create_order.assert_not_awaited()


def test_timeout_restart_and_bar_rollover_queries_without_resubmit(testnet_reverse,monkeypatch):
    e,f,q,old=testnet_reverse()
    a=e.account
    a.exchange.create_order.side_effect=TimeoutError('unknown execution')
    assert asyncio.run(try_auto_reverse(e,'CAP/USDT',f,q))
    ticket=a.position_meta[KEY]['CAP/USDT']
    assert ticket['phase']=='unknown'
    token=ticket['client_order_id']
    e._auto_reverse_locks={}
    monkeypatch.setattr(time,'time',lambda:(ticket['bar']+70000)/1000)
    a.exchange.fapiPrivateGetOrder.side_effect=TimeoutError('query unavailable')
    assert asyncio.run(try_auto_reverse(e,'CAP/USDT',f,q))
    assert ticket['phase']=='unknown'
    a.exchange.fapiPrivateGetOrder.side_effect=None
    a.exchange.fapiPrivateGetOrder.return_value=dict(status='FILLED',executedQty=2*old['qty'],avgPrice=q,orderId='recovered')
    monkeypatch.setattr(time,'time',lambda:(ticket['bar']+73000)/1000)
    assert asyncio.run(try_auto_reverse(e,'CAP/USDT',f,q))
    assert ticket['phase']=='consumed'
    assert a.positions['CAP/USDT']['side']=='SHORT'
    assert a.exchange.create_order.await_count==1
    assert a.exchange.fapiPrivateGetOrder.await_args.args[0]['origClientOrderId']==token
    assert len(a.trades)==2


@pytest.mark.parametrize('ratio',[.25,.5,.75])
def test_partial_fill_records_actual_position_without_topup(testnet_reverse,ratio):
    e,f,q,old=testnet_reverse()
    a=e.account
    filled=2*old['qty']*ratio
    a.exchange.create_order.return_value=dict(status='closed',filled=filled,average=q,id='partial')
    assert asyncio.run(try_auto_reverse(e,'CAP/USDT',f,q))
    assert a.position_meta[KEY]['CAP/USDT']['phase']=='partial'
    if ratio==.5:assert 'CAP/USDT' not in a.positions
    else:
        assert a.positions['CAP/USDT']['qty']==pytest.approx(abs(old['qty']-filled))
        assert a.positions['CAP/USDT']['side']==('LONG' if ratio<.5 else 'SHORT')
    assert not asyncio.run(try_auto_reverse(e,'CAP/USDT',f,q))
    assert a.exchange.create_order.await_count==1


def test_forged_direct_token_is_denied(reverse_setup):
    from core.services.entry_firewall import validate_account_entry
    e,f,q,old=reverse_setup('CAP/USDT','LONG')
    with pytest.raises(ValueError):
        asyncio.run(validate_account_entry(e.account,'CAP/USDT','SHORT',{'direct_reverse_token':'fake','is_manual':True}))


def test_explicit_exchange_rejection_does_not_flatten(testnet_reverse):
    import ccxt
    e,f,q,old=testnet_reverse()
    a=e.account
    a.exchange.create_order.side_effect=ccxt.InsufficientFunds('rejected')
    assert not asyncio.run(try_auto_reverse(e,'CAP/USDT',f,q))
    assert a.positions['CAP/USDT'] is old
    assert not a.trades
    assert a.position_meta[KEY]['CAP/USDT']['phase']=='rejected'


def test_unknown_order_blocks_ordinary_entry_across_bar_rollover(reverse_setup,monkeypatch):
    from core.services.entry_firewall import validate_account_entry
    e,f,q,old=reverse_setup('CAP/USDT','LONG')
    now=time.time()*1000
    e.account.position_meta[KEY]={'CAP/USDT':dict(mode='direct_netting_v1',phase='unknown',
                                               token='pending',bar=now-120000)}
    with pytest.raises(ValueError,match='反向成交狀態未確認'):
        asyncio.run(validate_account_entry(e.account,'CAP/USDT','SHORT',{'is_manual':True}))


def test_recovered_fill_settles_only_once(testnet_reverse):
    from core.services.direct_reverse import reconcile
    e,f,q,old=testnet_reverse()
    a=e.account
    assert asyncio.run(try_auto_reverse(e,'CAP/USDT',f,q))
    ticket=a.position_meta[KEY]['CAP/USDT']
    balance=a.realized_pnl
    a.exchange.fapiPrivateGetOrder.return_value=dict(status='FILLED',executedQty=2*old['qty'],avgPrice=q,orderId='order-1')
    assert asyncio.run(reconcile(a,'CAP/USDT',ticket))
    assert len(a.trades)==2
    assert a.realized_pnl==balance


def test_real_testnet_final_order_boundary(testnet_reverse,tmp_path,monkeypatch):
    from core.testnet_account import BinanceTestnetAccount
    e,f,q,old=testnet_reverse('SHORT')
    raw=e.account.exchange.create_order
    exchange=e.account.exchange
    monkeypatch.setattr(BinanceTestnetAccount,'_load_state',lambda self:None)
    a=BinanceTestnetAccount(exchange,state_file=str(tmp_path/'account.json'))
    a.positions={'CAP/USDT':old};a.balance=100.;a.available_balance=99.
    a.daily_loss_limit_hit=lambda:(False,0.)
    a._is_live_mainnet=lambda:False
    a._is_verified_reducing_order=lambda *args:False
    a.save_state=Mock();a.log=Mock();a._cancel_all_orders=AsyncMock()
    e.account=a;a._structure_close_engine=e;a.entry_frame_provider=e._entry_boundary_frame
    assert asyncio.run(try_auto_reverse(e,'CAP/USDT',f,q))
    assert raw.await_count==1
    assert raw.await_args.args[3]==2*old['qty']
    assert '_entry_context' not in raw.await_args.args[5]
    assert a.positions['CAP/USDT']['side']=='LONG'


def test_partial_fill_blocks_new_entry_after_rollover(testnet_reverse,monkeypatch):
    from core.services.entry_firewall import validate_account_entry
    e,f,q,old=testnet_reverse()
    a=e.account
    a.exchange.create_order.return_value=dict(status='closed',filled=old['qty'],average=q,id='partial-flat')
    assert asyncio.run(try_auto_reverse(e,'CAP/USDT',f,q))
    ticket=a.position_meta[KEY]['CAP/USDT']
    monkeypatch.setattr(time,'time',lambda:(ticket['bar']+70000)/1000)
    a.position_meta.pop('_entry_gate_halts')
    assert not asyncio.run(try_auto_reverse(e,'CAP/USDT',f,q))
    with pytest.raises(ValueError,match='反向部分成交'):
        asyncio.run(validate_account_entry(a,'CAP/USDT','SHORT',{}))
    assert a.exchange.create_order.await_count==1


def test_stale_exchange_refresh_preserves_new_reverse_and_halt(tmp_path,monkeypatch):
    from core.testnet_account import BinanceTestnetAccount
    monkeypatch.setattr(BinanceTestnetAccount,'_load_state',lambda self:None)
    exchange=SimpleNamespace(create_order=AsyncMock(),
        fapiPrivateV2GetBalance=AsyncMock(return_value=[dict(asset='USDT',balance='100',availableBalance='90')]))
    a=BinanceTestnetAccount(exchange,state_file=str(tmp_path/'account.json'))
    a.positions={'CAP/USDT':dict(side='LONG',qty=1.,entry_price=100.,open_timestamp=60.)}
    a._close_generation={}
    a._check_live_circuit_breaker=AsyncMock();a._check_daily_reset=lambda:None
    a._fetch_exchange_order_snapshot=AsyncMock();a.save_state=Mock()
    new=dict(side='SHORT',qty=1.,entry_price=99.,open_timestamp=70.,unrealized_pnl=0.)
    a.position_meta['_entry_gate_halts']={'CAP/USDT':dict(reason='DIRECT_REVERSE_PARTIAL_FILL')}
    async def stale_response():
        a.positions['CAP/USDT']=new
        a._close_generation['CAP/USDT']=1
        return [dict(symbol='CAPUSDT',positionAmt='1',positionSide='BOTH',entryPrice='100')]
    exchange.fapiPrivateV2GetPositionRisk=AsyncMock(side_effect=stale_response)
    asyncio.run(a.refresh(force=True))
    assert a.positions['CAP/USDT'] is new
    assert '_entry_gate_halts' in a.position_meta
