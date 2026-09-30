"""Missed initial breakout can continue, without bypassing execution controls."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from test_entry_gate_consistency import frame
from core.services.strategies.pure_trend_v2 import evaluate_v2_frame
from core.services.entry_firewall import validate_account_entry

SYMBOL = '1000PEPE/USDT'


def continuation_frame(side='LONG'):
    f = frame()
    # Four outside closes: the old fresh-consolidation gate must no longer veto.
    for i in f.index[-5:-2]:
        f.loc[i, ['open','close','high','low']] = [100.8,101.05,101.08,100.7]
    if side == 'SHORT':
        original=f.copy()
        for a,b in [('open','open'),('close','close'),('high','low'),('low','high'),
                    ('ma3','ma3'),('ma15','ma15'),('kc_upper','kc_lower'),
                    ('kc_lower','kc_upper'),('kc_middle','kc_middle')]:
            f[a]=200-original[b]
    return f


@pytest.mark.parametrize('side', ['LONG','SHORT'])
def test_continuation_passes_shared_and_account_revalidation(side):
    f=continuation_frame(side); saved=f.copy(deep=True)
    d=evaluate_v2_frame(f,symbol=SYMBOL)
    assert d and d['side']==side
    assert d['entry_phase']=='OUTSIDE_CONTINUATION'
    account=SimpleNamespace(trades=[],last_closed_at={},entry_frame_provider=AsyncMock(return_value=f))
    result=asyncio.run(validate_account_entry(account,SYMBOL,side,dict(
        entry_signal_code=d['type'],channel_confirmation_bar_id=d['confirmation_bar_id'])))
    assert result['entry_phase']=='OUTSIDE_CONTINUATION'
    assert f.equals(saved)


@pytest.mark.parametrize('side', ['LONG','SHORT'])
@pytest.mark.parametrize('fault', ['no_extreme','inside_live','touch_live','opposite_previous',
    'inside_previous','gap','distance','ck','cooldown','old_code'])
def test_continuation_preserves_guards(side,fault):
    f=continuation_frame(side); account=None; code=None
    long=side=='LONG'
    if fault=='no_extreme': f.loc[f.index[-1],'close']=101.14 if long else 98.86
    if fault in ('inside_live','touch_live'):
        edge='kc_upper' if long else 'kc_lower'
        f.loc[f.index[-1],edge]=float(f.iloc[-1].close)+(0 if fault=='touch_live' else .01 if long else -.01)
    if fault=='opposite_previous': f.loc[f.index[-2],'open']=101.12 if long else 98.88
    if fault=='inside_previous': f.loc[f.index[-2],'close']=100.99 if long else 99.01
    if fault=='gap': f.loc[f.index[-3],'timestamp']-=60000
    if fault=='distance':
        f.loc[f.index[-1],['close','high' if long else 'low']]=[102.,102.1] if long else [98.,97.9]
    if fault=='ck': f.loc[f.index[-2],'kc_middle']=99.9 if long else 100.1
    if fault=='cooldown': account=SimpleNamespace(trades=[dict(symbol=SYMBOL,action='CLOSE_'+side,id=float(f.iloc[-1].timestamp)-60000+1000)])
    if fault=='old_code': code='CONTINUATION_RE_ENTRY_'+side
    assert evaluate_v2_frame(f,account=account,symbol=SYMBOL,code=code) is None


@pytest.mark.parametrize('side', ['LONG','SHORT'])
@pytest.mark.parametrize('fault', [None,'slots','balance','quote_changed'])
def test_continuation_engine_paper_and_dedup(monkeypatch,side,fault):
    from core.engine import TradingEngine
    from core.paper_account import PaperAccount
    monkeypatch.setattr(PaperAccount,'load_state',lambda self:None)
    monkeypatch.setattr(PaperAccount,'save_state',lambda self:None)
    account=PaperAccount(); account.balance=100.
    engine=object.__new__(TradingEngine); engine.account=account
    f=continuation_frame(side); price=float(f.iloc[-1].close)
    engine.tickers={SYMBOL:price}
    engine.fetch_klines=AsyncMock(return_value=f)
    engine.strategy=SimpleNamespace(compute_indicators=lambda data:data)
    engine.symbol_rotation=SimpleNamespace(get_dynamic_leverage=lambda *args:2)
    engine._execution_price_is_safe=AsyncMock(return_value=True)
    d=evaluate_v2_frame(f,symbol=SYMBOL)
    if fault=='slots':
        monkeypatch.setattr('core.engine.MAX_SLOTS',1)
        account.positions['OTHER']={}
    if fault=='balance': account.balance=0.
    if fault=='quote_changed': engine.tickers[SYMBOL]=100.
    async def attempt():
        return await engine._execute_confirmed_channel_break(SYMBOL,f,price,side,
            v8_reason=d['type'],candidate_bar_id=d['confirmation_bar_id'])
    assert bool(asyncio.run(attempt())) is (fault is None)
    if fault is None:
        assert account.positions[SYMBOL]['side']==side
        account.positions.clear()
        assert not asyncio.run(attempt())
        assert len(account.trades)==1
    else:
        assert not account.trades
