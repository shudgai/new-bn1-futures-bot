import copy
import pytest
from unittest.mock import AsyncMock
from core.channel_direct_reverse import authorized
from core.channel_outer_entry import ck_direction
from test_channel_ck_reverse import setup, SYMBOL

@pytest.fixture
def anyio_backend(): return 'asyncio'


def prepare(side, monkeypatch):
    f,p,e=setup(side,monkeypatch)
    e._channel_entry_quote_times={SYMBOL:1202.}
    e._ck_reverse_new_leg_halted=lambda:False
    opened=e.account.open_position
    async def record_open(**kw):
        ok=await opened(**kw)
        if ok:
            e.account.positions[SYMBOL].update(kw['entry_context'],entry_price=kw['price'],qty=1.,open_timestamp=1202.)
            e.account.trades.append(dict(symbol=SYMBOL,action='OPEN_'+kw['side'],id=1202001))
        return ok
    e.account.open_position=record_open
    return f,p,e


def ticket(e,old):
    side='SHORT' if old=='LONG' else 'LONG'
    t=dict(mode='direct_reverse',phase='closed',side=side,old_side=old,token='t',close_reason='Channel Swing PROFIT_PROTECTION t',close_requested_at_ms=1202000)
    e.account.positions.clear();e.account.channel_profit_reentries={SYMBOL:t}
    e.account.trades=[dict(symbol=SYMBOL,action='CLOSE_'+old,id=1202000,reason=t['close_reason'])]
    return t

@pytest.mark.anyio
@pytest.mark.parametrize('old',['LONG','SHORT'])
async def test_opposite_ck_and_ma_open_once_with_grace(old,monkeypatch):
    f,p,e=prepare(old,monkeypatch);t=ticket(e,old)
    assert ck_direction(f)==old
    # Real direction/MA/outer checks would reject the opposite side.
    e._fresh_channel_entry_snapshot=AsyncMock(return_value=dict(frame=f,price=p,kc_upper=float(f.iloc[-1]['kc_upper']),kc_lower=float(f.iloc[-1]['kc_lower'])))
    e.tickers[SYMBOL]=p
    await e._try_profit_reentry(SYMBOL,f,p,False)
    assert [x[0] for x in e.account.events]==['open'],e.account.logs
    assert e.account.positions[SYMBOL]['side']==t['side']
    assert e.account.positions[SYMBOL]['channel_reverse_wait_ck']
    assert not await e._try_ck_reverse(SYMBOL,f,p,False)
    assert len(e.account.events)==1
    assert not authorized(e.account,SYMBOL,{'side':t['side'],'profit_reentry_token':'t'},1202.)

@pytest.mark.parametrize('case',['missing','wrong_reason','manual','stop','consumed','expired','wrong_side'])
def test_ticket_requires_exact_fill(case):
    from types import SimpleNamespace
    e=SimpleNamespace(account=SimpleNamespace(positions={},trades=[],channel_profit_reentries={}))
    t=ticket(e,'LONG');sig=dict(side='SHORT',profit_reentry_token='t');now=1202.
    if case=='missing':e.account.trades=[]
    elif case=='wrong_reason':e.account.trades[0]['reason']='other'
    elif case in ('manual','stop'):
        t['close_reason']='手動平倉' if case=='manual' else 'Channel Swing HARD_STOP'
        e.account.trades[0]['reason']=t['close_reason']
    elif case=='consumed':e.account.trades.append(dict(symbol=SYMBOL,action='OPEN_SHORT',id=1202001))
    elif case=='expired':now=1260.
    elif case=='wrong_side':sig['side']='LONG'
    assert not authorized(e.account,SYMBOL,sig,now)

@pytest.mark.anyio
@pytest.mark.parametrize('side',['LONG','SHORT'])
async def test_ck_close_immediate_reverse_and_failure(side,monkeypatch):
    f,p,e=prepare(side,monkeypatch)
    assert await e._try_ck_reverse(SYMBOL,f,p,False)
    assert [x[0] for x in e.account.events]==['close','open'],e.account.logs
    f,p,e=prepare(side,monkeypatch);e.account.close_succeeds=False
    assert await e._try_ck_reverse(SYMBOL,f,p,False)
    assert [x[0] for x in e.account.events]==['close']
    assert e.account.channel_profit_reentries[SYMBOL]['phase']=='closing'

@pytest.mark.anyio
@pytest.mark.parametrize('old',['LONG','SHORT'])
@pytest.mark.parametrize('block',['stale','daily','adverse','open_failure'])
async def test_risks_and_failed_open_retry(old,block,monkeypatch):
    f,p,e=prepare(old,monkeypatch);t=ticket(e,old)
    e._fresh_channel_entry_snapshot=AsyncMock(return_value=dict(frame=f,price=p,kc_upper=float(f.iloc[-1]['kc_upper']),kc_lower=float(f.iloc[-1]['kc_lower'])))
    if block=='stale':e._channel_entry_quote_times[SYMBOL]=1190.
    if block=='adverse':f.loc[f.index[-1],'open']=p+(-10 if t['side']=='SHORT' else 10)
    if block=='open_failure':e.account.open_position=AsyncMock(return_value=False)
    await e._try_profit_reentry(SYMBOL,f,p,block=='daily')
    assert not e.account.positions
    assert SYMBOL in e.account.channel_profit_reentries
    if block=='open_failure':
        assert e.account.open_position.await_count==1
        await e._try_profit_reentry(SYMBOL,f,p,False)
        assert e.account.open_position.await_count==2

@pytest.mark.anyio
@pytest.mark.parametrize('side',['LONG','SHORT'])
async def test_grace_persists_until_alignment_then_ck_exit(side,monkeypatch):
    f,p,e=prepare(side,monkeypatch)
    pos=e.account.positions[SYMBOL];pos['channel_reverse_wait_ck']=True
    assert not await e._try_ck_reverse(SYMBOL,f,p,False)
    # Reload position flag from metadata, then observe CK aligned.
    e.account.position_meta[SYMBOL]={'channel_reverse_wait_ck':True}
    pos.pop('channel_reverse_wait_ck')
    aligned=f.copy();aligned.loc[aligned.index[-2],'kc_middle']=float(aligned.iloc[-3]['kc_middle'])+(-.1 if side=='LONG' else .1)
    aligned.loc[aligned.index[-2],['kc_upper','kc_lower']]=aligned.iloc[-3][['kc_upper','kc_lower']].values
    assert ck_direction(aligned)==pos['side']
    assert not await e._try_ck_reverse(SYMBOL,aligned,p,False)
    assert pos['channel_reverse_wait_ck'] is False
    assert await e._try_ck_reverse(SYMBOL,f,p,False)
    assert e.account.events[0][0]=='close'

@pytest.mark.anyio
@pytest.mark.parametrize('old',['LONG','SHORT'])
async def test_fixed_profit_exit_reverses_on_quote_path(old,monkeypatch):
    from core.channel_profit_protection import protection
    from test_channel_fixed_steps import quote
    f,p,e=prepare(old,monkeypatch)
    e.account.positions[SYMBOL].update(side=old,entry_price=100.,qty=1.,open_timestamp=1.)
    price=quote(1.99,old)
    # Keep the current body ordinary while the fixed net floor is crossed.
    f.loc[f.index[-1],['open','close','high','low']]=[price,price,price+.01,price-.01]
    e.tickers[SYMBOL]=price
    protection(e.account.positions[SYMBOL],quote(4.01,old),.0005,.0001)
    async def closed(symbol,px,reason,is_manual=False):
        e.account.events.append(('close',symbol,px,reason))
        e.account.positions.pop(symbol)
        e.account.trades.append(dict(symbol=symbol,action='CLOSE_'+old,id=1202000,reason=reason))
        return True
    e.account.close_position=closed
    e._fresh_channel_entry_snapshot=AsyncMock(return_value=dict(frame=f,price=price,kc_upper=float(f.iloc[-1]['kc_upper']),kc_lower=float(f.iloc[-1]['kc_lower'])))
    await e._process_single_symbol_locked(SYMBOL,1202.,None,False,exit_frame=f,exit_quote=price,exit_only=True)
    assert [x[0] for x in e.account.events]==['close','open'],e.account.logs
    assert e.account.positions[SYMBOL]['side']!=old

@pytest.mark.parametrize('account_module',['core.paper_account','core.testnet_account'])
def test_grace_flag_is_persisted_by_accounts(account_module):
    import importlib
    assert 'channel_reverse_wait_ck' in importlib.import_module(account_module).ENTRY_CONTEXT_KEYS

@pytest.mark.anyio
@pytest.mark.parametrize('old',['LONG','SHORT'])
async def test_restart_recovers_only_matched_close_and_expiry(old,monkeypatch):
    f,p,e=prepare(old,monkeypatch);t=ticket(e,old);t['phase']='closing'
    e.account.channel_profit_reentries=copy.deepcopy(e.account.channel_profit_reentries)
    e._fresh_channel_entry_snapshot=AsyncMock(return_value=dict(frame=f,price=p,kc_upper=float(f.iloc[-1]['kc_upper']),kc_lower=float(f.iloc[-1]['kc_lower'])))
    await e._try_profit_reentry(SYMBOL,f,p,False)
    assert [x[0] for x in e.account.events]==['open']
    e.account.events.clear();ticket(e,old)
    monkeypatch.setattr('core.engine.time.time',lambda:1260.)
    await e._try_profit_reentry(SYMBOL,f,p,False)
    assert not e.account.events
    assert SYMBOL not in e.account.channel_profit_reentries
