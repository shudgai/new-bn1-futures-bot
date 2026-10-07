import pytest
from core.services.exits.peak_trailing_exit import evaluate_peak_trailing
from core.services.exits.structural_holding_exit import REASON,HARD
from core.services.exits.ma5_outer_pivot_exit import REASON as MA5_REASON
from test_ma5_outer_pivot_exit import sample

@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_structure_break_holds_until_closed_outer_ma5_turn(side):
    sign=1 if side=='LONG' else -1
    p=dict(side=side,entry_price=100.,qty=.2,entry_atr=1.,open_timestamp=60.,entry_mode='CHANNEL_SWING')
    s=dict(quote_ms=121000,reason=None,closed_bar_ms=60000,live_bar_ms=120000,live_open=100+sign*.7,atr=1.,
           ma5=100+sign*.4,last_ma5=100+sign*.3,ma15=100,kc_middle=100)
    s['swing_structure_'+side.lower()]=dict(side=side,level=100+sign*.2,closed_break_confirmed=True)
    assert evaluate_peak_trailing(p,100+sign*.8,s,fee=0,slippage=0) is None
    assert evaluate_peak_trailing(p,100+sign*.3,s,fee=0,slippage=0) is None
    assert evaluate_peak_trailing(p,100.,s,fee=0,slippage=0) is None
    _, confirmed, _ = sample(side)
    d=evaluate_peak_trailing(p,100.,confirmed,fee=0,slippage=0)
    assert d and d['reason']==MA5_REASON
    assert evaluate_peak_trailing(p,100+sign*.5,dict(quote_ms=302000,reason='NO_DATA'),fee=0,slippage=0)['reason']==MA5_REASON

@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_hard_stop_has_priority(side):
    sign=1 if side=='LONG' else -1
    p=dict(side=side,entry_price=100.,qty=2.,entry_atr=1.,open_timestamp=60.,entry_mode='CHANNEL_SWING')
    p['initial_sl'] = 100-sign*1.5
    p['margin'] = 10.
    assert evaluate_peak_trailing(p,100-sign*1.6,dict(quote_ms=121000,reason='NO_DATA'),fee=0,slippage=0)['reason']==HARD

@pytest.mark.parametrize('old',['EXIT_TERMINAL_DOJI_PRESSURE','EXIT_EARLY_PROFIT_REVERSAL','EXIT_NO_PROFIT_ADVERSE_PRESSURE','EXIT_ADVERSE_ABNORMAL_BODY'])
def test_old_strategy_pending_cannot_force_close(old):
    p=dict(side='LONG',entry_price=100.,qty=2.,entry_atr=1.,open_timestamp=60.,entry_mode='CHANNEL_SWING')
    evaluate_peak_trailing(p,100.5,dict(quote_ms=61000,reason='NO_DATA'),fee=0,slippage=0)
    p['peak_trailing_state'].update(pending=old,trigger=old)
    assert evaluate_peak_trailing(p,100.4,dict(quote_ms=62000,reason='NO_DATA'),fee=0,slippage=0) is None

@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_structural_exit_reaches_account_service(side,monkeypatch):
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import Mock,AsyncMock
    from core.services.exits import realtime_profit_exit as rt
    sign=1 if side=='LONG' else -1
    p=dict(side=side,entry_price=100.,qty=2.,entry_atr=1.,open_timestamp=60.,entry_mode='CHANNEL_SWING')
    _, s, _ = sample(side)
    a=SimpleNamespace(positions={'X':p},position_meta={},save_state=Mock(),log=Mock(),close_position=AsyncMock(return_value=True))
    e=SimpleNamespace(account=a,is_running=True,_channel_exit_frames={})
    monkeypatch.setattr(rt.time,'time',lambda:301.)
    monkeypatch.setattr(rt,'cached_tick_indicators',lambda *args:(s,1.))
    monkeypatch.setattr(rt,'enforce_hard_stop',AsyncMock(return_value=False))
    assert asyncio.run(rt.enforce_realtime_profit_exit(e,'X',100.,301000.))
    assert MA5_REASON in a.close_position.await_args.args[2]

@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_waterfall_risk_remains_effective(side):
    sign=1 if side=='LONG' else -1
    p=dict(side=side,entry_price=100.,qty=2.,entry_atr=1.,open_timestamp=60.,entry_mode='CHANNEL_SWING')
    s=dict(quote_ms=121000,reason=None,closed_bar_ms=60000,live_bar_ms=120000,live_open=100+sign*2.,atr=1.)
    d=evaluate_peak_trailing(p,100.,s,fee=0,slippage=0)
    assert d and d['trigger']=='WATERFALL_DROP'








@pytest.mark.parametrize('side',['LONG','SHORT'])
@pytest.mark.parametrize('old',['EXIT_FAILED_BREAKOUT_RECLAIM','EXIT_CLOSED_MA5_MA15_REVERSE_CROSS',REASON])
def test_retired_exits_and_kc_reverse_hold_without_pivot_break(side,old):
    sign=1 if side=='LONG' else -1
    p=dict(side=side,entry_price=100.,qty=.2,entry_atr=1.,open_timestamp=60.,entry_mode='CHANNEL_SWING',entry_failure_level=100+sign*.5)
    s=dict(quote_ms=181000,reason=None,closed_bar_ms=120000,live_bar_ms=180000,live_open=100.,atr=1.,ma5=100.,last_ma5=100+sign*.2,
           kc_closed_history=[dict(timestamp=t,middle=100-sign*i*.2) for i,t in enumerate((0,60000,120000))],
           closed_ma_cross=dict(timestamp=120000,direction='SHORT' if sign==1 else 'LONG'))
    evaluate_peak_trailing(p,100+sign*.2,s,fee=0,slippage=0)
    p['peak_trailing_state'].update(pending=old,trigger=old,holding_exit_policy='confirmed_structure_v1')
    assert evaluate_peak_trailing(p,100-sign*.2,s,fee=0,slippage=0) is None

@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_live_excursion_without_closed_pivot_break_holds(side):
    sign=1 if side=='LONG' else -1
    p=dict(side=side,entry_price=100.,qty=.2,entry_atr=1.,open_timestamp=60.,entry_mode='CHANNEL_SWING')
    s=dict(quote_ms=121000,reason=None,closed_bar_ms=60000,live_bar_ms=120000,live_open=100.,atr=1.)
    s['swing_structure_'+side.lower()]=dict(side=side,level=100+sign*.2,closed_break_confirmed=False)
    assert evaluate_peak_trailing(p,100.,s,fee=0,slippage=0) is None
