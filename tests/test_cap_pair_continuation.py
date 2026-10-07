import asyncio
import copy
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pandas as pd
import pytest

from core.services.cap_breakout_entry import STATE_KEY, observe_cap_breakout
from core.services.entry_contract import (
    evaluate_entry_contract, evaluate_continuation_entry, live_breakout_ma5_evidence,
)
from core.services.entry_firewall import validate_account_entry
from core.services.entry_gate_integrity import assert_commit_proof
from test_entry_chop_gate import eligible_frame, authority_code


def account():
    return SimpleNamespace(positions={}, position_meta={}, trades=[], last_closed_at={},
                           save_state=Mock(), log=Mock())


def observe(a, f, monkeypatch, price=None, stamp=None):
    stamp = float(f.iloc[-1].timestamp)+1000 if stamp is None else stamp
    monkeypatch.setattr('time.time', lambda: stamp/1000)
    observe_cap_breakout(a, 'CAP/USDT', f,
                        float(f.iloc[-1].close) if price is None else price, stamp)


def next_frame(f):
    f = f.copy()
    index = max(f.index)+1
    row = dict(f.iloc[-1])
    f.loc[f.index[-1], 'is_closed'] = True
    row.update(timestamp=float(row['timestamp'])+60000, is_closed=False,
               kc_middle=float(row['kc_middle'])+.01 if row['close'] > 100 else float(row['kc_middle'])-.01)
    f.loc[index] = row
    return f


def advance(a, f, monkeypatch):
    for second in range(1, 60):
        observe(a, f, monkeypatch, stamp=float(f.iloc[-1].timestamp)+second*1000)
    new = next_frame(f)
    observe(a, new, monkeypatch)
    return new


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_cap_only_two_closed_bars_and_third_live(side):
    f = eligible_frame('pair', side)
    d = evaluate_entry_contract(f, symbol='CAP/USDT')
    assert d and d['type'] == 'KC_2BAR_CONFIRM_'+side
    assert d['third_bar_id'] == float(f.iloc[-1].timestamp)
    assert d['third_bar_id']-d['pair_confirmation_bar_id'] == 60000
    assert (1 if side == 'LONG' else -1)*(d['price']-d['cap_quote_ma5']) > 0
    premature = f.iloc[:-1].copy()
    premature.loc[premature.index[-1], 'is_closed'] = False
    assert evaluate_entry_contract(premature, symbol='CAP/USDT') is None


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
@pytest.mark.parametrize('kind', ['live', 'pivot'])
def test_cap_disables_live_but_both_symbols_retain_independent_pivot(side, kind):
    f = eligible_frame(kind, side)
    diagnostics = {}
    result = evaluate_entry_contract(f, code=authority_code(kind, side), symbol='CAP/USDT',
                                    diagnostics=diagnostics)
    if kind == 'live':
        assert result is None
        assert diagnostics['reason'] == 'BLOCKED_CAP_LIVE_BREAKOUT_DISABLED'
    else:
        assert result and result['type'] == authority_code(kind, side)
    assert evaluate_entry_contract(f, code=authority_code(kind, side), symbol='龙虾/USDT')


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_cap_inside_pivot_is_independent_of_cancelled_outside_source(side, monkeypatch):
    a = account()
    f = eligible_frame('pivot', side)
    now = float(f.iloc[-1].timestamp)+1000
    monkeypatch.setattr('time.time', lambda: now/1000)
    f.attrs.update(entry_finality_verified=True, entry_finality_server_ms=now)
    a.position_meta[STATE_KEY] = {'CAP/USDT': dict(
        cancelled_second_ms=float(f.iloc[-2].timestamp), status='CAP_ORIGIN_CANCELLED_RAIL_RETURN')}
    a.entry_frame_provider = AsyncMock(return_value=f)
    context = dict(entry_signal_code=authority_code('pivot', side),
                   channel_confirmation_bar_id=float(f.iloc[-1].timestamp))
    asyncio.run(validate_account_entry(a, 'CAP/USDT', side, context))
    assert_commit_proof(a, 'CAP/USDT', side, context)
    result = evaluate_entry_contract(f, account=a, symbol='CAP/USDT')
    assert result['type'] == authority_code('pivot', side)
    assert f.iloc[-1].kc_lower < result['price'] < f.iloc[-1].kc_upper
    assert result['outer_run_bars'] == 10
    assert 'cap_quote_ma5' not in result
    assert 'origin' not in a.position_meta[STATE_KEY]['CAP/USDT']
    assert evaluate_continuation_entry(f, result['price'], account=a, symbol='CAP/USDT') is None


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_cap_quote_must_be_beyond_ma5_even_when_outside_kc(side):
    f = eligible_frame('pair', side)
    # Preserve live MA5's favorable slope and MA15 alignment, but move the
    # quote-derived MA5 outside the already-outside quote.
    sign = 1 if side == 'LONG' else -1
    for index in f.index[-5:-4]:
        close = 102.1 if sign == 1 else 97.9
        f.loc[index, ['open', 'close', 'high', 'low']] = [close-sign*.02, close, close+.1, close-.1]
    f.loc[f.index[-2], 'ma5'] = 101.2 if sign == 1 else 98.8
    diagnostics = {}
    assert evaluate_entry_contract(f, symbol='CAP/USDT', diagnostics=diagnostics) is None
    assert diagnostics['reason'] == 'BLOCKED_CAP_QUOTE_INSIDE_MA5'


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
@pytest.mark.parametrize('had_position', [False, True])
def test_missed_or_closed_entry_continues_without_new_two_bar_pair(side, had_position, monkeypatch):
    a = account()
    f = eligible_frame('pair', side)
    observe(a, f, monkeypatch)
    origin = copy.deepcopy(a.position_meta[STATE_KEY]['CAP/USDT']['origin'])
    if had_position:
        a.positions['CAP/USDT'] = dict(side=side)
    f = advance(a, f, monkeypatch)
    if had_position:
        a.positions.clear()
        a.last_closed_at['CAP/USDT'] = (float(f.iloc[-1].timestamp)-60000)/1000+1
    d = evaluate_entry_contract(f, account=a, symbol='CAP/USDT')
    assert d and d['type'] == 'CAP_KC_CONTINUATION_'+side
    assert d['cap_origin_id'] == origin['id']
    assert d['cap_origin_candles'] == origin['candles']
    assert evaluate_continuation_entry(f, float(f.iloc[-1].close),
                                       account=a, symbol='CAP/USDT')['type'] == d['type']
    assert evaluate_continuation_entry(f, float(f.iloc[-1].close), symbol='CAP/USDT') is None
    # Last K2's open was already outside, so this is not a new true breakout.
    assert evaluate_entry_contract(f, symbol='CAP/USDT') is None


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
@pytest.mark.parametrize('retreat', ['touch', 'inside'])
def test_rail_return_cancels_origin_and_cannot_rearm_same_pair(side, retreat, monkeypatch):
    a = account()
    f = eligible_frame('pair', side)
    observe(a, f, monkeypatch)
    sign = 1 if side == 'LONG' else -1
    edge = float(f.iloc[-1]['kc_upper' if sign == 1 else 'kc_lower'])
    observe(a, f, monkeypatch, price=edge if retreat == 'touch' else edge-sign*.01)
    assert 'origin' not in a.position_meta[STATE_KEY]['CAP/USDT']
    observe(a, f, monkeypatch)
    assert 'origin' not in a.position_meta[STATE_KEY]['CAP/USDT']
    assert evaluate_entry_contract(f, account=a, symbol='CAP/USDT') is None


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_restart_and_interrupted_market_do_not_replay_old_source(side, monkeypatch):
    a = account()
    f = eligible_frame('pair', side)
    observe(a, f, monkeypatch)
    restarted = account()
    restarted.position_meta = json.loads(json.dumps(a.position_meta))
    observe(restarted, f, monkeypatch)
    assert 'origin' not in restarted.position_meta[STATE_KEY]['CAP/USDT']
    assert evaluate_entry_contract(f, account=restarted, symbol='CAP/USDT') is None
    observe(a, f, monkeypatch, stamp=float(f.iloc[-1].timestamp)+7000)
    assert 'origin' not in a.position_meta[STATE_KEY]['CAP/USDT']


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_continuation_close_bar_fill_dedupe_and_other_symbol_isolation(side, monkeypatch):
    a = account()
    f = eligible_frame('pair', side)
    observe(a, f, monkeypatch)
    f = advance(a, f, monkeypatch)
    d = evaluate_entry_contract(f, account=a, symbol='CAP/USDT')
    assert d
    diagnostics = {}
    assert evaluate_entry_contract(f, account=a, symbol='龙虾/USDT', code=d['type'],
                                   diagnostics=diagnostics) is None
    assert diagnostics['reason'] == 'BLOCKED_CAP_AUTHORITY_WRONG_SYMBOL'
    a.last_closed_at['CAP/USDT'] = float(f.iloc[-1].timestamp)/1000+1
    assert evaluate_entry_contract(f, account=a, symbol='CAP/USDT') is None
    a.last_closed_at.clear()
    a.trades.append(dict(symbol='CAP/USDT', action='OPEN_'+side,
                         entry_snapshot=dict(pending_signal_id=d['pending_signal_id'])))
    assert evaluate_entry_contract(f, account=a, symbol='CAP/USDT') is None


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_firewall_rechecks_ma5_and_origin_before_continuation(side, monkeypatch):
    a = account()
    f = eligible_frame('pair', side)
    observe(a, f, monkeypatch)
    f = advance(a, f, monkeypatch)
    now = float(f.iloc[-1].timestamp)+1000
    f.attrs.update(entry_finality_verified=True, entry_finality_server_ms=now)
    a.entry_frame_provider = AsyncMock(return_value=f)
    context = dict(entry_signal_code='CAP_KC_CONTINUATION_'+side,
                   channel_confirmation_bar_id=float(f.iloc[-1].timestamp))
    asyncio.run(validate_account_entry(a, 'CAP/USDT', side, context))
    assert context['entry_snapshot']['cap_origin_id']
    edge = float(f.iloc[-1]['kc_upper' if side == 'LONG' else 'kc_lower'])
    observe(a, f, monkeypatch, price=edge)
    with pytest.raises(ValueError):
        asyncio.run(validate_account_entry(a, 'CAP/USDT', side, copy.deepcopy(context)))
    with pytest.raises(ValueError, match='CAP_ORIGIN_CANCELLED'):
        assert_commit_proof(a, 'CAP/USDT', side, context)
    assert not a.position_meta.get('_entry_gate_halts')


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
@pytest.mark.parametrize('phase', ['pair', 'continuation'])
@pytest.mark.parametrize('direction', ['flat', 'opposite'])
def test_cap_ma5_direction_cannot_be_bypassed(side, phase, direction, monkeypatch):
    a = account()
    f = eligible_frame('pair', side)
    observe(a, f, monkeypatch)
    if phase == 'continuation':
        f = advance(a, f, monkeypatch)
    sign = 1 if side == 'LONG' else -1
    live_ma5 = (float(f.iloc[-5:-1].close.sum())+float(f.iloc[-1].close))/5
    f.loc[f.index[-2], 'ma5'] = live_ma5 if direction == 'flat' else live_ma5+sign*.01
    diagnostics = {}
    assert evaluate_entry_contract(f, account=a, symbol='CAP/USDT',
                                   diagnostics=diagnostics) is None
    assert diagnostics['reason'] == 'BLOCKED_LIVE_MA5_FLAT_OPPOSITE_OR_INVALID'


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
@pytest.mark.parametrize('direction', ['flat', 'opposite'])
def test_lobster_pivot_also_requires_live_ma5_direction(side, direction):
    f = eligible_frame('pivot', side)
    sign = 1 if side == 'LONG' else -1
    live_ma5 = (float(f.iloc[-5:-1].close.sum())+float(f.iloc[-1].close))/5
    f.loc[f.index[-2], 'ma5'] = live_ma5 if direction == 'flat' else live_ma5+sign*.01
    diagnostics = {}
    assert evaluate_entry_contract(f, code=authority_code('pivot', side),
                                   symbol='龙虾/USDT', diagnostics=diagnostics) is None
    assert diagnostics['reason'] == 'BLOCKED_LIVE_MA5_FLAT_OPPOSITE_OR_INVALID'


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_ma5_slope_uses_the_same_filtered_indicator_basis(side):
    f = eligible_frame('pair', side)
    quote = float(f.iloc[-1].close)
    assert live_breakout_ma5_evidence(f, quote, side)
    f['close_price_spike_filtered'] = f.close-(.5 if side == 'LONG' else -.5)
    assert live_breakout_ma5_evidence(f, quote, side) is None


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_diagnostics_do_not_arm_or_consume_and_other_symbol_does_not_touch_cap(side, monkeypatch):
    a = account()
    f = eligible_frame('pair', side)
    evaluate_entry_contract(f, account=a, symbol='CAP/USDT', diagnostics={})
    assert STATE_KEY not in a.position_meta
    observe(a, f, monkeypatch)
    saved = copy.deepcopy(a.position_meta)
    observe_cap_breakout(a, '龙虾/USDT', f, 100.)
    assert a.position_meta == saved
    f = advance(a, f, monkeypatch)
    saved = copy.deepcopy(a.position_meta)
    assert evaluate_entry_contract(f, account=a, symbol='CAP/USDT')
    assert a.position_meta == saved


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_ma5_retreat_blocks_but_does_not_cancel_outside_origin(side, monkeypatch):
    a = account()
    f = eligible_frame('pair', side)
    observe(a, f, monkeypatch)
    f = advance(a, f, monkeypatch)
    ident = a.position_meta[STATE_KEY]['CAP/USDT']['origin']['id']
    sign = 1 if side == 'LONG' else -1
    quote = 101.15 if side == 'LONG' else 98.85
    f.loc[f.index[-1], ['close', 'high', 'low']] = [
        quote, max(quote, float(f.iloc[-1].open))+.1,
        min(quote, float(f.iloc[-1].open))-.1]
    # Keep MA5 direction valid to isolate quote versus MA5.
    f.loc[f.index[-2], 'ma5'] = 101.1 if side == 'LONG' else 98.9
    observe(a, f, monkeypatch)
    assert a.position_meta[STATE_KEY]['CAP/USDT']['origin']['id'] == ident
    assert sign*(quote-f.iloc[-1]['kc_upper' if sign == 1 else 'kc_lower']) > 0
    diagnostics = {}
    assert evaluate_entry_contract(f, account=a, symbol='CAP/USDT', diagnostics=diagnostics) is None
    assert diagnostics['reason'] == 'BLOCKED_CAP_QUOTE_INSIDE_MA5'


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
@pytest.mark.parametrize('phase', ['pair', 'continuation'])
def test_mock_testnet_cap_order_uses_current_authority(side, phase, monkeypatch, tmp_path):
    from decimal import Decimal, ROUND_DOWN
    from core.testnet_account import BinanceTestnetAccount
    from core.services.structure_risk_sizing import FULL_SLOT_POLICY
    monkeypatch.chdir(tmp_path)
    (tmp_path/'logs').mkdir()
    transport = AsyncMock(side_effect=AssertionError('No actual exchange requests'))
    exchange = SimpleNamespace(create_order=transport,
        market=lambda symbol: dict(linear=True, contractSize=1., info={'filters': [
            dict(filterType=name, minQty='.001', maxQty='1000000', stepSize='.001')
            for name in ('LOT_SIZE', 'MARKET_LOT_SIZE')]}),
        amount_to_precision=lambda symbol, qty: str(Decimal(qty).quantize(
            Decimal('.001'), rounding=ROUND_DOWN)))
    a = BinanceTestnetAccount(exchange, state_file=str(tmp_path/'testnet.json'))
    f = eligible_frame('pair', side)
    observe(a, f, monkeypatch)
    if phase == 'continuation':
        f = advance(a, f, monkeypatch)
    price = float(f.iloc[-1].close)
    now = float(f.iloc[-1].timestamp)+1000
    f.attrs.update(entry_finality_verified=True, entry_finality_server_ms=now)
    a._ensure_markets = AsyncMock()
    a._prepare_leverage = AsyncMock()
    a._send_order = AsyncMock(return_value=dict(id='mock-cap-order', average=price))
    a._finalize_new_position = AsyncMock(return_value=True)
    a.entry_frame_provider = AsyncMock(return_value=f)
    code = 'KC_2BAR_CONFIRM_'+side if phase == 'pair' else 'CAP_KC_CONTINUATION_'+side
    context = dict(entry_mode='CHANNEL_SWING', entry_signal_code=code,
                   channel_confirmation_bar_id=float(f.iloc[-1].timestamp))
    assert asyncio.run(a.open_position('CAP/USDT', side, price, 99., 0., 0.,
                      'CAP_TEST', atr=1., leverage=5., entry_context=context))
    saved = a._finalize_new_position.await_args.kwargs['entry_context']
    assert saved['structure_risk_policy'] == FULL_SLOT_POLICY
    assert saved['entry_snapshot']['signal_code'] == code
    assert a._send_order.await_count == 1
    transport.assert_not_awaited()


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_fresh_pair_after_cancellation_can_rearm(side, monkeypatch):
    a = account()
    f = eligible_frame('pair', side)
    observe(a, f, monkeypatch)
    observe(a, f, monkeypatch, price=100.)
    original_second = float(f.iloc[-2].timestamp)
    f['timestamp'] += 120000.
    observe(a, f, monkeypatch)
    assert a.position_meta[STATE_KEY]['CAP/USDT']['origin']['second_ms'] > original_second
    assert evaluate_entry_contract(f, account=a, symbol='CAP/USDT')


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
@pytest.mark.parametrize('phase', ['pair', 'continuation'])
@pytest.mark.parametrize('concurrent', [False, True])
def test_real_paper_cap_entry_uses_shared_submit_lock(side, phase, concurrent, monkeypatch, tmp_path):
    import core.paper_account as paper
    from test_two_slot_full_margin import make_engine
    monkeypatch.chdir(tmp_path)
    (tmp_path/'logs').mkdir()
    monkeypatch.setattr(paper, 'STATE_FILE', str(tmp_path/'paper.json'))
    a = paper.PaperAccount()
    a.balance = 200.
    f = eligible_frame('pair', side)
    observe(a, f, monkeypatch)
    if phase == 'continuation':
        f = advance(a, f, monkeypatch)
    engine, signal = make_engine(a, monkeypatch, side, 'CAP/USDT', entry_frame=f)
    quote = float(f.iloc[-1].close)
    if concurrent:
        async def both():
            return await asyncio.gather(*(engine._place_structured_entry('CAP/USDT', signal, quote)
                                          for _ in range(2)))
        assert asyncio.run(both()) == [True, False]
    else:
        assert asyncio.run(engine._place_structured_entry_locked('CAP/USDT', signal, quote))
    assert len(a.trades) == 1
    assert a.positions['CAP/USDT']['side'] == side
    assert a.trades[0]['entry_snapshot']['signal_code'] == (
        'KC_2BAR_CONFIRM_'+side if phase == 'pair' else 'CAP_KC_CONTINUATION_'+side)
    assert a.positions['CAP/USDT']['margin'] > 99.
