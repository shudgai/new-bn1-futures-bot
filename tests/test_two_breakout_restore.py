"""Restored public entry authority, including retired-code and account checks."""
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pandas as pd
import pytest
from test_lobster_cap_gates import frame
from core.services.entry_contract import ENTRY_CODES, evaluate_entry_contract


def ordinary(side='LONG'):
    f=frame()
    f.loc[3,['open','close','high','low','ma5']]=[100.8,101.3,101.5,100.7,101.0]
    f.loc[4,['open','close','high','low','ma5']]=[101.1,101.5,101.6,101.0,101.2]
    f.loc[5,['open','close','high','low']]=[101.4,101.45,101.6,101.35]
    if side=='SHORT':
        for k in ('open','close','high','low','ma3','ma5','ma15','kc_upper','kc_middle','kc_lower'):
            f[k]=200.-f[k]
        f[['high','low']]=f[['low','high']].to_numpy()
        f[['kc_upper','kc_lower']]=f[['kc_lower','kc_upper']].to_numpy()
    return f


@pytest.mark.parametrize('side',['LONG','SHORT'])
@pytest.mark.parametrize('fault',['kc_opposite','ma_flat','prior_breakout','wick_retrace'])
def test_instant_does_not_wait_for_later_added_filters(side,fault):
    f=frame(side)
    if fault=='kc_opposite':f['kc_middle']=100.
    elif fault=='ma_flat':f['ma5']=100.
    elif fault=='prior_breakout':
        sign=1 if side=='LONG' else -1
        f.loc[4,['open','close','high','low']]=([100.8,101.5,101.6,100.7] if sign==1 else [99.2,98.5,99.3,98.4])
    elif fault=='wick_retrace':
        f.loc[5,'high' if side=='LONG' else 'low']=103. if side=='LONG' else 97.
    d=evaluate_entry_contract(f,symbol='CAP/USDT')
    assert d and d['type']=='KC_LIVE_BODY_BREAKOUT_'+side


@pytest.mark.parametrize('side',['LONG','SHORT'])
@pytest.mark.parametrize('fault',['gap','touch','short_body','invalid_atr'])
def test_instant_requires_original_inside_open_strict_break_and_half_atr(side,fault):
    f=frame(side);sign=1 if side=='LONG' else -1
    if fault=='gap':f.loc[5,'open']=100+sign*1.1
    elif fault=='touch':f.loc[5,'close']=100+sign
    elif fault=='short_body':f.loc[5,'close']=100+sign*1.2
    else:f.loc[4,'atr']=float('nan')
    assert evaluate_entry_contract(f,code='KC_LIVE_BODY_BREAKOUT_'+side) is None


@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_normal_two_closed_breakouts(side):
    f=ordinary(side)
    d=evaluate_entry_contract(f)
    assert d and d['type']=='KC_2BAR_CONFIRM_'+side
    assert d['breakout_bar_id']==240000.


@pytest.mark.parametrize('side',['LONG','SHORT'])
@pytest.mark.parametrize('fault',['outside_first_open','opposite_second','small_body','quote_inside','ma3_opposite'])
def test_normal_confirmation_is_not_outside_chasing(side,fault):
    f=ordinary(side);sign=1 if side=='LONG' else -1
    if fault=='outside_first_open':f.loc[3,'open']=100+sign*1.1
    elif fault=='opposite_second':f.loc[4,'open']=100+sign*1.55
    elif fault=='small_body':
        f.loc[4,'high']=103.;f.loc[4,'low']=97.
    elif fault=='quote_inside':f.loc[5,'close']=100.
    else:f.loc[2,'close']=102. if side=='LONG' else 98.;f.loc[2,'high']=103.;f.loc[2,'low']=97.
    assert evaluate_entry_contract(f,code='KC_2BAR_CONFIRM_'+side) is None


@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_normal_third_bar_may_be_small_opposite_color(side):
    f=ordinary(side);f.loc[5,'open']=101.6 if side=='LONG' else 98.4
    assert evaluate_entry_contract(f)['type']=='KC_2BAR_CONFIRM_'+side


@pytest.mark.parametrize('code',['KC_MA_CROSS_TREND_LONG','KC_CLOSED_MA_CROSS_LONG',
    'KC_OUTSIDE_LONG','KC_MA3_TURN_REVERSE_LONG','KC_CHANNEL_LIVE_LONG_BODY_LONG',
    'KC_CONFIRMED_PULLBACK_RESUME_LONG'])
def test_retired_codes_fail_closed_even_when_a_valid_breakout_exists(code):
    assert code not in ENTRY_CODES
    diag={}
    assert evaluate_entry_contract(frame(),code=code,diagnostics=diag) is None
    assert diag['reason']=='BLOCKED_OBSOLETE_ENTRY_SIGNAL'


def test_recorded_cap_1326_ma_only_entry_is_rejected():
    e=json.loads((Path(__file__).parent/'fixtures/cap_132633_20261007.json').read_text())
    rows=[dict(r,is_closed=True) for r in e['candles']]+[e['live_candle']]
    assert evaluate_entry_contract(pd.DataFrame(rows)) is None


@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_final_account_boundary_rejects_quote_retracted_inside(side,monkeypatch):
    from core.services.entry_firewall import validate_account_entry
    f=frame(side);monkeypatch.setattr('time.time',lambda:361.)
    f.attrs.update(entry_finality_verified=True,entry_finality_server_ms=361000.)
    a=SimpleNamespace(positions={},trades=[],last_closed_at={},position_meta={},
        save_state=Mock(),log=Mock(),entry_frame_provider=AsyncMock(return_value=f))
    c=dict(entry_signal_code='KC_LIVE_BODY_BREAKOUT_'+side,channel_confirmation_bar_id=360000.)
    assert asyncio.run(validate_account_entry(a,'CAP/USDT',side,c))
    f.loc[5,'close']=100.
    with pytest.raises(ValueError,match='FORBIDDEN_ENTRY'):
        asyncio.run(validate_account_entry(a,'CAP/USDT',side,c))


def test_normal_close_waits_next_bar_then_allows_confirmed_entry():
    a=SimpleNamespace(positions={},trades=[dict(symbol='CAP/USDT',action='CLOSE_SHORT',id=360010.)],last_closed_at={})
    assert evaluate_entry_contract(ordinary(),account=a,symbol='CAP/USDT') is None
    f=ordinary();f['timestamp']+=60000.
    assert evaluate_entry_contract(f,account=a,symbol='CAP/USDT')['type']=='KC_2BAR_CONFIRM_LONG'
