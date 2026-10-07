import asyncio
import copy
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock,Mock
import pytest
from core.services.entry_gate_integrity import issue_proof,assert_commit_proof,VERSION
from core.services.entry_firewall import validate_account_entry
from test_lobster_cap_gates import frame


@pytest.fixture(autouse=True)
def stable_gate_test_clock(monkeypatch):
    # Keep the 50-cycle test within one minute; explicit expiry tests advance it.
    stamp = int(time.time()//60)*60+10.
    monkeypatch.setattr(time, 'time', lambda: stamp)


def ready(side='LONG'):
    f=frame(side);now=int(time.time()//60)*60000
    sign=1 if side=='LONG' else -1
    q=float(f.iloc[-1]['kc_upper' if sign==1 else 'kc_lower'])+sign*.4*float(f.iloc[-2].atr)
    f.loc[5,'close']=q;f.loc[5,'high']=max(q,float(f.loc[5,'open']))+.01;f.loc[5,'low']=min(q,float(f.loc[5,'open']))-.01
    f['timestamp']+=now-f.iloc[-1].timestamp
    f.attrs.update(entry_finality_verified=True,entry_finality_server_ms=time.time()*1000)
    a=SimpleNamespace(positions={},trades=[],last_closed_at={},position_meta={},save_state=Mock(),log=Mock(),entry_frame_provider=AsyncMock(return_value=f))
    c=dict(entry_signal_code='KC_LIVE_BODY_BREAKOUT_'+side,channel_confirmation_bar_id=now)
    return a,c,f

@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_final_gate_issues_evidence_and_commit_accepts(side):
    a,c,f=ready(side)
    asyncio.run(validate_account_entry(a,'CAP/USDT',side,c))
    assert_commit_proof(a,'CAP/USDT',side,c)
    assert c['entry_snapshot']['gate_version']==VERSION
    assert c['entry_snapshot']['gate_proof']['quote']==f.iloc[-1].close

@pytest.mark.parametrize('fault',['missing','quote','evidence','side','version','expired','restart'])
def test_missing_or_corrupt_proof_blocks_and_persists_symbol_halt(monkeypatch,fault):
    a,c,f=ready()
    asyncio.run(validate_account_entry(a,'CAP/USDT','LONG',c))
    p=c['entry_snapshot']['gate_proof']
    if fault=='missing':c['entry_snapshot'].pop('gate_proof')
    if fault=='quote':c['entry_snapshot']['quote_price']+=1
    if fault=='evidence':c['entry_snapshot']['evidence']['extra']='changed'
    if fault=='side':p['side']='SHORT'
    if fault=='version':p['version']='old'
    if fault=='expired':monkeypatch.setattr(time,'time',lambda:p['issued_ms']/1000+6)
    if fault=='restart':monkeypatch.setattr('core.services.entry_gate_integrity._SECRET',b'different-process-secret')
    with pytest.raises(ValueError,match='GATE_INTEGRITY_HALT'):
        assert_commit_proof(a,'CAP/USDT','LONG',c)
    assert 'CAP/USDT' in a.position_meta['_entry_gate_halts']
    assert a.save_state.called
    with pytest.raises(ValueError,match='GATE_INTEGRITY_HALT'):
        asyncio.run(validate_account_entry(a,'CAP/USDT','LONG',c))

@pytest.mark.parametrize('key',['entry_finality_server_ms','entry_quote_ms'])
@pytest.mark.parametrize('delta',[-6000,1000])
def test_stale_or_future_final_quote_is_rejected(key,delta):
    a,c,f=ready();f.attrs[key]=time.time()*1000+delta
    with pytest.raises(ValueError,match='取樣過期'):
        asyncio.run(validate_account_entry(a,'CAP/USDT','LONG',c))
    assert a.log.called


def test_many_cycles_recheck_retraced_quote_instead_of_reusing_permission():
    for i in range(50):
        a,c,f=ready('LONG' if i%2==0 else 'SHORT')
        side='LONG' if i%2==0 else 'SHORT'
        asyncio.run(validate_account_entry(a,'CAP/USDT',side,c))
        restarted=copy.deepcopy(c)
        # Same candidate and old proof cannot authorize a newly invalid quote.
        f.loc[f.index[-1],'close']=f.iloc[-1].open
        with pytest.raises(ValueError,match='FORBIDDEN_ENTRY'):
            asyncio.run(validate_account_entry(a,'CAP/USDT',side,restarted))

@pytest.mark.parametrize('fresh',[False,True])
def test_boundary_never_uses_undated_or_stale_ticker(monkeypatch,fresh):
    from core.engine import TradingEngine
    a,c,f=ready()
    original=float(f.iloc[-1].close)
    e=TradingEngine.__new__(TradingEngine)
    e.tickers={'CAP/USDT':original+.01}
    e.strategy=SimpleNamespace(compute_indicators=lambda value:value)
    e._channel_entry_quote_times={'CAP/USDT':time.time()-(0 if fresh else 30)}
    monkeypatch.setattr('core.services.entry_finality.fetch_settled_entry_frame',AsyncMock(return_value=f))
    result=asyncio.run(e._entry_boundary_frame('CAP/USDT'))
    assert result.iloc[-1].close==(original+.01 if fresh else original)
