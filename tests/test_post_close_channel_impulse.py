from types import SimpleNamespace
import pytest
from core.services.entry_contract import evaluate_entry_contract
from core.services.impulse_breakout import impulse_entry
from test_impulse_reverse_integration import impulse_frame

@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_next_bar_full_channel_cross_requires_actual_opposite_close(side):
    f=impulse_frame(side);symbol='CAP/USDT'
    opened=float(f.iloc[-1].kc_lower)-.2 if side=='LONG' else float(f.iloc[-1].kc_upper)+.2
    f.loc[f.index[-1],'open']=opened
    f.loc[f.index[-1],'low']=min(opened,float(f.iloc[-1].low))
    f.loc[f.index[-1],'high']=max(opened,float(f.iloc[-1].high))
    quote=float(f.iloc[-1].close)
    assert impulse_entry(f,quote,symbol) is None
    old='SHORT' if side=='LONG' else 'LONG'
    t=dict(id=float(f.iloc[-1].timestamp)-30000,symbol=symbol,action='CLOSE_'+old,status='CLOSED')
    a=SimpleNamespace(trades=[t],positions={})
    d=evaluate_entry_contract(f,quote,account=a,symbol=symbol)
    assert d['type']=='KC_IMPULSE_BREAKOUT_'+side
    assert d['strict_gate_evidence']['post_close_channel_cross']
    t['status']='OPEN'
    assert impulse_entry(f,quote,symbol,account=a) is None
    t['status']='CLOSED';t['id']-=60000
    assert impulse_entry(f,quote,symbol,account=a) is None
    t['id']+=60000;t['action']='CLOSE_'+side
    assert impulse_entry(f,quote,symbol,account=a) is None
    t['action']='CLOSE_'+old
    f.loc[f.index[-1],'ma5']=float(f.iloc[-2].ma5)+(-.1 if side=='LONG' else .1)
    diag={}
    assert evaluate_entry_contract(f,quote,account=a,symbol=symbol,diagnostics=diag) is None
    assert diag['reason']==('BLOCKED_BY_FALLING_MA5' if side=='LONG' else 'BLOCKED_BY_RISING_MA5')
    assert impulse_entry(f,100.,symbol,account=a) is None
