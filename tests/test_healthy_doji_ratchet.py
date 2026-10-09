import pytest
from core.services.exits.peak_trailing_exit import evaluate_peak_trailing,STATE_KEY,confirmed_doji_reversal
from core.services.post_profit_lock_gate import profit_exit_fields
from test_tiered_profit_gate import position

@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_healthy_pullback_vetoes_ma5_and_doji_requires_actual_closed_bars(side):
    p=position(side);sign=1 if side=='LONG' else -1
    evaluate_peak_trailing(p,100+sign*6,{'quote_ms':120000},fee=0,slippage=0)
    price=100+sign*5
    snap=dict(quote_ms=240000,ma5=price+sign,last_ma5=price+2*sign,ma15=price-sign,
              kc_middle=price-sign,live_open=price+sign,live_high=price+2,live_low=price-2)
    assert evaluate_peak_trailing(p,price,snap,fee=0,slippage=0) is None
    assert p[STATE_KEY]['trend_hold_reason']=='HEALTHY_PULLBACK_HOLD'
    doji=dict(ms=120000,o=price,c=price+.01,h=price+1,l=price-1)
    reversal=dict(ms=180000,o=price+sign*.2,c=price,h=price+1,l=price-1)
    snap['history_5']=[doji,reversal]
    d=evaluate_peak_trailing(p,price,snap,fee=0,slippage=0)
    code='EXIT_DOJI_BEARISH_CONFIRMATION' if side=='LONG' else 'EXIT_DOJI_BULLISH_CONFIRMATION'
    assert d['trigger']==code
    assert profit_exit_fields(p,code,240000)['last_profit_exit_side']==side
    reversal['ms']=120000
    assert confirmed_doji_reversal(p,snap) is None
    reversal['ms']=180000;doji['ms']=60000
    assert confirmed_doji_reversal(p,snap) is None

@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_doji_before_arming_cannot_close(side):
    p=position(side);sign=1 if side=='LONG' else -1
    price=100+sign
    snap=dict(quote_ms=240000,ma5=price+sign,last_ma5=price+2*sign,
              history_5=[dict(ms=120000,o=price,c=price,h=price+1,l=price-1),
                         dict(ms=180000,o=price+sign*.2,c=price,h=price+1,l=price-1)])
    assert evaluate_peak_trailing(p,price,snap,fee=0,slippage=0) is None
