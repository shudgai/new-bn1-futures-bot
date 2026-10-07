import copy
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from core.services.entry_gate_integrity import issue_proof, assert_commit_proof
from test_lobster_cap_gates import frame


def signed(side):
    f=frame(side);bar=float(f.iloc[-1].timestamp)
    c=dict(entry_signal_code='KC_LIVE_BODY_BREAKOUT_'+side,channel_confirmation_bar_id=bar,signal_id='ORIGINAL',candidate_bar_id=bar)
    d=dict(type=c['entry_signal_code'],side=side,confirmation_bar_id=bar,price=float(f.iloc[-1].close),pending_signal_id='ORIGINAL_PENDING',entry_phase='KC_LIVE_BODY_BREAKOUT')
    issue_proof(c,'CAP/USDT',side,d,f)
    a=SimpleNamespace(position_meta={},save_state=Mock())
    assert_commit_proof(a,'CAP/USDT',side,c)
    return a,c

@pytest.mark.parametrize('side',['LONG','SHORT'])
@pytest.mark.parametrize('fault',['signature','quote','evidence','symbol','bar','code','expired'])
def test_corrupt_commit_evidence_is_blocked(side,fault):
    a,c=signed(side);p=c['entry_snapshot']['gate_proof']
    if fault=='signature':p['signature']='forged'
    if fault=='quote':c['entry_snapshot']['quote_price']+=1
    if fault=='evidence':c['entry_snapshot']['evidence']['forged']=True
    if fault=='symbol':p['symbol']='OTHER/USDT'
    if fault=='bar':c['channel_confirmation_bar_id']+=60000
    if fault=='code':c['entry_signal_code']='UNKNOWN'
    if fault=='expired':p['issued_ms']-=6000
    with pytest.raises(ValueError):assert_commit_proof(a,'CAP/USDT',side,c)

@pytest.mark.parametrize('side',['LONG','SHORT'])
@pytest.mark.parametrize('key',['signal_id','candidate_bar_id','pending_signal_id'])
def test_provenance_mutation_must_be_rejected_at_commit(side,key):
    a,c=signed(side)
    if key=='candidate_bar_id':
        c[key]+=60000;c['entry_snapshot'][key]=c[key]
    elif key=='signal_id':
        c[key]='REPLACED';c['entry_snapshot'][key]=c[key]
    else:c['entry_snapshot'][key]='REPLACED'
    with pytest.raises(ValueError):assert_commit_proof(a,'CAP/USDT',side,c)

@pytest.mark.parametrize('side',['LONG','SHORT'])
@pytest.mark.parametrize('key',['symbol','side','signal_code','closed_bar','gate_version'])
def test_snapshot_identity_mutation_is_rejected(side,key):
    a,c=signed(side)
    c['entry_snapshot'][key]='FORGED'
    with pytest.raises(ValueError,match='GATE_INTEGRITY_HALT'):
        assert_commit_proof(a,'CAP/USDT',side,c)
    assert a.save_state.called

@pytest.mark.parametrize('side',['LONG','SHORT'])
@pytest.mark.parametrize('key',['signal_id','candidate_bar_id'])
def test_context_identity_only_mutation_is_rejected(side,key):
    a,c=signed(side)
    c[key]='FORGED'
    with pytest.raises(ValueError,match='GATE_INTEGRITY_HALT'):
        assert_commit_proof(a,'CAP/USDT',side,c)

@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_old_process_proof_fails_after_restart(monkeypatch,side):
    a,c=signed(side)
    monkeypatch.setattr('core.services.entry_gate_integrity._SECRET',b'new-process-secret')
    with pytest.raises(ValueError,match='GATE_INTEGRITY_HALT'):
        assert_commit_proof(a,'CAP/USDT',side,c)

@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_halted_symbol_cannot_reuse_unchanged_valid_proof(side):
    a,c=signed(side)
    a.position_meta['_entry_gate_halts']={'CAP/USDT':{'reason':'prior-integrity-failure'}}
    with pytest.raises(ValueError,match='GATE_INTEGRITY_HALT'):
        assert_commit_proof(a,'CAP/USDT',side,c)
