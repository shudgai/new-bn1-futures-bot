"""Regression coverage for the active entry signal/execution contract."""
import pandas as pd
import pytest
from core.services.entry_service import check_entry_signals, supported_entry_reason
from core.services.strategies.unified_entry_strategy import UnifiedEntryStrategy


def frame(side, mode):
    rows = [dict(timestamp=(i+1)*60000, open=100.2, close=100.,
                 high=101., low=99., ma3=100., ma15=100., kc_middle=100.,
                 kc_upper=101., kc_lower=99., atr=2., is_closed=i<6)
            for i in range(7)]
    if mode == 'cross':
        rows[5].update(close=101., ma3=100.5)
    elif mode == 'price_guard':
        rows[4].update(ma3=101., ma15=99.)
        rows[5].update(close=101., ma3=102., ma15=99.)
    elif mode == 'continuation':
        rows[4].update(open=101., close=101.2, ma3=100.5, ma15=99.)
        rows[5].update(open=101.2, close=101.4, ma3=100.7, ma15=99.)
    f = pd.DataFrame(rows)
    if side == 'SHORT':
        original=f.copy()
        for a,b in [('open','open'),('close','close'),('high','low'),('low','high'),
                    ('ma3','ma3'),('ma15','ma15'),('kc_middle','kc_middle'),
                    ('kc_upper','kc_lower'),('kc_lower','kc_upper')]:
            f[a]=200-original[b]
    f.attrs['timeframe_ms']=60000
    return f


@pytest.mark.parametrize('side',['LONG','SHORT'])
@pytest.mark.parametrize('mode',['cross','price_guard','continuation'])
def test_active_signal_contract(side,mode):
    f=frame(side,mode)
    result=check_entry_signals(f,side,0)
    allowed,reason,decision=UnifiedEntryStrategy().evaluate_entry(f,100.,side)
    if mode=='price_guard':
        assert not allowed
        assert 'BLOCKED_MA3_PRICE' in result['reason']
    else:
        assert allowed and result['action']=='ENTER'
        assert supported_entry_reason(result['reason'],side)
        assert not supported_entry_reason(result['reason'],'SHORT' if side=='LONG' else 'LONG')
        assert reason==result['reason']==decision['reason']
        assert result['side']==side
