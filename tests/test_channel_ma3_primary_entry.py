"""MA3-led slope entries: closed confirmation, auxiliary KC and real execution."""
from __future__ import annotations

import pandas as pd
import pytest

from core.engine import TradingEngine
from core.services.entry_diagnostics_service import entry_diagnostics
from core.services.strategies.outer_strategy import aligned_entry
from core.services.strategies.pivot_strategy import pivot_entry
from test_channel_swing_execution import SYMBOL, _execution_engine


@pytest.fixture
def anyio_backend() -> str:
    return 'asyncio'


def market(side: str, scale: float = 1.0) -> pd.DataFrame:
    closes = [103., 102., 100., 98., 99., 101., 101.1]
    opens = [103., 102., 100., 98.3, 99.3, 100.5, 101.1]
    frame = pd.DataFrame({'open': opens, 'close': closes})
    frame['high'] = frame[['open', 'close']].max(axis=1) + .2
    frame['low'] = frame[['open', 'close']].min(axis=1) - .2
    frame['ma3'] = frame.close.rolling(3).mean()
    frame['ma15'] = frame['kc_middle'] = 100.
    frame['kc_upper'], frame['kc_lower'], frame['atr'] = 110., 90., 4.
    if side == 'SHORT':
        original = frame.copy()
        for key in ('open', 'close', 'ma3', 'ma15', 'kc_middle'):
            frame[key] = 200. - original[key]
        frame['high'], frame['low'] = 200. - original.low, 200. - original.high
    frame *= scale
    frame['timestamp'] = [60_000 * i for i in range(len(frame))]
    return frame


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
@pytest.mark.parametrize('scale', [.001, 1., 1000.])
@pytest.mark.parametrize('slope', [0., .001, 1., -1.])
def test_ma3_turn_sets_direction_independently_of_kc(side: str, scale: float, slope: float) -> None:
    # Arrange: price lows/highs need not form a pivot alongside the MA3 turn.
    frame = market(side, scale)
    sign = 1 if side == 'LONG' else -1
    frame.loc[5, 'kc_middle'] = (100. + sign * slope) * scale
    price = float(frame.iloc[-1]['close'])
    # Act / Assert: CK opposition does not override a confirmed MA3 turn.
    for decision in (pivot_entry(frame, price), aligned_entry(frame, price),
                     TradingEngine._channel_swing_action(frame, price)):
        assert decision['action'] == 'ENTER', decision
        assert decision['side'] == side
        assert decision['reason'] in {'KC_MA15_TROUGH_LONG', 'KC_MA15_PEAK_SHORT'}


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
@pytest.mark.parametrize('case', ['no_turn', 'equal_ma3', 'wrong_color', 'doji', 'unclosed', 'bad_ma3', 'bad_kc'])
def test_missing_closed_confirmation_does_not_allow_a_kc_only_entry(side: str, case: str) -> None:
    frame = market(side)
    if case == 'no_turn':
        frame.loc[3:5, 'ma3'] = [99., 100., 101.] if side == 'LONG' else [101., 100., 99.]
    elif case == 'equal_ma3':
        frame.loc[5, 'ma3'] = frame.loc[4, 'ma3']
    elif case == 'wrong_color':
        frame.loc[5, 'open'] = frame.loc[5, 'close'] + (.1 if side == 'LONG' else -.1)
    elif case == 'doji':
        frame.loc[5, 'open'] = frame.loc[5, 'close']
    elif case == 'unclosed':
        frame = frame.iloc[:-1].copy()
    elif case == 'bad_ma3':
        frame.loc[4, 'ma3'] = float('nan')
    else:
        frame.loc[5, 'kc_middle'] = float('nan')
    assert pivot_entry(frame, float(frame.iloc[-1]['close']))['action'] == 'WAIT'


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_forming_ma3_and_kc_do_not_rewrite_the_closed_turn(side: str) -> None:
    frame = market(side)
    price = float(frame.iloc[-1]['close'])
    expected = pivot_entry(frame, price)
    frame.loc[6, ['ma3', 'kc_middle']] = [1000., 1.]
    assert pivot_entry(frame, price) == expected
    assert expected['action'] == 'ENTER'


@pytest.mark.anyio
@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
@pytest.mark.parametrize('slope', [0., .001])
async def test_scan_executes_inside_channel_pivot_with_flat_or_shallow_kc(side: str, slope: float, monkeypatch: pytest.MonkeyPatch) -> None:
    frame = market(side)
    frame.loc[5, 'kc_middle'] += slope * (1 if side == 'LONG' else -1)
    engine = _execution_engine(frame, side, True)
    engine._channel_chop_state = lambda *_: {"detected": False}
    engine.account.positions.clear()
    engine.account.save_state = lambda: None
    engine.tickers[SYMBOL] = float(frame.iloc[-1]['close'])
    engine._abnormal_market_entry_allowed = lambda *a, **k: True
    monkeypatch.setattr('core.engine.DEFAULT_SYMBOLS', [SYMBOL])
    await engine._process_single_symbol(SYMBOL, 361., None, False)
    assert [(event[0], event[2]) for event in engine.account.events] == [('open', side)], engine.account.logs


@pytest.mark.anyio
@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
async def test_snapshot_accepts_kc_turning_against_the_pivot(side: str) -> None:
    frame = market(side)
    engine = _execution_engine(frame, side, True)
    engine.account.positions.clear()
    engine.tickers[SYMBOL] = float(frame.iloc[-1]['close'])
    assert await engine._fresh_channel_entry_snapshot(SYMBOL, side, 5, pivot_entry_signal=True)
    frame.loc[5, 'kc_middle'] = 99. if side == 'LONG' else 101.
    assert await engine._fresh_channel_entry_snapshot(SYMBOL, side, 5, pivot_entry_signal=True) is not None


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
@pytest.mark.parametrize('case', ['ready', 'opposite', 'missing', 'stale'])
def test_diagnostics_describe_ma3_confirmation_without_kc_veto(side: str, case: str) -> None:
    frame = market(side)
    if case == 'opposite':
        frame.loc[5, 'kc_middle'] = 99. if side == 'LONG' else 101.
    elif case == 'missing':
        frame.loc[3:5, 'ma3'] = 100.
    engine = _execution_engine(frame, side, True)
    engine.account.positions.clear()
    engine.is_running = True
    engine._channel_entry_quote_times = {SYMBOL: 350. if case == 'stale' else 361.}
    result = entry_diagnostics(engine, SYMBOL, frame, float(frame.iloc[-1]['close']), 361.)
    if case in ('ready', 'opposite'):
        assert result['reason'] == 'KC_ENTRY_READY', result
        assert result['pivot_ready'] is True
        assert 'MA3峰谷入口' in result['message']
    elif case == 'stale':
        assert result['reason'] == 'KC_ENTRY_QUOTE_WAIT', result
    else:
        assert '等待MA3' in result['message'], result
