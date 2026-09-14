import pandas as pd
import pytest

from core.engine import TradingEngine
from core.services.strategies.outer_strategy import (
    aligned_entry, lobster_bearish_entry_ready, ma3_ma15_kc_reversal_exit_ready,
    same_side_special_k_ready,
)


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


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_ma3_ma15_and_kc_alignment_enters_inside_the_channel(side):
    up = side == 'LONG'
    rows = [dict(open=100., high=101., low=99., close=100., ma3=100., ma15=100.,
                 kc_upper=105., kc_middle=100., kc_lower=95.) for _ in range(4)]
    if up:
        rows[1].update(ma15=99., kc_middle=99.)
        rows[2].update(ma15=100., kc_middle=100.)
        rows[3].update(ma3=101., ma15=100.5)
    else:
        rows[1].update(ma15=101., kc_middle=101.)
        rows[2].update(ma15=100., kc_middle=100.)
        rows[3].update(ma3=99., ma15=99.5)
    result = TradingEngine._channel_swing_action(pd.DataFrame(rows), 101. if up else 99.)
    assert result == {'action': 'ENTER', 'side': side, 'reason': 'KC_MA3_MA15_TREND_' + side}


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_ma3_ma15_entry_rejects_a_conflicting_live_average(side):
    up = side == 'LONG'
    rows = [dict(open=100., high=106., low=94., close=100., ma3=100., ma15=100.,
                 kc_upper=105., kc_middle=100., kc_lower=95.) for _ in range(4)]
    if up:
        rows[1].update(ma15=99., kc_middle=99.)
        rows[2].update(ma15=100., kc_middle=100.)
        rows[3].update(ma3=100., ma15=101.)
    else:
        rows[1].update(ma15=101., kc_middle=101.)
        rows[2].update(ma15=100., kc_middle=100.)
        rows[3].update(ma3=100., ma15=99.)
    assert TradingEngine._channel_swing_action(pd.DataFrame(rows), 106. if up else 94.)['action'] == 'WAIT'


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_aligned_special_k_enters_immediately(side):
    up = side == 'LONG'
    rows = [dict(open=100., high=101., low=99., close=100., ma3=100., ma15=100., atr=1.,
                 kc_upper=105., kc_middle=100., kc_lower=95.) for _ in range(4)]
    if up:
        rows[1].update(ma15=99., kc_middle=99.)
        rows[2].update(ma15=100., kc_middle=100.)
        rows[3].update(open=100., high=107., close=107., ma3=106., ma15=105.)
    else:
        rows[1].update(ma15=101., kc_middle=101.)
        rows[2].update(ma15=100., kc_middle=100.)
        rows[3].update(open=100., low=93., close=93., ma3=94., ma15=95.)
    result = aligned_entry(pd.DataFrame(rows), 107. if up else 93.)
    assert result == {'action': 'ENTER', 'side': side, 'reason': 'KC_ALIGNED_SPECIAL_K_' + side}


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_held_position_only_promotes_a_same_direction_special_k(side):
    open_price = 100.0
    price = 102.0 if side == 'LONG' else 98.0
    frame = pd.DataFrame([
        dict(open=100., high=101., low=99., close=100., atr=1., kc_upper=105., kc_lower=95.),
        dict(open=open_price, high=max(open_price, price), low=min(open_price, price), close=price,
             atr=1., kc_upper=105., kc_lower=95.),
    ])
    assert same_side_special_k_ready(frame, price, side)
    opposite = 'SHORT' if side == 'LONG' else 'LONG'
    assert not same_side_special_k_ready(frame, price, opposite)


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_two_closed_same_color_bodies_enter_without_profit_room(side):
    sign = 1 if side == 'LONG' else -1
    rows = [
        dict(open=100., high=101., low=99., close=100., ma3=100., ma15=100., kc_middle=100., kc_upper=105., kc_lower=95.),
        dict(open=100., high=102. * sign if sign > 0 else 100., low=98. if sign > 0 else 96., close=101. * sign if sign > 0 else 97., ma3=100., ma15=100., kc_middle=100., kc_upper=105., kc_lower=95.),
        dict(open=101. if sign > 0 else 97., high=103. if sign > 0 else 98., low=100. if sign > 0 else 95., close=102. if sign > 0 else 96., ma3=100., ma15=100., kc_middle=100., kc_upper=105., kc_lower=95.),
        dict(open=102. if sign > 0 else 96., high=103. if sign > 0 else 97., low=101. if sign > 0 else 95., close=102. if sign > 0 else 96., ma3=100., ma15=100., kc_middle=100., kc_upper=105., kc_lower=95.),
    ]
    result = aligned_entry(pd.DataFrame(rows), 102. if side == 'LONG' else 96.)
    assert result == {'action': 'ENTER', 'side': side, 'reason': 'KC_TWO_CLOSED_BODIES_' + side}


def test_lobster_one_hour_bearish_red_candle_allows_short_without_two_bodies():
    frame = pd.DataFrame([{'open': 100.0}, {'open': 99.0}])
    assert lobster_bearish_entry_ready('龍蝦/USDT', frame, 98.0, -1)
    assert not lobster_bearish_entry_ready('龍蝦/USDT', frame, 100.0, -1)
    assert not lobster_bearish_entry_ready('龍蝦/USDT', frame, 98.0, 1)
    assert not lobster_bearish_entry_ready('SOL/USDT', frame, 98.0, -1)


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_confirmed_kc_direction_enters_without_two_same_color_bodies(side):
    sign = 1 if side == 'LONG' else -1
    rows = [
        dict(open=100., high=101., low=99., close=100., ma3=100., ma15=100., kc_middle=100., kc_upper=102., kc_lower=98.),
        dict(open=100., high=101., low=99., close=100. - sign, ma3=100., ma15=100., kc_middle=100. - sign, kc_upper=102., kc_lower=98.),
        dict(open=100., high=101., low=99., close=100., ma3=100., ma15=100., kc_middle=100., kc_upper=102., kc_lower=98.),
        dict(open=100., high=101., low=99., close=100., ma3=100., ma15=100., kc_middle=100., kc_upper=102., kc_lower=98.),
    ]
    result = aligned_entry(pd.DataFrame(rows), 100.)
    assert result == {'action': 'ENTER', 'side': side, 'reason': 'KC_DIRECTION_' + side}


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_one_minute_ma3_ma15_kc_reversal_exits_held_position(side):
    opposite = 'SHORT' if side == 'LONG' else 'LONG'
    up = opposite == 'LONG'
    rows = [
        dict(open=100., high=101., low=99., close=100., ma3=100., ma15=100., kc_middle=100., kc_upper=102., kc_lower=98.),
        dict(open=100., high=101., low=99., close=100., ma3=100., ma15=99. if up else 101., kc_middle=99. if up else 101., kc_upper=102., kc_lower=98.),
        dict(open=100., high=101., low=99., close=100., ma3=100., ma15=100., kc_middle=100., kc_upper=102., kc_lower=98.),
        dict(open=100., high=103. if up else 101., low=99. if up else 97., close=102. if up else 98., ma3=101. if up else 99., ma15=100.5 if up else 99.5, kc_middle=100., kc_upper=102., kc_lower=98.),
    ]
    assert ma3_ma15_kc_reversal_exit_ready(pd.DataFrame(rows), 102. if up else 98., side)
