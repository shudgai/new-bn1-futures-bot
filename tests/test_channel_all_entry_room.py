"""Structural room no longer blocks fresh, cached or reentry orders."""
from unittest.mock import AsyncMock
import pytest
from core.channel_entry_room import entry_room
from core.channel_outer_entry import aligned_entry_ready
from channel_test_frames import closed_outer_entry_frame
from test_channel_swing_execution import _execution_engine, SYMBOL

@pytest.fixture
def anyio_backend(): return 'asyncio'

@pytest.mark.anyio
@pytest.mark.parametrize('side',['LONG','SHORT'])
@pytest.mark.parametrize('cached',[False,True])
@pytest.mark.parametrize('reentry',[False,True])
@pytest.mark.parametrize('target',['far','near','missing'])
async def test_all_routes_ignore_room(side,cached,reentry,target,monkeypatch):
    f=closed_outer_entry_frame(side);price=float(f.iloc[-1]['close'])
    sign=1 if side=='LONG' else -1
    rail='high' if side=='LONG' else 'low'
    if target!='missing': f.loc[f.index[5],rail]=price+sign*(3. if target=='far' else .01)
    assert aligned_entry_ready(f,price,side)
    e=_execution_engine(f,side,True);e.account.positions.clear();e.account.save_state=lambda:None
    e.tickers[SYMBOL]=price;e._abnormal_market_entry_allowed=lambda *a,**k:True
    snapshot=dict(frame=f,price=price,kc_upper=float(f.iloc[-1]['kc_upper']),kc_lower=float(f.iloc[-1]['kc_lower']))
    e._fresh_channel_entry_snapshot=AsyncMock(return_value=snapshot)
    monkeypatch.setattr('core.engine.DEFAULT_SYMBOLS',[SYMBOL])
    signal=dict(side=side,score=100,entry_mode='CHANNEL_SWING',action='ENTER_MARKET',reason='room validation',
                profit_room_pct=99.,estimated_profit_target=999.)
    if reentry:signal['profit_reentry_token']='old-profit'
    result=await e._place_structured_entry(SYMBOL,signal,price,channel_snapshot=snapshot if cached else None)
    assert result is True,e.account.logs
    assert len(e.account.events)==1
    assert signal['profit_room_checked'] is False
    assert 'estimated_profit_target' not in signal
    assert 'profit_room_pct' not in signal

@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_live_extreme_cannot_supply_target(side):
    f=closed_outer_entry_frame(side);price=float(f.iloc[-1]['close'])
    f.loc[f.index[-1],'high' if side=='LONG' else 'low']=150. if side=='LONG' else 50.
    r=entry_room(f,price,side,.0005,.0001,.0015)
    assert not r['allowed'] and r['reason']=='KC_PROFIT_TARGET_UNAVAILABLE'
