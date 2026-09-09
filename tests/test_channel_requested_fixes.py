"""User requested short middle exit, solid bodies and pullback reentry."""
from unittest.mock import AsyncMock
import pytest
from core.engine import TradingEngine
from core.channel_profit_protection import reentry_gate
from test_channel_swing_execution import _execution_engine, _narrow_channel_frame, SYMBOL

@pytest.fixture
def anyio_backend():
    return 'asyncio'

def market():
    f = _narrow_channel_frame()
    f['kc_upper'], f['kc_lower'] = 102., 98.
    f['high'], f['low'] = 100.2, 99.8
    return f

@pytest.mark.parametrize('side', ['SHORT', 'LONG'])
@pytest.mark.parametrize('move,opened,prior_body,expected', [
    (0.2, -3., 2., True),       # 80% of the four-point channel.
    (0.2, -0.4, .2, True),     # Three times prior average, below 80% width.
    (0.2, -0.3, .2, False),    # Small body beyond middle.
    (0., -4., 2., False),      # Exactly at middle is not past it.
    (-0.1, -4., 2., False),    # Long body has not passed middle.
    (0.2, 0.3, .2, False),     # Favorable candle beyond middle.
])
def test_adverse_long_body_must_pass_middle(side, move, opened, prior_body, expected):
    f=market();sign=1 if side=='SHORT' else -1
    f.loc[:18, 'close']=100.+prior_body
    f.loc[19, 'open']=100.+sign*opened
    result=TradingEngine._channel_swing_action(f,100.+sign*move,side,exit_net_profitable=False)
    assert (result['action']=='EXIT') is expected
    if expected:
        assert result['reason']==('KC_SHORT_LIVE_GREEN_MIDDLE_EXIT' if side=='SHORT'
                                  else 'KC_LONG_LIVE_RED_MIDDLE_EXIT')

@pytest.mark.anyio
@pytest.mark.parametrize('side', ['SHORT', 'LONG'])
@pytest.mark.parametrize('close_ok',[True,False])
async def test_scan_mirrored_middle_exit_is_not_suppressed(side,close_ok):
    f=market();sign=1 if side=='SHORT' else -1
    f.loc[19,'open']=100.-sign*3.
    e=_execution_engine(f,side,close_ok)
    e.account.save_state=lambda:None
    e.tickers[SYMBOL]=100.+sign*.3
    await e._process_single_symbol(SYMBOL,1.,None,False)
    assert len(e.account.events)==1,e.account.logs
    assert '_MIDDLE_EXIT' in e.account.events[0][3]
    assert (SYMBOL in e.account.positions) is (not close_ok)

@pytest.mark.parametrize('first_open',[97.,98.01,99.])
def test_short_entry_needs_two_real_red_bodies(first_open):
    f=market();f.loc[16:18,'ma15']=[101.,100.,99.]
    f.loc[17,['open','close','high','low']]=[first_open,97.99,99.,97.8]
    f.loc[18,['open','close','high','low']]=[97.99,97.5,98.,97.4]
    f.loc[19,'open']=97.5
    result=TradingEngine._channel_swing_action(f,97.4)
    assert (result['action']=='ENTER') is (first_open==99.)

def test_long_reentry_waits_for_pullback_and_reclaim():
    f=market();ticket={'side':'LONG'}
    assert reentry_gate(ticket,f,103.)=='wait'
    assert reentry_gate(ticket,f,104.)=='wait'
    assert reentry_gate(ticket,f,102.)=='wait'
    assert reentry_gate(ticket,f,102.01)=='ready'

@pytest.mark.anyio
async def test_short_two_closed_bodies_reopen_without_new_low():
    f=market()
    f.loc[17,['open','close','high','low']]=[98.,97.5,98.1,97.4]
    f.loc[18,['open','close','high','low']]=[97.5,97.,97.6,96.9]
    f.loc[19,['open','close']]=[97.2,97.1]
    e=_execution_engine(f,'SHORT',True);e.account.positions.clear()
    e.account.save_state=lambda:None
    ticket={'side':'SHORT','phase':'closed','token':'test'}
    e.account.channel_profit_reentries={SYMBOL:ticket}
    e._place_structured_entry=AsyncMock(return_value=True)
    await e._try_profit_reentry(SYMBOL,f,97.1,False)
    e._place_structured_entry.assert_awaited_once()

@pytest.mark.anyio
async def test_long_pullback_ticket_survives_and_reopens_only_on_reclaim():
    f=market();f.loc[18,['open','close']]=[101.,102.]
    f.loc[19,'open']=102.
    e=_execution_engine(f,'LONG',True);e.account.positions.clear()
    e.account.save_state=lambda:None
    ticket={'side':'LONG','phase':'closed','token':'test'}
    e.account.channel_profit_reentries={SYMBOL:ticket}
    e._place_structured_entry=AsyncMock(return_value=True)
    await e._try_profit_reentry(SYMBOL,f,103.,False)
    e._place_structured_entry.assert_not_awaited()
    await e._try_profit_reentry(SYMBOL,f,101.9,False)
    assert SYMBOL in e.account.channel_profit_reentries
    await e._try_profit_reentry(SYMBOL,f,102.1,False)
    e._place_structured_entry.assert_awaited_once()
