"""Reject wick-only profit reentry and document actual stop-gap fills."""
import json
from pathlib import Path
from unittest.mock import AsyncMock
import pandas as pd
import pytest
from core.channel_profit_protection import protection, reentry_gate
from test_channel_requested_fixes import market
from test_channel_swing_execution import _execution_engine, SYMBOL

@pytest.fixture
def anyio_backend():
    return 'asyncio'

def ready_frame():
    f=market()
    f['timestamp']=f.index*60000
    f.loc[18,['open','close','high','low']]=[101.5,103.,103.1,101.4]
    f.loc[19,['open','close','high','low']]=[103.,103.2,103.3,102.9]
    return f

def ticket():
    return dict(side='LONG',phase='closed',token='test',pulled_back_inside=True,pullback_bar=18*60000)

@pytest.mark.parametrize('invalid',['wick','red','doji','small','old','gap','no_push','inside','bad','legacy','same_bar'])
def test_invalid_recovery_cannot_buy(invalid):
    f=ready_frame();t=ticket();price=103.2
    if invalid=='wick': f.loc[18,'close']=101.9
    if invalid=='red': f.loc[18,['open','close']]=[103.,101.5]
    if invalid=='doji': f.loc[18,'close']=f.loc[18,'open']
    if invalid=='small': f.loc[18,['open','close','high','low']]=[101.99,102.01,103.,101.]
    if invalid=='old': t['pullback_bar']=19*60000
    if invalid=='gap': f.loc[19,'open']=102.
    if invalid=='no_push': price=103.
    if invalid=='inside': price=101.9
    if invalid=='bad': f.loc[18,'close']=float('nan')
    if invalid=='legacy': t.pop('pullback_bar')
    if invalid=='same_bar': f.loc[19,'timestamp']=18*60000
    assert reentry_gate(t,f,price)=='wait'

def test_confirmed_body_and_live_successor_survive_restart():
    t=json.loads(json.dumps(ticket()))
    assert reentry_gate(t,ready_frame(),103.2)=='ready'

def test_new_pullback_invalidates_previous_closed_breakout():
    f=ready_frame();t=ticket()
    assert reentry_gate(t,f,101.9)=='wait'
    assert t['pullback_bar']==19*60000
    assert reentry_gate(t,f,103.2)=='wait'

@pytest.mark.anyio
async def test_historical_wick_reentry_rejected_at_scan_and_order_validation():
    f=pd.DataFrame(json.loads((Path(__file__).parent/'fixtures/lobster_wick_reentry_20260909.json').read_text()))
    f['timestamp']=f['time']*1000
    # Actual signal price from 22:57:03; final candle later became red.
    price=.065917
    e=_execution_engine(f,'LONG',True);e.account.positions.clear()
    e.account.save_state=lambda:None;e.tickers[SYMBOL]=price
    t=dict(side='LONG',phase='closed',token='actual',pulled_back_inside=True,pullback_bar=1788965760000)
    e.account.channel_profit_reentries={SYMBOL:t}
    e._place_structured_entry=AsyncMock(return_value=True)
    await e._try_profit_reentry(SYMBOL,f,price,False)
    e._place_structured_entry.assert_not_awaited()
    assert await e._fresh_channel_entry_snapshot(SYMBOL,'LONG',profit_reentry_token='actual') is None

@pytest.mark.anyio
async def test_valid_reentry_still_checks_latest_price():
    f=ready_frame();e=_execution_engine(f,'LONG',True);e.account.positions.clear()
    e.account.save_state=lambda:None;e.tickers[SYMBOL]=103.2
    e.account.channel_profit_reentries={SYMBOL:ticket()}
    assert await e._fresh_channel_entry_snapshot(SYMBOL,'LONG',profit_reentry_token='test') is not None
    e.tickers[SYMBOL]=101.9
    assert await e._fresh_channel_entry_snapshot(SYMBOL,'LONG',profit_reentry_token='test') is None

def test_actual_profit_protection_gap_can_finish_negative_after_fees():
    p=dict(side='LONG',entry_price=.06611461,qty=5646.61311307,open_timestamp=1788965692.5069396)
    peak=protection(p,p['entry_price']+1.4703/p['qty'],.0005,.0001)
    assert p['channel_profit_protection']['armed']
    assert peak['stop_price']==pytest.approx(.06636458085,abs=2e-9)
    result=protection(p,.06616938/(1-.0001),.0005,.0001)
    assert result['triggered']
    assert result['net_pnl']==pytest.approx(-.0642,abs=.0001)
    assert result['retracement_fraction']==.30

def test_red_candle_above_protection_line_does_not_close():
    p=dict(side='LONG',entry_price=100.,qty=2.,open_timestamp=1.)
    protection(p,105.,.0005,.0001)
    f=market();f.loc[19,'open']=105.
    assert not protection(p,104.8,.0005,.0001,frame=f)['triggered']
