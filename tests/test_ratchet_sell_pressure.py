import copy
import pytest
from core.services.exits.peak_trailing_exit import evaluate_peak_trailing,STATE_KEY
from core.services.post_profit_lock_gate import profit_exit_fields
from test_tiered_profit_gate import position


def observe(p,gain,stamp,mode='strong'):
    sign=1 if p['side']=='LONG' else -1
    price=100*(1+sign*gain)
    s=dict(quote_ms=stamp,live_open=price-sign,ma5=price-sign,last_ma5=price-2*sign)
    if mode=='below':s.update(ma5=price+sign,last_ma5=price+2*sign)
    if mode=='turn':s.update(ma5=price-sign,last_ma5=price)
    if mode=='red':s.update(ma5=price-sign,last_ma5=price,live_open=price+sign)
    if mode=='missing':s=dict(quote_ms=stamp)
    return evaluate_peak_trailing(p,price,s,fee=0,slippage=0)

@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_ratchet_arms_without_closing_and_never_retreats(side):
    p=position(side);sign=1 if side=='LONG' else -1
    assert observe(p,.049,61000) is None
    assert not p[STATE_KEY].get('ratchet_armed')
    for stamp,gain in [(62000,.05),(63000,.1),(64000,.2)]:
        assert observe(p,gain,stamp) is None
        assert p[STATE_KEY]['ratchet_armed']
    floor=p[STATE_KEY]['locked_floor_price']
    # A deep retracement alone cannot exit if MA5 remains supportive.
    assert observe(p,.16,65000) is None
    assert p[STATE_KEY]['locked_floor_price']==floor
    assert p[STATE_KEY]['trend_hold_reason']=='RIDING_STRONG_TREND'
    restored=copy.deepcopy(p)
    assert observe(restored,.16,66000) is None
    assert restored[STATE_KEY]['locked_floor_price']==floor

@pytest.mark.parametrize('side',['LONG','SHORT'])
@pytest.mark.parametrize('mode',['below','turn','red'])
def test_armed_pressure_exits_and_records_gate(side,mode):
    p=position(side)
    observe(p,.06,61000)
    d=observe(p,.04,62000,mode)
    assert d['trigger']=='PROFIT_LOCK_SELL_PRESSURE'
    assert profit_exit_fields(p,d['reason'],63000)['last_profit_exit_side']==side
    assert observe(p,.04,64000) is None
    assert 'pending' not in p[STATE_KEY]

@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_unarmed_pressure_never_exits_or_replays_legacy_ticket(side):
    p=position(side)
    observe(p,.02,61000)
    p[STATE_KEY].update(profit_lock_basis='price_return_v1',pending='PROFIT_LOCK_T1',trigger='PROFIT_LOCK_T1')
    assert observe(p,.01,62000,'below') is None
    assert 'pending' not in p[STATE_KEY]
    assert 'tiered_roi_peak' not in p[STATE_KEY]
    assert observe(p,.01,63000,'missing') is None
