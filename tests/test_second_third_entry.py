import pytest
from types import SimpleNamespace
from core.services.entry_contract import evaluate_entry_contract
from core.services.second_third_entry import evaluate_second_third
from test_strict_entry_gates_live import frame_for


@pytest.mark.parametrize('side',['LONG','SHORT'])
@pytest.mark.parametrize('number',[2,3])
def test_outside_non_doji_opens_without_old_gates(side,number):
    f=frame_for(side)
    sign=1 if side=='LONG' else -1
    if number==3:
        f.loc[f.index[-3],['open','close','high','low']]=([101.,103.,103.2,100.9] if sign==1 else [99.,97.,99.1,96.8])
        f.loc[f.index[-2],'open']=100+sign*2.8
    f.loc[f.index[-1],'ma5']=f.iloc[-2].ma5
    d=evaluate_entry_contract(f,float(f.iloc[-1].close),symbol='X')
    assert d['type']=='KC_SECOND_THIRD_'+side
    assert d['strict_gate_evidence']['candle_number']==number


@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_quote_retreat_doji_and_duplicate_block(side):
    f=frame_for(side)
    quote=float(f.iloc[-1].close)
    d=evaluate_second_third(f,quote,symbol='X')
    assert d
    assert evaluate_second_third(f,100.,symbol='X') is None
    f.loc[f.index[-1],'open']=quote
    assert evaluate_second_third(f,quote,symbol='X') is None
    f=frame_for(side)
    a=SimpleNamespace(positions={},trades=[dict(action='OPEN_'+side,entry_snapshot=d)])
    assert evaluate_second_third(f,quote,a,'X') is None


@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_real_account_firewall_revalidates_second_third(side):
    import asyncio
    from unittest.mock import AsyncMock
    from core.services.entry_firewall import validate_account_entry
    f=frame_for(side)
    a=SimpleNamespace(positions={},trades=[],entry_frame_provider=AsyncMock(return_value=f))
    d=evaluate_entry_contract(f,account=a,symbol='X')
    snap=dict(d,symbol='X',signal_code=d['type'],signal_id=d['pending_signal_id'],
              candidate_bar_id=d['confirmation_bar_id'],closed_bar=d['confirmation_bar_id'])
    context=dict(entry_signal_code=d['type'],signal_id=d['pending_signal_id'],
                 candidate_bar_id=d['confirmation_bar_id'],channel_confirmation_bar_id=d['confirmation_bar_id'],
                 entry_snapshot=snap)
    verified=asyncio.run(validate_account_entry(a,'X',side,context))
    assert verified['strict_gate_evidence']['policy']=='second_third_outside_non_doji_v1'
    f.loc[f.index[-1],'close']=float(f.iloc[-1].open)
    with pytest.raises(ValueError,match='BLOCKED_SECOND_THIRD_OUTSIDE_OR_DOJI'):
        asyncio.run(validate_account_entry(a,'X',side,context))


@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_engine_submission_uses_second_third_gate(side,monkeypatch):
    from test_strict_entry_gates_live import test_engine_to_account_uses_real_strict_gate_and_records_evidence
    test_engine_to_account_uses_real_strict_gate_and_records_evidence(side,'KC_SECOND_THIRD_'+side,monkeypatch)


@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_second_doji_does_not_block_third_outside_non_doji(side):
    f=frame_for(side)
    if side=='LONG':
        f.loc[f.index[-3],['open','close','high','low']]=[101.,103.,103.2,100.9]
        f.loc[f.index[-2],['open','close','high','low']]=[103.,103.,103.2,102.8]
    else:
        f.loc[f.index[-3],['open','close','high','low']]=[99.,97.,99.1,96.8]
        f.loc[f.index[-2],['open','close','high','low']]=[97.,97.,97.2,96.8]
    quote=float(f.iloc[-1].close)
    d=evaluate_entry_contract(f,quote,symbol='X')
    assert d['type']=='KC_SECOND_THIRD_'+side
    assert d['strict_gate_evidence']['candle_number']==3
    f.loc[f.index[-1],'open']=quote
    assert evaluate_entry_contract(f,quote,code=d['type'],symbol='X') is None
