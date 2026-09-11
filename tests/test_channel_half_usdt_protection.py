import json
import pytest
from core.services.exits.profit_protection_service import protection


def price_for_net(side,net):
    e,q,fee,slip=100.,2.,.0005,.0001
    return ((e*(1+fee)+net/q)/((1-slip)*(1-fee)) if side=='LONG'
            else (e*(1-fee)-net/q)/((1+slip)*(1+fee)))

@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_half_net_arms_and_twenty_percent_retracement(side):
    p=dict(side=side,entry_price=100.,qty=2.,open_timestamp=1.)
    assert protection(p,price_for_net(side,.499),.0005,.0001) is None
    r=protection(p,price_for_net(side,.5),.0005,.0001)
    assert r is not None
    assert p['channel_profit_protection']['locked_net']==pytest.approx(.4)
    assert not p['channel_profit_protection']['pending']
    protection(p,price_for_net(side,.401),.0005,.0001)
    assert not p['channel_profit_protection']['pending']
    protection(p,price_for_net(side,.399),.0005,.0001)
    assert p['channel_profit_protection']['pending']
    p=json.loads(json.dumps(p))
    protection(p,price_for_net(side,.8),.0005,.0001)
    assert p['channel_profit_protection']['pending']

@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_existing_peak_and_more_favorable_floor_preserved(side):
    p=dict(side=side,entry_price=100.,qty=2.,open_timestamp=1.)
    protection(p,price_for_net(side,2.),.0005,.0001)
    floor=p['channel_profit_protection']['locked_net']
    p=json.loads(json.dumps(p))
    protection(p,price_for_net(side,.6),.0005,.0001)
    assert p['channel_profit_protection']['locked_net']==floor
    assert p['channel_profit_protection']['pending']
