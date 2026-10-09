import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from core.services.entry_contract import detect_raw_triggers,evaluate_entry_contract
from core.services.entry_firewall import validate_account_entry
from test_strict_entry_gates_live import frame_for

@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_c_reaches_final_firewall_without_breakout_memory(side):
    f=frame_for(side);sign=1 if side=='LONG' else -1
    f.loc[f.index[-2],'ma5']=100+sign*.45
    f.loc[f.index[-1],'open']=100+sign*2.3
    f.loc[f.index[-1],'low']=min(float(f.iloc[-1].open),float(f.iloc[-1].close))-.1
    f.loc[f.index[-1],'high']=max(float(f.iloc[-1].open),float(f.iloc[-1].close))+.1
    a=SimpleNamespace(positions={},trades=[],entry_frame_provider=AsyncMock(return_value=f))
    assert detect_raw_triggers(f.iloc[:-1],a,'CAP/USDT')==(side,'TRIGGER_C_CONTINUATION')
    d=evaluate_entry_contract(f,code='TRIGGER_C_CONTINUATION',account=a,symbol='CAP/USDT')
    assert d and d['strict_gate_evidence']['opening_context']=='SAME_SIDE_CONTINUATION'
    snap=dict(d,symbol='CAP/USDT',signal_code=d['type'],signal_id=d['pending_signal_id'],candidate_bar_id=d['confirmation_bar_id'],closed_bar=d['confirmation_bar_id'])
    ctx=dict(entry_signal_code=d['type'],signal_id=d['pending_signal_id'],candidate_bar_id=d['confirmation_bar_id'],channel_confirmation_bar_id=d['confirmation_bar_id'],entry_snapshot=snap)
    assert asyncio.run(validate_account_entry(a,'CAP/USDT',side,ctx))['type']=='TRIGGER_C_CONTINUATION'
    f.loc[f.index[-2],'ma5']=float(f.iloc[-3].ma5)
    assert detect_raw_triggers(f.iloc[:-1],a,'CAP/USDT')[1]!='TRIGGER_C_CONTINUATION'
