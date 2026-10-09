import pytest
from core.services.exits.peak_trailing_exit import evaluate_peak_trailing, STATE_KEY
from test_tiered_profit_gate import position

@pytest.mark.parametrize('side',['LONG','SHORT'])
@pytest.mark.parametrize('leverage',[1,5,20])
def test_small_price_profit_never_arms_by_leverage(side,leverage):
    p=position(side);p['margin']=100/leverage;p['leverage']=leverage
    sign=1 if side=='LONG' else -1
    for gain in (.012,.008,.02,.001):
        assert evaluate_peak_trailing(p,100*(1+sign*gain),{'quote_ms':61000}) is None
    assert p[STATE_KEY]['tiered_price_peak'] < .05


def test_cap_actual_peak_does_not_trigger_t1():
    p=position('LONG');p.update(entry_price=.08473847,qty=4428.,margin=75.,leverage=5.)
    assert evaluate_peak_trailing(p,.08573,{'quote_ms':61000}) is None
    assert evaluate_peak_trailing(p,.08551145,{'quote_ms':62000}) is None


def test_old_roi_pending_is_revoked_without_losing_price_peak():
    p=position('LONG')
    evaluate_peak_trailing(p,101.2,{'quote_ms':61000})
    p[STATE_KEY].update(profit_lock_basis='margin_roi',tiered_roi_peak=.06,
                        pending='PROFIT_LOCK_T1',trigger='PROFIT_LOCK_T1')
    assert evaluate_peak_trailing(p,100.9,{'quote_ms':62000}) is None
    assert 'pending' not in p[STATE_KEY]
    assert p[STATE_KEY]['peak_price']==101.2
