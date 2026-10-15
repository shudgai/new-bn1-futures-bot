"""Regression coverage for signal -> firewall -> account -> market boundary."""
import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pandas as pd
import pytest

from core.services.strategies.unified_entry_strategy import evaluate_closed_entry
from core.services.entry_firewall import validate_account_entry, validate_entry_frame


def candles(side='LONG', body=.2):
    stamp = int(time.time() // 60) * 60000 - 60000
    rows = [dict(timestamp=stamp-(5-i)*60000, open=100., close=100.1,
                 high=100.3, low=99.8, ma3=100.2, ma15=100., atr=1.,
                 kc_upper=100.4, kc_middle=100., kc_lower=99.6,
                 is_closed=True) for i in range(6)]
    rows[-1].update(open=100.7-body, close=100.7, high=100.72,
                    low=100.68-body, ma3=100.4)
    f=pd.DataFrame(rows)
    if side=='SHORT':
        original=f.copy()
        for a,b in [('open','open'),('close','close'),('high','low'),('low','high'),
                    ('ma3','ma3'),('ma15','ma15'),('kc_upper','kc_lower'),
                    ('kc_lower','kc_upper'),('kc_middle','kc_middle')]:
            f[a]=200-original[b]
    f.attrs['timeframe_ms']=60000
    return f


def context(f, side):
    return dict(entry_mode='CHANNEL_SWING', entry_signal_code=evaluate_closed_entry(f,side)[1],
                channel_confirmation_bar_id=float(f.iloc[-1].timestamp), market_mode='CHOPPY')






@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_breakout_requires_five_previous_bars(side):
    assert not evaluate_closed_entry(candles(side).iloc[-5:],side)[0]




@pytest.mark.parametrize('fault',['cooldown','stale','changed'])
def test_account_rejects_with_concrete_reason(fault):
    f=candles();ctx=context(f,'LONG')
    account=SimpleNamespace(entry_frame_provider=AsyncMock(return_value=f),last_closed_at={})
    if fault=='cooldown':account.last_closed_at['TEST']=time.time()
    if fault=='stale':f['timestamp']-=300000;ctx['channel_confirmation_bar_id']-=300000
    if fault=='changed':ctx['channel_confirmation_bar_id']-=60000
    with pytest.raises(ValueError,match='FORBIDDEN_ENTRY'):
        asyncio.run(validate_account_entry(account,'TEST','LONG',ctx))










def test_ui_selects_execution_only_for_same_signal_bar():
    import ast
    from pathlib import Path
    module=ast.parse(Path('services/api.py').read_text())
    function=next(n for n in module.body if isinstance(n,ast.FunctionDef) and n.name=='latest_entry_diagnostic')
    namespace={}
    exec(compile(ast.Module(body=[function],type_ignores=[]),'diagnostic','exec'),namespace)
    choose=namespace['latest_entry_diagnostic']
    cache={('TEST','LONG','CLOSED_SIGNAL'):(1,'CLOSED_IGNITION_LONG'),
           ('TEST','LONG','EXECUTION'):(1,'ORDER_REJECT')}
    assert choose(cache,'TEST','LONG')==(1,'ORDER_REJECT')
    cache[('TEST','LONG','CLOSED_SIGNAL')]=(2,'WAIT_NEW')
    assert choose(cache,'TEST','LONG')==(2,'WAIT_NEW')


def test_blocked_word_is_not_misclassified_as_ck():
    import ast
    from pathlib import Path
    module=ast.parse(Path('services/api.py').read_text())
    function=next(n for n in module.body if isinstance(n,ast.FunctionDef) and n.name=='map_block_reason')
    namespace={}
    exec(compile(ast.Module(body=[function],type_ignores=[]),'reason_map','exec'),namespace)
    translate=namespace['map_block_reason']
    assert 'CK' not in translate('BLOCKED_OPPOSITE_CLOSED_BODY')
    assert translate('BLOCKED_INSUFFICIENT_MARGIN')=='BLOCKED_INSUFFICIENT_MARGIN'
    assert 'BTC' in translate('BLOCKED_BTC_DUMPING_FORBID_LONG')
