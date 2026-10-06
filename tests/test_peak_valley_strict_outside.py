import pytest
from test_early_swing_reversal import reversal_frame
from core.services.early_swing_reversal import evaluate_early_swing

@pytest.mark.parametrize('side',['LONG','SHORT'])
@pytest.mark.parametrize('location',['outside','touch','inside'])
def test_pivot_must_be_strictly_outside_its_own_rail(side,location):
    f=reversal_frame(side)
    sign=1 if side=='LONG' else -1
    key='kc_lower' if side=='LONG' else 'kc_upper'
    level=float(f.loc[3,'low' if side=='LONG' else 'high'])
    f.loc[3,key]=level+sign*({'outside':.1,'touch':0.,'inside':-.1}[location])
    assert bool(evaluate_early_swing(f,float(f.iloc[-1].close))) is (location=='outside')


@pytest.mark.parametrize('side',['LONG','SHORT'])
@pytest.mark.parametrize('body,allowed',[(.1,False),(.295,False),(.499,False),(.5,True),(.6,True)])
def test_live_reversal_needs_half_fixed_closed_atr(side,body,allowed):
    f=reversal_frame(side);sign=1 if side=='LONG' else -1
    q=float(f.iloc[-1].open)+sign*body
    f.loc[5,'close']=q;f.loc[5,'high']=max(q,float(f.loc[5,'open']))+.01;f.loc[5,'low']=min(q,float(f.loc[5,'open']))-.01
    assert bool(evaluate_early_swing(f,q)) is allowed


def test_recorded_cap_first_short_is_rejected_by_producer_and_contract():
    import json
    import pandas as pd
    from pathlib import Path
    from core.services.entry_contract import evaluate_entry_contract
    d=json.loads((Path(__file__).resolve().parent/'fixtures/cap_first_entry_20261007.json').read_text())
    rows=d['evidence']['candles']
    for row in rows:row['is_closed']=True
    f=pd.DataFrame(rows+[d['evidence']['live_candle']])
    code='KC_EARLY_SWING_REVERSAL_SHORT'
    assert evaluate_early_swing(f,d['price'],code) is None
    assert evaluate_entry_contract(f,d['price'],code=code,symbol='CAP/USDT') is None
