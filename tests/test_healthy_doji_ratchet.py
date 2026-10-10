import pytest
from core.services.exits.peak_trailing_exit import (
    evaluate_peak_trailing, STATE_KEY, confirmed_doji_reversal,
    lower_shadow_support_hold,
)
from core.services.post_profit_lock_gate import profit_exit_fields
from test_tiered_profit_gate import position

@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_healthy_pullback_vetoes_ma5_and_doji_requires_actual_closed_bars(side):
    p=position(side);sign=1 if side=='LONG' else -1
    evaluate_peak_trailing(p,100+sign*6,{'quote_ms':120000},fee=0,slippage=0)
    price=100+sign*5
    snap=dict(quote_ms=240000,ma15=price-sign,
              kc_middle=price-sign,live_open=price+sign,live_high=price+2,live_low=price-2)
    assert evaluate_peak_trailing(p,price,snap,fee=0,slippage=0) is None
    doji=dict(ms=120000,o=price,c=price+.01,h=price+1,l=price-1)
    reversal=dict(ms=180000,o=price+sign*.2,c=price,h=price+1,l=price-1)
    snap['history_5']=[doji,reversal]
    d=evaluate_peak_trailing(p,price,snap,fee=0,slippage=0)
    assert d is None
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

@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_live_next_candle_doji_overrides_strong_hold_immediately(side):
    p=position(side);sign=1 if side=='LONG' else -1
    evaluate_peak_trailing(p,100+sign*6,{'quote_ms':120000},fee=0,slippage=0)
    price=100+sign*5.5
    # MA5 still supports continuation; a confirmed doji reversal takes priority.
    snap=dict(quote_ms=180001,live_bar_ms=180000,live_open=price+sign*.1,
              ma5=price-sign,last_ma5=price-2*sign,ma15=price-2*sign,
              history_5=[dict(ms=120000,o=price,c=price+.01,h=price+.04,l=price-1)])
    d=evaluate_peak_trailing(p,price,snap,fee=0,slippage=0)
    assert d is None
    snap['quote_ms']=180002
    # No adverse body: prior close request is revoked, not blindly retried.
    assert evaluate_peak_trailing(p,float(snap['live_open']),snap,fee=0,slippage=0) is None

@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_live_doji_requires_entry_and_exact_next_minute(side):
    p=position(side);sign=1 if side=='LONG' else -1
    snap=dict(quote_ms=180001,live_bar_ms=180000,live_open=105,
              history_5=[dict(ms=120000,o=105,c=105,h=106,l=104)])
    price=105-sign*.1
    assert confirmed_doji_reversal(p,snap,price)
    p['open_timestamp']=120
    assert confirmed_doji_reversal(p,snap,price) is None
    p['open_timestamp']=60;snap['history_5'][0]['ms']=60000
    assert confirmed_doji_reversal(p,snap,price) is None


def test_profitable_long_doji_reversal_requires_body_low_or_ma5_break():
    p=position('LONG')
    doji=dict(ms=120000,o=100.,c=100.1,h=101.,l=99.)
    reversal=dict(ms=180000,o=100.2,c=99.9,h=100.3,l=99.8)
    snap=dict(quote_ms=240000,history_5=[doji,reversal])
    assert confirmed_doji_reversal(p,snap) == 'EXIT_DOJI_BEARISH_CONFIRMATION'

    reversal['c']=100.05
    reversal['ma5']=100.1
    assert confirmed_doji_reversal(p,snap) == 'EXIT_DOJI_BEARISH_CONFIRMATION'

    reversal['ma5']=99.9
    assert confirmed_doji_reversal(p,snap) is None


def test_long_lower_shadow_holds_soft_exit_above_ma15_and_kc_middle():
    p=position('LONG')
    snap=dict(live_open=101.,live_low=99.,ma15=100.,live_kc_middle=100.)
    assert lower_shadow_support_hold(p,snap,101.2)
    assert not lower_shadow_support_hold(p,snap,99.9)
    assert not lower_shadow_support_hold(position('SHORT'),snap,101.2)
