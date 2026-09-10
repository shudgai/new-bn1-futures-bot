from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from core import config
from core.channel_hard_stop import enforce_hard_stop, hard_stop_reason
from core.testnet_account import BinanceTestnetAccount
from core.engine import TradingEngine
from test_direct_break_execution import setup_engine
from test_channel_swing_execution import SYMBOL, _execution_engine
from test_channel_protected_only_exit import market

@pytest.fixture
def anyio_backend(): return 'asyncio'

@pytest.fixture(autouse=True)
def limits(monkeypatch):
    monkeypatch.setattr(config, 'MAX_POSITION_MARGIN_LOSS_RATIO', .10)
    monkeypatch.setattr(config, 'MAX_ACCEPTABLE_LOSS_PCT', -.02)

def position(side):
    return dict(side=side, entry_price=100., qty=3.75, margin=75., leverage=5,
                entry_mode='CHANNEL_SWING', open_timestamp=1201.)

@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
@pytest.mark.parametrize('move,hit', [(1.99,False),(2.,True),(3.,True)])
def test_threshold(side,move,hit):
    assert bool(hard_stop_reason(position(side), 100+move*(1 if side=='SHORT' else -1))) == hit

@pytest.mark.parametrize('side', ['LONG','SHORT'])
def test_each_limit_independent(side,monkeypatch):
    p=position(side);price=102. if side=='SHORT' else 98.
    monkeypatch.setattr(config,'MAX_POSITION_MARGIN_LOSS_RATIO',0.)
    assert hard_stop_reason(p,price)=='PRICE_LOSS'
    monkeypatch.setattr(config,'MAX_POSITION_MARGIN_LOSS_RATIO',.1)
    monkeypatch.setattr(config,'MAX_ACCEPTABLE_LOSS_PCT',0.)
    assert hard_stop_reason(p,price)=='MARGIN_LOSS'

@pytest.mark.anyio
@pytest.mark.parametrize('side',['LONG','SHORT'])
async def test_paper_update_closes_channel_before_skip(setup_engine,side):
    e,_=setup_engine(side,side)
    e.account.positions[SYMBOL].update(position(side))
    await e.account.update_positions({SYMBOL:102. if side=='SHORT' else 98.})
    assert SYMBOL not in e.account.positions
    assert 'HARD_STOP' in e.account.trades[0]['reason']

@pytest.mark.anyio
@pytest.mark.parametrize('side',['LONG','SHORT'])
async def test_testnet_update_closes_before_skip(side):
    p=position(side)
    a=SimpleNamespace(positions={SYMBOL:p},position_meta={SYMBOL:{'entry_mode':'CHANNEL_SWING'}},
        unrealized_pnl=0.,refresh=AsyncMock(),save_state=lambda:None,close_position=AsyncMock(return_value=False))
    await BinanceTestnetAccount.update_positions(a,{SYMBOL:102. if side=='SHORT' else 98.})
    a.close_position.assert_awaited_once()
    assert 'HARD_STOP' in a.close_position.call_args.args[2]

@pytest.mark.anyio
@pytest.mark.parametrize('side',['LONG','SHORT'])
async def test_pending_survives_recovery(side):
    p=position(side)
    a=SimpleNamespace(positions={SYMBOL:p},position_meta={},save_state=lambda:None,
                      close_position=AsyncMock(return_value=False))
    assert await enforce_hard_stop(a,SYMBOL,102. if side=='SHORT' else 98.)
    assert await enforce_hard_stop(a,SYMBOL,100.)
    assert a.close_position.await_count==2
    assert a.position_meta[SYMBOL]['channel_hard_stop_pending']=='MARGIN_LOSS'

@pytest.mark.anyio
@pytest.mark.parametrize('side',['LONG','SHORT'])
async def test_quote_hard_stop_in_entry_bar(side,monkeypatch):
    f,_=market(side,'normal');f['atr']=100.
    e=_execution_engine(f,side,True);e.is_running=True
    e.account.save_state=lambda:None
    e.account.positions[SYMBOL].update(position(side))
    e._channel_exit_frames={SYMBOL:f}
    monkeypatch.setattr('core.engine.time.time',lambda:1201.)
    await e._channel_quote_exit(SYMBOL,102. if side=='SHORT' else 98.,1201000)
    assert len(e.account.events)==1
    assert 'HARD_STOP' in e.account.events[0][3]

@pytest.mark.anyio
@pytest.mark.parametrize('side',['LONG','SHORT'])
async def test_old_ma3_pending_removed(side):
    f,_=market(side,'normal');e=_execution_engine(f,side,True)
    e.account.save_state=lambda:None
    p=e.account.positions[SYMBOL];p.update(position(side),channel_live_ma3_turn_exit_pending=True,
                                         channel_ma3_turn_observed_bar=1200.)
    e.account.position_meta[SYMBOL]={'channel_live_ma3_turn_exit_pending':True}
    e.tickers[SYMBOL]=100.
    await e._process_single_symbol(SYMBOL,1201.,None,False)
    assert not e.account.events
    assert 'channel_live_ma3_turn_exit_pending' not in p
    assert 'channel_live_ma3_turn_exit_pending' not in e.account.position_meta[SYMBOL]

@pytest.mark.anyio
@pytest.mark.parametrize('armed',[False,True])
async def test_hard_stop_overrides_protection_and_restores_metadata(armed):
    p=position('LONG')
    p['channel_profit_protection']={'armed':armed,'peak_gross':10.}
    a=SimpleNamespace(positions={SYMBOL:p},position_meta={SYMBOL:{'channel_hard_stop_pending':'MARGIN_LOSS'}},
        channel_profit_reentries={SYMBOL:{'phase':'closed'}},save_state=lambda:None,
        close_position=AsyncMock(return_value=False))
    assert await enforce_hard_stop(a,SYMBOL,100.)
    assert not a.channel_profit_reentries
    a.close_position.assert_awaited_once()

@pytest.mark.anyio
@pytest.mark.parametrize('price',[None,float('nan'),float('inf'),0.,-1.])
async def test_invalid_quote_never_sent(price):
    a=SimpleNamespace(positions={SYMBOL:position('LONG')},position_meta={},close_position=AsyncMock())
    assert not await enforce_hard_stop(a,SYMBOL,price)
    a.close_position.assert_not_awaited()
