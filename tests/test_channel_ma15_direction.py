import pandas as pd
import pytest

from core.engine import TradingEngine


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
@pytest.mark.parametrize('case', ['aligned', 'flat', 'turn', 'invalid', 'opposite'])
@pytest.mark.parametrize('live_ma15', [1., 1000.])
def test_three_closed_ma15_controls_entry(side, case, live_ma15):
    # Broad history opposes the recent slope; unfinished MA15 must be ignored.
    up = side == 'LONG'
    rows = [dict(open=100., high=100.5, low=99.5, close=100.,
                 ma3=100., ma15=110. if up else 90.,
                 kc_upper=102., kc_lower=98.) for _ in range(70)]
    values = [99., 100., 101.] if up else [101., 100., 99.]
    if case == 'flat':
        values[0] = values[1]
    elif case == 'turn':
        values[0] = values[2]
    elif case == 'invalid':
        values[0] = float('nan')
    elif case == 'opposite':
        values.reverse()
    for index, value in zip([66, 67, 68], values):
        rows[index]['ma15'] = value
    for index in [67, 68]:
        rows[index].update(open=101.5 if up else 98.5,
                           close=102.5 if up else 97.5,
                           high=102.6 if up else 98.6,
                           low=101.4 if up else 97.4)
    rows[69]['ma15'] = live_ma15
    result = TradingEngine._channel_swing_action(
        pd.DataFrame(rows), 102.5 if up else 97.5)
    assert result['action'] == ('ENTER' if case == 'aligned' else 'WAIT')
    if case == 'aligned':
        assert result['side'] == side
