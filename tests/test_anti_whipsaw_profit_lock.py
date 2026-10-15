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
def test_restart_does_not_restore_retired_line(side):
    p=pos(side);sgn=1 if side=='LONG' else -1
    observe(p,100+sgn*2.5,61000,atr=1.)
    restored=copy.deepcopy(p)
    assert observe(restored,100+sgn*.9,62000,atr=0.) is None


