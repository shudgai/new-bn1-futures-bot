import copy
import json
import pytest
from core.channel_entry_diagnostics import entry_diagnostics
from core.services.swing_service import significant_ma3_turn
from test_channel_live_pivot import prepare, quote, anyio_backend
from test_channel_significant_ma3 import setup as ma_setup
from test_channel_swing_execution import SYMBOL


@pytest.mark.anyio
@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
@pytest.mark.parametrize('failure', ['fetch', 'reversal', 'adverse'])
async def test_transient_live_failure_allows_fresh_turn_same_candle(side, failure, monkeypatch):
    f, p, e, clock = prepare(side, monkeypatch)
    sign = 1 if side == 'LONG' else -1
    for offset in [0, -.1, -.09]: quote(e, clock, p + sign * offset)
    original = e.fetch_klines
    async def bad(*args, **kwargs):
        if failure == 'fetch': return f.iloc[:0].copy()
        if failure == 'reversal': quote(e, clock, p - sign * .2)
        if failure == 'adverse': f.loc[f.index[-1], 'open'] = p + sign * 10
        return await original(*args, **kwargs)
    e.fetch_klines = bad
    await e._channel_quote_pivot_entry(SYMBOL, p - sign * .09)
    assert not e.account.events
    assert not getattr(e, '_channel_invalid_entry_candidates', set())
    e.fetch_klines = original
    f.loc[f.index[-1], 'open'] = p
    for offset in [-.3, -.29]: quote(e, clock, p + sign * offset)
    await e._channel_quote_pivot_entry(SYMBOL, p - sign * .29)
    assert [x[0] for x in e.account.events] == ['open'], e.account.logs


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
@pytest.mark.parametrize('fraction', [0., .001, .01, .05, .099, .10, .11])
def test_ma3_actual_reverse_slope_must_reach_fixed_threshold(side, fraction):
    f, p, sign = ma_setup(side)
    significant_ma3_turn(p, f, 100.)
    significant_ma3_turn(p, f, 100. + sign * .9)
    # Peak retracement exceeds 0.10 ATR already, but a tiny line slope is not enough.
    assert significant_ma3_turn(p, f, 100. - sign * fraction * 3) is (fraction >= .10)


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_migrate_old_tiny_slope_pending_without_losing_extreme(side):
    f, p, sign = ma_setup(side)
    significant_ma3_turn(p, f, 100.)
    significant_ma3_turn(p, f, 100. + sign * .9)
    state = p['channel_significant_ma3_turn']
    state.update(version=2, pending=True)
    extreme, threshold = state['extreme'], state['threshold']
    assert not significant_ma3_turn(p, f, 100. - sign * .03)
    assert state['version'] == 3 and not state['pending']
    assert state['extreme'] == extreme and state['threshold'] == threshold
    assert significant_ma3_turn(p, f, 100. - sign * .3)
    p = json.loads(json.dumps(p))
    assert significant_ma3_turn(p, f, 100. + sign * .6)


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_chart_refresh_cannot_create_consume_or_reset_a_live_turn(side, monkeypatch):
    f, p, e, clock = prepare(side, monkeypatch)
    sign = 1 if side == 'LONG' else -1
    for offset in [0, -.1, -.09]: quote(e, clock, p + sign * offset)
    before = copy.deepcopy(e._channel_live_pivots.states)
    for _ in range(5):
        d = entry_diagnostics(e, SYMBOL, f, p - sign * .09, clock[0])
        assert d['pivot_ready'] and d['reason'] == 'KC_ENTRY_READY'
    assert before == e._channel_live_pivots.states
    entry_diagnostics(e, SYMBOL, f, p - sign * .2, clock[0])
    assert before == e._channel_live_pivots.states


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_diagnostics_show_room_block_even_when_pivot_qualifies(side, monkeypatch):
    f, p, e, clock = prepare(side, monkeypatch)
    sign = 1 if side == 'LONG' else -1
    f.loc[5, 'high' if side == 'LONG' else 'low'] = p
    for offset in [0, -.1, -.09]: quote(e, clock, p + sign * offset)
    d = entry_diagnostics(e, SYMBOL, f, p - sign * .09, clock[0])
    assert d['pivot_ready']
    assert d['reason'] in ('KC_PROFIT_TARGET_UNAVAILABLE', 'KC_PROFIT_ROOM_INSUFFICIENT')


def test_diagnostics_explain_pepe_middle_up_upper_down(monkeypatch):
    f, p, e, clock = prepare('LONG', monkeypatch)
    quote(e, clock, p)
    f.loc[f.index[-2], 'kc_upper'] = f.iloc[-3]['kc_upper'] - .01
    d = entry_diagnostics(e, SYMBOL, f, p, clock[0])
    assert d['reason'] == 'KC_DIRECTION_WAIT'
    assert '持倉側外軌不得逆向' in d['detail']


@pytest.mark.anyio
@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
async def test_actual_chart_loader_is_read_only(side, monkeypatch):
    import services.api as api
    f, p, e, clock = prepare(side, monkeypatch)
    e.pivot_prealerts = {}
    monkeypatch.setattr(api, 'engine', e)
    sign = 1 if side == 'LONG' else -1
    for offset in [0, -.1, -.09]: quote(e, clock, p + sign * offset)
    before = copy.deepcopy(e._channel_live_pivots.states)
    for _ in range(2):
        response = await api._load_klines(SYMBOL, '1m', 200, True)
        assert response['entry_block']['reason'] == 'KC_ENTRY_READY'
    assert e._channel_live_pivots.states == before
    assert e.account.events == []


@pytest.mark.anyio
@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
async def test_existing_candidate_lock_is_not_cleared_by_recovery(side, monkeypatch):
    f, p, e, clock = prepare(side, monkeypatch)
    sign = 1 if side == 'LONG' else -1
    for offset in [0, -.1, -.09]: quote(e, clock, p + sign * offset)
    key = (SYMBOL, side, 'live:' + str(float(f.iloc[-1]['timestamp'])))
    e._channel_invalid_entry_candidates = {key}
    await e._channel_quote_pivot_entry(SYMBOL, p - sign * .09)
    assert e.account.events == []
    assert e._channel_invalid_entry_candidates == {key}


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_pepe_scale_tiny_slope_does_not_exit(side):
    f, p, sign = ma_setup(side)
    scale = .0033 / 100.
    for col in ['open', 'high', 'low', 'close', 'atr', 'kc_upper', 'kc_lower']:
        f[col] *= scale
    p['entry_price'] *= scale
    assert not significant_ma3_turn(p, f, 100. * scale)
    assert not significant_ma3_turn(p, f, (100. + sign * .9) * scale)
    assert not significant_ma3_turn(p, f, (100. - sign * .03) * scale)
    assert significant_ma3_turn(p, f, (100. - sign * .30) * scale)
