import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from core.services.entry_contract import evaluate_entry_contract,kc_band_expansion_reason
from core.services.entry_firewall import validate_account_entry
from test_strict_entry_gates_live import frame_for
from test_impulse_reverse_integration import impulse_frame

@pytest.mark.parametrize('side',['LONG','SHORT'])
@pytest.mark.parametrize('entry',['second','impulse','TRIGGER_A_KC_BREAKOUT','TRIGGER_C_CONTINUATION'])
def test_all_entries_require_expansion(side,entry,monkeypatch):
    f=impulse_frame(side) if entry=='impulse' else frame_for(side)
    code=('KC_IMPULSE_BREAKOUT_'+side if entry=='impulse' else 'KC_SECOND_THIRD_'+side if entry=='second' else entry)
    if entry.startswith('TRIGGER'):
        monkeypatch.setattr('core.services.entry_contract.detect_raw_triggers',lambda *args:(side,code))
    diag={}
    assert evaluate_entry_contract(f,code=code,symbol='CAP/USDT',diagnostics=diag) is None
    assert diag['reason']=='BLOCKED_BY_KC_BAND_CONTRACTING'
    f.loc[f.index[-1],'kc_upper']+=.1
    f.loc[f.index[-1],'kc_lower']-=.1
    assert evaluate_entry_contract(f,code=code,symbol='CAP/USDT',diagnostics=diag),diag

@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_target_rail_must_diverge_even_when_width_expands(side):
    f=frame_for(side)
    if side=='LONG':
        f.loc[f.index[-1],'kc_upper']=float(f.iloc[-2].kc_upper)
        f.loc[f.index[-1],'kc_lower']-=1
    else:
        f.loc[f.index[-1],'kc_lower']=float(f.iloc[-2].kc_lower)
        f.loc[f.index[-1],'kc_upper']+=1
    assert kc_band_expansion_reason(f,side)==('BLOCKED_BY_KC_UPPER_FLAT' if side=='LONG' else 'BLOCKED_BY_KC_LOWER_FLAT')
    f.loc[f.index[-1],'kc_upper']=float('nan')
    assert kc_band_expansion_reason(f,side)=='BLOCKED_BY_INVALID_KC_BAND'

@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_account_rechecks_expansion_after_candidate(side):
    f=frame_for(side);f.loc[f.index[-1],'kc_upper']+=.1;f.loc[f.index[-1],'kc_lower']-=.1
    a=SimpleNamespace(positions={},trades=[],entry_frame_provider=AsyncMock(return_value=f))
    d=evaluate_entry_contract(f,account=a,symbol='CAP/USDT')
    snap=dict(d,symbol='CAP/USDT',signal_code=d['type'],signal_id=d['pending_signal_id'],candidate_bar_id=d['confirmation_bar_id'],closed_bar=d['confirmation_bar_id'])
    ctx=dict(entry_signal_code=d['type'],signal_id=d['pending_signal_id'],candidate_bar_id=d['confirmation_bar_id'],channel_confirmation_bar_id=d['confirmation_bar_id'],entry_snapshot=snap)
    assert asyncio.run(validate_account_entry(a,'CAP/USDT',side,ctx))
    f.loc[f.index[-1],'kc_upper']=float(f.iloc[-2].kc_upper)
    f.loc[f.index[-1],'kc_lower']=float(f.iloc[-2].kc_lower)
    with pytest.raises(ValueError,match='BLOCKED_BY_KC_BAND_CONTRACTING'):
        asyncio.run(validate_account_entry(a,'CAP/USDT',side,ctx))
