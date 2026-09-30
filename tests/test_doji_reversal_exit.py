import asyncio
import copy
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pandas as pd
import pytest
from core.services.exits.doji_reversal_exit import observe_doji_reversal, REASON
from core.services.exits.dual_track_exit_service import DualTrackExitStrategy
from core.engine import TradingEngine


def sample(side='SHORT', bar=120000):
    p=dict(side=side,entry_price=100.,qty=1.,open_timestamp=bar/1000-120,
           entry_mode='CHANNEL_SWING',margin=100.,leverage=1.,entry_atr=10.,
           sl=120. if side=='SHORT' else 80.)
    p['open_timestamp']=max(1.,p['open_timestamp'])
    f=pd.DataFrame([dict(timestamp=bar-60000,open=100.,close=100.25,high=100.5,low=99.5,
                         atr=1.,kc_middle=100.,is_closed=True),
                    dict(timestamp=bar,open=100.,close=100.,high=101.,low=99.,
                         atr=99.,kc_middle=100.,is_closed=False)])
    return p,f


@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_first_live_body_boundary_and_retry(side):
    p,f=sample(side); sign=1 if side=='SHORT' else -1
    assert observe_doji_reversal(p,f,100+sign*.499,121000) is None
    assert observe_doji_reversal(p,f,100+sign*.5,122000)==REASON
    assert p['doji_reversal_state']['atr']==1.
    assert observe_doji_reversal(copy.deepcopy(p),None,100.,123000)==REASON
    assert DualTrackExitStrategy().evaluate_exit(p,current_price=100.)==REASON


@pytest.mark.parametrize('side',['LONG','SHORT'])
@pytest.mark.parametrize('fault',['not_doji','flat','invalid_atr','nonadjacent','stale_frame',
 'same_direction','wick_only','before_entry','entry_bar','nan','closed_live','second_bar'])
def test_no_false_exit(side,fault):
    p,f=sample(side); sign=1 if side=='SHORT' else -1; price=100+sign*.5; stamp=122000
    if fault=='not_doji':f.loc[0,'close']=100.251
    if fault=='flat':f.loc[0,['open','close','high','low']]=100.
    if fault=='invalid_atr':f.loc[0,'atr']=0.
    if fault=='nonadjacent':f.loc[0,'timestamp']-=60000
    if fault=='stale_frame':f.loc[1,'timestamp']-=60000
    if fault=='same_direction':price=100-sign*.5
    if fault=='wick_only':price=100.
    if fault=='before_entry':p['open_timestamp']=123.
    if fault=='entry_bar':p['open_timestamp']=121.
    if fault=='nan':price=float('nan')
    if fault=='closed_live':f.loc[1,'is_closed']=True
    if fault=='second_bar':stamp+=60000
    assert observe_doji_reversal(p,f,price,stamp) is None


def test_old_position_pending_does_not_transfer():
    p,f=sample();assert observe_doji_reversal(p,f,100.5,122000)==REASON
    p['open_timestamp']=121.
    assert observe_doji_reversal(p,None,100.,123000) is None
    assert DualTrackExitStrategy().evaluate_exit(p,current_price=100.) is None


def test_fast_path_uses_no_rest_and_retries_persisted_close():
    async def run():
        now=time.time();bar=int(now//60)*60000;p,f=sample(bar=bar)
        account=SimpleNamespace(positions={'X':p},position_meta={},save_state=Mock(),
            close_position=AsyncMock(return_value=False),log=Mock())
        engine=object.__new__(TradingEngine);engine.account=account;engine.is_running=True
        engine._channel_exit_frames={'X':f}
        engine.fetch_klines=AsyncMock(side_effect=AssertionError('No REST'))
        lock=asyncio.Lock();await lock.acquire();engine._channel_symbol_locks={'X':lock}
        assert await engine._instant_quote_exit('X',100.5,now*1000)
        assert account.close_position.await_count==1
        assert account.position_meta['X']['doji_reversal_state']['pending']
        account.positions['X']=sample(bar=bar)[0];engine._channel_exit_frames={}
        assert await engine._instant_quote_exit('X',100.,now*1000)
        assert account.close_position.await_count==2
        assert account.close_position.await_args.args[2]==REASON
        engine.fetch_klines.assert_not_called();lock.release()
    asyncio.run(run())
