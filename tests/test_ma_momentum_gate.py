import time
from types import SimpleNamespace
import pytest
from core.services.entry_contract import evaluate_entry_contract, ma_momentum_reason
from core.services.post_profit_lock_gate import profit_exit_fields, post_profit_lock_reason
from test_strict_entry_gates_live import frame_for
from test_impulse_reverse_integration import impulse_frame

@pytest.mark.parametrize('side', ['LONG','SHORT'])
@pytest.mark.parametrize('entry', ['second_third','impulse'])
def test_all_relaxed_entries_reject_opposite_ma5(side, entry):
    f = frame_for(side) if entry == 'second_third' else impulse_frame(side)
    f.loc[f.index[-1], 'ma5'] = float(f.iloc[-2].ma5) + (-.01 if side == 'LONG' else .01)
    diagnostics = {}
    assert evaluate_entry_contract(f, symbol='CAP/USDT', diagnostics=diagnostics) is None
    assert diagnostics['reason'] == ('BLOCKED_BY_FALLING_MA5' if side == 'LONG' else 'BLOCKED_BY_RISING_MA5')

@pytest.mark.parametrize('side', ['LONG','SHORT'])
def test_latest_quote_rechecks_ma5(side):
    f = frame_for(side)
    f.loc[f.index[-1], 'ma5'] = float(f.iloc[-2].ma5)
    price = float(f.iloc[-1].close) + (-.1 if side == 'LONG' else .1)
    assert ma_momentum_reason(f, side, price)
    assert ma_momentum_reason(f, side, float(f.iloc[-1].close)) is None


def test_profitable_pivot_records_actual_net_even_without_cached_pnl():
    p = dict(side='LONG', peak_trailing_state=dict(peak_price=110))
    fields = profit_exit_fields(p, 'THREE_POINT_PIVOT', time.time()*1000, net_pnl=2.4)
    assert fields['last_profit_exit_peak_price'] == 110
    assert profit_exit_fields(p, 'THREE_POINT_PIVOT', time.time()*1000, net_pnl=-1) == {}


def test_legacy_profitable_pivot_cannot_silently_skip_gate():
    now=time.time()*1000
    a=SimpleNamespace(trades=[dict(id=now-120000, symbol='CAP/USDT', status='CLOSED',
                                 action='CLOSE_LONG', pnl=2.45, reason='THREE_POINT_PIVOT')])
    f=frame_for().iloc[:-1]
    assert post_profit_lock_reason(a,'CAP/USDT',f,'LONG',now)=='BLOCKED_BY_POST_PROFIT_COOLDOWN'
    assert post_profit_lock_reason(a,'CAP/USDT',f,'LONG',now+240000)=='BLOCKED_BY_POST_PROFIT_INVALID_STATE'
    assert post_profit_lock_reason(a,'CAP/USDT',f,'LONG',now+900000) is None

@pytest.mark.parametrize('side', ['LONG','SHORT'])
def test_final_account_boundary_rejects_ma_turn_after_candidate(side):
    import asyncio
    from unittest.mock import AsyncMock
    from core.services.entry_firewall import validate_account_entry
    f=frame_for(side)
    a=SimpleNamespace(positions={},trades=[],entry_frame_provider=AsyncMock(return_value=f))
    d=evaluate_entry_contract(f,account=a,symbol='CAP/USDT')
    snap=dict(d,symbol='CAP/USDT',signal_code=d['type'],signal_id=d['pending_signal_id'],
              candidate_bar_id=d['confirmation_bar_id'],closed_bar=d['confirmation_bar_id'])
    ctx=dict(entry_signal_code=d['type'],signal_id=d['pending_signal_id'],
             candidate_bar_id=d['confirmation_bar_id'],channel_confirmation_bar_id=d['confirmation_bar_id'],entry_snapshot=snap)
    f.loc[f.index[-1],'ma5']=float(f.iloc[-2].ma5)+(-.01 if side=='LONG' else .01)
    with pytest.raises(ValueError,match='BLOCKED_BY_(FALLING|RISING)_MA5'):
        asyncio.run(validate_account_entry(a,'CAP/USDT',side,ctx))
