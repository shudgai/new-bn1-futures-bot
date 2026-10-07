import copy
import pytest
from test_confirmed_pivot_exit import sample
from core.services.exits.peak_trailing_exit import evaluate_peak_trailing
from core.services.exits.structural_holding_exit import PIVOT_EXIT, REVERSAL_EXIT, WATERFALL, HARD
from core.services.exits.ma5_outer_pivot_exit import REASON as MA5_EXIT


def aligned(side):
    p,s,q,sign=sample(side)
    s.update(ma5=100+sign*1.3,last_ma5=100+sign*1.1,
        kc_closed_history=[dict(timestamp=180000.,middle=100+sign*.1),
                           dict(timestamp=240000.,middle=100+sign*.2)])
    s['swing_structure_'+side.lower()]=dict(side=side,intact=True,level=100.,closed_break_confirmed=False)
    for i, row in enumerate(s['ma5_pivot_history']):
        row['ma5'] = 100+sign*(1.1+i*.1)
    evaluate_peak_trailing(p,100+sign*2,dict(quote_ms=61000.,reason='NO_DATA'),fee=0,slippage=0)
    return p,s,q,sign


@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_local_price_pivot_closes_even_when_structure_kc_and_ma5_align(side):
    p,s,q,sign=aligned(side)
    assert evaluate_peak_trailing(p,q,s,fee=0,slippage=0)['reason'] == MA5_EXIT
    assert p['peak_trailing_state']['ma5_pivot_status'] == 'CONFIRMED'
    assert 'profit_stop_price' not in p['peak_trailing_state']


@pytest.mark.parametrize('side',['LONG','SHORT'])
@pytest.mark.parametrize('release',['ma5_reverse','ma5_flat','kc_reverse','kc_flat','structure_broken'])
def test_price_pivot_exit_is_independent_of_ma5_or_structure_direction(side,release):
    p,s,q,sign=aligned(side)
    if release=='ma5_reverse':s['ma5']=s['last_ma5']-sign*.1
    elif release=='ma5_flat':s['ma5']=s['last_ma5']
    elif release=='kc_reverse':s['kc_closed_history'][-1]['middle']=100.
    elif release=='kc_flat':s['kc_closed_history'][-1]['middle']=s['kc_closed_history'][0]['middle']
    else:s['swing_structure_'+side.lower()]['intact']=False
    assert evaluate_peak_trailing(p,q,s,fee=0,slippage=0)['reason'] == MA5_EXIT


@pytest.mark.parametrize('side',['LONG','SHORT'])
@pytest.mark.parametrize('old',[PIVOT_EXIT,REVERSAL_EXIT])
def test_pre_guard_soft_ticket_cannot_override_intact_trend(side,old):
    p,s,q,sign=aligned(side)
    for key in ('pivot_exit_history', 'ma5_pivot_history'):
        for row in s[key]:
            row['high'], row['low'] = 104., 96.
    p['peak_trailing_state'].update(pending=old,trigger=old)
    assert evaluate_peak_trailing(p,q,s,fee=0,slippage=0) is None
    assert 'pending' not in p['peak_trailing_state']


@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_verified_new_ticket_keeps_retry_after_later_rebound(side):
    p,s,q,sign=aligned(side)
    weaker=copy.deepcopy(s)
    for row, value in zip(weaker['ma5_pivot_history'], (1.1, 1.8, 1.4)):
        row['ma5'] = 100+sign*value
    assert evaluate_peak_trailing(p,q,weaker,fee=0,slippage=0)['reason']==MA5_EXIT
    s['quote_ms']+=1000.
    assert evaluate_peak_trailing(p,q,s,fee=0,slippage=0)['reason']==MA5_EXIT


@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_pullback_guard_keeps_waterfall_but_initial_stop_is_retired(side):
    p,s,q,sign=aligned(side);s['live_open']=q+sign*1.6
    assert evaluate_peak_trailing(p,q,s,fee=0,slippage=0)['reason']==WATERFALL
    p,s,q,sign=aligned(side);p['initial_sl']=100+sign*1.5
    for key in ('pivot_exit_history', 'ma5_pivot_history'):
        for row in s[key]:
            row['high'], row['low'] = 104., 96.
    p['entry_price']=100+sign*2.;p['initial_sl']=100+sign*1.5
    assert evaluate_peak_trailing(p,q,s,fee=0,slippage=0) is None
    p['margin'] = 1.
    assert evaluate_peak_trailing(p,q,s,fee=0,slippage=0)['reason']==HARD


def test_recorded_cap_1401_early_exit_now_holds():
    p=dict(side='LONG',entry_mode='CHANNEL_SWING',entry_price=.086398639,qty=1.,margin=1.,
        leverage=1.,entry_atr=.000308,initial_sl=.0857792,open_timestamp=1791352668.)
    evaluate_peak_trailing(p,.08687,dict(quote_ms=1791352799000.,reason='NO_DATA'))
    s=dict(quote_ms=1791352865420.,live_bar_ms=1791352860000.,closed_bar_ms=1791352800000.,
        live_open=.08654,atr=.00025,ma5=.086544,last_ma5=.086514,reason=None,
        kc_closed_history=[dict(timestamp=1791352740000.,middle=.08621587684898951),
                           dict(timestamp=1791352800000.,middle=.08624865048241909)],
        swing_structure_long=dict(side='LONG',intact=True,level=.08581,closed_break_confirmed=False),
        early_swing_reversal=dict(side='SHORT',reversal_pivot_ms=1791352740000.,
            reversal_confirmed_ms=1791352800000.,reversal_edge=.08646))
    assert evaluate_peak_trailing(p,.08636,s) is None
    assert p['peak_trailing_state']['ma5_pivot_status'] == 'BLOCKED_MA5_HISTORY'
