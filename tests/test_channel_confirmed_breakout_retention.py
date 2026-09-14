"""Completed breakout confirmation survives advancing candles and rejected quotes."""
import pandas as pd
import pytest

from core.engine import TradingEngine
from core.services.entry_diagnostics_service import entry_diagnostics
from core.services.strategies.outer_strategy import aligned_entry, confirmed_outer_breakout_bars, confirmed_outer_breakout_ready
from test_channel_breakout_valley_fix import breakout_market
from test_channel_swing_execution import SYMBOL, _execution_engine


@pytest.fixture
def anyio_backend() -> str:
    return 'asyncio'


def continued_market(side: str, extra: int = 2) -> pd.DataFrame:
    frame = breakout_market(side)
    sign = 1 if side == 'LONG' else -1
    for _ in range(extra):
        frame.loc[frame.index[-1], 'close'] = float(frame.iloc[-1]['open']) + sign * .4
        frame.loc[frame.index[-1], 'high'] = frame.iloc[-1][['open', 'close']].max() + .1
        frame.loc[frame.index[-1], 'low'] = frame.iloc[-1][['open', 'close']].min() - .1
        last = frame.iloc[-1].copy()
        row = last.copy()
        row['open'], row['close'] = last['close'], last['close'] + sign * .4
        row['high'], row['low'] = max(row['open'], row['close']) + .1, min(row['open'], row['close']) - .1
        row['ma3'] += sign * .1
        row['kc_middle'] += sign * .1
        row['timestamp'] += 60_000
        frame = pd.concat([frame, row.to_frame().T], ignore_index=True)
    return frame


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
@pytest.mark.parametrize('extra', [1, 2, 5])
def test_completed_confirmation_does_not_expire_on_next_candle(side: str, extra: int) -> None:
    frame = continued_market(side, extra)
    price = float(frame.iloc[-1]['close'])
    assert confirmed_outer_breakout_ready(frame, price, side)
    assert aligned_entry(frame, price)['side'] == side


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
@pytest.mark.parametrize('case', ['inside_close', 'opposite', 'doji', 'invalid', 'no_root', 'live_inside'])
def test_broken_sequence_cannot_reuse_old_confirmation(side: str, case: str) -> None:
    frame = continued_market(side)
    price = float(frame.iloc[-1]['close'])
    index = frame.index[-2]
    if case == 'inside_close':
        frame.loc[index, 'close'] = 100.
    elif case == 'opposite':
        frame.loc[index, 'open'] = frame.loc[index, 'close'] + (.1 if side == 'LONG' else -.1)
    elif case == 'doji':
        frame.loc[index, 'open'] = frame.loc[index, 'close']
    elif case == 'invalid':
        frame.loc[index, 'close'] = float('nan')
    elif case == 'no_root':
        frame.loc[1, 'open'] = 102.2 if side == 'LONG' else 97.8
    else:
        price = 100.
    assert not confirmed_outer_breakout_ready(frame, price, side)


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_continuation_does_not_make_a_pre_exit_breakout_new(side: str) -> None:
    frame = continued_market(side)
    price = float(frame.iloc[-1]['close'])
    for exit_bar, expected in [(60_000, False), (120_000, True), (180_000, True)]:
        assert TradingEngine._channel_peak_exit_reentry_blocked(
            'ENTER', False, side, frame, dict(exit_bar_id=exit_bar), SYMBOL, live_price=price,
        ) is expected


@pytest.mark.anyio
@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
async def test_failed_snapshot_can_retry_after_confirmation_recovers(side: str, monkeypatch) -> None:
    frame = continued_market(side)
    engine = _execution_engine(frame, side, True)
    engine.account.positions.clear()
    engine.account.save_state = lambda: None
    engine._channel_chop_state = lambda *_: {'detected': False}
    engine._abnormal_market_entry_allowed = lambda *a, **k: True
    price = float(frame.iloc[-1]['close'])
    now = float(frame.iloc[-1]['timestamp']) / 1000 + 1.
    monkeypatch.setattr('core.engine.DEFAULT_SYMBOLS', [SYMBOL])
    monkeypatch.setattr('core.engine.time.time', lambda: now)
    engine.is_running = True
    engine._channel_entry_quote_times = {SYMBOL: now}
    engine.tickers[SYMBOL] = price
    engine._channel_intrabar_ready = TradingEngine._channel_intrabar_ready.__get__(engine)
    signal = dict(side=side, score=100, entry_mode='CHANNEL_SWING', action='ENTER_MARKET',
                  signal_code='KC_UPPER_BREAKOUT_STRICT' if side == 'LONG' else 'KC_LOWER_BREAKOUT_STRICT',
                  candidate_bar_id=engine._channel_candidate_bar_id(frame), reason='confirmed breakout')
    snapshot = await engine._fresh_channel_entry_snapshot(SYMBOL, side)
    assert snapshot
    engine.tickers[SYMBOL] = 100.
    assert not await engine._place_structured_entry(SYMBOL, signal, price, snapshot)
    assert not getattr(engine, '_channel_invalid_entry_candidates', set())
    # Even a legacy lock must yield to a fully revalidated ordinary breakout.
    engine._channel_invalid_entry_candidates = {(SYMBOL, side, signal['candidate_bar_id'])}
    engine.tickers[SYMBOL] = price
    assert entry_diagnostics(engine, SYMBOL, frame, price, now)['reason'] == 'KC_ENTRY_READY'
    assert await engine._place_structured_entry(SYMBOL, signal, price)
    assert len(engine.account.events) == 1


def test_reported_lobster_1835_retains_1832_1833_confirmation() -> None:
    # Closed local API bars from 18:31-18:34, with the observed 18:35 live range.
    frame = pd.DataFrame([
        [.137742, .1385, .1377, .138439, .1379313333, .1380542596, .1385842596, .1375242596],
        [.138439, .139043, .138439, .138928, .1383783333, .1381374730, .1386767730, .1375981730],
        [.138881, .139871, .138846, .139547, .1389713333, .1382717137, .1388609137, .1376825137],
        [.139472, .14365, .139472, .141214, .1398963333, .1385519314, .1394649314, .1376389314],
        [.141272, .14252, .14077, .142246, .1410023333, .1389037475, .1399435475, .1378639475],
    ], columns=['open', 'high', 'low', 'close', 'ma3', 'kc_middle', 'kc_upper', 'kc_lower'])
    frame['timestamp'] = [1789381860000 + i * 60_000 for i in range(5)]
    frame['atr'] = .0006
    price = .141731
    assert confirmed_outer_breakout_bars(frame, price, 'LONG') == (1, 2)
    assert confirmed_outer_breakout_ready(frame, price, 'LONG')
    # Confirmation is complete; the live rejection wick is a different guard.
    assert aligned_entry(frame, price)['reason'] == 'KC_BREAKOUT_REJECTION_WAIT'
