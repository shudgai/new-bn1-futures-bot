"""Profit floor, peak retracement and recovery order regressions."""
import json
from unittest.mock import AsyncMock

import pandas as pd
import pytest
from test_channel_outer_cycle import setup as outer_cycle_market
from test_channel_pivot_entry import market as pivot_market

from core.channel_profit_protection import protection, reentry_gate, trend_style
from core.engine import TradingEngine
from test_channel_swing_execution import _execution_engine, SYMBOL
from test_channel_symmetric_rules import market as confirmed_reentry_frame


@pytest.fixture
def anyio_backend():
    return 'asyncio'


def position(side):
    return dict(side=side, entry_price=100., qty=2., open_timestamp=1.)


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_net_floor_includes_both_fees_and_adverse_slippage(side):
    p = position(side)
    sign = 1 if side == 'LONG' else -1
    assert protection(p, 100 + sign * .5, .0005, .0001) is None
    result = protection(p, 100 + sign, .0005, .0001)
    assert not result['triggered']
    floor = p['channel_profit_protection']['net_floor_price']
    execution = floor * (1 - sign * .0001)
    net = sign * (execution - 100) * 2 - (100 + execution) * 2 * .0005
    assert net == pytest.approx(1.)


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_peak_retracement_monotonic_and_survives_json_restart(side):
    p = position(side)
    sign = 1 if side == 'LONG' else -1
    peak = protection(p, 100 + sign * 5, .0005, .0001)
    assert peak['peak_gross'] == 10.
    assert peak['stop_price'] == pytest.approx(100 + sign * 4.0)
    p = json.loads(json.dumps(p))
    before = protection(p, 100 + sign * 4.1, .0005, .0001)
    assert not before['triggered']
    assert before['stop_price'] == peak['stop_price']
    assert protection(p, 100 + sign * 4.0, .0005, .0001)['triggered']
    p['open_timestamp'] = 2.
    assert protection(p, 100., .0005, .0001) is None
    assert not p['channel_profit_protection']['armed']


def frame():
    return pd.DataFrame(dict(open=[103.] * 12, close=[103.1] * 12,
                             high=[103.2] * 12, low=[102.9] * 12,
                             kc_upper=[102.] * 12, kc_lower=[98.] * 12,
                             ma3=[103.] * 12, ma15=[101.] * 12,
                             timestamp=list(range(12))))


def test_long_anomaly_requires_pullback_then_reclaim():
    f = frame()
    f.loc[11, ['open', 'high', 'low', 'close']] = [108., 108., 102.5, 103.]
    ticket = {'side': 'LONG'}
    assert reentry_gate(ticket, f, 103.) == 'wait'
    assert reentry_gate(ticket, f, 102.) == 'wait'
    assert ticket['pullback_bar'] == 11
    assert reentry_gate(ticket, f, 102.1) == 'wait'
    fresh = confirmed_reentry_frame('LONG')
    assert reentry_gate(ticket, fresh, 104.5) == 'ready'
    # A newer pullback invalidates the earlier breakout.
    assert reentry_gate(ticket, fresh, 102.) == 'wait'
    assert ticket['pullback_bar'] == 19
    assert reentry_gate(ticket, fresh, 104.5) == 'wait'


@pytest.mark.parametrize('side,price', [('LONG', 103.1), ('SHORT', 97.)])
def test_ordinary_outer_reentry_requires_pullback_and_new_confirmation(side, price):
    f = confirmed_reentry_frame(side)
    outside = float(f.iloc[-1]['close'])
    rail = 102. if side == 'LONG' else 98.
    ticket = {'side':side}
    assert reentry_gate(ticket, f, outside) == 'wait'
    assert reentry_gate(ticket, f, rail) == 'wait'
    assert ticket['pullback_bar'] == 19
    # The next breakout/confirmation pair occurs after the observed pullback.
    later = f.copy(); later.index = later.index + 3
    assert reentry_gate(ticket, later, outside) == 'ready'
    assert reentry_gate(ticket, later, rail) == 'wait'
    assert ticket['pullback_bar'] == 22
    assert reentry_gate(ticket, later, outside) == 'wait'


@pytest.mark.anyio
@pytest.mark.parametrize('close_ok', [False, True])
async def test_profit_close_must_succeed_before_same_side_reentry(close_ok):
    f = frame()
    e = _execution_engine(f, 'LONG', close_ok)
    e.account.save_state = lambda: None
    e.account.positions[SYMBOL].update(position('LONG'))
    protection(e.account.positions[SYMBOL], 105., .0005, .0001)
    e.tickers[SYMBOL] = 103.1
    e._channel_swing_action = lambda *a, **k: {'action':'HOLD'}
    e._place_structured_entry = AsyncMock(return_value=True)
    await e._process_single_symbol(SYMBOL, 1., None, False)
    assert len(e.account.events) == 1, e.account.logs
    assert 'PROFIT_PROTECTION' in e.account.events[0][3]
    e._place_structured_entry.assert_not_awaited()
    if close_ok:
        assert SYMBOL not in e.account.positions
        assert e.account.channel_profit_reentries[SYMBOL]['phase'] == 'closed'
        e._channel_swing_action = TradingEngine._channel_swing_action
        f.loc[11,'open'] = 102.
        await e._try_profit_reentry(SYMBOL, f, 102., False)
        e._place_structured_entry.assert_not_awaited()
        fresh, price = outer_cycle_market('LONG')
        await e._try_profit_reentry(SYMBOL, fresh, 100., False)
        await e._try_profit_reentry(SYMBOL, fresh, price, False)
        e._place_structured_entry.assert_awaited_once()
        assert e._place_structured_entry.call_args.args[1]['side'] == 'LONG'
    else:
        assert SYMBOL in e.account.positions
        await e._try_profit_reentry(SYMBOL, pivot_market('LONG'), 98.1, False)
        e._place_structured_entry.assert_not_awaited()
    assert SYMBOL not in e.account.channel_profit_reentries


@pytest.mark.anyio
async def test_general_exit_has_priority_over_profit_close():
    e = _execution_engine(frame(), 'LONG', True)
    e.account.save_state = lambda: None
    e.account.positions[SYMBOL].update(position('LONG'))
    protection(e.account.positions[SYMBOL], 105., .0005, .0001)
    e.tickers[SYMBOL] = 103.1
    e._channel_swing_action = lambda *a, **k: {'action': 'EXIT', 'reason': 'GENERAL_EXIT'}
    e._place_structured_entry = AsyncMock()
    await e._process_single_symbol(SYMBOL, 1., None, False)
    assert e.account.events[0][3] == 'Channel Swing GENERAL_EXIT', e.account.logs
    e._place_structured_entry.assert_not_awaited()
    assert e.account.channel_profit_reentries[SYMBOL]["requires_pullback"]
    assert e.account.channel_profit_reentries[SYMBOL]["phase"] == "closed"


@pytest.mark.anyio
@pytest.mark.parametrize('matched', [False, True])
async def test_restart_requires_matching_successful_close(matched):
    f = pivot_market('LONG')
    e = _execution_engine(f, 'LONG', True)
    e.account.positions.clear()
    e.account.save_state = lambda: None
    e.account.channel_profit_reentries = {SYMBOL: dict(side='LONG', token='abc', phase='closing',
        pulled_back_inside=True, pullback_bar=17., exit_bar_id=17.)}
    e.account.trades = [dict(symbol=SYMBOL, action='CLOSE_LONG',
                            reason='Channel Swing PROFIT_PROTECTION abc')] if matched else []
    e._place_structured_entry = AsyncMock(return_value=True)
    await e._try_profit_reentry(SYMBOL, f, 98.1, False)
    e._place_structured_entry.assert_not_awaited()
    assert (SYMBOL in e.account.channel_profit_reentries) is matched
    if matched:
        fresh, price = outer_cycle_market('LONG')
        await e._try_profit_reentry(SYMBOL, fresh, 100., False)
        await e._try_profit_reentry(SYMBOL, fresh, price, False)
        e._place_structured_entry.assert_awaited_once()
    assert SYMBOL not in e.account.channel_profit_reentries



def styled_frame(style, side='LONG'):
    f = frame()
    f['timestamp'] = [i * 1000 for i in range(len(f))]
    if style == 'STACKED':
        f.loc[8:10, 'open'] = [101., 102., 103.]
        f.loc[8:10, 'close'] = [102., 103., 104.]
        f.loc[11, ['open', 'close']] = 105.
    elif style == 'SMOOTH':
        f['open'] = [100.2 + i * .2 for i in range(len(f))]
        f['close'] = f['open'] + .15
        f['kc_upper'] = f['close'] + .2
        f['kc_lower'] = f['kc_upper'] - 4.
    f['high'] = f[['open', 'close']].max(axis=1) + .1
    f['low'] = f[['open', 'close']].min(axis=1) - .1
    if side == 'SHORT':
        for field in ('open', 'close', 'high', 'low', 'kc_upper', 'kc_lower'):
            f[field] = 200 - f[field]
        f['high'], f['low'] = f['low'].copy(), f['high'].copy()
        f['kc_upper'], f['kc_lower'] = f['kc_lower'].copy(), f['kc_upper'].copy()
    return f


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
@pytest.mark.parametrize('style', ['STACKED', 'SMOOTH', 'CHOPPY'])
def test_style_classification(side, style):
    assert trend_style(styled_frame(style, side), side, 1.) == style


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_smooth_trend_arms_twenty_percent_protection(side):
    p = position(side)
    sign = 1 if side == 'LONG' else -1
    f = styled_frame('SMOOTH', side)
    first = protection(p, 100 + sign * 5, .0005, .0001, f)
    assert first['retracement_fraction'] == .20
    assert p['channel_profit_protection']['armed']
    result = protection(p, 100 + sign * 5, .0005, .0001, styled_frame('CHOPPY', side))
    assert result['stop_price'] == pytest.approx(100 + sign * 4.0)
    # Reclassification cannot remove or loosen the existing eight-dollar line.
    result = protection(p, 100 + sign * 4.0, .0005, .0001, f)
    assert result['triggered']


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_stack_live_opposite_tightens_ten_dollar_peak_to_nine(side):
    p = position(side)
    sign = 1 if side == 'LONG' else -1
    f = styled_frame('STACKED', side)
    result = protection(p, 100 + sign * 5, .0005, .0001, f)
    assert result['stop_price'] == pytest.approx(100 + sign * 4.0)
    assert result['retracement_fraction'] == .20  # Live doji is not opposite.
    # close remains favorable/doji in the frame; ticker alone forms the red K.
    result = protection(p, 100 + sign * 4.8, .0005, .0001, f)
    assert result['retracement_fraction'] == .10
    assert result['stop_price'] == pytest.approx(100 + sign * 4.5)
    assert not result['triggered']
    assert protection(p, 100 + sign * 4.5, .0005, .0001, f)['triggered']
    p = json.loads(json.dumps(p))
    result = protection(p, 100 + sign * 6, .0005, .0001, styled_frame('SMOOTH', side))
    assert result['retracement_fraction'] == .10
    assert result['stop_price'] == pytest.approx(100 + sign * 5.4)
    p['open_timestamp'] = 100.
    assert protection(p, 100., .0005, .0001, f) is None
    assert not p['channel_profit_protection'].get('tightened')


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_first_opposite_tick_already_beyond_ten_percent_exits(side):
    p = position(side)
    sign = 1 if side == 'LONG' else -1
    f = styled_frame('STACKED', side)
    protection(p, 100 + sign * 5, .0005, .0001, f)
    assert protection(p, 100 + sign * 4.4, .0005, .0001, f)['triggered']


def test_pre_entry_stack_does_not_tighten_and_live_bar_cannot_complete_stack():
    f = styled_frame('STACKED')
    assert trend_style(f, 'LONG', 11.) != 'STACKED'
    f.loc[9, 'close'] = f.loc[9, 'open']
    assert trend_style(f, 'LONG', 1.) != 'STACKED'
    p = position('LONG')
    protection(p, 105., .0005, .0001, f)
    result = protection(p, 104.4, .0005, .0001, f)
    assert result['retracement_fraction'] == .20
    assert not result['triggered']


def test_ten_percent_never_arms_below_net_one_dollar():
    p = position('LONG')
    f = styled_frame('STACKED')
    assert protection(p, 100.5, .0005, .0001, f) is None
    assert not p['channel_profit_protection']['armed']


@pytest.mark.anyio
@pytest.mark.parametrize('action', ['HOLD', 'EXIT', 'REVERSE'])
async def test_engine_passes_live_frame_and_preserves_exit_priority(action):
    f = styled_frame('STACKED')
    e = _execution_engine(f, 'LONG', True)
    e.account.save_state = lambda: None
    e.account.positions[SYMBOL].update(position('LONG'))
    # The first scan must recognize stacking through the real integration.
    e._channel_swing_action = lambda *a, **k: {'action': 'HOLD'}
    e.tickers[SYMBOL] = 105.
    await e._process_single_symbol(SYMBOL, 1., None, False)
    assert e.account.positions[SYMBOL]['channel_profit_protection']['stacked_seen']
    e.tickers[SYMBOL] = 104.4
    reason = 'KC_LOWER_BREAKOUT' if action == 'REVERSE' else 'GENERAL_EXIT'
    e._channel_swing_action = lambda *a, **k: {'action': action, 'reason': reason, 'side': 'SHORT'}
    e._execute_confirmed_channel_break = AsyncMock()
    e._try_profit_reentry = AsyncMock()
    await e._process_single_symbol(SYMBOL, 2., None, False)
    if action == 'REVERSE':
        e._execute_confirmed_channel_break.assert_awaited_once()
        assert e.account.events == []
    else:
        assert len(e.account.events) == 1, e.account.logs
        close_reason = e.account.events[0][3]
        assert ('PROFIT_PROTECTION' in close_reason) is (action == 'HOLD')
        assert e._try_profit_reentry.await_count == int(action == 'HOLD')



def test_tightening_on_opposite_tick_honors_previously_observed_peak():
    p = position('LONG')
    assert protection(p, 105., .0005, .0001, styled_frame('SMOOTH'))['retracement_fraction'] == .20
    result = protection(p, 104.4, .0005, .0001, styled_frame('STACKED'))
    assert result['peak_gross'] == 10.
    assert result['stop_price'] == 104.5
    assert result['triggered']



@pytest.mark.anyio
@pytest.mark.parametrize('style', ['CHOPPY', 'STACKED', 'SMOOTH'])
async def test_unarmed_middle_exit_uses_price_for_every_style(style):
    f = styled_frame(style)
    middle = (float(f.iloc[-1]['kc_upper'])+float(f.iloc[-1]['kc_lower']))/2
    e = _execution_engine(f, 'LONG', True)
    e.account.save_state = lambda: None
    # Entry above all tested prices ensures protection cannot arm.
    e.account.positions[SYMBOL].update(position('LONG'), entry_price=110.)
    # Enter after the existing turn; this case isolates the middle-price exit.
    e.account.positions[SYMBOL]['open_timestamp'] = float(f.iloc[-1]['timestamp']) / 1000
    e._channel_swing_action = lambda *a, **k: {'action':'EXIT', 'reason':'KC_REACHED_MIDDLE_COMPRESSED'}
    e.tickers[SYMBOL] = middle + .01
    await e._process_single_symbol(SYMBOL, 1., None, False)
    assert not e.account.events
    assert SYMBOL in e.account.positions
    e.tickers[SYMBOL] = middle
    await e._process_single_symbol(SYMBOL, 2., None, False)
    assert len(e.account.events) == 1, e.account.logs
    assert e.account.events[0][3].endswith('KC_LONG_UNARMED_MIDDLE_EXIT')
    assert SYMBOL not in e.account.positions


@pytest.mark.anyio
async def test_middle_signal_cannot_preempt_profit_exit_or_cancel_reentry():
    f = frame()
    e = _execution_engine(f, 'LONG', True)
    e.account.save_state = lambda: None
    e.account.positions[SYMBOL].update(position('LONG'))
    protection(e.account.positions[SYMBOL], 105., .0005, .0001)
    e.tickers[SYMBOL] = 103.1
    e._channel_swing_action = lambda *a, **k: {'action':'EXIT', 'reason':'KC_REACHED_MIDDLE_COMPRESSED'}
    e._place_structured_entry = AsyncMock(return_value=True)
    await e._process_single_symbol(SYMBOL, 1., None, False)
    assert 'PROFIT_PROTECTION' in e.account.events[0][3], e.account.logs
    assert e.account.channel_profit_reentries[SYMBOL]['phase'] == 'closed'
    e._place_structured_entry.assert_not_awaited()
    e._channel_swing_action = TradingEngine._channel_swing_action
    f.loc[11,'open'] = 102.
    await e._try_profit_reentry(SYMBOL, f, 102., False)
    fresh, price = outer_cycle_market('LONG')
    await e._try_profit_reentry(SYMBOL, fresh, 100., False)
    await e._try_profit_reentry(SYMBOL, fresh, price, False)
    e._place_structured_entry.assert_awaited_once()


@pytest.mark.parametrize('style', ['CHOPPY', 'SMOOTH'])
def test_reopened_position_reclassifies_without_previous_ten_percent_lock(style):
    p = position('LONG')
    protection(p, 105., .0005, .0001, styled_frame('STACKED'))
    protection(p, 104.4, .0005, .0001, styled_frame('STACKED'))
    assert p['channel_profit_protection']['tightened']
    # Even an accidentally retained state must reset for a new position identity.
    p['open_timestamp'] = 20.
    result = protection(p, 101., .0005, .0001, styled_frame(style))
    assert p['channel_profit_protection']['trend_style'] == style
    assert not p['channel_profit_protection'].get('tightened')
    assert result['retracement_fraction'] == .20
    assert p['channel_profit_protection']['armed']



def test_unknown_style_still_arms_twenty_percent_protection():
    p = position('LONG')
    f = frame().iloc[:2]
    result = protection(p, 105., .0005, .0001, f)
    assert result['trend_style'] == 'UNKNOWN'
    assert result['retracement_fraction'] == .20
    assert result['stop_price'] == 104.0
    assert protection(p, 104.0, .0005, .0001, f)['triggered']
