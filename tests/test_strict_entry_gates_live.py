"""Exercise the standalone strict-entry validator's current contract."""
import time

import pandas as pd
import pytest

from core.services.strict_entry_gates import validate_strict_entry


def frame_for(side='LONG'):
    stamp = int(time.time() // 60) * 60000
    rows = []
    for i in range(60):
        mid = 100 + i * .001
        rows.append(dict(timestamp=stamp-(60-i)*60000, open=100., close=100.1,
                         high=101., low=99., atr=1., ma3=100., ma5=100.4,
                         ma15=99., kc_middle=mid, kc_lower=mid-2, kc_upper=mid+2,
                         is_closed=True))
    rows[30]['high'] = 110.
    rows[-1].update(open=102., close=103., high=103.2, low=101.9)
    rows.append(dict(timestamp=stamp, open=101., close=103., high=103.1, low=100.9,
                     atr=1., ma3=102., ma5=100.5, ma15=99., kc_middle=100.06,
                     kc_lower=98.06, kc_upper=102.06, is_closed=False))
    f = pd.DataFrame(rows)
    if side == 'SHORT':
        source = f.copy()
        for a,b in [('open','open'),('close','close'),('high','low'),('low','high'),
                    ('ma3','ma3'),('ma5','ma5'),('ma15','ma15'),
                    ('kc_middle','kc_middle'),('kc_upper','kc_lower'),('kc_lower','kc_upper')]:
            f[a] = 200-source[b]
    f.attrs.update(timeframe_ms=60000, entry_finality_verified=True,
                   entry_finality_server_ms=time.time()*1000)
    return f


@pytest.mark.parametrize('side', ['LONG','SHORT'])
def test_valid_symmetric_gate_and_recorded_costs(side):
    f = frame_for(side)
    passed, reason, evidence = validate_strict_entry(f, f.iloc[-1].close, side)
    assert passed, reason
    assert evidence['passed']
    assert evidence['body_atr'] >= .5
    assert 0 < evidence['distance_atr'] <= 1.5


@pytest.mark.parametrize(('failure', 'expected_reason'), [
    ('inside', 'BLOCKED_STRICT_LIVE_DOJI'),
    ('body', 'BLOCKED_STRICT_BODY_BELOW_HALF_ATR'),
    ('ma5', 'BLOCKED_BY_MA5_SLOPE_DOWN'),
    ('narrow', 'BLOCKED_STRICT_CHANNEL_CONVERGENCE'),
    ('ma_spacing', 'BLOCKED_STRICT_MA_SPACING'),
    ('rail_spacing', 'BLOCKED_STRICT_RAIL_SPACING'),
    ('bad_data', 'BLOCKED_STRICT_INVALID_LIVE_DATA'),
    ('gap', 'BLOCKED_STRICT_INVALID_LIVE_DATA'),
    ('distance', 'BLOCKED_BY_EXTENDED_BREAKOUT'),
])
def test_strict_validator_rejects_invalid_live_evidence(failure, expected_reason):
    f = frame_for()
    quote = 103.
    changes = {
        'inside': {'close':101.}, 'body': {'open':102.8, 'low':102.7},
        'ma5': {'ma5':100.4},
        'ma_spacing': {'ma15':100.4}, 'rail_spacing': {'ma5':102.},
        'bad_data': {'ma5':float('nan')}, 'gap': {'open':97.},
    }
    for key,value in changes.get(failure,{}).items():
        f.loc[f.index[-1],key] = value
    if failure == 'inside': quote=101.
    if failure == 'distance': quote=106.
    if failure == 'narrow':
        f.loc[f.index[-5], ['kc_lower','kc_upper']] = [80.,120.]
    passed, reason, evidence = validate_strict_entry(f, quote, 'LONG')
    assert not passed
    assert reason == expected_reason
    assert evidence['passed'] is False


@pytest.mark.parametrize('side', ['LONG','SHORT'])
def test_same_side_continuation_requires_prior_outside_and_ck(side):
    f = frame_for(side)
    sign = 1 if side=='LONG' else -1
    f.loc[f.index[-1],'open'] = 100+sign*2.3
    assert validate_strict_entry(f,100+sign*3,side)[0]
    f.loc[f.index[-2], ['open','close','high','low']] = [100.,100.,104.,96.]
    assert validate_strict_entry(f,100+sign*3,side)[1] == 'BLOCKED_STRICT_PREVIOUS_NOT_OUTSIDE'
    f = frame_for(side)
    f.loc[f.index[-1],'open'] = 100+sign*2.3
    f.loc[f.index[-2],'kc_middle'] = f.iloc[-3].kc_middle
    assert validate_strict_entry(f,100+sign*3,side)[1] == 'BLOCKED_STRICT_CK_DIRECTION'


def test_half_atr_boundary_and_current_extension_cap():
    f = frame_for()
    f.loc[f.index[-1],'open'] = 102.5
    assert validate_strict_entry(f,103.,'LONG')[0]
    f.loc[f.index[-1],'open'] = 102.50001
    assert validate_strict_entry(f,103.,'LONG')[1] == 'BLOCKED_STRICT_BODY_BELOW_HALF_ATR'
    f = frame_for()
    edge = float(f.iloc[-1].kc_upper)
    assert validate_strict_entry(f, edge+1.5, 'LONG')[0]
    assert validate_strict_entry(f, edge+1.50001, 'LONG')[1] == 'BLOCKED_BY_EXTENDED_BREAKOUT'


def test_validator_fails_closed_without_history_or_live_bar():
    f=frame_for()
    assert not validate_strict_entry(f.tail(30),103.,'LONG')[0]
    assert not validate_strict_entry(f.iloc[:-1],103.,'LONG')[0]
