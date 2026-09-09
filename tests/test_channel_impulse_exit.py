"""2026-09-09: exit after favorable impulses reverse; small candles alone keep holding."""
import pandas as pd
import pytest

from core.engine import TradingEngine
from test_channel_swing_execution import _execution_engine, SYMBOL


@pytest.fixture(autouse=True)
def abnormal_threshold(monkeypatch):
    monkeypatch.setattr('core.engine.RAPID_PIVOT_IMMEDIATE_REVERSE_BODY_ATR', 0.5)


@pytest.fixture
def anyio_backend():
    return 'asyncio'


def _turn_frame(side, timing='live', bodies=(0.2, 0.2), reverse=0.6):
    frame = pd.DataFrame({
        'open': [102.] * 20, 'close': [102.] * 20,
        'high': [102.4] * 20, 'low': [101.2] * 20,
        'ma3': [102.3] * 20, 'ma15': [101.8] * 20,
        'kc_upper': [102.] * 20, 'kc_lower': [98.] * 20,
        'atr': [1.] * 20, 'volume': [150.] * 20, 'vol_ma_20': [100.] * 20,
    })
    turn = 19 if timing == 'live' else 18
    frame.loc[turn - 2, ['open', 'close']] = [102. - sum(bodies), 102. - bodies[-1]]
    frame.loc[turn - 1, ['open', 'close']] = [102. - bodies[-1], 102.]
    frame.loc[turn, ['open', 'close', 'ma3']] = [102., 102. - reverse, 102.2]
    if timing == 'closed':
        frame.loc[19, ['open', 'close', 'ma3']] = [102. - reverse, 102. - reverse, 102.2]
    frame['high'] = frame[['high', 'open', 'close']].max(axis=1)
    frame['low'] = frame[['low', 'open', 'close']].min(axis=1)
    if side == 'SHORT':
        for column in ('open', 'close', 'ma3', 'ma15'):
            frame[column] = 200. - frame[column]
        frame['high'], frame['low'] = 200. - frame['low'], 200. - frame['high']
    return frame


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
@pytest.mark.parametrize('timing', ['live', 'closed'])
@pytest.mark.parametrize('bodies', [(0.2, 0.2), (0.3, 0.7), (0.6, -0.2)])
def test_ordinary_run_then_long_reversal_holds(side, timing, bodies):
    frame = _turn_frame(side, timing, bodies)
    result = TradingEngine._channel_swing_action(frame, frame.iloc[-1]['close'], side)
    assert result['action'] == 'HOLD', result


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
@pytest.mark.parametrize('timing', ['live', 'closed'])
@pytest.mark.parametrize('bodies', [(0.2, 1.2), (0.6, 0.6)])
def test_favorable_impulse_then_long_pivot_exits(side, timing, bodies):
    frame = _turn_frame(side, timing, bodies)
    result = TradingEngine._channel_swing_action(frame, frame.iloc[-1]['close'], side)
    assert result['action'] == 'EXIT'


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
@pytest.mark.parametrize('reverse', [0., 0.2])
def test_favorable_waterfall_without_long_reversal_holds(side, reverse):
    frame = _turn_frame(side, bodies=(0.2, 1.2), reverse=reverse)
    assert TradingEngine._channel_swing_action(frame, frame.iloc[-1]['close'], side)['action'] == 'HOLD'


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
@pytest.mark.parametrize('timing', ['live', 'closed'])
def test_adverse_waterfall_without_structure_failure_holds(side, timing):
    frame = _turn_frame(side, timing, reverse=1.2)
    result = TradingEngine._channel_swing_action(frame, frame.iloc[-1]['close'], side)
    assert result['action'] == 'HOLD'


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_two_abnormal_adverse_bars_without_structure_failure_hold(side):
    frame = _turn_frame(side, bodies=(-0.6, -0.6), reverse=0.1)
    result = TradingEngine._channel_swing_action(frame, frame.iloc[-1]['close'], side)
    assert result['action'] == 'HOLD'


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
@pytest.mark.parametrize('atr', [0., float('nan'), float('inf'), None])
def test_invalid_atr_does_not_turn_normal_pivot_into_exit(side, atr):
    frame = _turn_frame(side)
    frame['atr'] = atr
    assert TradingEngine._channel_swing_action(frame, frame.iloc[-1]['close'], side)['action'] == 'HOLD'


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_long_wick_without_reversal_body_holds(side):
    frame = _turn_frame(side, bodies=(0.2, 1.2), reverse=0.1)
    frame.loc[19, 'low' if side == 'LONG' else 'high'] = 90. if side == 'LONG' else 110.
    assert TradingEngine._channel_swing_action(frame, frame.iloc[-1]['close'], side)['action'] == 'HOLD'


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_live_pivot_uses_latest_price_when_frame_close_lags(side):
    frame = _turn_frame(side, bodies=(0.2, 1.2))
    price = frame.iloc[-1]['close']
    frame.loc[19, 'close'] = frame.loc[19, 'open']
    assert TradingEngine._channel_swing_action(frame, price, side)['action'] == 'EXIT'


@pytest.mark.anyio
@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
@pytest.mark.parametrize('scenario', ['ordinary', 'favorable', 'adverse', 'pair'])
@pytest.mark.parametrize('close_ok', [False, True])
async def test_impulse_execution_only_closes_after_favorable_run(side, scenario, close_ok):
    bodies = {'ordinary': (0.2, 0.2), 'favorable': (0.6, 0.6), 'adverse': (0.2, 0.2), 'pair': (-0.6, -0.6)}[scenario]
    frame = _turn_frame(side, bodies=bodies, reverse=1.2 if scenario == 'adverse' else 0.6)
    engine = _execution_engine(frame, side, close_ok)
    engine.tickers[SYMBOL] = frame.iloc[-1]['close']
    engine.market_prebreakout_directions = {}
    engine.st_direction_1h_cache = {}
    engine._channel_swing_peak_exit_info = {}
    engine._channel_max_net_loss_action = lambda *_: {'action': 'HOLD'}
    _, candidates = await engine._process_single_symbol(SYMBOL, 1., None, False)
    assert not any('處理失敗' in message for message, _ in engine.account.logs), engine.account.logs
    assert candidates == []
    assert [event[0] for event in engine.account.events] == (['close'] if scenario == 'favorable' else [])
    assert (SYMBOL in engine.account.positions) is (scenario != 'favorable' or not close_ok)


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_ordinary_pivot_near_converged_ma15_still_holds(side):
    frame = _turn_frame(side, timing='closed')
    rail = 'kc_upper' if side == 'LONG' else 'kc_lower'
    direction = 1. if side == 'LONG' else -1.
    for index, gap in zip(range(14, 19), [0.9, 0.75, 0.55, 0.35, 0.1]):
        frame.loc[index, 'ma15'] = frame.loc[index, rail] - direction * gap
    frame.loc[18, 'ma3'] = frame.loc[18, rail] + direction * 0.05
    assert TradingEngine._channel_swing_action(frame, frame.iloc[-1]['close'], side)['action'] == 'HOLD'


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_opposite_confirmed_outer_break_reverses_even_with_abnormal_pair(side):
    frame = _turn_frame('LONG')
    frame.loc[17, ['open', 'close']] = [101.8, 102.6]
    frame.loc[18, ['open', 'close']] = [102.6, 103.2]
    frame.loc[19, ['open', 'close']] = [103.2, 103.2]
    frame['high'] = frame[['high', 'open', 'close']].max(axis=1)
    frame['low'] = frame[['low', 'open', 'close']].min(axis=1)
    if side == 'LONG':
        for column in ('open', 'close', 'ma3', 'ma15'):
            frame[column] = 200. - frame[column]
        frame['high'], frame['low'] = 200. - frame['low'], 200. - frame['high']
    result = TradingEngine._channel_swing_action(frame, frame.iloc[-1]['close'], side)
    assert result == {'action': 'REVERSE', 'side': 'SHORT' if side == 'LONG' else 'LONG', 'reason': 'KC_LOWER_BREAKOUT' if side == 'LONG' else 'KC_UPPER_BREAKOUT'}


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
@pytest.mark.parametrize('timing', ['live', 'closed'])
@pytest.mark.parametrize('space', [0.5, 0.65])
def test_outer_ma3_with_half_channel_room_holds_turn(side, timing, space):
    frame = _turn_frame(side, timing, bodies=(0.6, 0.6))
    width = frame['kc_upper'] - frame['kc_lower']
    frame['ma15'] = frame['kc_upper'] - width * space if side == 'LONG' else frame['kc_lower'] + width * space
    result = TradingEngine._channel_swing_action(frame, frame.iloc[-1]['close'], side)
    assert result['action'] == 'HOLD', result


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_half_space_guard_requires_ma3_to_stay_outside(side):
    frame = _turn_frame(side)
    frame['ma15'] = 100.
    prior, current = frame.iloc[-2].copy(), frame.iloc[-1].copy()
    assert TradingEngine._channel_outer_half_space_hold(prior, current, side)
    current['ma3'] = 100.
    assert not TradingEngine._channel_outer_half_space_hold(prior, current, side)
    current, prior = prior, current
    assert not TradingEngine._channel_outer_half_space_hold(prior, current, side)


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
@pytest.mark.parametrize('timing', ['live', 'closed'])
def test_long_shadow_with_small_body_never_triggers_impulse_exit(side, timing):
    frame = _turn_frame(side, timing, bodies=(0.6, 0.6), reverse=0.1)
    turn = 19 if timing == 'live' else 18
    frame.loc[turn, ['high', 'low']] = [120., 80.]
    assert TradingEngine._channel_swing_action(frame, frame.iloc[-1]['close'], side)['action'] == 'HOLD'


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
@pytest.mark.parametrize('timing', ['live', 'closed'])
def test_expanding_gap_overrides_favorable_impulse_exit(side, timing):
    f = _turn_frame(side, timing, bodies=(.6, .6))
    rail = 'kc_upper' if side == 'LONG' else 'kc_lower'
    for index, gap in zip((16, 17, 18), (.05, .1, .2)):
        f.loc[index, 'ma15'] = f.loc[index, rail] + (-gap if side == 'LONG' else gap)
    result = TradingEngine._channel_swing_action(f, float(f.iloc[-1]['close']), side)
    assert result['action'] == 'HOLD'


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
@pytest.mark.parametrize('count', [1, 2, 5])
@pytest.mark.parametrize('body,expected', [(.2, 'HOLD'), (.6, 'EXIT')])
def test_small_candle_run_never_accumulates_into_favorable_impulse(side, count, body, expected):
    f = _turn_frame('LONG', bodies=(0., 0.), reverse=1.2)
    for index in range(19-count, 19):
        close = 102. - (18-index)*body
        f.loc[index, ['open', 'close']] = [close-body, close]
    if side == 'SHORT':
        for column in ('open', 'close', 'ma3', 'ma15'):
            f[column] = 200. - f[column]
    # One ordinary long candle is not a waterfall or a two-candle run.
    if count == 1: expected = 'HOLD'
    assert TradingEngine._channel_swing_action(f, float(f.iloc[-1]['close']), side)['action'] == expected


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
@pytest.mark.parametrize('timing', ['live', 'closed'])
@pytest.mark.parametrize('favorable', [True, False])
def test_two_adverse_bars_require_immediately_preceding_favorable_run(side, timing, favorable):
    f = _turn_frame('LONG', timing, bodies=(0., 0.), reverse=.6)
    end = 19 if timing == 'live' else 18
    body = .6 if favorable else .2
    f.loc[end-3, ['open', 'close']] = [102.-2*body, 102.-body]
    f.loc[end-2, ['open', 'close']] = [102.-body, 102.]
    f.loc[end-1, ['open', 'close']] = [102., 101.4]
    f.loc[end, ['open', 'close']] = [101.4, 100.8]
    if timing == 'closed': f.loc[19, ['open', 'close']] = 100.8
    if side == 'SHORT':
        for column in ('open', 'close', 'ma3', 'ma15'):
            f[column] = 200. - f[column]
    assert TradingEngine._channel_impulse_turn_allowed(f, side, -1 if timing == 'live' else -2, float(f.iloc[-1]['close'])) is favorable


@pytest.mark.anyio
@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
@pytest.mark.parametrize('close_ok', [False, True])
async def test_impulse_exit_repeated_scans_never_reopen_old_signal(side, close_ok):
    f = _turn_frame(side, bodies=(.6, .6))
    e = _execution_engine(f, side, close_ok)
    e.tickers[SYMBOL] = float(f.iloc[-1]['close'])
    e.market_prebreakout_directions = {}; e.st_direction_1h_cache = {}
    e._channel_swing_peak_exit_info = {}
    for scan in range(3):
        await e._process_single_symbol(SYMBOL, float(scan), None, False)
    assert [event[0] for event in e.account.events] == (['close'] if close_ok else ['close'] * 3)
    assert (SYMBOL in e.account.positions) is (not close_ok)
    assert bool(e._channel_swing_peak_exit_info) is close_ok
    assert not any('處理失敗' in message for message, _ in e.account.logs)
