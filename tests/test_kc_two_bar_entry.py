"""Closed KC two-bar rules and real scanner/account boundaries, without real orders."""
import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from core.services.entry_contract import evaluate_entry_contract
from core.services.entry_firewall import validate_account_entry
from test_entry_without_wick_filter import frame_for


def fast_frame(side):
    f = frame_for(side)
    sign = 1 if side == 'LONG' else -1
    edge = 'kc_upper' if side == 'LONG' else 'kc_lower'
    f.loc[3, edge] = float(f.loc[3, 'close']) - sign * .3
    f.loc[2, 'ma5'] = 100 + sign
    f.loc[3, 'ma5'] = 100 + sign * 1.2
    return f


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_scan_and_account_use_new_reason_without_live_body(side):
    from core.services.symbol_runner import process_single_symbol_runner
    f = fast_frame(side)
    f.loc[4, 'close'] = f.loc[4, 'open']
    code = 'KC_2BAR_BREAKOUT_' + side
    d = evaluate_entry_contract(f)
    assert d['type'] == code
    assert d['confirmation_bar_id'] == f.loc[3, 'timestamp']
    assert d['kc_distance_atr'] == pytest.approx(.3)
    account = SimpleNamespace(positions={}, log=lambda *args: None,
                              entry_frame_provider=AsyncMock(return_value=f))
    context = dict(entry_signal_code=code, channel_confirmation_bar_id=d['confirmation_bar_id'])
    assert asyncio.run(validate_account_entry(account, 'TEST', side, context))['type'] == code
    assert context['entry_snapshot']['confirmation_ma5'] == f.loc[3, 'ma5']
    assert 'third_open' not in context['entry_snapshot']
    engine = SimpleNamespace(account=account, tickers={'TEST': float(f.iloc[-1].close)},
                             _execute_confirmed_channel_break=AsyncMock())
    asyncio.run(process_single_symbol_runner(engine, 'TEST', time.time(), None, False, exit_frame=f))
    assert engine._execute_confirmed_channel_break.await_args.kwargs['v8_reason'] == code


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
@pytest.mark.parametrize('distance,allowed', [(0, False), (.499, True), (.5, True), (.501, False)])
def test_closed_distance_boundary(side, distance, allowed):
    f = fast_frame(side)
    sign = 1 if side == 'LONG' else -1
    edge = 'kc_upper' if side == 'LONG' else 'kc_lower'
    f.loc[3, edge] = float(f.loc[3, 'close']) - sign * distance
    assert bool(evaluate_entry_contract(f, code='KC_2BAR_BREAKOUT_' + side)) == allowed


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
@pytest.mark.parametrize('fault', ['first_color', 'second_color', 'first_inside', 'ma_flat', 'ma_reverse', 'ma_alignment', 'ma_missing', 'ma_nan', 'forming_second', 'same_close', 'position'])
def test_required_conditions_and_account_guards(side, fault):
    f = fast_frame(side)
    sign = 1 if side == 'LONG' else -1
    account = SimpleNamespace(positions={}, trades=[])
    if fault in ('first_color', 'second_color'):
        i = 2 if fault == 'first_color' else 3
        f.loc[i, 'open'] = f.loc[i, 'close']
    elif fault == 'first_inside':
        f.loc[2, 'kc_upper' if side == 'LONG' else 'kc_lower'] = f.loc[2, 'close']
    elif fault == 'ma_flat': f.loc[3, 'ma5'] = f.loc[2, 'ma5']
    elif fault == 'ma_reverse': f.loc[3, 'ma5'] = float(f.loc[2, 'ma5']) - sign
    elif fault == 'ma_alignment': f.loc[3, 'ma15'] = f.loc[3, 'ma5']
    elif fault == 'ma_missing': f = f.drop(columns=['ma5'])
    elif fault == 'ma_nan': f.loc[3, 'ma5'] = float('nan')
    elif fault == 'forming_second':
        f = f.iloc[:4].copy()
        f.loc[3, 'is_closed'] = False
    elif fault == 'same_close': account.trades = [dict(symbol='TEST', action='CLOSE_' + side, id=float(f.loc[4, 'timestamp']) + 1)]
    elif fault == 'position': account.positions = {'TEST': {}}
    assert evaluate_entry_contract(f, code='KC_2BAR_BREAKOUT_' + side, account=account, symbol='TEST') is None


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_only_closed_indicators_generate_signal_and_legacy_remains(side):
    f = fast_frame(side)
    code = 'KC_2BAR_BREAKOUT_' + side
    assert evaluate_entry_contract(f.iloc[:-1].copy(), code=code)['type'] == code
    sign = 1 if side == 'LONG' else -1
    f.loc[4, ['ma5', 'ma15']] = [float('nan'), float('nan')]
    # Even a live quote beyond the OLD open-distance limit cannot alter closed conditions.
    assert evaluate_entry_contract(f, float(f.loc[4, 'open']) + sign * .2, code=code)['type'] == code
    old = frame_for(side)
    assert evaluate_entry_contract(old)['type'] == 'CLOSED_BODY_BREAKOUT_' + side
    assert evaluate_entry_contract(old, code=code) is None


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_account_never_falls_back_for_invalidated_fast_signal(side):
    f = fast_frame(side)
    d = evaluate_entry_contract(f)
    f.loc[3, 'ma5'] = f.loc[2, 'ma5']
    assert evaluate_entry_contract(f)['type'] == 'CLOSED_BODY_BREAKOUT_' + side
    account = SimpleNamespace(entry_frame_provider=AsyncMock(return_value=f))
    context = dict(entry_signal_code=d['type'], channel_confirmation_bar_id=d['confirmation_bar_id'])
    with pytest.raises(ValueError, match='WAIT_KC_2BAR_BREAKOUT'):
        asyncio.run(validate_account_entry(account, 'TEST', side, context))


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_real_engine_paper_fill_and_dedup(monkeypatch, side):
    from core.engine import TradingEngine
    from core.paper_account import PaperAccount
    from core.services.symbol_runner import process_single_symbol_runner
    monkeypatch.setattr(PaperAccount, 'load_state', lambda self: None)
    monkeypatch.setattr(PaperAccount, 'save_state', lambda self: None)
    monkeypatch.setattr('core.services.entry_finality.READ_INTERVAL_SECONDS', 0.)
    account = PaperAccount()
    account.balance = 100.
    engine = object.__new__(TradingEngine)
    engine.account = account
    symbol = '1000PEPE/USDT'
    f = fast_frame(side)
    f.loc[4, 'close'] = f.loc[4, 'open']
    engine.exchange = SimpleNamespace(fetch_time=AsyncMock(return_value=float(f.iloc[-1].timestamp) + 10000))
    engine.tickers = {symbol: float(f.iloc[-1].close)}
    engine.fetch_klines = AsyncMock(return_value=f)
    engine.strategy = SimpleNamespace(compute_indicators=lambda frame: frame)
    engine.symbol_rotation = SimpleNamespace(get_dynamic_leverage=lambda *args: 2)
    engine._execution_price_is_safe = AsyncMock(return_value=True)
    asyncio.run(process_single_symbol_runner(engine, symbol, time.time(), None, False, exit_frame=f))
    assert account.positions[symbol]['side'] == side
    assert account.trades[0]['entry_snapshot']['signal_code'] == 'KC_2BAR_BREAKOUT_' + side
    assert account.trades[0]['entry_snapshot']['kc_distance_atr'] == pytest.approx(.3)
    account.positions.clear()
    asyncio.run(process_single_symbol_runner(engine, symbol, time.time(), None, False, exit_frame=f))
    assert len(account.trades) == 1
