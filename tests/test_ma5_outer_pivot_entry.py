"""Independent pivot entry, fresh firewall and full-slot execution regressions."""
import asyncio
import copy
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from core.services.entry_contract import evaluate_entry_contract, ENTRY_CODES
from core.services.entry_firewall import validate_account_entry
from core.services.entry_gate_integrity import assert_commit_proof, VERSION
from core.services.ma5_outer_pivot_entry import PHASE
from test_lobster_cap_gates import frame
from test_two_slot_full_margin import make_engine

SYMBOLS = ['龙虾/USDT', 'CAP/USDT']


@pytest.fixture(autouse=True)
def isolate_files_and_clock(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    (tmp_path/'logs').mkdir()
    monkeypatch.setattr('time.time', lambda: 361.)


def pivot_frame(side='SHORT'):
    f = frame()
    f.loc[2:4, 'ma5'] = [101., 102., 101.7]
    f.loc[3, ['open', 'close', 'high', 'low']] = [102., 103., 104., 100.2]
    f.loc[4, ['open', 'close', 'high', 'low']] = [102., 101.4, 103., 100.5]
    f.loc[5, ['open', 'close', 'high', 'low']] = [100.3, 100.1, 100.4, 100.]
    if side == 'LONG':
        old = f.copy()
        for key, source in [('open', 'open'), ('close', 'close'), ('high', 'low'),
                            ('low', 'high'), ('ma5', 'ma5'), ('ma3', 'ma3'),
                            ('ma15', 'ma15'), ('kc_upper', 'kc_lower'),
                            ('kc_lower', 'kc_upper'), ('kc_middle', 'kc_middle')]:
            f[key] = 200.-old[source]
    f.attrs.update(entry_finality_verified=True, entry_finality_server_ms=361000.)
    return f


def account_for(f):
    return SimpleNamespace(positions={}, position_meta={}, pending_limit_orders={}, trades=[],
                           last_closed_at={}, channel_profit_reentries={}, save_state=Mock(),
                           log=Mock(), entry_frame_provider=AsyncMock(return_value=f))


@pytest.mark.parametrize('symbol', SYMBOLS)
@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_independent_pivot_inside_kc_needs_no_opposite_rail_break(side, symbol):
    f = pivot_frame(side)
    result = evaluate_entry_contract(f, symbol=symbol)
    assert result and result['type'] == PHASE+'_'+side
    assert result['type'] in ENTRY_CODES
    assert result['side'] == side
    assert f.iloc[-1].kc_lower < result['price'] < f.iloc[-1].kc_upper
    assert result['ma5_entry_boundary'] == f.iloc[3]['high' if side == 'LONG' else 'low']
    assert result['close_price'] == f.iloc[4].close
    assert 'kc_distance_atr' not in result
    assert evaluate_entry_contract(f, code='KC_LIVE_BODY_BREAKOUT_'+side) is None


@pytest.mark.parametrize('symbol', SYMBOLS)
@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
@pytest.mark.parametrize('fault', ['touch_price', 'retraced', 'wick_only', 'inside_pivot',
    'rail_touch', 'flat_left', 'flat_right', 'tiny_turn', 'no_ma5', 'nan_ma5',
    'unclosed_confirmation', 'gap', 'bad_ohlc', 'zero_range', 'bad_atr', 'past_live'])
def test_false_turns_and_invalid_evidence_cannot_authorize_entry(side, symbol, fault):
    f = pivot_frame(side)
    sign = 1 if side == 'LONG' else -1
    boundary = float(f.iloc[3]['high' if side == 'LONG' else 'low'])
    if fault in ('touch_price', 'retraced', 'wick_only'):
        f.loc[5, 'close'] = boundary if fault == 'touch_price' else boundary-sign*.05
    elif fault == 'inside_pivot': f.loc[3, 'ma5'] = 100.
    elif fault == 'rail_touch': f.loc[3, 'ma5'] = f.iloc[3]['kc_lower' if side == 'LONG' else 'kc_upper']
    elif fault == 'flat_left': f.loc[2, 'ma5'] = f.iloc[3].ma5
    elif fault == 'flat_right': f.loc[4, 'ma5'] = f.iloc[3].ma5
    elif fault == 'tiny_turn': f.loc[4, 'ma5'] = f.iloc[3].ma5+sign*1e-11
    elif fault == 'no_ma5': f = f.drop(columns='ma5')
    elif fault == 'nan_ma5': f.loc[3, 'ma5'] = float('nan')
    elif fault == 'unclosed_confirmation': f.loc[4, 'is_closed'] = False
    elif fault == 'gap': f.loc[3, 'timestamp'] += 1.
    elif fault == 'bad_ohlc': f.loc[3, 'high'] = f.iloc[3].low-.1
    elif fault == 'zero_range': f.loc[3, ['open', 'close', 'high', 'low']] = 100.
    elif fault == 'bad_atr': f.loc[4, 'atr'] = 0.
    elif fault == 'past_live': f.loc[5, 'timestamp'] += 60000.
    diagnostics = {}
    assert evaluate_entry_contract(f, symbol=symbol, code=PHASE+'_'+side, diagnostics=diagnostics) is None
    assert diagnostics['reason']


@pytest.mark.parametrize('symbol', SYMBOLS)
@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_position_close_bar_and_successful_fill_keep_gates_after_restart(side, symbol):
    f = pivot_frame(side)
    a = account_for(f)
    a.positions[symbol] = dict(side=side)
    assert evaluate_entry_contract(f, symbol=symbol, account=a) is None
    assert evaluate_entry_contract(f, symbol=symbol, account=a, code=PHASE+'_'+side, evaluate_held=True) is None
    a.positions.clear()
    a.last_closed_at[symbol] = 361.
    assert evaluate_entry_contract(f, symbol=symbol, account=a) is None
    a.last_closed_at.clear()
    result = evaluate_entry_contract(f, symbol=symbol, account=a)
    assert result
    a.trades = json.loads(json.dumps([dict(symbol=symbol, action='OPEN_'+side,
                                           entry_snapshot=result)]))
    diagnostics = {}
    assert evaluate_entry_contract(f, symbol=symbol, account=a, diagnostics=diagnostics) is None
    assert diagnostics['reason'] == 'BLOCKED_KC_BREAKOUT_ALREADY_FILLED'
    other = SYMBOLS[1] if symbol == SYMBOLS[0] else SYMBOLS[0]
    assert evaluate_entry_contract(f, symbol=other, account=a)


@pytest.mark.parametrize('symbol', SYMBOLS)
@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_final_firewall_revalidates_pivot_and_quote_and_issues_proof(side, symbol):
    f = pivot_frame(side)
    a = account_for(f)
    context = dict(entry_signal_code=PHASE+'_'+side, channel_confirmation_bar_id=360000.)
    asyncio.run(validate_account_entry(a, symbol, side, context))
    assert_commit_proof(a, symbol, side, context)
    assert context['entry_snapshot']['gate_version'] == VERSION
    assert context['entry_snapshot']['ma5_entry_boundary'] == f.iloc[3]['high' if side == 'LONG' else 'low']
    cached = copy.deepcopy(context)
    f.loc[5, 'close'] = context['entry_snapshot']['ma5_entry_boundary']
    with pytest.raises(ValueError, match='WAIT_CLOSED_OUTER_MA5_PRICE_BREAK'):
        asyncio.run(validate_account_entry(a, symbol, side, cached))


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_next_candle_does_not_reuse_untriggered_old_turn(side):
    f = pivot_frame(side)
    sign = 1 if side == 'LONG' else -1
    old_live = f.iloc[-1].copy()
    f.loc[5, 'is_closed'] = True
    f.loc[5, 'ma5'] = f.iloc[4].ma5+sign*.2
    f.loc[6] = old_live
    f.loc[6, 'timestamp'] = 420000.
    assert evaluate_entry_contract(f, code=PHASE+'_'+side) is None


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
@pytest.mark.parametrize('fault', ['stale', 'future', 'unverified'])
def test_firewall_requires_authoritative_fresh_market_data(side, fault):
    f = pivot_frame(side)
    if fault == 'unverified': f.attrs.pop('entry_finality_verified')
    else: f.attrs['entry_quote_ms'] = 355000. if fault == 'stale' else 362000.
    a = account_for(f)
    c = dict(entry_signal_code=PHASE+'_'+side, channel_confirmation_bar_id=360000.)
    with pytest.raises(ValueError, match='FORBIDDEN_ENTRY'):
        asyncio.run(validate_account_entry(a, 'CAP/USDT', side, c))


@pytest.mark.parametrize('phase', ['submitting', 'unknown', 'partial'])
def test_pivot_cannot_bypass_uncertain_order_quarantine(phase):
    f = pivot_frame()
    a = account_for(f)
    a.position_meta['_auto_reverse_tickets'] = {
        'CAP/USDT': dict(mode='direct_netting_v1', phase=phase)}
    c = dict(entry_signal_code=PHASE+'_SHORT', channel_confirmation_bar_id=360000.)
    with pytest.raises(ValueError, match='FORBIDDEN_ENTRY'):
        asyncio.run(validate_account_entry(a, 'CAP/USDT', 'SHORT', c))


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
@pytest.mark.parametrize('requested', ['pivot', 'breakout', 'pair', 'default'])
def test_opposite_authorities_fail_closed_even_with_explicit_code(side, requested):
    f = pivot_frame('SHORT')
    f.loc[3, 'low'] = 101.6
    f.loc[4, 'ma5'] = 101.6
    f.loc[5, ['open', 'close', 'high', 'low']] = [100.8, 101.5, 101.5, 100.6]
    if side == 'LONG':
        old = f.copy()
        for key, source in [('open', 'open'), ('close', 'close'), ('high', 'low'),
                            ('low', 'high'), ('ma5', 'ma5'), ('kc_upper', 'kc_lower'),
                            ('kc_lower', 'kc_upper'), ('kc_middle', 'kc_middle')]:
            f[key] = 200.-old[source]
    opposite = 'SHORT' if side == 'LONG' else 'LONG'
    code = (PHASE+'_'+side if requested == 'pivot' else
            'KC_LIVE_BODY_BREAKOUT_'+opposite if requested == 'breakout' else
            'KC_2BAR_CONFIRM_'+opposite if requested == 'pair' else None)
    diagnostics = {}
    assert evaluate_entry_contract(f, code=code, diagnostics=diagnostics) is None
    assert diagnostics['reason'] == 'BLOCKED_OPPOSITE_ENTRY_AUTHORITIES'


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_same_side_authorities_have_one_selected_decision(side):
    f = pivot_frame('SHORT')
    f.loc[5, ['open', 'close', 'high', 'low']] = [99.2, 98.5, 99.4, 98.4]
    if side == 'LONG':
        old = f.copy()
        for key, source in [('open', 'open'), ('close', 'close'), ('high', 'low'),
                            ('low', 'high'), ('ma5', 'ma5'), ('kc_upper', 'kc_lower'),
                            ('kc_lower', 'kc_upper'), ('kc_middle', 'kc_middle')]:
            f[key] = 200.-old[source]
    result = evaluate_entry_contract(f)
    assert result and result['type'] == 'KC_LIVE_BODY_BREAKOUT_'+side
    pivot = evaluate_entry_contract(f, code=PHASE+'_'+side)
    assert pivot and pivot['side'] == result['side']


@pytest.mark.parametrize('symbol', SYMBOLS)
@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
@pytest.mark.parametrize('concurrent', [False, True])
def test_real_paper_pivot_entry_uses_shared_full_half_budget(side, symbol, concurrent, monkeypatch, tmp_path):
    import core.paper_account as paper
    monkeypatch.setattr(paper, 'STATE_FILE', str(tmp_path/'paper.json'))
    a = paper.PaperAccount()
    a.balance = 200.
    engine, _ = make_engine(a, monkeypatch, side, symbol)
    f = pivot_frame(side)
    quote = float(f.iloc[-1].close)
    result = evaluate_entry_contract(f, symbol=symbol)
    engine.tickers[symbol] = quote
    engine._entry_boundary_frame = AsyncMock(return_value=f)
    engine._fresh_channel_entry_snapshot = AsyncMock(return_value=dict(frame=f, decision=result, price=quote))
    signal = dict(side=side, entry_mode='CHANNEL_SWING', signal_code=result['type'],
                  candidate_bar_id=result['confirmation_bar_id'], score=100)
    if concurrent:
        async def submit_twice():
            return await asyncio.gather(*(engine._place_structured_entry(symbol, signal, quote)
                                          for _ in range(2)))
        assert asyncio.run(submit_twice()) == [True, False]
    else:
        assert asyncio.run(engine._place_structured_entry_locked(symbol, signal, quote))
    assert a.positions[symbol]['side'] == side
    assert a.positions[symbol]['margin'] == pytest.approx(100./(1+5*paper.TAKER_FEE_RATE))
    assert a.trades[0]['entry_snapshot']['ma5_entry_boundary'] == result['ma5_entry_boundary']
    assert not asyncio.run(engine._place_structured_entry_locked(symbol, signal, quote))


@pytest.mark.parametrize('symbol', SYMBOLS)
@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_mock_testnet_pivot_entry_reaches_verified_order_boundary(side, symbol, tmp_path):
    from decimal import Decimal, ROUND_DOWN
    from core.testnet_account import BinanceTestnetAccount
    from core.services.structure_risk_sizing import FULL_SLOT_POLICY
    f = pivot_frame(side)
    price = float(f.iloc[-1].close)
    order_transport = AsyncMock(side_effect=AssertionError('No live exchange calls'))
    exchange = SimpleNamespace(
        create_order=order_transport,
        market=lambda symbol: dict(linear=True, contractSize=1., info={'filters': [
            dict(filterType=name, minQty='.001', maxQty='1000000', stepSize='.001')
            for name in ('LOT_SIZE', 'MARKET_LOT_SIZE')]}),
        amount_to_precision=lambda symbol, qty: str(Decimal(qty).quantize(Decimal('.001'), rounding=ROUND_DOWN)))
    a = BinanceTestnetAccount(exchange, state_file=str(tmp_path/'testnet.json'))
    a._ensure_markets = AsyncMock()
    a._prepare_leverage = AsyncMock()
    a._send_order = AsyncMock(return_value=dict(id='mock-pivot-order', average=price))
    a._finalize_new_position = AsyncMock(return_value=True)
    a.entry_frame_provider = AsyncMock(return_value=f)
    context = dict(entry_mode='CHANNEL_SWING', entry_signal_code=PHASE+'_'+side,
                   channel_confirmation_bar_id=360000.)
    assert asyncio.run(a.open_position(symbol, side, price, 99., 0., 0.,
                      'PIVOT_TEST', atr=1., leverage=5., entry_context=context))
    final_context = a._finalize_new_position.await_args.kwargs['entry_context']
    assert final_context['structure_risk_policy'] == FULL_SLOT_POLICY
    assert final_context['entry_snapshot']['ma5_entry_boundary'] == f.iloc[3]['high' if side == 'LONG' else 'low']
    assert a._send_order.await_count == 1
    order_transport.assert_not_awaited()
