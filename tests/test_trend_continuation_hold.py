import pytest
from core.services.exits.peak_trailing_exit import evaluate_peak_trailing,STATE_KEY,trend_continuation_hold
from test_tiered_profit_gate import position

@pytest.mark.parametrize('side',['LONG','SHORT'])
@pytest.mark.parametrize('feature',['candle','previous','ma','rail'])
def test_each_momentum_feature_independently_holds(side,feature):
    sign=1 if side=='LONG' else -1
    s=dict(ma5=100+sign,last_ma5=100+2*sign,live_open=100+sign,last_close=100+sign)
    if feature=='candle':s['live_open']=100-sign
    if feature=='previous':s['last_close']=100-sign
    if feature=='ma':s['last_ma5']=s['ma5']
    if feature=='rail':s['kc_upper' if side=='LONG' else 'kc_lower']=100-sign
    assert trend_continuation_hold(side,100,s)

@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_profit_lock_waits_for_momentum_and_revalidates_pending(side):
    p=position(side);sign=1 if side=='LONG' else -1
    assert evaluate_peak_trailing(p,100+sign*6,{'quote_ms':61000},fee=0,slippage=0) is None
    price=100+sign*4.8
    hold=dict(quote_ms=62000,ma5=price-sign,last_ma5=price-2*sign,live_open=price-sign)
    assert evaluate_peak_trailing(p,price,hold,fee=0,slippage=0) is None
    assert p[STATE_KEY]['soft_exit_blocked']
    turn=dict(quote_ms=63000,ma5=price+sign,last_ma5=price+2*sign,live_open=price+sign,last_close=price+sign)
    assert evaluate_peak_trailing(p,price,turn,fee=0,slippage=0)['trigger']=='PROFIT_LOCK_T1'
    hold['quote_ms']=64000
    assert evaluate_peak_trailing(p,price,hold,fee=0,slippage=0) is None
    assert 'pending' not in p[STATE_KEY]

@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_missing_ma_cannot_authorize_profit_lock(side):
    p=position(side);sign=1 if side=='LONG' else -1
    evaluate_peak_trailing(p,100+sign*6,{'quote_ms':61000},fee=0,slippage=0)
    assert evaluate_peak_trailing(p,100+sign*4,{'quote_ms':62000},fee=0,slippage=0) is None
