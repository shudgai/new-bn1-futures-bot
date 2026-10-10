import copy
import pytest
from core.services.exits.peak_trailing_exit import evaluate_peak_trailing, STATE_KEY, migrate_peak_state


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
@pytest.mark.parametrize('roe', [5.0, 9.0, 13.0, 21.0])
def test_ui_net_roe_profit_lock_is_disabled(side, roe):
    p = position(side)
    assert tick(p, roe / 100, 61000) is None
    assert tick(p, (roe - 2.) / 100, 62000) is None
    assert not p[STATE_KEY].get('net_roe_lock_armed')
    assert 'net_roe_lock_floor_pct' not in p[STATE_KEY]


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_unarmed_pullback_and_stale_quote_cannot_close(side):
    p = position(side)
    assert tick(p, .049, 62000) is None
    assert tick(p, .03, 63000) is None
    assert tick(p, .07, 62000) is None
    assert not p[STATE_KEY].get('net_roe_lock_armed')


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_legacy_armed_state_is_cleared_on_restart(side):
    p = position(side)
    p[STATE_KEY] = dict(identity=['LONG', 60., 100., 1.], pending='EXIT_REALTIME_PEAK_TRAILING',
                        trigger='NET_ROE_STAGED_GIVEBACK', net_roe_peak_pct=9.,
                        net_roe_lock_armed=True, net_roe_lock_floor_pct=7.5)
    meta = {STATE_KEY: copy.deepcopy(p[STATE_KEY])}
    migrate_peak_state(p, meta)
    restored = copy.deepcopy(p)
    assert tick(restored, .075, 63000) is None
    assert not restored[STATE_KEY].get('pending')
    assert not meta[STATE_KEY].get('net_roe_lock_armed')


@pytest.mark.parametrize('margin', [0., None, float('nan')])
def test_missing_ui_margin_does_not_invent_a_percentage(margin):
    p = position('LONG')
    p['margin'] = margin
    assert tick(p, .07, 62000) is None
    assert tick(p, .05, 63000) is None
