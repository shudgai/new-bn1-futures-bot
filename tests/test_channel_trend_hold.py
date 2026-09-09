"""Trend continuation and closed structural exits, latest user specification."""
import asyncio
import pandas as pd
import pytest
from core.engine import TradingEngine
from test_channel_swing_execution import _execution_engine, SYMBOL

@pytest.fixture
def anyio_backend(): return 'asyncio'


def frame_for(side='SHORT', ratio=.4):
    f = pd.DataFrame({'open': [98.] * 20, 'close': [98.1] * 20,
                      'high': [98.5] * 20, 'low': [97.5] * 20,
                      'kc_upper': [105.] * 20, 'kc_lower': [95.] * 20,
                      'ma3': [97.8] * 20, 'ma15': [100.] * 20, 'atr': [1.] * 20,
                      'volume': [100.] * 20, 'vol_ma_20': [100.] * 20,
                      'timestamp': list(range(20))})
    lower, upper = 100. - ratio * 5., 100. + ratio * 5.
    f.loc[17:19, ['kc_lower', 'kc_upper', 'ma15']] = [lower, upper, lower + .4]
    f.loc[17, 'ma3'] = lower - .2
    f.loc[18:19, 'ma3'] = lower + .2
    if side == 'LONG':
        for column in ('open', 'close', 'ma3', 'ma15'):
            f[column] = 200. - f[column]
        f['high'], f['low'] = 200. - f['low'], 200. - f['high']
    return f

@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
@pytest.mark.parametrize('ratio,expected', [(.3, 'EXIT'), (.4, 'EXIT'), (.401, 'HOLD'), (.5, 'HOLD')])
def test_compression_boundary_with_closed_ma3_reentry(side, ratio, expected):
    f = frame_for(side, ratio)
    result = TradingEngine._channel_swing_action(f, f.iloc[-1]['close'], side)
    assert result['action'] == expected, result
    if expected == 'EXIT': assert result['reason'] == 'KC_MA3_REENTER_EXIT'

@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
@pytest.mark.parametrize('invalid', ['live_only', 'outside', 'already_inside', 'ma15_far', 'wick'])
def test_narrowing_alone_or_unconfirmed_cross_holds(side, invalid):
    f = frame_for(side)
    outside = 102.2 if side == 'LONG' else 97.8
    inside = 101.8 if side == 'LONG' else 98.2
    if invalid in ('live_only', 'outside', 'wick'): f.loc[18, 'ma3'] = outside
    if invalid in ('outside', 'wick'): f.loc[19, 'ma3'] = outside
    if invalid == 'already_inside': f.loc[17, 'ma3'] = inside
    if invalid == 'ma15_far': f.loc[18:19, 'ma15'] = 100.
    if invalid == 'wick': f.loc[18:19, ['high', 'low']] = [120., 80.]
    assert TradingEngine._channel_swing_action(f, f.iloc[-1]['close'], side)['action'] == 'HOLD'

@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_entry_channel_width_preserves_compression_reference(side):
    f = frame_for(side)
    f.loc[:16, ['kc_lower', 'kc_upper']] = [98., 102.]
    assert TradingEngine._channel_swing_action(f, 100., side)['action'] == 'HOLD'
    assert TradingEngine._channel_swing_action(f, 100., side, entry_kc_upper=105., entry_kc_lower=95.)['action'] == 'EXIT'

@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
@pytest.mark.parametrize('confirmed', [False, True])
def test_structure_failure_needs_two_closed_prices_and_adverse_channel(side, confirmed):
    f = frame_for('SHORT', ratio=1.)
    for index, middle, ma3, close in [(16, 100., 99.8, 99.8), (17, 100.1, 100.3, 100.4), (18, 100.2, 100.5, 100.6)]:
        f.loc[index, ['kc_lower', 'kc_upper', 'ma3', 'close', 'ma15']] = [middle - 2., middle + 2., ma3, close, 100.25]
    if not confirmed: f.loc[17, 'close'] = 99.9
    if side == 'LONG':
        for col in ('open', 'close', 'ma3', 'ma15'): f[col] = 200. - f[col]
        f['kc_upper'], f['kc_lower'] = 200. - f['kc_lower'], 200. - f['kc_upper']
    result = TradingEngine._channel_swing_action(f, f.iloc[-1]['close'], side)
    assert result['action'] == ('EXIT' if confirmed else 'HOLD'), result
    if confirmed: assert result['reason'] == 'KC_STRUCTURE_FAILURE_EXIT'

@pytest.mark.anyio
@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
@pytest.mark.parametrize('close_ok', [False, True])
async def test_exit_then_repeated_scans_do_not_reopen_old_signal(side, close_ok):
    f = frame_for(side)
    e = _execution_engine(f, side, close_ok)
    e.tickers[SYMBOL] = float(f.iloc[-1]['close'])
    e.market_prebreakout_directions = {}; e.st_direction_1h_cache = {}
    e._channel_swing_peak_exit_info = {}
    e._channel_max_net_loss_action = lambda *_: {'action': 'HOLD'}
    for i in range(3): await e._process_single_symbol(SYMBOL, float(i), None, False)
    assert not any('處理失敗' in message for message, _ in e.account.logs), e.account.logs
    assert [event[0] for event in e.account.events] == (['close'] if close_ok else ['close'] * 3)
    assert (SYMBOL in e.account.positions) is (not close_ok)
    assert bool(e._channel_swing_peak_exit_info) is close_ok

@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_only_new_closed_body_break_releases_exit_lock(side):
    f = frame_for(side)
    info = {'side': side, 'require_new_closed_break': True, 'exit_bar_id': 19, 'bar_count': 100}
    gate = TradingEngine._channel_peak_exit_reentry_blocked
    assert gate('ENTER', False, side, f, info, SYMBOL)
    for i in (20, 21, 22):
        f.loc[i] = f.loc[19].copy(); f.loc[i, 'timestamp'] = i
    if side == 'LONG':
        f.loc[20, ['open', 'close']] = [101.9, 102.2]
        f.loc[21, ['open', 'close']] = [102.2, 102.4]
    else:
        f.loc[20, ['open', 'close']] = [98.1, 97.8]
        f.loc[21, ['open', 'close']] = [97.8, 97.6]
    assert not gate('ENTER', False, side, f, info, SYMBOL)
    assert TradingEngine._channel_peak_reversal_action(f, f.iloc[-1]['close'], info)['action'] == 'WAIT'
    f.loc[21, 'close'] = f.loc[21, 'open']
    assert gate('ENTER', False, side, f, info, SYMBOL)

@pytest.mark.anyio
@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
async def test_post_exit_gate_is_checked_again_at_order_time(side, monkeypatch):
    monkeypatch.setattr('core.engine.DEFAULT_SYMBOLS', [SYMBOL])
    f = frame_for(side)
    e = _execution_engine(f, side, True)
    e.account.positions.clear()
    e._channel_swing_peak_exit_info = {SYMBOL: {'side': side, 'require_new_closed_break': True, 'exit_bar_id': 19}}
    # A live outer signal cannot bypass the exit lock through direct execution.
    assert await e._fresh_channel_entry_snapshot(SYMBOL, side, allow_live_outer=True) is None
    assert not await e._execute_confirmed_channel_break(SYMBOL, f, 103. if side == 'LONG' else 97., side)
    assert e.account.events == []
    for i in (20, 21, 22):
        f.loc[i] = f.loc[19].copy(); f.loc[i, 'timestamp'] = i
    if side == 'LONG':
        f.loc[20, ['open', 'close']] = [101.9, 102.2]
        f.loc[21, ['open', 'close']] = [102.2, 102.4]
    else:
        f.loc[20, ['open', 'close']] = [98.1, 97.8]
        f.loc[21, ['open', 'close']] = [97.8, 97.6]
    f['high'] = f[['high', 'open', 'close']].max(axis=1)
    f['low'] = f[['low', 'open', 'close']].min(axis=1)
    e.tickers[SYMBOL] = float(f.loc[21, 'close'])
    e._abnormal_market_entry_allowed = lambda *_: True
    # Fresh confirmed breakout may reopen once through the actual account route.
    results = await asyncio.gather(*(e._execute_confirmed_channel_break(SYMBOL, f, e.tickers[SYMBOL], side) for _ in range(3)))
    assert sum(results) == 1, e.account.logs
    assert [event[0] for event in e.account.events] == ['open']
    assert SYMBOL not in e._channel_swing_peak_exit_info

@pytest.mark.parametrize('entry_upper,entry_lower', [(105., None), (float('inf'), 95.), (95.,105.)])
def test_incomplete_entry_rails_do_not_invent_a_compression_reference(entry_upper, entry_lower):
    f = frame_for('SHORT')
    f.loc[:16, ['kc_lower', 'kc_upper']] = [98., 102.]
    assert TradingEngine._channel_swing_action(f, 98.1, 'SHORT', entry_kc_upper=entry_upper, entry_kc_lower=entry_lower)['action'] == 'HOLD'
