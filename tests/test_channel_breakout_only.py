"""Only confirmed outer breaks survive scan, reentry and cached-order validation."""
from unittest.mock import AsyncMock

import pytest

from core.services.strategies.outer_strategy import aligned_entry, aligned_entry_ready, outside_reentry
from core.engine import TradingEngine
from channel_test_frames import closed_outer_entry_frame
from test_channel_swing_execution import _execution_engine, SYMBOL


@pytest.fixture
def anyio_backend():
    return 'asyncio'


def invalid_frame(side, case):
    f = closed_outer_entry_frame(side)
    sign = 1 if side == 'LONG' else -1
    rail = 'kc_upper' if side == 'LONG' else 'kc_lower'
    price = float(f.iloc[-1]['close'])
    if case in ('inside', 'touch'):
        price = float(f.iloc[-1][rail]) - (sign * .1 if case == 'inside' else 0)
    elif case in ('flat', 'reverse'):
        # CK, rather than MA3, now determines the permitted entry direction.
        f.loc[18, 'kc_middle'] = f.loc[17, 'kc_middle'] - (sign * .1 if case == 'reverse' else 0)
    elif case == 'no_cross':
        f.loc[17, 'open'] = float(f.loc[17, rail]) + sign * .1
    elif case == 'one_body':
        f.loc[17, 'open'] = f.loc[17, 'close']
    elif case == 'wrong_color':
        f.loc[18, 'open'] = f.loc[18, 'close'] + sign * .1
    elif case == 'small_body':
        f.loc[18, 'open'] = f.loc[18, 'close'] - sign * .01
    elif case == 'invalid':
        price = float('nan')
    f['high'] = f[['open', 'close']].max(axis=1) + .1
    f['low'] = f[['open', 'close']].min(axis=1) - .1
    return f, price


CASES = ['inside', 'touch', 'flat', 'reverse', 'no_cross', 'one_body', 'wrong_color', 'small_body', 'invalid']


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
@pytest.mark.parametrize('case', CASES)
def test_scan_reentry_and_legacy_flag_share_rejection(side, case):
    f, price = invalid_frame(side, case)
    assert aligned_entry(f, price)['action'] == 'WAIT'
    assert not aligned_entry_ready(f, price, side)
    assert outside_reentry(f, price, side)['action'] == 'WAIT'
    assert TradingEngine._channel_swing_action(f, price, outer_entry_only=True)['action'] == 'WAIT'


@pytest.mark.anyio
@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
@pytest.mark.parametrize('case', CASES)
@pytest.mark.parametrize('cached', [False, True])
async def test_order_rejects_invalid_breakout_despite_old_signal(side, case, cached, monkeypatch):
    f, price = invalid_frame(side, case)
    e = _execution_engine(f, side, True)
    e.account.positions.clear()
    snapshot = dict(frame=f, price=price, kc_upper=float(f.iloc[-1]['kc_upper']), kc_lower=float(f.iloc[-1]['kc_lower']))
    e._fresh_channel_entry_snapshot = AsyncMock(return_value=snapshot)
    monkeypatch.setattr('core.engine.DEFAULT_SYMBOLS', [SYMBOL])
    signal = dict(side=side, entry_mode='CHANNEL_SWING', action='ENTER_MARKET', signal_code='KC_MIDDLE_TREND_' + side)
    assert not await e._place_structured_entry(SYMBOL, signal, price, channel_snapshot=snapshot if cached else None)
    assert not e.account.events


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_ma3_turn_uses_quote_and_requires_post_entry_observation(side):
    f = closed_outer_entry_frame(side)
    f['timestamp'] = [(i + 1) * 60000 for i in range(len(f))]
    sign = 1 if side == 'LONG' else -1
    p = dict(side=side, open_timestamp=1201.)
    flat = float(f.iloc[-4]['close'])
    assert not TradingEngine._channel_live_ma3_turn_exit(p, f, flat - sign)
    assert not TradingEngine._channel_live_ma3_turn_exit(p, f, flat + sign)
    assert not TradingEngine._channel_live_ma3_turn_exit(p, f, flat)
    assert TradingEngine._channel_live_ma3_turn_exit(p, f, flat - sign)
    p['channel_live_ma3_turn_exit_pending'] = True
    p['channel_profit_protection'] = {'armed': True}
    assert TradingEngine._channel_live_ma3_turn_exit(p, None, flat + sign)


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_opposite_color_does_not_tighten_twenty_percent_protection(side):
    from core.services.exits.profit_protection_service import protection
    from test_channel_profit_protection import styled_frame
    sign = 1 if side == 'LONG' else -1
    p = dict(side=side, entry_price=100., qty=2., open_timestamp=1.)
    f = styled_frame('STACKED', side)
    protection(p, 100 + sign * 5, .0005, .0001, f)
    price = 100 + sign * 4.4
    f.loc[f.index[-1], 'open'] = price + sign
    result = protection(p, price, .0005, .0001, f)
    assert result['retracement_fraction'] == .20
    assert not result['triggered']
    assert protection(p, 100 + sign * 4, .0005, .0001, f)['triggered']


@pytest.mark.anyio
@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
async def test_failed_ma3_close_is_retried_after_quote_recovers(side):
    import json
    from test_channel_ma3_outer_exit import fixture
    f, p, price = fixture(side)
    f['atr'] = 100.
    e = _execution_engine(f, side, False)
    e.account.save_state = lambda: None
    e.account.positions[SYMBOL].update(p)
    e.tickers[SYMBOL] = price
    await e._process_single_symbol(SYMBOL, 1., None, False)
    assert len(e.account.events) == 1, e.account.logs
    assert e.account.events[0][3].endswith('LIVE_MA3_TURN_EXIT')
    e.account.positions[SYMBOL] = json.loads(json.dumps(e.account.positions[SYMBOL]))
    e.tickers[SYMBOL] = p['entry_price']
    await e._process_single_symbol(SYMBOL, 2., None, False)
    assert len(e.account.events) == 2, e.account.logs
    assert e.account.events[1][3] == e.account.events[0][3]
