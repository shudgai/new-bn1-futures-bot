from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from core.services.account_exposure_snapshot import account_exposure_snapshot

@pytest.mark.parametrize('paper,testnet,mode,verified',[(True,False,'paper','VERIFIED'),(True,True,'paper','VERIFIED'),(False,True,'testnet','UNVERIFIED'),(False,False,'live','UNVERIFIED')])
def test_memory_snapshot_is_read_only_and_honest(paper,testnet,mode,verified):
    a=SimpleNamespace(positions={'CAP/USDT':{'side':'LONG'}},pending_limit_orders={'龙虾/USDT':{}},update_positions=Mock(side_effect=AssertionError('must not update')),refresh=Mock(side_effect=AssertionError('must not refresh')))
    d=account_exposure_snapshot(a,paper_trading=paper,use_testnet=testnet,is_running=True)
    assert d['mode']==mode and d['verification']==verified
    assert d['position_count']==1 and d['pending_count']==1
    assert d['position_symbols']==['CAP/USDT']
    assert d['exchange_authoritative'] is False
    a.update_positions.assert_not_called();a.refresh.assert_not_called()
    assert a.positions=={'CAP/USDT':{'side':'LONG'}}

def test_endpoint_no_lifespan_or_account_mutation():
    import asyncio
    from unittest.mock import patch
    from services import api
    a=SimpleNamespace(positions={},pending_limit_orders={},update_positions=Mock(side_effect=AssertionError('must not update')))
    with patch.object(api.engine,'account',a),patch.object(api,'PAPER_TRADING',True):
        result=asyncio.run(api.get_account_exposure())
    assert result.status_code==200 and b'VERIFIED' in result.body
    assert result.headers['cache-control']=='no-store'
    a.update_positions.assert_not_called()
