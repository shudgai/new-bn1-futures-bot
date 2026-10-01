"""Current three-closed-bar policy must return a valid signal, not NameError."""
import pandas as pd
import pytest
from core.services.entry_contract import evaluate_entry_contract


@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_current_three_bar_signal_has_latest_closed_price(side):
    rows=[]
    for i,(opening,close,closed) in enumerate([(100.,100.2,True),(101.,101.5,True),
                                             (101.5,101.8,True),(101.8,102.1,True),
                                             (102.1,102.15,False)]):
        rows.append(dict(timestamp=60000*(i+1),open=opening,close=close,
                         high=max(opening,close)+.01,low=min(opening,close)-.01,
                         atr=1.,kc_upper=101.4,kc_middle=100.,kc_lower=98.6,
                         ma3=101.,ma5=101.,ma15=100.,is_closed=closed))
    f=pd.DataFrame(rows)
    if side=='SHORT':
        original=f.copy()
        for a,b in [('open','open'),('close','close'),('high','low'),('low','high'),
                    ('kc_upper','kc_lower'),('kc_lower','kc_upper'),('kc_middle','kc_middle'),
                    ('ma3','ma3'),('ma5','ma5'),('ma15','ma15')]:
            f[a]=200-original[b]
    result=evaluate_entry_contract(f)
    assert result is not None
    assert result['side']==side
    assert result['close_price']==float(f.iloc[-2].close)
