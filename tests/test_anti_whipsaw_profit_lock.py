import copy
import pytest
from test_intraday_instant_exit import pos, observe

@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_retired_tiers_do_not_close(side):
    p=pos(side);sgn=1 if side=='LONG' else -1
    def tick(gain,stamp):return observe(p,100+sgn*gain,stamp,atr=1.)
    assert tick(1.,61000) is None
    assert tick(0.,62000) is None
    assert tick(1.799,63000) is None
    assert tick(.05,64000) is None
    assert tick(1.8,65000) is None
    assert tick(.101,66000) is None
    assert tick(.099,67000) is None

@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_old_tier2_line_does_not_close(side):
    p=pos(side);sgn=1 if side=='LONG' else -1
    assert observe(p,100+sgn*2.5,61000,atr=1.) is None
    assert observe(p,100+sgn*1.001,62000,atr=1.) is None
    assert observe(p,100+sgn*1.,63000,atr=1.) is None

@pytest.mark.parametrize('side',['LONG','SHORT'])
@pytest.mark.parametrize('qty,peak,atr',[(1,10,10),(.1,3,1)])
def test_tier3_either_usdt_or_atr_and_strict_drawdown(side,qty,peak,atr):
    p=pos(side);p['qty']=qty;sgn=1 if side=='LONG' else -1
    assert observe(p,100+sgn*peak,61000,atr=atr) is None
    assert observe(p,100+sgn*peak*.75,62000,atr=atr) is None
    assert observe(p,100+sgn*(peak*.75-.001),63000,atr=atr)=='EXIT_PEAK_DRAWDOWN_25PCT'

@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_restart_does_not_restore_retired_line(side):
    p=pos(side);sgn=1 if side=='LONG' else -1
    observe(p,100+sgn*2.5,61000,atr=1.)
    restored=copy.deepcopy(p)
    assert observe(restored,100+sgn*.9,62000,atr=0.) is None

def test_old_peak_migrated_without_fabricating_old_atr():
    p=pos()
    p['instant_exit_state']=dict(identity=['SHORT',60.,100.,1.],peak=14.,last_ms=61000)
    assert observe(p,89.5,62000,atr=100.) is None
    assert p['instant_exit_state']['peak_gain_atr']==.105
    assert observe(p,89.51,63000,atr=100.)=='EXIT_PEAK_DRAWDOWN_25PCT'

def test_retired_spike_pending_is_revoked():
    p=pos()
    p['instant_exit_state']=dict(identity=['SHORT',60.,100.,1.],peak=14.,last_ms=61000,
                                pending='EXIT_INTRADAY_ANOMALY_SPIKE')
    assert observe(p,90.,62000,atr=1.)=='EXIT_PEAK_DRAWDOWN_25PCT'
