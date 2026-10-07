"""Legal live breakout signals retain the latest completed candle price."""
import pytest
from core.services.entry_contract import evaluate_entry_contract
from test_lobster_cap_gates import frame


@pytest.mark.parametrize('side',['LONG','SHORT'])
@pytest.mark.parametrize('distance_atr', [.4, .5])
def test_live_breakout_signal_has_latest_closed_price(side, distance_atr):
    f=frame(side);sign=1 if side=='LONG' else -1
    edge=float(f.iloc[-1]['kc_upper' if sign==1 else 'kc_lower'])
    q=edge+sign*distance_atr*float(f.iloc[-2].atr)
    f.loc[5,'close']=q;f.loc[5,'high']=max(q,float(f.loc[5,'open']))+.01;f.loc[5,'low']=min(q,float(f.loc[5,'open']))-.01
    diagnostics={}
    result=evaluate_entry_contract(f,diagnostics=diagnostics)
    assert result is not None, diagnostics
    assert result['side']==side
    assert result['type']=='KC_LIVE_BODY_BREAKOUT_'+side
    assert result['price']==q
    assert result['close_price']==float(f.iloc[-2].close)
    assert result['close_price']!=q
    assert result['kc_distance_atr']==pytest.approx(distance_atr)


@pytest.mark.parametrize('side',['LONG','SHORT'])
@pytest.mark.parametrize('distance_atr', [.500001, 1.])
def test_live_breakout_over_chase_limit_is_rejected(side, distance_atr):
    f=frame(side);sign=1 if side=='LONG' else -1
    edge=float(f.iloc[-1]['kc_upper' if sign==1 else 'kc_lower'])
    q=edge+sign*distance_atr*float(f.iloc[-2].atr)
    f.loc[5,'close']=q;f.loc[5,'high']=max(q,float(f.loc[5,'open']))+.01;f.loc[5,'low']=min(q,float(f.loc[5,'open']))-.01
    diagnostics={}
    assert evaluate_entry_contract(f,diagnostics=diagnostics) is None
    assert diagnostics['reason']=='BLOCKED_OUTSIDE_RAIL_CHASE_OVER_0_5_ATR'
