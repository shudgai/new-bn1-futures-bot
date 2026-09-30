import pytest
from test_intraday_instant_exit import pos, observe
from core.services.exits.dual_track_exit_service import DualTrackExitStrategy, POLICY

@pytest.mark.parametrize('reason',[
    'EXIT_INTRADAY_ANOMALY_SPIKE','EXIT_BREAKEVEN_LOCK','EXIT_PROFIT_TIER2_LOCK','EXIT_INTRADAY_PROFIT_DRAWDOWN_20PCT',
    'EMERGENCY_GIANT_REVERSE_CANDLE','EMERGENCY_FLASH_SURGE_SHORT',
    'EMERGENCY_FLASH_CRASH_LONG','EMERGENCY_BTC_CRASH','MA3_TURN','TIMEOUT_EXIT'])
def test_retired_pending_cannot_retry(reason):
    p=pos()
    p['closed_exit_state']=dict(policy=POLICY,pending=True,reason=reason)
    assert DualTrackExitStrategy().evaluate_exit(p,current_price=100.) is None
    assert not p['closed_exit_state'].get('pending')

@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_removed_lines_migrate_without_dropping_peaks(side):
    p=pos(side)
    p['instant_exit_state']=dict(identity=[side,60.,100.,1.],peak=2.5,peak_gain_atr=2.5,
        pending='EXIT_PROFIT_TIER2_LOCK',tier2_line=101.,breakeven_line=100.1)
    p['closed_exit_state']=dict(policy=POLICY,pending=True,reason='EXIT_PROFIT_TIER2_LOCK')
    assert observe(p,100.,62000,atr=10.) is None
    assert p['instant_exit_state']['peak']==2.5
    assert 'tier2_line' not in p['instant_exit_state']
    assert 'breakeven_line' not in p['instant_exit_state']
    assert not p['closed_exit_state'].get('pending')

@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_large_rebound_below_profit_activation_does_not_exit(side):
    p=pos(side);sgn=1 if side=='LONG' else -1
    assert observe(p,100+sgn*.5,61000,atr=1.) is None
    assert observe(p,100-sgn*.4,62000,atr=1.) is None
    assert 'pending' not in p['instant_exit_state']

@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_invalid_legacy_instant_pending_cannot_survive(side):
    p=pos(side)
    p['instant_exit_state']=dict(identity=[side,60.,100.,1.],peak=.4,
        pending='EXIT_INTRADAY_ANOMALY_SPIKE',low=1.,high=1000.)
    p['closed_exit_state']=dict(policy=POLICY,pending=True,reason='EXIT_INTRADAY_ANOMALY_SPIKE')
    assert observe(p,100.,62000,atr=1.) is None
    assert not p['closed_exit_state'].get('pending')
    assert not p['instant_exit_state'].get('pending')
