import asyncio
import copy
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock,Mock
import pytest
from core.engine import TradingEngine
from core.services.entry_contract import evaluate_entry_contract
from core.services.entry_firewall import validate_account_entry
from core.services.impulse_breakout import impulse_entry,reverse_close_fields,reverse_receipt,REASONS
from core.services.exits.peak_trailing_exit import evaluate_peak_trailing,STATE_KEY,migrate_peak_state,ABNORMAL_REASON
from test_strict_entry_gates_live import frame_for
from test_kc_outer_pivot_strategy import position as old_position,snapshot


def impulse_frame(side):
    f=frame_for(side)
    f.loc[f.index[-2],['open','close','high','low']]=[100.,100.1,101.,99.] if side=='LONG' else [100.,99.9,101.,99.]
    f.loc[f.index[-1],'open']=100.
    f.loc[f.index[-1],'low']=min(100.,float(f.iloc[-1].low))
    f.loc[f.index[-1],'high']=max(100.,float(f.iloc[-1].high))
    # Deliberately lagging MA/KC slope; impulse must not wait for them.
    f.loc[f.index[-1],'ma5']=100.
    f.loc[f.index[-1],'ma15']=100.
    return f


@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_first_impulse_bypasses_lag_but_revalidates_and_respects_profit_gate(side):
    f=impulse_frame(side);a=SimpleNamespace(positions={},trades=[],entry_frame_provider=AsyncMock(return_value=f))
    d=evaluate_entry_contract(f,account=a,symbol='CAP/USDT')
    assert d['type']=='KC_IMPULSE_BREAKOUT_'+side
    assert d['intrabar']
    snap=dict(d,symbol='CAP/USDT',side=side,signal_code=d['type'],signal_id=d['pending_signal_id'],
              candidate_bar_id=d['confirmation_bar_id'],closed_bar=d['confirmation_bar_id'])
    ctx=dict(entry_signal_code=d['type'],signal_id=d['pending_signal_id'],candidate_bar_id=d['confirmation_bar_id'],
             channel_confirmation_bar_id=d['confirmation_bar_id'],entry_snapshot=snap)
    assert asyncio.run(validate_account_entry(a,'CAP/USDT',side,ctx))['type']==d['type']
    a.trades=[dict(symbol='CAP/USDT',side=side,action='CLOSE_'+side,status='CLOSED',last_profit_exit_side=side,
                   last_profit_exit_timestamp=time.time()*1000,last_profit_exit_peak_price=110. if side=='LONG' else 90.)]
    diagnostics={}
    assert evaluate_entry_contract(f,account=a,symbol='CAP/USDT',diagnostics=diagnostics) is None
    assert diagnostics['reason']=='BLOCKED_BY_POST_PROFIT_COOLDOWN'
    a.trades=[]
    f.loc[f.index[-1],'close']=100.
    with pytest.raises(ValueError,match='BLOCKED_IMPULSE_REVALIDATION'):
        asyncio.run(validate_account_entry(a,'CAP/USDT',side,ctx))


@pytest.mark.parametrize('side',['LONG','SHORT'])
@pytest.mark.parametrize('outcome',['fill','failed','partial','retreat','race'])
def test_actual_engine_reverse_waits_for_close_and_consumes_once(side,outcome,monkeypatch):
    symbol='CAP/USDT';f=impulse_frame(side);quote=float(f.iloc[-1].close)
    old_side='SHORT' if side=='LONG' else 'LONG'
    p=dict(side=old_side,entry_price=100.,qty=1.,margin=10.,leverage=10.,
           open_timestamp=time.time()-30,entry_mode='CHANNEL_SWING')
    a=SimpleNamespace(positions={symbol:p},position_meta={},trades=[],save_state=Mock(),log=Mock(),logs=[],
                      pending_limit_orders={},get_wallet_balance=lambda:100.,get_available_balance=lambda:100.)
    e=object.__new__(TradingEngine);e.is_running=True;e.account=a;e._channel_exit_frames={symbol:f}
    e.tickers={symbol:quote};e._entry_boundary_frame=AsyncMock(return_value=f)
    e._execution_price_is_safe=AsyncMock(return_value=True);e._abnormal_market_entry_allowed=Mock(return_value=True)
    e.symbol_rotation=SimpleNamespace(get_dynamic_leverage=lambda *x:2.)
    monkeypatch.setattr('core.config.is_entry_disabled',lambda _:False)
    monkeypatch.setattr('core.services.pre_entry_space_shadow.record_pre_entry_space_shadow',lambda **kw:None)
    # Old fill in this live candle normally blocks another entry.
    a.trades.append(dict(id=1,symbol=symbol,side=old_side,action='OPEN_'+old_side,
                         channel_confirmation_bar_id=float(f.iloc[-1].timestamp)))
    async def close(symbol,price,reason,**kw):
        if outcome=='failed':return False
        if outcome=='partial':return True # insufficient: old position still exists
        old=a.positions.pop(symbol)
        a.trades.insert(0,dict(id=int(time.time()*1000),symbol=symbol,side=old_side,status='CLOSED',
                              action='CLOSE_'+old_side,reason=reason,**reverse_close_fields(old,reason,time.time()*1000)))
        if outcome=='retreat':
            f.loc[f.index[-1],'close']=100.;e.tickers[symbol]=100.
        if outcome=='race':a.positions[symbol]=dict(side=old_side)
        return True
    async def opened(**kwargs):
        assert symbol not in a.positions
        await validate_account_entry(a,symbol,side,kwargs['entry_context'])
        a.positions[symbol]=dict(side=side)
        return True
    a.close_position=AsyncMock(side_effect=close);a.open_position=AsyncMock(side_effect=opened)
    async def run():
        await asyncio.gather(*(e._try_reverse_on_breakout(symbol,quote,time.time()*1000) for _ in range(3)))
    asyncio.run(run())
    if outcome=='fill':
        a.open_position.assert_awaited_once()
        assert a.positions[symbol]['side']==side
        assert next(t for t in a.trades if t.get('status')=='CLOSED')['reverse_entry_consumed']
    else:
        a.open_position.assert_not_awaited()
    if outcome in ('fill','retreat'):a.close_position.assert_awaited_once()


@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_unarmed_pivot_holds_supported_lifeline_and_retires_pending(side):
    p=old_position(side,'CAP/USDT');p['margin']=100.
    data=snapshot(side,ma15=99. if side=='LONG' else 101.,kc_middle=100.)
    price=101. if side=='LONG' else 99.
    assert evaluate_peak_trailing(p,price,data,fee=0.,slippage=0.) is None
    assert not p[STATE_KEY].get('pending')
    assert evaluate_peak_trailing(p,98. if side=='LONG' else 102.,data,fee=0.,slippage=0.)['trigger']=='THREE_POINT_PIVOT'


@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_actual_paper_reverse_receipt_and_new_position_survive_restart(tmp_path,monkeypatch,side):
    import core.paper_account as pm
    monkeypatch.setattr(pm,'STATE_FILE',str(tmp_path/'paper.json'))
    monkeypatch.setattr('core.config.is_entry_disabled',lambda _:False)
    monkeypatch.setattr('core.services.pre_entry_space_shadow.record_pre_entry_space_shadow',lambda **kw:None)
    a=pm.PaperAccount();a.balance=100.
    symbol='CAP/USDT';old='SHORT' if side=='LONG' else 'LONG'
    a.positions[symbol]=dict(symbol=symbol,side=old,entry_price=100.,qty=1.,margin=10.,leverage=10.,
                             open_timestamp=time.time()-30,entry_mode='CHANNEL_SWING')
    f=impulse_frame(side);e=object.__new__(TradingEngine);e.is_running=True;e.account=a
    e._channel_exit_frames={symbol:f};e.tickers={symbol:float(f.iloc[-1].close)}
    e._entry_boundary_frame=AsyncMock(return_value=f)
    e._execution_price_is_safe=AsyncMock(return_value=True)
    e._abnormal_market_entry_allowed=Mock(return_value=True)
    e.symbol_rotation=SimpleNamespace(get_dynamic_leverage=lambda *x:2.)
    assert asyncio.run(e._try_reverse_on_breakout(symbol,float(f.iloc[-1].close),time.time()*1000))
    b=pm.PaperAccount()
    assert b.positions[symbol]['side']==side
    receipt=next(t for t in b.trades if t.get('reason')==REASONS[side])
    assert receipt['reverse_entry_consumed']
    assert b.positions[symbol]['entry_snapshot']['reverse_close_trade_id']==receipt['id']


@pytest.mark.parametrize('filled',[True,False])
def test_testnet_reverse_requires_full_close_receipt(tmp_path,monkeypatch,filled):
    import core.testnet_account as tm
    from test_testnet_account import FakeTestnetExchange
    monkeypatch.setattr(tm,'notify_email',lambda *a,**kw:None)
    a=tm.BinanceTestnetAccount(FakeTestnetExchange(),state_file=str(tmp_path/'testnet.json'))
    p=dict(side='SHORT',entry_price=100.,qty=1.,margin=10.,open_timestamp=time.time()-30,
           entry_mode='CHANNEL_SWING',reverse_breakout_pending=dict(side='LONG',live_bar_ms=60000.))
    a.positions['CAP/USDT']=p;a.position_meta['CAP/USDT']={}
    a._cancel_all_orders=AsyncMock();a.refresh=AsyncMock()
    a._send_order=AsyncMock(return_value=dict(status='closed' if filled else 'open',filled=1 if filled else .5,average=103.))
    assert asyncio.run(a.close_position('CAP/USDT',103.,REASONS['LONG'],is_manual=True)) is filled
    if filled:
        assert a.trades[0]['reverse_entry_side']=='LONG'
    else:
        assert 'CAP/USDT' in a.positions and not a.trades


def test_receipt_restart_retry_and_expiry():
    side='LONG';f=impulse_frame(side);d=impulse_entry(f,float(f.iloc[-1].close),'CAP/USDT')
    closed=dict(id=123,symbol='CAP/USDT',side='SHORT',action='CLOSE_SHORT',status='CLOSED',
                reason=REASONS[side],reverse_entry_side=side,
                reverse_breakout_bar_ms=float(f.iloc[-1].timestamp))
    a=SimpleNamespace(positions={},trades=[closed])
    restored=copy.deepcopy(a)
    decision=evaluate_entry_contract(f,account=restored,symbol='CAP/USDT',code=d['type'])
    assert decision['reverse_close_trade_id']==123
    restored.trades[0]['reverse_entry_consumed']=True
    assert reverse_receipt(restored,'CAP/USDT',d) is None
    restored.trades[0]['reverse_entry_consumed']=False
    d['strict_gate_evidence']['live_bar_ms']+=60000
    assert reverse_receipt(restored,'CAP/USDT',d) is None


def test_legacy_pivot_pending_is_revalidated_without_erasing_roi_peak():
    p=old_position('LONG','CAP/USDT')
    migrate_peak_state(p)
    p[STATE_KEY].pop('lifeline_policy_version',None)
    p[STATE_KEY].update(pending=ABNORMAL_REASON,trigger='THREE_POINT_PIVOT',tiered_roi_peak=.12)
    migrate_peak_state(p)
    assert not p[STATE_KEY].get('pending')
    assert p[STATE_KEY]['tiered_roi_peak']==.12


@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_impulse_half_atr_boundary_opposite_gap_and_stale_bar(side):
    f=impulse_frame(side);quote=float(f.iloc[-1].close)
    f.loc[f.index[-2],'atr']=6.
    assert impulse_entry(f,quote,'CAP/USDT')
    f.loc[f.index[-2],'atr']=6.0001
    assert impulse_entry(f,quote,'CAP/USDT') is None
    f=impulse_frame(side)
    f.loc[f.index[-1],'open']=97. if side=='LONG' else 103.
    f.loc[f.index[-1],'low']=min(float(f.iloc[-1].low),float(f.iloc[-1].open))
    f.loc[f.index[-1],'high']=max(float(f.iloc[-1].high),float(f.iloc[-1].open))
    assert impulse_entry(f,quote,'CAP/USDT') is None
    f=impulse_frame(side)
    f.loc[f.index[-1],'timestamp']+=60000
    assert impulse_entry(f,quote,'CAP/USDT') is None
