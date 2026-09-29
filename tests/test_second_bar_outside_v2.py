"""Second-bar live entry contracts; no external orders or account state writes."""
import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from test_v2_execution_boundary import candles
from test_red_eye_v2_pressure import engine_fixture
from core.services.strategies.pure_trend_v2 import PureTrendStrategyV2, evaluate_v2_frame
from core.services.entry_firewall import validate_account_entry, validate_entry_frame

SYMBOL = '1000PEPE/USDT'


def second_frame(side='LONG'):
    f = candles(side)
    # Exactly one completed directional outside candle precedes the live bar.
    f.loc[f.index[-3], ['open', 'close', 'high', 'low']] = [100., 100., 100.4, 99.6]
    f.loc[f.index[-2], 'open'] = 100.8 if side == 'LONG' else 99.2
    f.loc[f.index[-2], 'low' if side == 'LONG' else 'high'] = 100.7 if side == 'LONG' else 99.3
    return f


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_one_closed_breakout_immediately_enters_live_second(side):
    f = second_frame(side)
    d = evaluate_v2_frame(f)
    assert d['type'] == 'SECOND_BAR_OUTSIDE_' + side
    assert d['intrabar'] is True
    assert d['confirmation_bar_id'] == float(f.iloc[-1].timestamp)
    assert d['breakout_bar_id'] == float(f.iloc[-2].timestamp)
    assert d['close_price'] == float(f.iloc[-2].close)
    a = SimpleNamespace(trades=[], last_closed_at={}, entry_frame_provider=AsyncMock(return_value=f))
    ctx = dict(entry_signal_code=d['type'], channel_confirmation_bar_id=d['confirmation_bar_id'])
    assert asyncio.run(validate_account_entry(a, SYMBOL, side, ctx))['type'] == d['type']


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
@pytest.mark.parametrize('fraction,allowed', [(0., True), (.49, True), (.5, True), (.50001, False), (1., False)])
def test_adverse_body_fifty_percent_boundary(side, fraction, allowed):
    f = second_frame(side)
    body = abs(float(f.iloc[-2].close) - float(f.iloc[-2].open))
    price = float(f.iloc[-1].close)
    f.loc[f.index[-1], 'open'] = price + (1 if side == 'LONG' else -1) * fraction * body
    assert bool(evaluate_v2_frame(f)) is allowed


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
@pytest.mark.parametrize('fault', ['first_live', 'first_doji', 'first_opposite', 'wick_only',
    'first_touch', 'second_touch', 'inside', 'ma_equal', 'ma_opposite', 'ma_nan',
    'closed_second', 'gap', 'duplicate', 'invalid_flag', 'invalid_channel', 'invalid_atr'])
def test_invalid_confirmation_and_live_conditions(side, fault):
    f = second_frame(side)
    edge = 'kc_upper' if side == 'LONG' else 'kc_lower'
    if fault == 'first_live': f.loc[f.index[-2], 'is_closed'] = False
    if fault == 'first_doji': f.loc[f.index[-2], 'open'] = f.iloc[-2].close
    if fault == 'first_opposite': f.loc[f.index[-2], 'open'] = 101.9 if side == 'LONG' else 98.1
    if fault == 'wick_only': f.loc[f.index[-2], 'close'] = 101.3 if side == 'LONG' else 98.7
    if fault == 'first_touch': f.loc[f.index[-2], 'close'] = f.iloc[-2][edge]
    if fault == 'second_touch': f.loc[f.index[-1], 'close'] = f.iloc[-1][edge]
    if fault == 'inside': f.loc[f.index[-1], 'close'] = 100.
    if fault == 'ma_equal': f.loc[f.index[-1], 'ma3'] = f.iloc[-1].ma15
    if fault == 'ma_opposite': f.loc[f.index[-1], 'ma3'] = 99. if side == 'LONG' else 101.
    if fault == 'ma_nan': f.loc[f.index[-1], 'ma3'] = float('nan')
    if fault == 'closed_second': f.loc[f.index[-1], 'is_closed'] = True
    if fault == 'gap': f.loc[f.index[-1], 'timestamp'] += 60000
    if fault == 'duplicate': f.loc[f.index[-1], 'timestamp'] = f.iloc[-2].timestamp
    if fault == 'invalid_flag':
        f['is_closed'] = f['is_closed'].astype(object)
        f.loc[f.index[-1], 'is_closed'] = 'false'
    if fault == 'invalid_channel': f.loc[f.index[-1], 'kc_lower'] = 102.
    if fault == 'invalid_atr': f.loc[f.index[-2], 'atr'] = 0.
    assert evaluate_v2_frame(f) is None


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_first_open_already_outside_is_allowed_by_supplied_predicate(side):
    f = second_frame(side)
    f.loc[f.index[-2], 'open'] = 101.5 if side == 'LONG' else 98.5
    assert evaluate_v2_frame(f)


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_quote_recomputes_ma_alignment_without_mutating_frame(side):
    f = second_frame(side)
    f.loc[f.index[-1], 'open'] = float(f.iloc[-1].close)
    f.loc[f.index[-1], 'ma3'] = 100.001 if side == 'LONG' else 99.999
    saved = f.copy(deep=True)
    assert evaluate_v2_frame(f)
    quote = float(f.iloc[-1].close) + (-.01 if side == 'LONG' else .01)
    assert evaluate_v2_frame(f, quote) is None
    assert f.equals(saved)


@pytest.mark.parametrize('code', ['THIRD_BAR_CONFIRMED_LONG', 'SUPER_BREAKOUT_LONG',
    'CONTINUATION_RE_ENTRY_LONG', 'CLOSED_IGNITION_LONG', 'CLOSED_TREND_BREAKOUT_LONG'])
def test_obsolete_cached_codes_rejected_everywhere(code):
    f = second_frame()
    assert evaluate_v2_frame(f, code=code) is None
    with pytest.raises(ValueError):
        validate_entry_frame(f, 'LONG', code)
    a = SimpleNamespace(entry_frame_provider=AsyncMock(return_value=f))
    with pytest.raises(ValueError):
        asyncio.run(validate_account_entry(a, SYMBOL, 'LONG', dict(entry_signal_code=code)))
    a.entry_frame_provider.assert_not_awaited()


@pytest.mark.parametrize('bars,allowed', [(0, False), (1, False), (2, True), (8, True)])
def test_post_exit_cooldown_preserved_but_no_alternate_reentry(bars, allowed):
    f = second_frame()
    a = SimpleNamespace(trades=[dict(symbol=SYMBOL, action='CLOSE_LONG',
        id=float(f.iloc[-1].timestamp) - bars * 60000 + 1000)])
    assert bool(evaluate_v2_frame(f, account=a, symbol=SYMBOL)) is allowed
    f.loc[f.index[-2], 'open'] = 101.9
    assert evaluate_v2_frame(f, account=a, symbol=SYMBOL) is None


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_latest_quote_can_retreat_and_recover_without_consuming_signal(side):
    f = second_frame(side)
    quote = float(f.iloc[-1].close)
    assert evaluate_v2_frame(f, quote)
    assert evaluate_v2_frame(f, 100.) is None
    assert evaluate_v2_frame(f, quote)


def test_missing_or_string_closed_flag_fails_direct_helper():
    f = second_frame()
    current, previous = f.iloc[-1].to_dict(), f.iloc[-2].to_dict()
    previous['is_closed'] = 'true'
    assert PureTrendStrategyV2().evaluate_second_bar_outside_entry(SYMBOL, current, previous) is None
    previous.pop('is_closed')
    assert PureTrendStrategyV2().evaluate_second_bar_outside_entry(SYMBOL, current, previous) is None


def test_default_engine_signal_uses_live_bar_and_failed_order_can_retry(monkeypatch):
    engine, _ = engine_fixture(monkeypatch)
    f = second_frame()
    engine.fetch_klines = AsyncMock(return_value=f)
    original = engine.account.open_position
    engine.account.open_position = AsyncMock(return_value=False)
    assert not asyncio.run(engine._execute_confirmed_channel_break(SYMBOL, f, 101.8, 'LONG'))
    engine.account.open_position = original
    assert asyncio.run(engine._execute_confirmed_channel_break(SYMBOL, f, 101.8, 'LONG'))
    assert engine.account.trades[-1]['channel_confirmation_bar_id'] == float(f.iloc[-1].timestamp)


def test_restart_dedupe_reads_persisted_fill(monkeypatch):
    engine, _ = engine_fixture(monkeypatch)
    f = second_frame()
    engine.fetch_klines = AsyncMock(return_value=f)
    engine.account.trades = [dict(symbol=SYMBOL, action='OPEN_LONG', id=time.time()*1000,
        channel_confirmation_bar_id=float(f.iloc[-1].timestamp))]
    assert not asyncio.run(engine._execute_confirmed_channel_break(SYMBOL, f, 101.8, 'LONG'))
    assert SYMBOL not in engine.account.positions


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_websocket_quote_enters_second_bar_without_waiting_for_scan(monkeypatch, side):
    import core.engine as module
    monkeypatch.setattr(module, 'SYMBOL_ROTATION_ENABLED', False)
    monkeypatch.setattr(module, 'BTC_1M_PULSE_FILTER_ENABLED', False)
    engine, _ = engine_fixture(monkeypatch)
    engine.is_running = True
    f = second_frame(side)
    engine.fetch_klines = AsyncMock(return_value=f)
    engine.tickers[SYMBOL] = float(f.iloc[-1].close)
    asyncio.run(engine._channel_quote_pivot_entry(SYMBOL, engine.tickers[SYMBOL], time.time()*1000))
    assert engine.account.positions[SYMBOL]['side'] == side
    assert len(engine.account.trades) == 1


@pytest.mark.parametrize('fault', ['stale', 'stopped', 'rotation', 'daily', 'btc', 'unknown'])
def test_websocket_risk_gates(monkeypatch, fault):
    import core.engine as module
    monkeypatch.setattr(module, 'SYMBOL_ROTATION_ENABLED', fault == 'rotation')
    monkeypatch.setattr(module, 'BTC_1M_PULSE_FILTER_ENABLED', fault == 'btc')
    engine, _ = engine_fixture(monkeypatch)
    engine.is_running = fault != 'stopped'
    f = second_frame()
    engine.fetch_klines = AsyncMock(return_value=f)
    engine._detect_btc_1m_pulse = lambda *args: 'SHORT'
    if fault == 'daily': engine.account.daily_loss_limit_hit = lambda: (True, 10.)
    quote_ms = (time.time() - (10 if fault == 'stale' else 0)) * 1000
    asyncio.run(engine._channel_quote_pivot_entry('OTHER' if fault == 'unknown' else SYMBOL, 101.8, quote_ms))
    assert not engine.account.trades


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_submit_rejects_quote_changed_after_candidate(monkeypatch, side):
    engine, _ = engine_fixture(monkeypatch)
    f = second_frame(side)
    engine.fetch_klines = AsyncMock(return_value=f)
    engine.tickers[SYMBOL] = 100.  # Candidate was outside; executable quote is inside.
    assert not asyncio.run(engine._execute_confirmed_channel_break(
        SYMBOL, f, float(f.iloc[-1].close), side))
    assert not engine.account.trades


def test_fill_in_opposite_direction_still_locks_same_symbol_bar(monkeypatch):
    engine, _ = engine_fixture(monkeypatch)
    f = second_frame()
    engine.fetch_klines = AsyncMock(return_value=f)
    engine.account.trades = [dict(symbol=SYMBOL, action='OPEN_SHORT', id=time.time()*1000,
        channel_confirmation_bar_id=float(f.iloc[-1].timestamp))]
    assert not asyncio.run(engine._execute_confirmed_channel_break(SYMBOL, f, 101.8, 'LONG'))
    assert SYMBOL not in engine.account.positions


def test_boundary_updates_quote_before_computing_indicators(monkeypatch):
    engine, _ = engine_fixture(monkeypatch)
    f = second_frame()
    f['close_price_spike_filtered'] = f['close']
    saved = f.copy(deep=True)
    engine.fetch_klines = AsyncMock(return_value=f)
    engine.tickers[SYMBOL] = 102.5
    def compute(frame):
        assert frame.iloc[-1].close == 102.5
        assert frame.iloc[-1].high == 102.5
        assert frame.iloc[-1].close_price_spike_filtered == 102.5
        assert frame.iloc[-2].close == saved.iloc[-2].close
        frame['ma3'] = frame['close'].rolling(3).mean()
        return frame
    engine.strategy.compute_indicators = compute
    result = asyncio.run(engine._entry_boundary_frame(SYMBOL))
    assert result.iloc[-1].ma3 == pytest.approx(f['close'].iloc[-3:-1].sum() / 3 + 102.5 / 3)
    assert f.equals(saved)


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
@pytest.mark.parametrize('atr', [.01, 1., 10.])
def test_entry_and_paper_submit_ignore_profit_estimates(monkeypatch, side, atr):
    engine, _ = engine_fixture(monkeypatch)
    f = second_frame(side)
    f.loc[f.index[-2], 'atr'] = atr
    engine.fetch_klines = AsyncMock(return_value=f)
    engine.tickers[SYMBOL] = float(f.iloc[-1].close)
    decision = evaluate_v2_frame(f)
    assert decision['type'] == 'SECOND_BAR_OUTSIDE_' + side
    assert decision['entry_atr'] == atr
    assert asyncio.run(engine._execute_confirmed_channel_break(
        SYMBOL, f, float(f.iloc[-1].close), side))
    assert engine.account.positions[SYMBOL]['side'] == side
    assert len(engine.account.trades) == 1


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_large_favorable_quote_not_blocked_by_old_reward_risk(side):
    f = second_frame(side)
    quote = 103. if side == 'LONG' else 97.
    assert evaluate_v2_frame(f, quote)['price'] == quote
