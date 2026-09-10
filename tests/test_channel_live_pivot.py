import asyncio
import pytest
from core.channel_live_pivot import LivePivot
from core.channel_outer_entry import aligned_entry_ready
from test_channel_ck_reverse import setup
from test_channel_swing_execution import SYMBOL


@pytest.fixture
def anyio_backend():
    return 'asyncio'


def prepare(side, monkeypatch):
    frame, price, engine = setup(side, monkeypatch)
    engine.account.positions.clear()
    engine.symbol_rotation.last_rotation_at = 1.
    engine.is_running = True
    engine._channel_exit_frames = {SYMBOL: frame}
    # Inside CK, mixed completed candles: no old breakout/continuation can qualify.
    frame['kc_upper'] += 5.
    frame['kc_lower'] -= 5.
    assert not aligned_entry_ready(frame, price, side)
    clock = [1202.]
    monkeypatch.setattr('core.engine.time.time', lambda: clock[0])
    return frame, price, engine, clock


def quote(engine, clock, price):
    clock[0] += .1
    engine.tickers[SYMBOL] = price
    engine._observe_channel_entry_quote(SYMBOL, price, clock[0] * 1000)


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_first_actual_reversal_needs_no_extra_candle_or_amplitude(side, monkeypatch):
    f, p, e, clock = prepare(side, monkeypatch)
    sign = 1 if side == 'LONG' else -1
    for offset, expected in [(0, False), (-.1, False), (-.099999, True), (-.099999, True), (-.2, False)]:
        quote(e, clock, p + sign * offset)
        assert e._live_pivot_ready(SYMBOL, f, p + sign * offset, side) is expected


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
@pytest.mark.parametrize('invalid', ['bar', 'side', 'stale', 'missing', 'nan', 'held', 'adverse', 'out_of_order', 'clock_rollover'])
def test_invalid_turn_cannot_be_reused(side, invalid, monkeypatch):
    f, p, e, clock = prepare(side, monkeypatch)
    sign = 1 if side == 'LONG' else -1
    for offset in [0, -.1, -.09]:
        quote(e, clock, p + sign * offset)
    p -= sign * .09
    assert e._live_pivot_ready(SYMBOL, f, p, side)
    if invalid == 'bar':
        f['timestamp'] += 60000
        clock[0] += 60
    elif invalid == 'side':
        f.loc[f.index[-2], 'kc_middle'] = f.iloc[-3]['kc_middle']
    elif invalid == 'clock_rollover':
        clock[0] = 1259.4
        for offset in [0, -.1, -.09]:
            quote(e, clock, p + sign * offset)
        p -= sign * .09
        assert e._live_pivot_ready(SYMBOL, f, p, side)
        clock[0] = 1260.01
    elif invalid == 'stale':
        clock[0] += 6
    elif invalid == 'missing':
        e._channel_entry_quote_times.clear()
    elif invalid == 'nan':
        p = float('nan')
    elif invalid == 'held':
        e.account.positions[SYMBOL] = {'side': side}
    elif invalid == 'adverse':
        f.loc[f.index[-1], 'open'] = p + sign * 10
    else:
        e._channel_entry_quote_times[SYMBOL] -= .01
    assert not e._live_pivot_ready(SYMBOL, f, p, side)


@pytest.mark.anyio
@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
async def test_quote_path_executes_on_first_turn_and_deduplicates(side, monkeypatch):
    f, p, e, clock = prepare(side, monkeypatch)
    sign = 1 if side == 'LONG' else -1
    for offset in [0, -.1]:
        quote(e, clock, p + sign * offset)
        await e._channel_quote_pivot_entry(SYMBOL, p + sign * offset)
        assert e.account.events == []
    quote(e, clock, p - sign * .09)
    await asyncio.gather(*(e._channel_quote_pivot_entry(SYMBOL, p - sign * .09) for _ in range(2)))
    assert [event[0] for event in e.account.events] == ['open'], e.account.logs
    assert e.account.positions[SYMBOL]['side'] == side


@pytest.mark.anyio
@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
@pytest.mark.parametrize('risk', ['room', 'daily', 'frequency', 'rotation', 'adverse', 'latest_reverse', 'bar'])
async def test_order_rechecks_risk_and_turn(side, risk, monkeypatch):
    f, p, e, clock = prepare(side, monkeypatch)
    sign = 1 if side == 'LONG' else -1
    for offset in [0, -.1, -.09]:
        quote(e, clock, p + sign * offset)
    price = p - sign * .09
    if risk == 'room':
        f.loc[5, 'high' if side == 'LONG' else 'low'] = p
    elif risk == 'daily':
        e.account.daily_loss_limit_hit = lambda: (True, .1)
    elif risk == 'frequency':
        e._channel_entry_minute = {SYMBOL: 20}
    elif risk == 'rotation':
        monkeypatch.setattr('core.engine.SYMBOL_ROTATION_ENABLED', True)
        e.symbol_rotation.last_rotation_at = 0
    elif risk == 'adverse':
        f.loc[f.index[-1], 'open'] = price + sign * 10
    else:
        fetch = e.fetch_klines
        async def changed(*args, **kwargs):
            if risk == 'latest_reverse':
                quote(e, clock, p - sign * .2)
            else:
                f['timestamp'] += 60000
                clock[0] += 60
                quote(e, clock, price)
            return await fetch(*args, **kwargs)
        e.fetch_klines = changed
    await e._channel_quote_pivot_entry(SYMBOL, price)
    assert e.account.events == [], e.account.logs


@pytest.mark.anyio
@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
@pytest.mark.parametrize('abnormal', [False, True])
async def test_normal_reentry_uses_live_turn_abnormal_ticket_keeps_pullback(side, abnormal, monkeypatch):
    f, p, e, clock = prepare(side, monkeypatch)
    e.account.channel_profit_reentries = {SYMBOL: dict(token='closed', phase='closed', side=side,
        mode='outer_cycle', requires_pullback=abnormal, exit_bar_id=1140000,
        close_reason='Channel Swing EMERGENCY_EXIT_2_CANDLE_ADVERSE' if abnormal else 'Channel Swing PROFIT_PROTECTION closed')}
    sign = 1 if side == 'LONG' else -1
    for offset in [0, -.1, -.09]:
        quote(e, clock, p + sign * offset)
    await e._channel_quote_pivot_entry(SYMBOL, p - sign * .09)
    assert [event[0] for event in e.account.events] == ([] if abnormal else ['open']), e.account.logs


def test_no_completed_wick_or_restart_can_invent_a_turn(monkeypatch):
    f, p, e, clock = prepare('LONG', monkeypatch)
    f.loc[f.index[-1], ['low', 'high']] = [p - 20, p + 20]
    quote(e, clock, p)
    assert not e._live_pivot_ready(SYMBOL, f, p, 'LONG')
    e._channel_live_pivots = LivePivot()
    quote(e, clock, p + .01)
    assert not e._live_pivot_ready(SYMBOL, f, p + .01, 'LONG')


@pytest.mark.anyio
@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
async def test_scan_uses_same_live_entry_inside_channel(side, monkeypatch):
    f, p, e, clock = prepare(side, monkeypatch)
    sign = 1 if side == 'LONG' else -1
    for offset in [0, -.1, -.09]:
        quote(e, clock, p + sign * offset)
    await e._process_single_symbol(SYMBOL, clock[0], None, False)
    assert [event[0] for event in e.account.events] == ['open'], e.account.logs


@pytest.mark.anyio
@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
async def test_failed_order_can_retry_without_consuming_turn(side, monkeypatch):
    f, p, e, clock = prepare(side, monkeypatch)
    sign = 1 if side == 'LONG' else -1
    for offset in [0, -.1, -.09]:
        quote(e, clock, p + sign * offset)
    original = e.account.open_position
    async def fail(**kwargs):
        return False
    e.account.open_position = fail
    await e._channel_quote_pivot_entry(SYMBOL, p - sign * .09)
    e.account.open_position = original
    await e._channel_quote_pivot_entry(SYMBOL, p - sign * .09)
    assert [event[0] for event in e.account.events] == ['open'], e.account.logs


@pytest.mark.anyio
@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
async def test_final_quote_change_during_execution_check_cancels(side, monkeypatch):
    f, p, e, clock = prepare(side, monkeypatch)
    sign = 1 if side == 'LONG' else -1
    for offset in [0, -.1, -.09]:
        quote(e, clock, p + sign * offset)
    async def changed(*args):
        quote(e, clock, p - sign * .2)
        return True
    e._execution_price_is_safe = changed
    await e._channel_quote_pivot_entry(SYMBOL, p - sign * .09)
    assert e.account.events == []


@pytest.mark.anyio
@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
async def test_successful_close_this_minute_still_blocks_pivot(side, monkeypatch):
    f, p, e, clock = prepare(side, monkeypatch)
    e.account.last_closed_at[SYMBOL] = 1201.
    sign = 1 if side == 'LONG' else -1
    for offset in [0, -.1, -.09]:
        quote(e, clock, p + sign * offset)
    await e._channel_quote_pivot_entry(SYMBOL, p - sign * .09)
    assert e.account.events == []


def test_duplicate_timestamp_cannot_create_a_turn_and_gap_resets(monkeypatch):
    f, p, e, clock = prepare('LONG', monkeypatch)
    tracker = LivePivot()
    assert not tracker.observe(SYMBOL, f, p, 'LONG', 1202.)
    assert not tracker.observe(SYMBOL, f, p - .1, 'LONG', 1202.)
    assert not tracker.observe(SYMBOL, f, p + .1, 'LONG', 1202.1)
    assert not tracker.observe(SYMBOL, f, p - .1, 'LONG', 1202.2)
    assert not tracker.observe(SYMBOL, f, p, 'LONG', 1208.)
