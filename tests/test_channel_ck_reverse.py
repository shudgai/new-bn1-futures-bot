import asyncio
import copy
import pytest
from core.services.strategies.outer_strategy import ck_direction, aligned_entry_ready
from core.engine import TradingEngine
from channel_test_frames import closed_outer_entry_frame
from test_channel_swing_execution import _execution_engine, SYMBOL


@pytest.fixture
def anyio_backend():
    return 'asyncio'


def setup(side, monkeypatch, success=True):
    frame = closed_outer_entry_frame(side)
    frame['timestamp'] = [(i + 1) * 60000 for i in range(len(frame))]
    frame['atr'] = 1.
    middle_key = 'kc_middle' if 'kc_middle' in frame.columns else 'ema_20'
    sign = 1 if side == 'LONG' else -1
    middle = frame[middle_key].astype(float)
    frame.loc[frame.index[-3], middle_key] = middle.iloc[-3]
    frame.loc[frame.index[-2], middle_key] = middle.iloc[-3] + sign * 3
    frame.loc[frame.index[-3], 'kc_upper'] = middle.iloc[-3] + 10.
    frame.loc[frame.index[-2], 'kc_upper'] = middle.iloc[-3] + 10. + sign
    frame.loc[frame.index[-3], 'kc_lower'] = middle.iloc[-3] - 10.
    frame.loc[frame.index[-2], 'kc_lower'] = middle.iloc[-3] - 10. + sign
    price = float(frame.iloc[-1]['close'])
    frame.loc[5, 'high' if side == 'LONG' else 'low'] = price + sign * 3.
    # CK has turned even though the moving averages still point the other way.
    frame['ma3'] = [100. - sign * i * .01 for i in range(len(frame))]
    frame['ma15'] = frame['ma3'] + sign
    old = 'SHORT' if side == 'LONG' else 'LONG'
    engine = _execution_engine(frame, old, success)
    del engine._channel_intrabar_ready
    engine.account.save_state = lambda: None
    engine.account.positions[SYMBOL].update(entry_price=price, qty=1., open_timestamp=1201.)
    engine.account.trades = []
    engine.account.last_closed_at = {}
    engine.tickers[SYMBOL] = price
    engine._abnormal_market_entry_allowed = lambda *a, **kw: True
    monkeypatch.setattr('core.engine.DEFAULT_SYMBOLS', [SYMBOL])
    monkeypatch.setattr('core.engine.time.time', lambda: 1202.)
    close = engine.account.close_position
    async def record_close(symbol, quote, reason, is_manual=False):
        result = await close(symbol, quote, reason, is_manual)
        if result:
            engine.account.last_closed_at[symbol] = 1202.
            engine.account.trades.append(dict(symbol=symbol, action='CLOSE_' + old,
                                             id=1202000, reason=reason))
        return result
    engine.account.close_position = record_close
    return frame, price, engine


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_breakout_does_not_wait_for_ma_alignment(side, monkeypatch):
    f, p, e = setup(side, monkeypatch)
    assert ck_direction(f) == side
    assert aligned_entry_ready(f, p, side)
    f.loc[f.index[-1], ['kc_middle', 'kc_upper', 'kc_lower']] = [999., 1000., 998.]
    assert ck_direction(f) == side


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_first_closed_ck_turn_and_invalid_direction(side, monkeypatch):
    f, p, e = setup(side, monkeypatch)
    sign = 1 if side == 'LONG' else -1
    f.loc[f.index[-4], 'kc_middle'] = f.iloc[-3]['kc_middle'] + sign * .1
    assert ck_direction(f) == side
    f.loc[f.index[-2], 'kc_middle'] = f.iloc[-3]['kc_middle']
    assert ck_direction(f) is None
    f.loc[f.index[-2], 'kc_middle'] = float('nan')
    assert ck_direction(f) is None


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_unclear_or_reversed_closed_ck_requires_exit(side):
    f = closed_outer_entry_frame(side)
    assert TradingEngine._channel_ck_exit_reason(f, side) is None
    f.loc[f.index[-2], 'kc_middle'] = f.iloc[-3]['kc_middle']
    assert TradingEngine._channel_ck_exit_reason(f, side) == 'KC_CK_DIRECTION_UNCLEAR_EXIT'
    f.loc[f.index[-2], 'kc_middle'] = f.iloc[-3]['kc_middle'] - (0.5 if side == 'LONG' else -0.5)
    assert TradingEngine._channel_ck_exit_reason(f, side) == 'KC_CK_DIRECTION_REVERSED_EXIT'


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_invalid_closed_ck_does_not_force_exit(side):
    f = closed_outer_entry_frame(side)
    f.loc[f.index[-2], 'kc_middle'] = float('nan')
    assert TradingEngine._channel_ck_exit_reason(f, side) is None


@pytest.mark.anyio
@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
@pytest.mark.parametrize('success', [True, False])
async def test_reverse_close_first_same_candle_and_dedup(side, success, monkeypatch):
    f, p, e = setup(side, monkeypatch, success)
    e._channel_entry_minute = {SYMBOL: 20}
    await asyncio.gather(*(e._try_ck_reverse(SYMBOL, f, p, False) for _ in range(2)))
    kinds = [event[0] for event in e.account.events]
    assert kinds == (['close', 'open'] if success else ['close', 'close'])
    assert e.account.positions[SYMBOL]['side'] == (side if success else ('SHORT' if side == 'LONG' else 'LONG'))


@pytest.mark.anyio
@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
@pytest.mark.parametrize('block', ['daily', 'room', 'no_fill'])
async def test_close_is_not_delayed_by_new_leg_risk(side, block, monkeypatch):
    f, p, e = setup(side, monkeypatch)
    if block == 'room':
        f.loc[5, 'high' if side == 'LONG' else 'low'] = p
    if block == 'no_fill':
        async def unrecorded_close(symbol, quote, reason, is_manual=False):
            e.account.events.append(('close',))
            e.account.positions.pop(symbol)
            return True
        e.account.close_position = unrecorded_close
    await e._try_ck_reverse(SYMBOL, f, p, block == 'daily')
    assert [event[0] for event in e.account.events] == ['close']
    assert SYMBOL not in e.account.positions


@pytest.mark.anyio
@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
async def test_restart_recovers_close_ticket_but_not_consumed_ticket(side, monkeypatch):
    f, p, e = setup(side, monkeypatch)
    await e._try_ck_reverse(SYMBOL, f, p, True)
    ticket = copy.deepcopy(e.account.channel_profit_reentries[SYMBOL])
    ticket['phase'] = 'closing'
    e.account.channel_profit_reentries[SYMBOL] = ticket
    await e._try_profit_reentry(SYMBOL, f, p, False)
    assert [event[0] for event in e.account.events] == ['close', 'open']
    e.account.positions.clear()
    e.account.channel_profit_reentries[SYMBOL] = ticket
    ticket['phase'] = 'closed'
    e.account.trades.append(dict(symbol=SYMBOL, action='OPEN_' + side, id=1202001))
    await e._try_profit_reentry(SYMBOL, f, p, False)
    assert len(e.account.events) == 2


@pytest.mark.anyio
@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
@pytest.mark.parametrize('armed', [True, False])
async def test_quote_path_reverses_even_when_profit_protection_armed(side, armed, monkeypatch):
    f, p, e = setup(side, monkeypatch)
    e.is_running = True
    e._channel_exit_frames = {SYMBOL: f}
    e.account.positions[SYMBOL]['channel_profit_protection'] = {'armed': armed}
    await e._channel_quote_exit(SYMBOL, p, 1202000)
    assert [event[0] for event in e.account.events] == ['close', 'open'], e.account.logs


@pytest.mark.anyio
@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
@pytest.mark.parametrize('risk', ['daily', 'crash', 'adverse', 'expired', 'direction'])
async def test_reversal_retry_rechecks_risk_and_confirmation(side, risk, monkeypatch):
    f, p, e = setup(side, monkeypatch)
    await e._try_ck_reverse(SYMBOL, f, p, True)
    if risk == 'daily':
        e.account.daily_loss_limit_hit = lambda: (True, .1)
    elif risk == 'crash':
        e._market_crash_entry_cooldown_until = 1300.
    elif risk == 'adverse':
        f.loc[f.index[-1], 'open'] = p + (10. if side == 'LONG' else -10.)
    elif risk == 'expired':
        f['timestamp'] += 60000
    else:
        f.loc[f.index[-2], 'kc_middle'] = f.iloc[-3]['kc_middle']
    await e._try_profit_reentry(SYMBOL, f, p, False)
    assert [event[0] for event in e.account.events] == ['close']


@pytest.mark.anyio
@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
async def test_reversal_can_enter_inside_channel_without_waiting_for_breakout(side, monkeypatch):
    f, p, e = setup(side, monkeypatch)
    f['kc_upper'] += 5.
    f['kc_lower'] -= 5.
    assert not aligned_entry_ready(f, p, side)
    await e._try_ck_reverse(SYMBOL, f, p, False)
    assert [event[0] for event in e.account.events] == ['close', 'open']


def test_testnet_persists_reversal_ticket(tmp_path, monkeypatch):
    import core.testnet_account as module
    monkeypatch.setattr(module, 'DATA_DIR', str(tmp_path))
    monkeypatch.setattr(module, 'STATE_FILE', str(tmp_path / 'account.json'))
    account = module.BinanceTestnetAccount(None)
    account.channel_profit_reentries[SYMBOL] = dict(mode='ck_reverse', phase='closing', token='pending')
    account.save_state()
    restored = module.BinanceTestnetAccount(None)
    assert restored.channel_profit_reentries == account.channel_profit_reentries
