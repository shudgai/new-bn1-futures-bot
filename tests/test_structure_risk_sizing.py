import pytest
from core.services.structure_risk_sizing import structure_risk_plan,POLICY
from core.services.exits.hard_stop_service import hard_stop_reason
from core.services.exits.peak_trailing_exit import evaluate_peak_trailing

@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_same_budget_allows_structural_pullback_by_reducing_size(side):
    sign=1 if side=='LONG' else -1
    plan=structure_risk_plan(100,side,1,100-sign*3,80,5,.05,.0005,.0001)
    qty=plan['amount']*5/100
    assert plan['structure_risk_budget_usdt']==4
    assert plan['amount']<80
    assert qty*(3+(100+plan['stop'])*.0005+plan['stop']*.0001)<=4+1e-12
    p=dict(side=side,entry_price=100,qty=qty,margin=plan['amount'],leverage=5,
           structure_risk_policy=POLICY,structure_risk_budget_usdt=4,initial_sl=plan['stop'],
           entry_atr=1,open_timestamp=60,entry_mode='CHANNEL_SWING')
    # A one-percent counter move no longer closes from margin percentage alone.
    assert hard_stop_reason(p,100-sign*1.01) is None
    assert evaluate_peak_trailing(p,100-sign*1.6,dict(quote_ms=121000,reason='NO_DATA')) is None
    # Initial risk is enforced before the profit evaluator, including no-data ticks.
    assert hard_stop_reason(p,100-sign*3.01)=='INITIAL_ATR'
    assert hard_stop_reason(p,100-sign*(4/qty+.01))=='INITIAL_ATR'
    assert hard_stop_reason({**p,'initial_sl':None},100-sign*(4/qty+.01))=='MARGIN_LOSS'


def test_legacy_positions_keep_existing_limit():
    p=dict(side='LONG',entry_price=100,qty=4,margin=80,leverage=5)
    assert hard_stop_reason(p,98.99)=='MARGIN_LOSS'

@pytest.mark.parametrize('key,value',[('atr',0),('stop',0),('margin',0),('ratio',0),('stop',float('nan'))])
def test_invalid_risk_data_rejects_order(key,value):
    args=dict(entry=100,side='LONG',atr=1,stop=97,margin=80,leverage=5,ratio=.05,fee=.0005,slippage=.0001)
    args[key]=value
    with pytest.raises(ValueError):structure_risk_plan(**args)
