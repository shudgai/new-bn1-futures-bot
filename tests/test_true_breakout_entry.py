"""Real breakout provenance and quote-derived MA5 gates, independent of pivots."""
import asyncio
import copy
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from core.services.entry_contract import evaluate_entry_contract
from core.services.entry_firewall import validate_account_entry
from test_lobster_cap_gates import frame
from test_two_breakout_restore import ordinary
from test_ma5_outer_pivot_entry import pivot_frame
from core.services.ma5_outer_pivot_entry import PHASE


@pytest.mark.parametrize('symbol', ['龙虾/USDT', 'CAP/USDT'])
@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
@pytest.mark.parametrize('kind', ['live', 'pair'])
@pytest.mark.parametrize('slope', ['aligned', 'flat', 'tiny', 'opposite', 'invalid'])
def test_breakout_requires_latest_quote_ma5_direction(symbol, side, kind, slope):
    f = frame(side) if kind == 'live' else ordinary(side)
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
    f = frame(side)
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
@pytest.mark.parametrize('kind', ['live', 'pair'])
def test_final_firewall_revokes_cached_breakout_when_live_ma5_turns(side, kind, monkeypatch):
    monkeypatch.setattr('time.time', lambda: 361.)
    f = frame(side) if kind == 'live' else ordinary(side)
    sign = 1 if side == 'LONG' else -1
    # A quote retreat remains outside KC but reverses quote-derived MA5.
    original = float(f.iloc[-1].close)
    current = (float(f.iloc[-5:-1].close.sum())+original)/5.
    f.loc[4, 'ma5'] = current-sign*.005
    f.attrs.update(entry_finality_verified=True, entry_finality_server_ms=361000.)
    a = SimpleNamespace(positions={}, trades=[], last_closed_at={}, position_meta={},
                        save_state=Mock(), log=Mock(), entry_frame_provider=AsyncMock(return_value=f))
    code = ('KC_LIVE_BODY_BREAKOUT_' if kind == 'live' else 'KC_2BAR_CONFIRM_')+side
    context = dict(entry_signal_code=code, channel_confirmation_bar_id=360000.)
    asyncio.run(validate_account_entry(a, 'CAP/USDT', side, context))
    cached = copy.deepcopy(context)
    f.loc[5, 'close'] = original-sign*.03
    assert f.iloc[-1].kc_lower < f.iloc[-1].kc_upper
    edge = float(f.iloc[-1]['kc_upper' if side == 'LONG' else 'kc_lower'])
    assert sign*(float(f.iloc[-1].close)-edge)>0
    with pytest.raises(ValueError, match='BLOCKED_LIVE_MA5_FLAT_OPPOSITE_OR_INVALID'):
        asyncio.run(validate_account_entry(a, 'CAP/USDT', side, cached))
