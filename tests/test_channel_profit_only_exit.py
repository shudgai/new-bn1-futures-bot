"""Profit-only exits keep protection while ignoring obsolete strategy triggers."""
import pytest
from core.engine import TradingEngine
from test_channel_protected_only_exit import market
from test_channel_swing_execution import _execution_engine, SYMBOL

@pytest.fixture
def anyio_backend():
    return 'asyncio'

@pytest.mark.anyio
@pytest.mark.parametrize('side',['LONG','SHORT'])
@pytest.mark.parametrize('case',['waterfall','closed_waterfall','double','single','normal'])
async def test_unarmed_holds_and_clears_old_pending_requests(side,case):
    f,price=market(side,case)
    e=_execution_engine(f,side,True);e.account.save_state=lambda:None
    p=e.account.positions[SYMBOL];p.update(entry_price=100.,qty=1.,open_timestamp=1.)
    stale=dict(channel_exception_exit_pending='EMERGENCY_EXIT_LIVE_ADVERSE_ABNORMAL',
               channel_live_ma3_turn_exit_pending=True,channel_outer_ma3_turn_exit_pending=True,
               channel_live_ma3_exit_pending=True,channel_live_ma3_favorable_bar=1.,channel_ma3_turn_observed_bar=1.)
    p.update(stale);e.account.position_meta[SYMBOL].update(stale)
    e.tickers[SYMBOL]=price
    await e._process_single_symbol(SYMBOL,1.,None,False)
    assert not e.account.events,e.account.logs
    assert SYMBOL in e.account.positions
    assert not set(stale)&set(p)
    assert not set(stale)&set(e.account.position_meta[SYMBOL])

@pytest.mark.anyio
@pytest.mark.parametrize('side',['LONG','SHORT'])
@pytest.mark.parametrize('success',[False,True])
async def test_armed_uses_twenty_percent_retracement_and_retries(side,success):
    f,_=market(side,'normal');e=_execution_engine(f,side,success)
    e.account.save_state=lambda:None
    p=e.account.positions[SYMBOL];p.update(entry_price=100.,qty=2.,open_timestamp=1.)
    sign=1 if side=='LONG' else -1
    for offset in (5.,4.4):
        # Huge opposite live body, but price remains above/below profit protection.
        price=100+sign*offset;f.loc[19,'open']=price+sign*10
        f.loc[19,'close']=price
        f['high']=f[['open','close']].max(axis=1)+.1
        f['low']=f[['open','close']].min(axis=1)-.1
        e.tickers[SYMBOL]=price
        await e._process_single_symbol(SYMBOL,1.,None,False)
        assert not e.account.events,e.account.logs
    assert p['channel_profit_protection']['peak_gross']==10.
    assert p['channel_profit_protection']['armed']
    e.tickers[SYMBOL]=100+sign*4
    await e._process_single_symbol(SYMBOL,2.,None,False)
    assert len(e.account.events)==1,e.account.logs
    assert 'PROFIT_PROTECTION' in e.account.events[0][3]
    if not success:
        await e._process_single_symbol(SYMBOL,33.,None,False)
        assert len(e.account.events)==2
        assert 'PROFIT_PROTECTION' in e.account.events[1][3]
