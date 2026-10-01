"""Strict state-machine signals through real engine and isolated accounts."""
import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pandas as pd
import pytest

from core.services.entry_contract import evaluate_entry_contract as evaluate_v2_frame
from core.services.entry_firewall import validate_account_entry, validate_entry_frame


@pytest.fixture(autouse=True)
def no_network_wait(monkeypatch):
    monkeypatch.setattr('core.services.entry_finality.READ_INTERVAL_SECONDS', 0.)


def candles(side='LONG', live=True):
    stamp = int(time.time() // 60) * 60000
    rows = [dict(timestamp=stamp-(5-i)*60000, open=100., close=100.2,
                 high=100.4, low=99.8, ma3=100.2, ma15=100., atr=1.,
                 kc_upper=101.4, kc_middle=100., kc_lower=98.6, is_closed=True)
            for i in range(6)]
    rows[-3].update(open=101.0, close=101.5, high=101.6, low=100.9)
    rows[-2].update(open=101.5, close=101.8, high=101.9, low=101.4)
    rows[-1].update(open=101.5, close=101.8, high=101.9, low=101.4, kc_middle=100.1, is_closed=not live)
    if not live:
        rows = rows[:-1]
    f = pd.DataFrame(rows)
    if side == 'SHORT':
        original = f.copy()
        for a,b in [('open','open'),('close','close'),('high','low'),('low','high'),
                    ('ma3','ma3'),('ma15','ma15'),('kc_upper','kc_lower'),
                    ('kc_lower','kc_upper'),('kc_middle','kc_middle')]:
            f[a] = 200-original[b]
    f.attrs['timeframe_ms'] = 60000
    f.attrs['entry_finality_verified'] = True
    return f


def context(f, side):
    d = evaluate_v2_frame(f)
    return dict(entry_mode='CHANNEL_SWING', entry_signal_code=d['type'],
                channel_confirmation_bar_id=d['confirmation_bar_id'])


@pytest.mark.parametrize('side', ['LONG','SHORT'])
@pytest.mark.parametrize('live', [True,False])
def test_runner_to_paper_fill_and_dedup(monkeypatch, side, live):
    from core.engine import TradingEngine
    from core.paper_account import PaperAccount
    from core.services.symbol_runner import process_single_symbol_runner
    monkeypatch.setattr(PaperAccount, 'load_state', lambda self: None)
    monkeypatch.setattr(PaperAccount, 'save_state', lambda self: None)
    account = PaperAccount(); account.balance = 100.
    engine = object.__new__(TradingEngine); engine.account = account
    symbol = '1000PEPE/USDT'; f = candles(side, live)
    engine.exchange = SimpleNamespace(fetch_time=AsyncMock(return_value=float(f.iloc[-1].timestamp)+10000))
    engine.tickers = {symbol: float(f.iloc[-1].close)}
    engine.fetch_klines = AsyncMock(return_value=f)
    engine.strategy = SimpleNamespace(compute_indicators=lambda frame: frame)
    engine.symbol_rotation = SimpleNamespace(get_dynamic_leverage=lambda *args: 2)
    engine._execution_price_is_safe = AsyncMock(return_value=True)
    asyncio.run(process_single_symbol_runner(engine,symbol,time.time(),None,False,exit_frame=f))
    if not live:
        assert not account.positions
        return
    assert account.positions[symbol]['side'] == side
    assert account.positions[symbol]['entry_atr'] == 1.
    assert engine._entry_gate_diagnostics[(symbol,side,'EXECUTION')][1] == 'FILLED'
    account.positions.clear()
    asyncio.run(process_single_symbol_runner(engine,symbol,time.time(),None,False,exit_frame=f))
    assert len(account.trades) == 1
    assert 'already filled' in engine._entry_gate_diagnostics[(symbol,side,'EXECUTION')][1]


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
    account = SimpleNamespace(entry_frame_provider=AsyncMock(return_value=f), last_closed_at={})
    with pytest.raises(ValueError):
        asyncio.run(validate_account_entry(account,'TEST',side,ctx))


@pytest.mark.parametrize('side', ['LONG','SHORT'])
def test_exchange_boundary_sends_v2_order(monkeypatch, side):
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
    for prefix in ('THIRD_BAR_INTRA_', 'THIRD_BAR_TRACK_RIDING_', 'THREE_BAR_BREAKOUT_', 'CONTINUATION_', 'SECOND_BAR_OUTSIDE_', 'VALID_OUTSIDE_KC_'):
        with pytest.raises(ValueError):
            validate_entry_frame(f,side,prefix+side)


@pytest.mark.parametrize('fault', ['slots','daily','balance','quote','changed','exchange_error'])
def test_engine_risk_and_failure_gates(monkeypatch, fault):
    from core.engine import TradingEngine
    from core.paper_account import PaperAccount
    monkeypatch.setattr(PaperAccount,'load_state',lambda self:None)
    monkeypatch.setattr(PaperAccount,'save_state',lambda self:None)
    account=PaperAccount(); account.balance=100.
    engine=object.__new__(TradingEngine); engine.account=account
    symbol='1000PEPE/USDT'; f=candles(); ctx=context(f,'LONG')
    engine.exchange=SimpleNamespace(fetch_time=AsyncMock(return_value=float(f.iloc[-1].timestamp)+10000))
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
    assert not asyncio.run(engine._execute_confirmed_channel_break(symbol,f,101.8,'LONG',
        v8_reason=ctx['entry_signal_code'],candidate_bar_id=ctx['channel_confirmation_bar_id']))
    reason=engine._entry_gate_diagnostics[(symbol,'LONG','EXECUTION')][1]
    assert {'slots':'MAX_SLOTS','daily':'daily loss','balance':'INSUFFICIENT_MARGIN',
            'quote':'execution_price','changed':'snapshot is None','exchange_error':'exchange rejected'}[fault] in reason
    assert symbol not in account.positions


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
@pytest.mark.parametrize('fault', ['under_half','second_live','second_opposite','second_doji','wick_only','gap','retreat','no_seed'])
def test_closed_pair_required(side, fault):
    f=candles(side); sign=1 if side=='LONG' else -1
    assert evaluate_v2_frame(f)
    if fault=='under_half': f.loc[f.index[-3], 'open']=100+sign*1.01
    if fault=='second_live':
        f=f.iloc[:-1].copy(); f.loc[f.index[-1],'is_closed']=False
    if fault=='second_opposite': f.loc[f.index[-2],'open']=100+sign*1.85
    if fault=='second_doji': f.loc[f.index[-2],'open']=f.iloc[-2].close
    if fault=='wick_only': f.loc[f.index[-3],'close']=100+sign*1.3
    if fault=='gap': f.loc[f.index[-3],'open']=100+sign*1.41
    if fault=='no_seed': f.loc[f.index[-3],'open']=100+sign*1.45
    if fault=='retreat':
        assert evaluate_v2_frame(f,100.) is None
        return
    assert evaluate_v2_frame(f) is None

@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_prior_closed_atr_and_exact_half(side):
    f=candles(side)
    assert evaluate_v2_frame(f)
    f.loc[f.index[-3], 'atr']=100.
    assert evaluate_v2_frame(f)  # First candle's changing ATR is not the threshold.
    f.loc[f.index[-4], 'atr']=1.1
    assert evaluate_v2_frame(f) is None

@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_continuation_after_missed_entry_and_close(side):
    f=candles(side); sign=1 if side=='LONG' else -1
    extra=f.iloc[-1].copy(); extra['timestamp']+=60000
    f.loc[f.index[-1],'is_closed']=True
    f.loc[len(f)]=extra
    d=evaluate_v2_frame(f)
    assert d['entry_phase']=='OUTSIDE_CONTINUATION'
    account=SimpleNamespace(positions={},trades=[dict(symbol='TEST',action='CLOSE_'+side,id=float(f.iloc[-2].timestamp)+1000)])
    assert evaluate_v2_frame(f,account=account,symbol='TEST')['entry_phase']=='POST_EXIT_CONTINUATION'
    account.trades[0]['id']=float(f.iloc[-1].timestamp)+1000
    assert evaluate_v2_frame(f,account=account,symbol='TEST') is None
    # A closed return inside invalidates the original pair, even if price exits again.
    f.loc[f.index[-2], 'close']=100+sign*1.3
    f.loc[f.index[-2], 'low' if side=='LONG' else 'high']=100+sign*1.2
    assert evaluate_v2_frame(f) is None

@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_state_machine_and_firewall_share_closed_policy(side):
    from core.services.strategies.strict_state_machine import StrictStateMachineStrategy
    f=candles(side);d=evaluate_v2_frame(f)
    assert StrictStateMachineStrategy().evaluate_tick('TEST',f,float(f.iloc[-1].close))['reason']==d['type']
    assert validate_entry_frame(f,side,d['type'])['confirmation_bar_id']==float(f.iloc[-2].timestamp)

@pytest.mark.parametrize('symbol', ['1000PEPE/USDT', '龙虾/USDT'])
@pytest.mark.parametrize('side', ['LONG','SHORT'])
def test_post_close_continuation_reaches_account(monkeypatch,symbol,side):
    from core.engine import TradingEngine
    from core.paper_account import PaperAccount
    from core.services.symbol_runner import process_single_symbol_runner
    monkeypatch.setattr(PaperAccount,'load_state',lambda self:None)
    monkeypatch.setattr(PaperAccount,'save_state',lambda self:None)
    f=candles(side)
    f['timestamp']-=60000
    extra=f.iloc[-1].copy();extra['timestamp']+=60000
    f.loc[f.index[-1],'is_closed']=True
    f.loc[len(f)]=extra
    account=PaperAccount();account.balance=100.
    stamp=float(f.iloc[-2].timestamp)+1000
    account.trades=[dict(symbol=symbol,action='CLOSE_'+side,id=stamp)]
    account.last_closed_at[symbol]=stamp/1000
    engine=object.__new__(TradingEngine);engine.account=account
    engine.exchange=SimpleNamespace(fetch_time=AsyncMock(return_value=float(f.iloc[-1].timestamp)+10000))
    engine.tickers={symbol:float(f.iloc[-1].close)}
    engine.fetch_klines=AsyncMock(return_value=f)
    engine.strategy=SimpleNamespace(compute_indicators=lambda data:data)
    engine.symbol_rotation=SimpleNamespace(get_dynamic_leverage=lambda *args:2)
    engine._execution_price_is_safe=AsyncMock(return_value=True)
    asyncio.run(process_single_symbol_runner(engine,symbol,time.time(),None,False,exit_frame=f))
    assert account.positions[symbol]['side']==side
    assert engine._entry_gate_diagnostics[(symbol,side,'EXECUTION')][1]=='FILLED'

@pytest.mark.parametrize('side', ['LONG','SHORT'])
def test_leading_indicator_warmup_does_not_block_valid_pair(side):
    f=candles(side)
    for key in ('atr','kc_upper','kc_lower'):
        f.loc[f.index[:2],key]=float('nan')
    d=evaluate_v2_frame(f)
    assert d is not None
    assert d['side']==side
    assert validate_entry_frame(f,side,d['type'])['type']==d['type']

@pytest.mark.parametrize('side', ['LONG','SHORT'])
@pytest.mark.parametrize('location', [-4,-3,-2,-1])
def test_nonleading_missing_indicator_remains_blocked(side,location):
    f=candles(side)
    f.loc[f.index[location],'atr']=float('nan')
    assert evaluate_v2_frame(f) is None

@pytest.mark.parametrize('side',['LONG','SHORT'])
@pytest.mark.parametrize('ratio',[.99,1.,1.01,3.])
def test_second_directional_wick_boundary(side,ratio):
    f=candles(side); second=f.iloc[-2]; body=abs(second.close-second.open)
    key='high' if side=='LONG' else 'low'
    f.loc[f.index[-2],key]=(max(second.open,second.close)+body*ratio if side=='LONG'
                              else min(second.open,second.close)-body*ratio)
    assert (evaluate_v2_frame(f) is not None)==(ratio<1.)

@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_prior_close_does_not_mislabel_new_breakout(side):
    f=candles(side)
    account=SimpleNamespace(positions={},trades=[dict(symbol='TEST',action='CLOSE_'+side,id=float(f.iloc[0].timestamp))])
    assert evaluate_v2_frame(f,account=account,symbol='TEST')['entry_phase']=='INITIAL_BREAKOUT'

@pytest.mark.parametrize('fault',['changed_close','changed_high','no_next','too_early','future','changed_bar'])
def test_settlement_rejects_revisions_and_stale_tail(fault):
    from core.services.entry_finality import settled_pair
    a=candles();b=a.copy();server=float(a.iloc[-1].timestamp)+10000
    if fault=='changed_close': b.loc[b.index[-2],'close']=101.4
    if fault=='changed_high': b.loc[b.index[-2],'high']=102.
    if fault=='no_next': a=a.iloc[:-1];b=b.iloc[:-1]
    if fault=='too_early': server=float(a.iloc[-1].timestamp)+1000
    if fault=='future': server=float(a.iloc[-1].timestamp)-1
    if fault=='changed_bar': b['timestamp']+=60000
    assert not settled_pair(a,b,server)


def test_revision_between_order_reads_blocks_submission(monkeypatch):
    from core.engine import TradingEngine
    f=candles('SHORT'); revised=f.copy()
    revised.loc[revised.index[-2],'close']=revised.iloc[-2].open+.01
    engine=object.__new__(TradingEngine)
    engine.exchange=SimpleNamespace(fetch_time=AsyncMock(return_value=float(f.iloc[-1].timestamp)+10000))
    engine.fetch_klines=AsyncMock(side_effect=[f,revised])
    engine.strategy=SimpleNamespace(compute_indicators=lambda x:x)
    assert asyncio.run(engine._entry_boundary_frame('TEST')) is None


def test_account_requires_settled_provider():
    f=candles();ctx=context(f,'LONG');f.attrs.pop('entry_finality_verified')
    account=SimpleNamespace(entry_frame_provider=AsyncMock(return_value=f))
    with pytest.raises(ValueError,match='獨立取樣'):
        asyncio.run(validate_account_entry(account,'TEST','LONG',ctx))

@pytest.mark.parametrize('side',['LONG','SHORT'])
@pytest.mark.parametrize('index',[-3,-2,-1])
@pytest.mark.parametrize('shape',['doji','upper_t','lower_t'])
def test_doji_and_both_t_shapes_block_every_entry_candle(side,index,shape):
    f=candles(side)
    row=f.iloc[index];body=abs(row.close-row.open)
    if shape=='doji':
        f.loc[f.index[index],'open']=row.close
    elif shape=='upper_t':
        f.loc[f.index[index],'high']=max(row.open,row.close)+2*body
    else:
        f.loc[f.index[index],'low']=min(row.open,row.close)-2*body
    assert evaluate_v2_frame(f) is None


def test_latest_quote_turns_live_candle_into_doji():
    f=candles()
    assert evaluate_v2_frame(f)
    assert evaluate_v2_frame(f,float(f.iloc[-1].open)) is None

@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_account_rejects_live_t_after_signal(side):
    f=candles(side);ctx=context(f,side)
    row=f.iloc[-1];body=abs(row.close-row.open)
    f.loc[f.index[-1],'low']=min(row.open,row.close)-3*body
    account=SimpleNamespace(entry_frame_provider=AsyncMock(return_value=f))
    with pytest.raises(ValueError):
        asyncio.run(validate_account_entry(account,'TEST',side,ctx))


def test_settlement_rechecks_exchange_time_after_slow_second_read():
    from core.services.entry_finality import fetch_settled_entry_frame
    frame = candles()
    start = float(frame.iloc[-1].timestamp) + 10000
    engine = SimpleNamespace(
        fetch_klines=AsyncMock(return_value=frame),
        exchange=SimpleNamespace(fetch_time=AsyncMock(side_effect=[start, start + 60000])),
    )
    assert asyncio.run(fetch_settled_entry_frame(engine, 'TEST')) is None


def test_settlement_records_time_of_completed_verification():
    from core.services.entry_finality import fetch_settled_entry_frame
    frame = candles()
    start = float(frame.iloc[-1].timestamp) + 10000
    engine = SimpleNamespace(
        fetch_klines=AsyncMock(return_value=frame),
        exchange=SimpleNamespace(fetch_time=AsyncMock(side_effect=[start, start + 1000])),
    )
    result = asyncio.run(fetch_settled_entry_frame(engine, 'TEST'))
    assert result.attrs['entry_finality_server_ms'] == start + 1000


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
@pytest.mark.parametrize('index', [-3, -2, -1])
@pytest.mark.parametrize('wick_side', ['upper', 'lower'])
@pytest.mark.parametrize('ratio', [.99, 1., 1.01])
@pytest.mark.parametrize('price_scale', [1., .00001])
def test_any_entry_wick_body_boundary(side, index, wick_side, ratio, price_scale):
    f = candles(side)
    row = f.iloc[index]
    body = abs(row.close - row.open)
    if wick_side == 'upper':
        f.loc[f.index[index], 'high'] = max(row.open, row.close) + body * ratio
    else:
        f.loc[f.index[index], 'low'] = min(row.open, row.close) - body * ratio
    price_keys = ['open', 'high', 'low', 'close', 'ma3', 'ma15',
                  'atr', 'kc_upper', 'kc_middle', 'kc_lower']
    f[price_keys] *= price_scale
    assert (evaluate_v2_frame(f) is not None) == (ratio < 1.)


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
@pytest.mark.parametrize('index', [-4, -3, -2, -1])
@pytest.mark.parametrize('wick_side', ['upper', 'lower'])
@pytest.mark.parametrize('post_exit', [False, True])
def test_long_wick_blocks_continuation_and_reentry(side, index, wick_side, post_exit):
    f = candles(side)
    extra = f.iloc[-1].copy()
    extra['timestamp'] += 60000
    f.loc[f.index[-1], 'is_closed'] = True
    f.loc[len(f)] = extra
    account = SimpleNamespace(positions={}, trades=[])
    if post_exit:
        account.trades = [dict(symbol='TEST', action='CLOSE_' + side,
                              id=float(f.iloc[-2].timestamp) + 1000)]
    assert evaluate_v2_frame(f, account=account, symbol='TEST')
    row = f.iloc[index]
    body = abs(row.close - row.open)
    if wick_side == 'upper':
        f.loc[f.index[index], 'high'] = max(row.open, row.close) + 1.2 * body
    else:
        f.loc[f.index[index], 'low'] = min(row.open, row.close) - 1.2 * body
    assert evaluate_v2_frame(f, account=account, symbol='TEST') is None


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_latest_quote_creates_long_wick_and_recovers_without_lock(side):
    f = candles(side)
    sign = 1 if side == 'LONG' else -1
    row = f.iloc[-1]
    assert evaluate_v2_frame(f)
    # A retreat leaves a 1.22-body wick but not the former 2-body T shape.
    quote = float(row.open) + sign * .18
    diagnostics = {}
    assert evaluate_v2_frame(f, quote, diagnostics=diagnostics) is None
    assert diagnostics['reason'] == 'BLOCKED_LIVE_LONG_WICK_OR_DOJI'
    assert evaluate_v2_frame(f, float(row.close))


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
@pytest.mark.parametrize('wick_side', ['upper', 'lower'])
def test_account_rejects_long_wick_formed_after_signal(side, wick_side):
    f = candles(side)
    ctx = context(f, side)
    row = f.iloc[-1]
    body = abs(row.close - row.open)
    if wick_side == 'upper':
        f.loc[f.index[-1], 'high'] = max(row.open, row.close) + 1.2 * body
    else:
        f.loc[f.index[-1], 'low'] = min(row.open, row.close) - 1.2 * body
    account = SimpleNamespace(entry_frame_provider=AsyncMock(return_value=f))
    with pytest.raises(ValueError):
        asyncio.run(validate_account_entry(account, 'TEST', side, ctx))


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
@pytest.mark.parametrize('wick_side', ['upper', 'lower'])
def test_exchange_order_not_sent_when_fresh_frame_has_long_wick(monkeypatch, side, wick_side):
    from core.testnet_account import BinanceTestnetAccount
    monkeypatch.setattr(BinanceTestnetAccount, '_load_state', lambda self: None)
    monkeypatch.setattr(BinanceTestnetAccount, 'save_state', lambda self, **kwargs: None)
    raw = AsyncMock()
    account = BinanceTestnetAccount(SimpleNamespace(create_order=raw))
    f = candles(side)
    ctx = context(f, side)
    row = f.iloc[-1]
    body = abs(row.close - row.open)
    if wick_side == 'upper':
        f.loc[f.index[-1], 'high'] = max(row.open, row.close) + 1.2 * body
    else:
        f.loc[f.index[-1], 'low'] = min(row.open, row.close) - 1.2 * body
    account.entry_frame_provider = AsyncMock(return_value=f)
    account.last_closed_at = {}
    with pytest.raises(ValueError):
        asyncio.run(account._send_order('TEST', 'market', 'buy' if side == 'LONG' else 'sell',
                                       1, entry_context=ctx))
    raw.assert_not_awaited()


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_scan_and_idle_strategy_reject_long_wick(side):
    from core.services.symbol_runner import process_single_symbol_runner
    from core.services.strategies.strict_state_machine import StrictStateMachineStrategy
    f = candles(side)
    row = f.iloc[-1]
    f.loc[f.index[-1], 'high'] = max(row.open, row.close) + 1.2 * abs(row.close - row.open)
    quote = float(row.close)
    strategy = StrictStateMachineStrategy()
    decision = strategy.evaluate_tick('TEST', f, quote)
    assert decision == {'action': 'WAIT', 'reason': 'BLOCKED_LIVE_LONG_WICK_OR_DOJI'}
    account = SimpleNamespace(positions={}, log=lambda *args: None)
    engine = SimpleNamespace(account=account, tickers={'TEST': quote},
                             _execute_confirmed_channel_break=AsyncMock())
    asyncio.run(process_single_symbol_runner(engine, 'TEST', time.time(), None, False,
                                             exit_frame=f))
    engine._execute_confirmed_channel_break.assert_not_awaited()
