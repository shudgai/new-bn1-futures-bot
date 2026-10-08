"""Shared entry contract -> engine -> real account boundaries, without network."""
import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
import pandas as pd

from core.services.entry_contract import evaluate_entry_contract
from core.services.entry_firewall import validate_account_entry, validate_entry_frame


def candles(side='LONG', live=True):
    from test_strict_entry_contract import candles as general_candles
    f = general_candles(side)
    prior = f.iloc[[0]].copy()
    prior["timestamp"] -= 60000
    f = pd.concat([prior, f], ignore_index=True)
    prefix = []
    for offset in range(8, 0, -1):
        row = f.iloc[0].copy()
        row["timestamp"] -= offset*60000
        prefix.append(row)
    f = pd.concat([pd.DataFrame(prefix, index=range(-8, 0)), f])
    f.attrs['timeframe_ms'] = 60000
    f.attrs['entry_finality_verified'] = True
    if not live:
        f['is_closed'] = True
        f['timestamp'] -= 60000
    return f


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_account_boundary_blocks_newly_flat_closed_ma5(side):
    f = candles(side)
    ctx = context(f, side)
    f.loc[f.index[-2], 'ma5'] = float(f.iloc[-3].ma5)
    account = SimpleNamespace(entry_frame_provider=AsyncMock(return_value=f), last_closed_at={},
                              save_state=Mock(), log=Mock())
    with pytest.raises(ValueError, match='BLOCKED_MA5_FLAT_OPPOSITE_OR_INVALID'):
        asyncio.run(validate_account_entry(account, 'CAP/USDT', side, ctx))


def context(f, side):
    d = evaluate_entry_contract(f, symbol='CAP/USDT')
    return dict(entry_mode='CHANNEL_SWING', entry_signal_code=d['type'],
                channel_confirmation_bar_id=d['confirmation_bar_id'])


@pytest.mark.parametrize('side', ['LONG','SHORT'])
@pytest.mark.parametrize('live', [True,False])
def test_runner_to_paper_fill_and_dedup(monkeypatch, side, live):
    from core.engine import TradingEngine
    from core.paper_account import PaperAccount
    from core.services.symbol_runner import process_single_symbol_runner
    monkeypatch.setattr(PaperAccount, 'load_state', lambda self: None)
    monkeypatch.setattr(PaperAccount, 'save_state', lambda self, **kwargs: None)
    account = PaperAccount(); account.balance = 100.
    engine = object.__new__(TradingEngine); engine.account = account
    symbol = 'CAP/USDT'; f = candles(side, live)
    monkeypatch.setattr('core.services.entry_finality.READ_INTERVAL_SECONDS',0.)
    engine.exchange = SimpleNamespace(fetch_time=AsyncMock(return_value=time.time()*1000))
    engine.tickers = {symbol: float(f.iloc[-1].close)}
    engine.fetch_klines = AsyncMock(return_value=f)
    engine.strategy = SimpleNamespace(compute_indicators=lambda frame: frame)
    engine.symbol_rotation = SimpleNamespace(get_dynamic_leverage=lambda *args: 2)
    engine._execution_price_is_safe = AsyncMock(return_value=True)
    if not live:
        asyncio.run(process_single_symbol_runner(engine,symbol,time.time(),None,False,exit_frame=f))
        assert not account.trades
        return
    asyncio.run(process_single_symbol_runner(engine,symbol,time.time(),None,False,exit_frame=f))
    assert account.positions[symbol]['side'] == side
    assert account.positions[symbol]['entry_atr'] == 1.
    assert engine._entry_gate_diagnostics[(symbol,side,'EXECUTION')][1] == 'FILLED'
    account.positions.clear()
    asyncio.run(process_single_symbol_runner(engine,symbol,time.time(),None,False,exit_frame=f))
    assert len(account.trades) == 1
    assert engine._entry_gate_diagnostics[(symbol,'NONE','SIGNAL')][1] == 'BLOCKED_KC_BREAKOUT_ALREADY_FILLED'


@pytest.mark.parametrize('side', ['LONG','SHORT'])
@pytest.mark.parametrize('fault', ['inside','atr','wrong_side','unknown','expired','changed'])
def test_account_boundary_rejects_invalid(side, fault):
    f = candles(side); ctx = context(f,side)
    if fault == 'inside': f.loc[f.index[-1], 'close'] = 100.
    if fault == 'atr': f.loc[f.index[-2], 'atr'] = float('nan')
    if fault == 'wrong_side': side = 'SHORT' if side == 'LONG' else 'LONG'
    if fault == 'unknown': ctx['entry_signal_code'] = 'UNKNOWN'
    if fault == 'expired':
        f['timestamp'] -= 300000; ctx['channel_confirmation_bar_id'] -= 300000
    if fault == 'changed': ctx['channel_confirmation_bar_id'] -= 60000
    account = SimpleNamespace(entry_frame_provider=AsyncMock(return_value=f), last_closed_at={},
                              save_state=Mock(), log=Mock())
    with pytest.raises(ValueError):
        asyncio.run(validate_account_entry(account,'TEST',side,ctx))


@pytest.mark.parametrize('side', ['LONG','SHORT'])
def test_exchange_boundary_sends_supported_order(monkeypatch, side):
    from core.testnet_account import BinanceTestnetAccount
    monkeypatch.setattr(BinanceTestnetAccount,'_load_state',lambda self:None)
    monkeypatch.setattr(BinanceTestnetAccount,'save_state',lambda self,**kwargs:None)
    raw = AsyncMock(return_value={'id':'test-order','status':'closed'})
    account = BinanceTestnetAccount(SimpleNamespace(create_order=raw))
    f = candles(side); account.entry_frame_provider = AsyncMock(return_value=f)
    account.last_closed_at = {}
    asyncio.run(account._send_order('TEST','market','buy' if side=='LONG' else 'sell',1,
                                   entry_context=context(f,side)))
    raw.assert_awaited_once()


@pytest.mark.parametrize('side', ['LONG','SHORT'])
def test_obsolete_entry_codes_rejected(side):
    f = candles(side)
    for prefix in ('THIRD_BAR_INTRA_', 'THIRD_BAR_TRACK_RIDING_', 'THREE_BAR_BREAKOUT_', 'CONTINUATION_'):
        with pytest.raises(ValueError):
            validate_entry_frame(f,side,prefix+side)


@pytest.mark.parametrize('fault', ['slots','daily','balance','quote','changed','exchange_error'])
def test_engine_risk_and_failure_gates(monkeypatch, fault):
    from core.engine import TradingEngine
    from core.paper_account import PaperAccount
    monkeypatch.setattr(PaperAccount,'load_state',lambda self:None)
    monkeypatch.setattr(PaperAccount,'save_state',lambda self,**kwargs:None)
    account=PaperAccount(); account.balance=100.
    engine=object.__new__(TradingEngine); engine.account=account
    symbol='CAP/USDT'; f=candles(); ctx=context(f,'LONG')
    monkeypatch.setattr('core.services.entry_finality.READ_INTERVAL_SECONDS',0.)
    engine.exchange=SimpleNamespace(fetch_time=AsyncMock(return_value=time.time()*1000))
    engine.tickers={symbol:float(f.iloc[-1].close)}
    engine.fetch_klines=AsyncMock(return_value=f)
    engine.strategy=SimpleNamespace(compute_indicators=lambda frame:frame)
    engine.symbol_rotation=SimpleNamespace(get_dynamic_leverage=lambda *args:2)
    engine._execution_price_is_safe=AsyncMock(return_value=fault!='quote')
    if fault=='slots':
        monkeypatch.setattr('core.engine.MAX_SLOTS',1)
        account.positions['OTHER']={}
    if fault=='daily': account.daily_loss_limit_hit=lambda:(True,10.)
    if fault=='balance': account.balance=0.
    if fault=='changed': ctx['channel_confirmation_bar_id']-=60000
    if fault=='exchange_error': account.open_position=AsyncMock(side_effect=RuntimeError('exchange rejected'))
    assert not asyncio.run(engine._execute_confirmed_channel_break(symbol,f,float(f.iloc[-1].close),'LONG',
        v8_reason=ctx['entry_signal_code'],candidate_bar_id=ctx['channel_confirmation_bar_id']))
    reason=engine._entry_gate_diagnostics[(symbol,'LONG','EXECUTION')][1]
    assert {'slots':'MAX_SLOTS','daily':'daily loss','balance':'INSUFFICIENT_MARGIN',
            'quote':'execution_price','changed':'snapshot is None','exchange_error':'exchange rejected'}[fault] in reason
    assert symbol not in account.positions
