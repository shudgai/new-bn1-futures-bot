"""Every independent automatic authority rejects inverse live MA alignment."""
import asyncio
import copy
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from core.services.entry_contract import evaluate_entry_contract, live_ma_alignment_evidence
from core.services.entry_firewall import validate_account_entry
from core.services.ma5_outer_pivot_entry import PHASE
from test_lobster_cap_gates import frame
from test_two_breakout_restore import ordinary
from test_ma5_outer_pivot_entry import pivot_frame


@pytest.mark.parametrize('symbol', ['龙虾/USDT', 'CAP/USDT'])
@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
@pytest.mark.parametrize('kind', ['live', 'pair', 'pivot'])
@pytest.mark.parametrize('alignment', ['favorable', 'touch', 'inverse', 'invalid'])
def test_all_authorities_require_live_alignment(symbol, side, kind, alignment):
    f = frame(side) if kind == 'live' else ordinary(side) if kind == 'pair' else pivot_frame(side)
    ma5 = (float(f.iloc[-5:-1].close.sum())+float(f.iloc[-1].close))/5.
    sign = 1 if side == 'LONG' else -1
    f.loc[f.index[-1], 'ma15'] = (ma5-sign*.1 if alignment == 'favorable' else ma5
                                if alignment == 'touch' else ma5+sign*.1
                                if alignment == 'inverse' else float('nan'))
    code = ('KC_LIVE_BODY_BREAKOUT_' if kind == 'live' else 'KC_2BAR_CONFIRM_'
            if kind == 'pair' else PHASE+'_')+side
    diagnostics = {}
    result = evaluate_entry_contract(f, symbol=symbol, code=code, diagnostics=diagnostics)
    if alignment == 'favorable':
        assert result and result['type'] == code
        assert sign*(result['entry_live_ma5']-result['entry_live_ma15'])>0
    else:
        assert result is None
        assert diagnostics['reason'] == 'BLOCKED_LIVE_MA5_MA15_ALIGNMENT'


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_ma15_and_ma5_use_same_new_quote(side):
    f = frame(side)
    old = float(f.iloc[-1].close)
    quote = old+(1 if side == 'LONG' else -1)*.02
    result = live_ma_alignment_evidence(f, quote, side)
    assert result
    assert result['entry_live_ma15'] == pytest.approx(float(f.iloc[-1].ma15)+(quote-old)/15.)
    assert result['entry_live_ma5'] == pytest.approx((float(f.iloc[-5:-1].close.sum())+quote)/5.)


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_alignment_uses_indicator_filtered_close_basis(side):
    f = frame(side)
    f['close_price_spike_filtered'] = f.close-.01
    quote = float(f.iloc[-1].close)
    result = live_ma_alignment_evidence(f, quote, side)
    assert result
    assert result['entry_live_ma5'] == pytest.approx(
        (float(f.iloc[-5:-1].close_price_spike_filtered.sum())+quote)/5.)
    assert result['entry_live_ma15'] == pytest.approx(float(f.iloc[-1].ma15)+.01/15.)


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
@pytest.mark.parametrize('invalid', ['missing_ma15', 'missing_close', 'short_history', 'bad_quote'])
def test_alignment_fails_closed_for_missing_inputs(side, invalid):
    f = frame(side)
    quote = float(f.iloc[-1].close)
    if invalid == 'missing_ma15':
        f = f.drop(columns=['ma15'])
    elif invalid == 'missing_close':
        f = f.drop(columns=['close'])
    elif invalid == 'short_history':
        f = f.iloc[-4:]
    else:
        quote = float('nan')
    assert live_ma_alignment_evidence(f, quote, side) is None


def test_cap_saved_indicator_evidence_is_not_a_downward_cross():
    f = frame('LONG')
    f.loc[f.index[-5:-1], 'close'] = [.08657, .08698, .08713, .08668]
    f.loc[f.index[-1], ['close', 'ma15']] = [.08685, .08655866666666666]
    result = live_ma_alignment_evidence(f, .08685, 'LONG')
    assert result
    assert result['entry_live_ma5'] == pytest.approx(.086842)
    assert result['entry_live_ma5'] > result['entry_live_ma15']


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_firewall_rejects_cached_permission_after_inverse_cross(side, monkeypatch):
    monkeypatch.setattr('time.time', lambda: 361.)
    f = frame(side)
    f.attrs.update(entry_finality_verified=True, entry_finality_server_ms=361000.)
    a = SimpleNamespace(positions={}, trades=[], position_meta={}, last_closed_at={},
                        log=Mock(), save_state=Mock(), entry_frame_provider=AsyncMock(return_value=f))
    context = dict(entry_signal_code='KC_LIVE_BODY_BREAKOUT_'+side, channel_confirmation_bar_id=360000.)
    asyncio.run(validate_account_entry(a, 'CAP/USDT', side, context))
    cached = copy.deepcopy(context)
    ma5 = (float(f.iloc[-5:-1].close.sum())+float(f.iloc[-1].close))/5.
    f.loc[5, 'ma15'] = ma5+(1 if side == 'LONG' else -1)*.01
    with pytest.raises(ValueError, match='BLOCKED_LIVE_MA5_MA15_ALIGNMENT'):
        asyncio.run(validate_account_entry(a, 'CAP/USDT', side, cached))
