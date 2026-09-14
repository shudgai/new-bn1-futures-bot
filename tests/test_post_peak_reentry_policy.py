"""Post-peak reentry requires a fresh pivot or a fresh outer breakout."""
import pytest

from core.engine import TradingEngine
from core.services.entry_diagnostics_service import entry_diagnostics
from test_channel_ma3_primary_entry import market
from test_channel_breakout_valley_fix import breakout_market
from test_channel_swing_execution import SYMBOL, _execution_engine


@pytest.fixture
def anyio_backend() -> str:
    return 'asyncio'


def exit_info(side: str, bar: float) -> dict:
    return dict(side=side, exit_bar_id=bar, require_new_closed_break=True,
                allow_new_outer_signal=True, bar_count=100)


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
@pytest.mark.parametrize('kind', ['new_pivot', 'old_pivot', 'new_break', 'old_break', 'middle'])
def test_only_fresh_pivot_or_fresh_break_unlocks(side: str, kind: str) -> None:
    frame = market(side) if 'pivot' in kind else breakout_market(side)
    bar = float(frame.iloc[-3]['timestamp'])
    if kind == 'old_pivot':
        bar = float(frame.iloc[-2]['timestamp'])
    elif kind == 'new_break':
        bar = float(frame.iloc[-4]['timestamp'])
    elif kind == 'middle':
        frame['kc_upper'], frame['kc_lower'] = 110., 90.
        bar = 0.
    price = float(frame.iloc[-1]['close'])
    for old_side in ('LONG', 'SHORT'):
        info = exit_info(old_side, bar)
        # Legacy flags, elapsed bars, opposite direction and a return inside cannot unlock.
        for required in (True, False):
            info['require_new_closed_break'] = required
            blocked = TradingEngine._channel_peak_exit_reentry_blocked(
                'ENTER', False, side, frame, info, SYMBOL, live_price=price,
            )
            allowed = kind in ('new_pivot', 'new_break') or (kind == 'old_break' and old_side == side)
            assert blocked is (not allowed)


@pytest.mark.anyio
@pytest.mark.parametrize('route', ['pivot', 'ticket', 'live', 'normal'])
@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
async def test_snapshot_early_returns_cannot_reuse_old_pivot(side: str, route: str) -> None:
    frame = market(side)
    engine = _execution_engine(frame, side, True)
    engine.account.positions.clear()
    engine.tickers[SYMBOL] = float(frame.iloc[-1]['close'])
    engine._channel_swing_peak_exit_info = {SYMBOL: exit_info(side, frame.iloc[-2]['timestamp'])}
    options = {'pivot': dict(pivot_entry_signal=True), 'ticket': dict(profit_reentry_token='old'),
               'live': dict(live_pivot=True), 'normal': {}}[route]
    assert await engine._fresh_channel_entry_snapshot(SYMBOL, side, **options) is None


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_restart_recovers_only_latest_unmatched_peak_close(side: str) -> None:
    engine = _execution_engine(market(side), side, True)
    engine.account.positions.clear()
    engine.account.trades = [dict(symbol=SYMBOL, action='CLOSE_' + side, id=300001,
                                  reason='Channel Swing KC_THREE_POINT_PIVOT_EXIT')]
    assert engine._channel_post_peak_exit_info(SYMBOL)['exit_bar_id'] == 300000
    engine.account.trades.insert(0, dict(symbol=SYMBOL, action='OPEN_' + side, id=360001))
    assert engine._channel_post_peak_exit_info(SYMBOL) is None
    engine.account.trades.append(dict(symbol='OTHER/USDT', action='CLOSE_LONG', id=400001,
                                     reason='Channel Swing KC_THREE_POINT_PIVOT_EXIT'))
    assert engine._channel_post_peak_exit_info(SYMBOL) is None


@pytest.mark.anyio
@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
@pytest.mark.parametrize('fresh', [True, False])
async def test_scanner_after_restart_opens_only_new_confirmed_pivot(side: str, fresh: bool, monkeypatch) -> None:
    frame = market(side)
    price = float(frame.iloc[-1]['close'])
    engine = _execution_engine(frame, side, True)
    engine.account.positions.clear()
    engine.account.save_state = lambda: None
    engine._channel_chop_state = lambda *_: {'detected': False}
    engine._abnormal_market_entry_allowed = lambda *a, **k: True
    engine.tickers[SYMBOL] = price
    bar = float(frame.iloc[-3 if fresh else -2]['timestamp'])
    engine.account.trades = [dict(symbol=SYMBOL, action='CLOSE_' + side, id=bar + 1,
                                  reason='Channel Swing KC_THREE_POINT_PIVOT_EXIT')]
    engine.is_running = True
    engine._channel_entry_quote_times = {SYMBOL: 361.}
    diagnostic = entry_diagnostics(engine, SYMBOL, frame, price, 361.)
    assert diagnostic['reason'] == ('KC_ENTRY_READY' if fresh else 'KC_POST_PEAK_BREAKOUT_WAIT')
    monkeypatch.setattr('core.engine.DEFAULT_SYMBOLS', [SYMBOL])
    await engine._process_single_symbol(SYMBOL, 361., None, False)
    assert len(engine.account.events) == int(fresh), engine.account.logs


@pytest.mark.anyio
@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
async def test_close_observed_during_order_safety_check_cancels_stale_entry(side: str, monkeypatch) -> None:
    frame = market(side)
    engine = _execution_engine(frame, side, True)
    engine.account.positions.clear()
    engine.account.save_state = lambda: None
    engine.tickers[SYMBOL] = float(frame.iloc[-1]['close'])
    engine._channel_chop_state = lambda *_: {'detected': False}
    engine._abnormal_market_entry_allowed = lambda *a, **k: True
    observed = []

    async def close_during_safety_check(*_args) -> bool:
        observed.append(True)
        engine._channel_swing_peak_exit_info = {SYMBOL: exit_info(side, frame.iloc[-2]['timestamp'])}
        return True

    engine._execution_price_is_safe = close_during_safety_check
    monkeypatch.setattr('core.engine.DEFAULT_SYMBOLS', [SYMBOL])
    await engine._process_single_symbol(SYMBOL, 361., None, False)
    assert observed
    assert not engine.account.events, engine.account.logs
    assert any('KC_POST_PEAK_BREAKOUT_WAIT' in message for message, _ in engine.account.logs)
