import copy
import subprocess
import sys
import pytest
from test_emergency_ma3_trend_hold import case
from core.services.exits.dual_track_exit_service import DualTrackExitStrategy, observe_breakeven

@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_ma3_turn_holds_without_strong_trend_flag(side):
    f,p=case(side)
    assert DualTrackExitStrategy().evaluate_exit(p,f) is None
    assert p['swing_breakeven_armed']
    assert p['stop_loss']==100.

@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_ma15_break_exits_even_when_ma3_slope_favorable(side):
    f,p=case(side);p['entry_atr']=4.;sign=1 if side=='LONG' else -1
    f.loc[5,'close']=f.loc[5,'ma15']-sign*.1
    f.loc[5,'ma3']=f.loc[4,'ma3']+sign
    assert DualTrackExitStrategy().evaluate_exit(p,f)=='EXIT_MA15_DEFENSE_CLOSED'

@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_cross_holds_when_close_still_above_support(side):
    f,p=case(side);sign=1 if side=='LONG' else -1
    f.loc[5,'ma3']=f.loc[5,'ma15']-sign*.1
    assert DualTrackExitStrategy().evaluate_exit(p,f) is None
    _,p=case(side)  # Independent no-cross case, not a pending close retry.
    f.loc[4,'ma3']=f.loc[4,'ma15']-sign*.1
    assert DualTrackExitStrategy().evaluate_exit(p,f) is None

@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_touch_ma15_or_ma3_equality_is_not_break(side):
    f,p=case(side)
    f.loc[5,'close']=f.loc[5,'ma15']
    f.loc[5,'ma3']=f.loc[5,'ma15']
    assert DualTrackExitStrategy().evaluate_exit(p,f) is None

@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_quote_peak_persists_and_breakeven_only_at_entry(side):
    f,p=case(side);sign=1 if side=='LONG' else -1
    observe_breakeven(p,100+sign*2.4,2.)
    assert p['stop_loss']==100.
    restored=copy.deepcopy(p)
    assert DualTrackExitStrategy().evaluate_exit(restored,f,current_price=100+sign*.01) is None
    assert DualTrackExitStrategy().evaluate_exit(restored,f,current_price=100.)=='EXIT_BREAKEVEN'

@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_no_pre_entry_wick_inferred_or_early_arming(side):
    f,p=case(side);sign=1 if side=='LONG' else -1
    observe_breakeven(p,100+sign*2.399,2.)
    assert not p.get('swing_breakeven_armed')
    p['channel_confirmation_bar_id']=float(f.iloc[-1].timestamp)
    DualTrackExitStrategy().evaluate_exit(p,f,current_price=100.)
    assert not p.get('swing_breakeven_armed')


def test_import_is_available_in_new_process():
    r=subprocess.run([sys.executable,'-c','from core.services.exits.profit_protection_service import assess_market_regime; import core.services.entry_firewall; import core.engine'],capture_output=True,text=True)
    assert r.returncode==0,r.stderr


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_quote_only_adapter_arms_and_exits_on_entry_candle(side):
    from core.services.exits.entry_atr_protection import atr_exit_reason
    f, p = case(side)
    sign = 1 if side == 'LONG' else -1
    p.update(entry_atr=2., channel_confirmation_bar_id=float(f.iloc[-1].timestamp))
    assert atr_exit_reason(p, 100+sign*2.4, f) is None
    assert p['swing_breakeven_armed']
    assert atr_exit_reason(p, 100., f) == 'EXIT_BREAKEVEN'
    assert atr_exit_reason(copy.deepcopy(p), 100+sign, None) == 'EXIT_BREAKEVEN'


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_quote_only_account_persists_and_retries_after_reload(side):
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, Mock
    from core.services.exits.entry_atr_protection import enforce_atr_protection
    _, p = case(side)
    p['entry_atr'] = 2.
    sign = 1 if side == 'LONG' else -1
    account = SimpleNamespace(positions={'TEST': p}, position_meta={},
                              save_state=Mock(), close_position=AsyncMock(return_value=False))
    assert not asyncio.run(enforce_atr_protection(account, 'TEST', 100+sign*2.4))
    assert account.position_meta['TEST']['swing_breakeven_armed']
    account.positions['TEST'] = dict(side=side, entry_price=100., open_timestamp=1.)
    assert asyncio.run(enforce_atr_protection(account, 'TEST', 100.))
    assert account.position_meta['TEST']['closed_exit_state']['pending']
    assert asyncio.run(enforce_atr_protection(account, 'TEST', 100+sign))
    assert account.close_position.await_count == 2


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_invalid_quote_does_not_arm_or_exit(side):
    from core.services.exits.entry_atr_protection import atr_exit_reason
    _, p = case(side)
    p['entry_atr'] = 2.
    for quote in (float('nan'), float('inf'), -1., 0.):
        assert atr_exit_reason(p, quote) is None
        assert not p.get('swing_breakeven_armed')


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_live_candle_cannot_trigger_structure_exit(side):
    import pandas as pd
    f, p = case(side)
    sign = 1 if side == 'LONG' else -1
    live = f.iloc[-1].copy()
    live['timestamp'] += 60000
    live['is_closed'] = False
    live['close'] = 100-sign*5
    live['ma3'] = 100-sign*5
    frame = pd.concat([f, pd.DataFrame([live])], ignore_index=True)
    assert DualTrackExitStrategy().evaluate_exit(p, frame, current_price=100+sign) is None


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_removed_cross_pending_is_retired_but_ma15_still_exits(side):
    from core.services.exits.dual_track_exit_service import POLICY
    f,p=case(side)
    p['entry_atr']=4.  # Exercise the initial MA15 stage, before trailing.
    p['closed_exit_state']=dict(policy=POLICY,pending=True,reason='EXIT_MA3_MA15_CROSS_CLOSED')
    assert DualTrackExitStrategy().evaluate_exit(p,f) is None
    assert p['closed_exit_state']['pending'] is False
    sign=1 if side=='LONG' else -1
    f.loc[5,'close']=f.loc[5,'ma15']-sign*.1
    assert DualTrackExitStrategy().evaluate_exit(p,f)=='EXIT_MA15_DEFENSE_CLOSED'


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_valid_stop_pending_survives_structure_change(side):
    from core.services.exits.dual_track_exit_service import POLICY
    f,p=case(side)
    p['closed_exit_state']=dict(policy=POLICY,pending=True,reason='EXIT_BREAKEVEN')
    assert DualTrackExitStrategy().evaluate_exit(p,f)=='EXIT_BREAKEVEN'
