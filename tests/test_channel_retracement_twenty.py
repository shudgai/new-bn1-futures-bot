import json
import pytest
from core.services.exits.profit_protection_service import protection

@pytest.mark.parametrize('side,sign', [('LONG', 1), ('SHORT', -1)])
@pytest.mark.parametrize('tightened', [False, True])
def test_twenty_percent_migrates_existing_peak_without_losing_ten(side, sign, tightened):
    p = dict(side=side, entry_price=100., qty=2., open_timestamp=1.)
    p['channel_profit_protection'] = dict(identity=[side, 1., 100., 2.],
        peak_gross=10., armed=True, tightened=tightened,
        stop_price=100 + sign * (4.5 if tightened else 3.5))
    p = json.loads(json.dumps(p))
    result = protection(p, 100 + sign * 4.6, .0005, .0001)
    assert result['retracement_fraction'] == (.10 if tightened else .20)
    assert result['stop_price'] == pytest.approx(100 + sign * (4.5 if tightened else 4.))
    assert not result['triggered']
    stop = result['stop_price']
    assert protection(p, stop, .0005, .0001)['triggered']
    assert protection(p, 100 + sign * 3., .0005, .0001)['stop_price'] == stop

@pytest.mark.parametrize('side,sign', [('LONG', 1), ('SHORT', -1)])
def test_new_position_keeps_net_floor_and_eighty_percent(side, sign):
    p = dict(side=side, entry_price=100., qty=2., open_timestamp=1.)
    assert protection(p, 100 + sign * .4, .0005, .0001) is None
    result = protection(p, 100 + sign * 5., .0005, .0001)
    assert result['peak_gross'] == 10.
    assert result['stop_price'] == pytest.approx(100 + sign * 4.)
    assert result['retracement_fraction'] == .20
