import copy
import pytest
from core.services.exits.peak_trailing_exit import evaluate_peak_trailing, STATE_KEY


def position(side):
    return dict(side=side, symbol='SUI/USDT', entry_price=100., qty=1.,
                margin=10., leverage=10., open_timestamp=60., entry_mode='CHANNEL_SWING')


def price_for(side, roe, fee=.0005, slip=.0001):
    sign = 1 if side == 'LONG' else -1
    return (roe * 10 + sign * 100 + 100 * fee) / (sign - fee - slip)


def tick(p, roe, stamp, fee=.0005, slip=.0001):
    return evaluate_peak_trailing(p, price_for(p['side'], roe, fee, slip),
                                  {'quote_ms': stamp}, fee=fee, slippage=slip)


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
@pytest.mark.parametrize(
    ('peak', 'floor'),
    [(5.0, 3.5), (9.0, 7.5), (13.0, 11.5), (17.0, 15.5), (21.0, 19.5)],
)
def test_ui_net_roe_staged_giveback_boundaries(side, peak, floor):
    p = position(side)
    assert tick(p, (peak - .0001) / 100, 61000) is None
    if peak == 5.0:
        assert not p[STATE_KEY].get('net_roe_lock_armed')
    else:
        assert p[STATE_KEY].get('net_roe_lock_armed')
    assert p[STATE_KEY]['net_roe_peak_pct'] == pytest.approx(peak - .0001)
    assert tick(p, peak / 100, 62000) is None
    assert p[STATE_KEY]['net_roe_lock_armed']
    assert p[STATE_KEY]['net_roe_peak_pct'] == pytest.approx(peak)
    assert p[STATE_KEY]['net_roe_lock_floor_pct'] == pytest.approx(floor)
    assert tick(p, (floor + .0001) / 100, 63000) is None
    result = tick(p, floor / 100, 64000)
    assert result['trigger'] == 'NET_ROE_STAGED_GIVEBACK'
    # Restart restoration and close-failure retry keep the authorized exit.
    restored = copy.deepcopy(p)
    assert tick(restored, peak / 100, 65000)['trigger'] == result['trigger']


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_unarmed_pullback_and_stale_quote_cannot_close(side):
    p = position(side)
    assert tick(p, .049, 62000) is None
    assert tick(p, .03, 63000) is None
    assert tick(p, .07, 62000) is None
    assert not p[STATE_KEY].get('net_roe_lock_armed')


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_armed_state_survives_restart_before_giveback(side):
    p = position(side)
    tick(p, .09, 62000)
    restored = copy.deepcopy(p)
    assert tick(restored, .075, 63000)['trigger'] == 'NET_ROE_STAGED_GIVEBACK'


@pytest.mark.parametrize('margin', [0., None, float('nan')])
def test_missing_ui_margin_does_not_invent_a_percentage(margin):
    p = position('LONG')
    p['margin'] = margin
    assert tick(p, .07, 62000) is None
    assert tick(p, .05, 63000) is None
