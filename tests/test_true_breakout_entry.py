"""Real breakout provenance and quote-derived MA5 gates, independent of pivots."""
import asyncio
import copy
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from core.services.entry_contract import evaluate_entry_contract
from core.services.entry_firewall import validate_account_entry
from test_entry_chop_gate import eligible_frame
from core.services.entry_gate_integrity import assert_commit_proof
from test_ma5_outer_pivot_entry import pivot_frame
from core.services.ma5_outer_pivot_entry import PHASE


@pytest.mark.parametrize('symbol', ['龙虾/USDT', 'CAP/USDT'])
@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
@pytest.mark.parametrize('kind', ['live', 'pair'])
@pytest.mark.parametrize('slope', ['aligned', 'flat', 'tiny', 'opposite', 'invalid'])
def test_breakout_requires_latest_quote_ma5_direction(symbol, side, kind, slope):
    f = eligible_frame(kind, side)
    quote = float(f.iloc[-1].close)
    current = (float(f.iloc[-5:-1].close.sum())+quote)/5.
    sign = 1 if side == 'LONG' else -1
    f.loc[4, 'ma5'] = (current-sign*.1 if slope == 'aligned' else
                       current if slope == 'flat' else
                       current-sign*1e-11 if slope == 'tiny' else
                       current+sign*.1 if slope == 'opposite' else float('nan'))
    code = ('KC_LIVE_BODY_BREAKOUT_' if kind == 'live' else 'KC_2BAR_CONFIRM_')+side
    diagnostics = {}
    result = evaluate_entry_contract(f, code=code, symbol=symbol, diagnostics=diagnostics)
    if slope == 'aligned':
        assert result and result['type'] == code
        assert result['breakout_live_ma5'] == pytest.approx(current)
        assert result['breakout_previous_ma5'] == f.iloc[-2].ma5
        if kind == 'live':
            assert result['breakout_live_open'] == f.iloc[-1].open
            assert sign*(quote-result['breakout_live_edge'])>0
            assert result['breakout_body']>=result['breakout_min_body']
    else:
        assert result is None
        assert diagnostics['reason']


@pytest.mark.parametrize('symbol', ['龙虾/USDT', 'CAP/USDT'])
@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
@pytest.mark.parametrize('fault', ['inside', 'touch', 'wick_only', 'gap', 'other_side_gap', 'small_body'])
def test_live_authority_never_accepts_non_breakout(symbol, side, fault):
    f = eligible_frame('live', side)
    sign = 1 if side == 'LONG' else -1
    edge = float(f.iloc[-1]['kc_upper' if side == 'LONG' else 'kc_lower'])
    if fault in ('inside', 'touch', 'wick_only'):
        f.loc[5, 'close'] = edge if fault == 'touch' else edge-sign*.1
    elif fault == 'gap':
        f.loc[5, 'open'] = edge+sign*.1
    elif fault == 'other_side_gap':
        f.loc[5, 'open'] = float(f.iloc[-1]['kc_lower' if side == 'LONG' else 'kc_upper'])-sign*.1
        f.loc[5, 'low'] = min(float(f.iloc[-1].low), float(f.iloc[-1].open))
        f.loc[5, 'high'] = max(float(f.iloc[-1].high), float(f.iloc[-1].open))
    else: f.loc[5, 'close'] = edge+sign*.2
    assert evaluate_entry_contract(f, code='KC_LIVE_BODY_BREAKOUT_'+side, symbol=symbol) is None


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_pivot_is_independent_and_cannot_be_mislabeled_as_breakout(side):
    f = pivot_frame(side)
    result = evaluate_entry_contract(f)
    assert result and result['type'] == PHASE+'_'+side
    for prefix in ('KC_LIVE_BODY_BREAKOUT_', 'KC_2BAR_CONFIRM_'):
        assert evaluate_entry_contract(f, code=prefix+side) is None


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
@pytest.mark.parametrize('symbol', ['龙虾/USDT', 'CAP/USDT'])
@pytest.mark.parametrize('kind', ['live', 'pair'])
def test_final_firewall_revokes_cached_breakout_when_live_ma5_turns(side, symbol, kind, monkeypatch):
    monkeypatch.setattr('time.time', lambda: 421.)
    f = eligible_frame(kind, side)
    sign = 1 if side == 'LONG' else -1
    # A quote retreat remains outside KC but reverses quote-derived MA5.
    original = float(f.iloc[-1].close)
    current = (float(f.iloc[-5:-1].close.sum())+original)/5.
    f.loc[4, 'ma5'] = current-sign*.005
    f.attrs.update(entry_finality_verified=True, entry_finality_server_ms=421000.)
    a = SimpleNamespace(positions={}, trades=[], last_closed_at={}, position_meta={},
                        save_state=Mock(), log=Mock(), entry_frame_provider=AsyncMock(return_value=f))
    code = ('KC_LIVE_BODY_BREAKOUT_' if kind == 'live' else 'KC_2BAR_CONFIRM_')+side
    context = dict(entry_signal_code=code, channel_confirmation_bar_id=420000.)
    asyncio.run(validate_account_entry(a, symbol, side, context))
    cached = copy.deepcopy(context)
    f.loc[5, 'close'] = original-sign*.03
    assert f.iloc[-1].kc_lower < f.iloc[-1].kc_upper
    edge = float(f.iloc[-1]['kc_upper' if side == 'LONG' else 'kc_lower'])
    assert sign*(float(f.iloc[-1].close)-edge)>0
    with pytest.raises(ValueError, match='BLOCKED_LIVE_MA5_FLAT_OPPOSITE_OR_INVALID'):
        asyncio.run(validate_account_entry(a, symbol, side, cached))


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
@pytest.mark.parametrize('symbol', ['龙虾/USDT', 'CAP/USDT'])
def test_live_breakout_proof_expires_at_candle_boundary_even_when_fresh(side, symbol, monkeypatch):
    f = eligible_frame('live', side)
    monkeypatch.setattr('time.time', lambda: 479.999)
    f.attrs.update(entry_finality_verified=True, entry_finality_server_ms=479999.)
    a = SimpleNamespace(positions={}, trades=[], last_closed_at={}, position_meta={},
                        save_state=Mock(), log=Mock(), entry_frame_provider=AsyncMock(return_value=f))
    context = dict(entry_signal_code='KC_LIVE_BODY_BREAKOUT_'+side,
                   channel_confirmation_bar_id=420000.)
    asyncio.run(validate_account_entry(a, symbol, side, context))
    assert_commit_proof(a, symbol, side, context)
    monkeypatch.setattr('time.time', lambda: 480.001)
    with pytest.raises(ValueError, match='LIVE_BREAKOUT_CANDLE_EXPIRED'):
        assert_commit_proof(a, symbol, side, context)
    assert not a.position_meta.get('_entry_gate_halts')


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
@pytest.mark.parametrize('symbol', ['龙虾/USDT', 'CAP/USDT'])
@pytest.mark.parametrize('fault', ['inside', 'touch', 'short_body'])
def test_cached_live_permission_revoked_when_actual_break_disappears(side, symbol, fault, monkeypatch):
    f = eligible_frame('live', side)
    monkeypatch.setattr('time.time', lambda: 421.)
    f.attrs.update(entry_finality_verified=True, entry_finality_server_ms=421000.)
    a = SimpleNamespace(positions={}, trades=[], last_closed_at={}, position_meta={},
                        save_state=Mock(), log=Mock(), entry_frame_provider=AsyncMock(return_value=f))
    context = dict(entry_signal_code='KC_LIVE_BODY_BREAKOUT_'+side,
                   channel_confirmation_bar_id=420000.)
    asyncio.run(validate_account_entry(a, symbol, side, context))
    sign = 1 if side == 'LONG' else -1
    edge = float(f.iloc[-1]['kc_upper' if sign == 1 else 'kc_lower'])
    f.loc[5, 'close'] = edge if fault == 'touch' else edge-sign*.1 if fault == 'inside' else edge+sign*.2
    with pytest.raises(ValueError):
        asyncio.run(validate_account_entry(a, symbol, side, copy.deepcopy(context)))


@pytest.mark.parametrize('symbol', ['龙虾/USDT', 'CAP/USDT'])
@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
@pytest.mark.parametrize('body,passes', [(.499999, False), (.5, True), (.500001, True)])
def test_live_body_half_previous_closed_atr_inclusive(symbol, side, body, passes):
    f = eligible_frame('live', side)
    sign = 1 if side == 'LONG' else -1
    edge = float(f.iloc[-1]['kc_upper' if sign == 1 else 'kc_lower'])
    quote = edge+sign*.4
    f.loc[5, 'open'] = quote-sign*body
    f.loc[5, 'close'] = quote
    f.loc[5, 'high'] = max(float(f.iloc[-1].high), quote)
    f.loc[5, 'low'] = min(float(f.iloc[-1].low), quote)
    f.loc[4, 'atr'] = 1.
    # Live ATR must not replace the preceding completed reference.
    f.loc[5, 'atr'] = 100.
    result = evaluate_entry_contract(f, code='KC_LIVE_BODY_BREAKOUT_'+side, symbol=symbol)
    assert bool(result) is passes
    if result:
        assert result['breakout_min_body'] == .5
        assert result['breakout_body'] == pytest.approx(body)


@pytest.mark.parametrize('symbol', ['龙虾/USDT', 'CAP/USDT'])
@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_open_on_breakout_rail_is_allowed_but_open_outside_is_not(symbol, side):
    f = eligible_frame('live', side)
    sign = 1 if side == 'LONG' else -1
    edge = float(f.iloc[-1]['kc_upper' if sign == 1 else 'kc_lower'])
    f.loc[5, 'open'] = edge
    f.loc[5, 'close'] = edge+sign*.5
    f.loc[5, 'high'] = max(float(f.iloc[-1].high), float(f.iloc[-1].close))
    f.loc[5, 'low'] = min(float(f.iloc[-1].low), float(f.iloc[-1].close))
    code = 'KC_LIVE_BODY_BREAKOUT_'+side
    assert evaluate_entry_contract(f, code=code, symbol=symbol)
    f.loc[5, 'open'] = edge+sign*.000001
    assert evaluate_entry_contract(f, code=code, symbol=symbol) is None


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_cap_live_is_independent_and_cannot_create_pair_continuation_origin(side, monkeypatch):
    from core.services.cap_breakout_entry import STATE_KEY, observe_cap_breakout
    from core.services.entry_contract import evaluate_continuation_entry
    f = eligible_frame('live', side)
    a = SimpleNamespace(positions={}, position_meta={}, trades=[], last_closed_at={},
                        save_state=Mock(), log=Mock())
    monkeypatch.setattr('time.time', lambda: 421.)
    # Old cancelled pair authority cannot veto a fresh independent live breakout.
    a.position_meta[STATE_KEY] = {'CAP/USDT': dict(cancelled_second_ms=420000.)}
    observe_cap_breakout(a, 'CAP/USDT', f, float(f.iloc[-1].close), 421000.)
    decision = evaluate_entry_contract(f, account=a, symbol='CAP/USDT')
    assert decision and decision['type'] == 'KC_LIVE_BODY_BREAKOUT_'+side
    assert evaluate_continuation_entry(f, decision['price'], account=a, symbol='CAP/USDT') is None
    assert not a.position_meta[STATE_KEY]['CAP/USDT'].get('origin')
    f.attrs.update(entry_finality_verified=True, entry_finality_server_ms=421000.)
    a.entry_frame_provider = AsyncMock(return_value=f)
    context = dict(entry_signal_code=decision['type'], channel_confirmation_bar_id=420000.)
    asyncio.run(validate_account_entry(a, 'CAP/USDT', side, context))
    assert_commit_proof(a, 'CAP/USDT', side, context)
