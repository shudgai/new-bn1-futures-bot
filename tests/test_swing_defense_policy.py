import copy
import subprocess
import sys
import pytest
from test_emergency_ma3_trend_hold import case
from core.services.exits.dual_track_exit_service import DualTrackExitStrategy, observe_breakeven



@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_cross_holds_when_close_still_above_support(side):
    f,p=case(side);sign=1 if side=='LONG' else -1
    f.loc[5,'ma3']=f.loc[5,'ma15']-sign*.1
    assert DualTrackExitStrategy().evaluate_exit(p,f) is None
    _,p=case(side)  # Independent no-cross case, not a pending close retry.
    f.loc[4,'ma3']=f.loc[4,'ma15']-sign*.1
    assert DualTrackExitStrategy().evaluate_exit(p,f) is None

@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_touch_ma15_or_ma3_equality_is_not_break(side):
    f,p=case(side)
    f.loc[5,'close']=f.loc[5,'ma15']
    f.loc[5,'ma3']=f.loc[5,'ma15']
    assert DualTrackExitStrategy().evaluate_exit(p,f) is None




def test_import_is_available_in_new_process():
    r=subprocess.run([sys.executable,'-c','from core.services.exits.profit_protection_service import assess_market_regime; import core.services.entry_firewall; import core.engine'],capture_output=True,text=True)
    assert r.returncode==0,r.stderr












