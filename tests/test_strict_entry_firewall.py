import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pandas as pd
import pytest

from core.services.entry_firewall import validate_entry_frame, validate_account_entry
from core.services.strategies.unified_entry_strategy import evaluate_closed_entry


def frame(rule='B', side='LONG'):
    stamp = int(time.time() // 60) * 60000 - 60000
    rows = [dict(timestamp=stamp - (2-i)*60000, open=100., close=100.2,
                 high=104., low=96., atr=1., ma3=100., ma15=99.,
                 kc_upper=102., kc_lower=98., kc_middle=100., is_closed=True)
            for i in range(3)]
    if rule == 'B':
        rows[1].update(open=100.5, close=100.)
        rows[2].update(open=100., close=101.5, ma3=100.5)
    elif rule == 'E':
        for row in rows:
            row.update(open=102.1, close=102.2, kc_upper=101., kc_middle=100.5)
        rows[2].update(open=101.4, close=102.2, ma3=100.5)
    result = pd.DataFrame(rows)
    if side == 'SHORT':
        original = result.copy()
        for dest, source in [('open','open'),('close','close'),('high','low'),('low','high'),
                             ('ma3','ma3'),('ma15','ma15'),('kc_upper','kc_lower'),
                             ('kc_lower','kc_upper'),('kc_middle','kc_middle')]:
            result[dest] = 200 - original[source]
    result.attrs['timeframe_ms'] = 60000
    return result


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
@pytest.mark.parametrize('rule', ['B', 'E'])
def test_exact_rules(side, rule):
    f = frame(rule, side)
    assert evaluate_closed_entry(f, side)[1] == f'CLOSED_{rule}_{side}'


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
@pytest.mark.parametrize('fault', ['flat_ma', 'wrong_ma', 'distance', 'small_body', 'inside'])
def test_e_rejects_each_failed_condition(side, fault):
    f = frame('E', side)
    sign = 1 if side == 'LONG' else -1
    if fault == 'flat_ma': f.loc[2, 'ma3'] = f.loc[1, 'ma3']
    if fault == 'wrong_ma': f.loc[2, 'ma3'] = f.loc[1, 'ma3'] - sign
    if fault == 'distance': f.loc[2, 'kc_middle'] = f.loc[2, 'close'] - sign * 2.21
    if fault == 'small_body': f.loc[2, 'open'] = f.loc[2, 'close'] - sign * .79
    if fault == 'inside': f.loc[0, 'close'] = f.loc[0, 'kc_middle']
    assert not evaluate_closed_entry(f, side)[0]


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
@pytest.mark.parametrize('fault', ['flat_ma', 'large_previous', 'not_engulfed', 'small_current'])
def test_b_exact_contract(side, fault):
    f = frame('B', side)
    sign = 1 if side == 'LONG' else -1
    if fault == 'flat_ma': f.loc[2, 'ma3'] = f.loc[1, 'ma3']
    if fault == 'large_previous': f.loc[1, 'open'] = f.loc[1, 'close'] + sign*.61
    if fault == 'not_engulfed': f.loc[2, 'open'] = f.loc[1, 'close'] + sign*.01
    if fault == 'small_current': f.loc[2, 'atr'] = 1.51
    assert evaluate_closed_entry(f, side)[1] != f'CLOSED_B_{side}'


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
@pytest.mark.parametrize('fault', ['doji', 'opposite', 'inside', 'nan', 'code'])
def test_physical_veto(side, fault):
    f = frame('B', side)
    sign = 1 if side == 'LONG' else -1
    code = f'CLOSED_B_{side}'
    if fault == 'doji': f.loc[2, 'close'] = f.loc[2, 'open']
    if fault == 'opposite': f.loc[2, 'close'] = f.loc[2, 'open'] - sign*.1
    if fault == 'inside': f.loc[2, 'close'] = f.loc[2, 'open'] + sign*1.1
    if fault == 'nan': f.loc[2, 'atr'] = float('nan')
    if fault == 'code': code = 'RSI_BOTTOM_LONG'
    with pytest.raises(ValueError, match='FORBIDDEN_ENTRY'):
        validate_entry_frame(f, side, code)


@pytest.mark.parametrize('fault', ['missing_provider', 'missing_code', 'stale', 'changed', 'closed', 'fetch_error'])
def test_account_fails_closed(fault):
    f = frame()
    provider = AsyncMock(return_value=f)
    account = SimpleNamespace(entry_frame_provider=provider, last_closed_at={})
    context = dict(entry_signal_code='CLOSED_B_LONG', channel_confirmation_bar_id=float(f.iloc[-1].timestamp))
    if fault == 'missing_provider': account.entry_frame_provider = None
    if fault == 'missing_code': context = {'manual_entry': True}
    if fault == 'stale': f['timestamp'] -= 300000
    if fault == 'changed': context['channel_confirmation_bar_id'] -= 60000
    if fault == 'closed': account.last_closed_at['TEST'] = time.time()
    if fault == 'fetch_error': provider.side_effect = RuntimeError('no data')
    with pytest.raises(ValueError, match='FORBIDDEN_ENTRY'):
        asyncio.run(validate_account_entry(account, 'TEST', 'LONG', context))


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_actual_exchange_boundary(side):
    from core.testnet_account import BinanceTestnetAccount
    f = frame('B', side)
    account = object.__new__(BinanceTestnetAccount)
    account.exchange = SimpleNamespace(create_order=AsyncMock(return_value={'id': 'ok'}))
    account.entry_frame_provider = AsyncMock(return_value=f)
    account.last_closed_at = {}
    context = dict(entry_signal_code=f'CLOSED_B_{side}', channel_confirmation_bar_id=float(f.iloc[-1].timestamp))
    exchange_side = 'buy' if side == 'LONG' else 'sell'
    asyncio.run(account._send_order('TEST', 'market', exchange_side, 1, entry_context=context))
    account.exchange.create_order.assert_awaited_once()
    account.exchange.create_order.reset_mock()
    f.loc[2, 'close'] = f.loc[2, 'open']
    with pytest.raises(ValueError, match='FORBIDDEN_ENTRY'):
        asyncio.run(account._send_order('TEST', 'limit', exchange_side, 1, 100, entry_context=context))
    account.exchange.create_order.assert_not_awaited()
    asyncio.run(account._send_order('TEST', 'market', exchange_side, 1, params={'reduceOnly': True}))
    account.exchange.create_order.assert_awaited_once()


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_b_inclusive_atr_boundaries(side):
    f = frame('B', side)
    sign = 1 if side == 'LONG' else -1
    f.loc[1, 'open'] = f.loc[1, 'close'] + sign * .6
    f.loc[2, 'atr'] = 1.5
    assert evaluate_closed_entry(f, side)[1] == f'CLOSED_B_{side}'


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_e_exact_distance_boundary(side):
    f = frame('E', side)
    sign = 1 if side == 'LONG' else -1
    f.loc[2, 'kc_middle'] = f.loc[2, 'close'] - sign * 2.2
    assert evaluate_closed_entry(f, side)[1] == f'CLOSED_E_{side}'
    f.loc[2, 'kc_middle'] -= sign * .00001
    assert not evaluate_closed_entry(f, side)[0]


def test_successful_close_cannot_open_in_same_runner(monkeypatch):
    import core.services.symbol_runner as module
    f = frame()
    account = SimpleNamespace(positions={'TEST': {'side': 'SHORT'}}, position_meta={},
                              save_state=lambda: None)
    async def close(*args, **kwargs):
        account.positions.clear()
        return True
    account.close_position = close
    engine = SimpleNamespace(account=account, _take_over_manual_position=lambda *args: None,
                             _execute_confirmed_channel_break=AsyncMock())
    monkeypatch.setattr(module.DualTrackExitStrategy, 'evaluate_exit', lambda *args: 'EXIT_TEST')
    asyncio.run(module.process_single_symbol_runner(engine, 'TEST', 0, None, False,
                                                    exit_frame=f, exit_quote=101.5))
    engine._execute_confirmed_channel_break.assert_not_awaited()


def test_physical_gate_uses_latest_atr():
    f = frame('B')
    f.loc[2, 'atr'] = 1.5
    assert evaluate_closed_entry(f, 'LONG')[1] == 'CLOSED_B_LONG'
    with pytest.raises(ValueError, match='FORBIDDEN_ENTRY'):
        validate_entry_frame(f, 'LONG', 'CLOSED_B_LONG')
