"""Latest user instruction: structural/reversal exits without profit locks."""
import asyncio
import copy
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from core.services.exits.peak_trailing_exit import evaluate_peak_trailing, migrate_peak_state
from core.services.exits.structural_holding_exit import REASON, WATERFALL, HARD, REVERSAL_EXIT, RETIRED_CLOSE_REASONS


def position(side='LONG'):
    sign=1 if side=='LONG' else -1
    return dict(side=side,entry_mode='CHANNEL_SWING',entry_price=100.,qty=1.,
        margin=100.,leverage=1.,entry_atr=1.,open_timestamp=60.,initial_sl=100-sign*10.)


def snapshot(side='LONG'):
    return dict(quote_ms=181000.,live_bar_ms=180000.,closed_bar_ms=120000.,
                live_open=100.,atr=1.,reason=None)


@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_large_gain_then_twenty_percent_giveback_does_not_lock_or_close(side):
    p=position(side);sign=1 if side=='LONG' else -1
    assert evaluate_peak_trailing(p,100+sign*3,dict(quote_ms=61000.,reason='NO_DATA'),fee=0,slippage=0) is None
    s=snapshot(side);s['live_open']=100+sign*2.5
    assert evaluate_peak_trailing(p,100+sign*2.3,s,fee=0,slippage=0) is None
    state=p['peak_trailing_state']
    assert not state['armed']
    assert 'swing_atr_profit_lock' not in state and 'profit_stop_price' not in state


@pytest.mark.parametrize('side',['LONG','SHORT'])
@pytest.mark.parametrize('fault',['intrabar_only','inside_buffer','quote_reclaimed','stale'])
def test_structure_needs_closed_confirmation_and_buffer(side,fault):
    p=position(side);s=snapshot(side);sign=1 if side=='LONG' else -1
    s['swing_structure_'+side.lower()]=dict(side=side,level=100+sign*.2,closed_break_confirmed=True)
    q=100.
    if fault=='intrabar_only':s['swing_structure_'+side.lower()]['closed_break_confirmed']=False
    elif fault=='inside_buffer':q=100+sign*.15
    elif fault=='quote_reclaimed':q=100+sign*.3
    else:s['closed_bar_ms']=60000.
    assert evaluate_peak_trailing(p,q,s,fee=0,slippage=0) is None


@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_structure_break_exits_and_retries_without_new_entry_provider(side,monkeypatch):
    from core.paper_account import PaperAccount
    from core.services.exits import realtime_profit_exit as rt
    p=position(side);s=snapshot(side);sign=1 if side=='LONG' else -1
    s['swing_structure_'+side.lower()]=dict(side=side,level=100+sign*.2,closed_break_confirmed=True)
    monkeypatch.setattr(PaperAccount,'load_state',lambda self:None)
    monkeypatch.setattr(PaperAccount,'save_state',lambda self:None)
    monkeypatch.setattr(PaperAccount,'log',lambda *args:None)
    a=PaperAccount();a.positions={'CAP/USDT':p};a.trades=[]
    provider=AsyncMock(side_effect=AssertionError('a close must not wait for entry'))
    e=SimpleNamespace(account=a,is_running=True,_channel_exit_frames={},_entry_boundary_frame=provider)
    monkeypatch.setattr(rt.time,'time',lambda:181.)
    monkeypatch.setattr(rt,'cached_tick_indicators',lambda *args:(s,1.))
    assert asyncio.run(rt.enforce_realtime_profit_exit(e,'CAP/USDT',100.,181000.))
    assert 'CAP/USDT' not in a.positions
    assert a.trades[0]['reason']=='Channel Swing '+REASON
    provider.assert_not_awaited()


@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_today_ma5_only_exit_is_retired(side):
    p=position(side);s=snapshot(side);sign=1 if side=='LONG' else -1
    s['closed_trend_history']=[dict(timestamp=60000.,ma5=100.,close=100.,atr=1.),
        dict(timestamp=120000.,ma5=100-sign*.2,close=100-sign*.5,atr=1.)]
    assert evaluate_peak_trailing(p,100-sign*.4,s,fee=0,slippage=0) is None


@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_baseline_confirmed_postentry_pivot_reversal(side):
    p=position(side);sign=1 if side=='LONG' else -1
    assert evaluate_peak_trailing(p,100+sign*.7,dict(quote_ms=61000.,reason='NO_DATA'),fee=0,slippage=0) is None
    s=snapshot(side);s['early_swing_reversal']=dict(side='SHORT' if sign==1 else 'LONG',
        reversal_pivot_ms=90000.,reversal_confirmed_ms=120000.,reversal_edge=100+sign*.1)
    assert evaluate_peak_trailing(p,100.,s,fee=0,slippage=0)['reason']==REVERSAL_EXIT
    assert evaluate_peak_trailing(p,100+sign*.5,dict(quote_ms=182000.,reason='NO_DATA'),fee=0,slippage=0)['reason']==REVERSAL_EXIT


@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_waterfall_and_initial_risk_remain(side):
    p=position(side);s=snapshot(side);sign=1 if side=='LONG' else -1
    s['live_open']=100+sign*1.6
    assert evaluate_peak_trailing(p,100.,s,fee=0,slippage=0)['reason']==WATERFALL
    p=position(side);p['initial_sl']=100-sign*.3
    assert evaluate_peak_trailing(p,100-sign*.4,dict(quote_ms=61000.,reason='NO_DATA'),fee=0,slippage=0)['reason']==HARD


@pytest.mark.parametrize('old',sorted(RETIRED_CLOSE_REASONS))
def test_retired_pending_and_profit_lines_clear_both_persisted_copies(old):
    p=position();state=migrate_peak_state(p)
    state.update(pending=old,trigger=old,profit_stop_price=102.,armed=True,
        swing_atr_profit_lock={'armed':True},same_bar_profit_lock={'armed':True})
    meta=copy.deepcopy(p)
    migrate_peak_state(p,meta)
    for source in (p['peak_trailing_state'],meta['peak_trailing_state']):
        assert not source['armed']
        assert 'pending' not in source and 'profit_stop_price' not in source
        assert 'swing_atr_profit_lock' not in source
    from core.services.exits.swing_atr_profit_lock import prepare_close
    a=SimpleNamespace(positions={'CAP/USDT':p},position_meta={'CAP/USDT':meta},log=Mock())
    assert not prepare_close(a,'CAP/USDT',101.,'Channel Swing '+old)


def test_lobster_1332_giveback_holds_without_other_exit():
    p=dict(side='SHORT',entry_mode='CHANNEL_SWING',entry_price=.069913008,qty=991.29613156,
        entry_atr=.000749,initial_sl=.0726749,open_timestamp=1791351130.277,
        margin=13.86,leverage=5.,structure_risk_policy='structure_fixed_budget_v1',structure_risk_budget_usdt=2.81)
    assert evaluate_peak_trailing(p,.06841,dict(quote_ms=1791351138356.,reason='NO_DATA')) is None
    s=dict(quote_ms=1791351138786.,live_bar_ms=1791351120000.,closed_bar_ms=1791351060000.,
           live_open=.0701,atr=.000749,reason=None)
    assert evaluate_peak_trailing(p,.0687,s) is None


def test_exchange_sync_cancels_old_profit_order_and_never_posts_replacement():
    from core.services.exits.exchange_profit_stop import sync_exchange_profit_stop
    a=SimpleNamespace(positions={'CAP/USDT':dict(position(),moving_profit_order={'algo_id':'old'})},
        position_meta={},closing_lock=set(),save_state=Mock(),log=Mock(),
        exchange=SimpleNamespace(request=AsyncMock(return_value={})))
    asyncio.run(sync_exchange_profit_stop(a,'CAP/USDT',101.))
    a.exchange.request.assert_awaited_once_with('algoOrder','fapiPrivate','DELETE',{'algoId':'old'})
    assert 'moving_profit_order' not in a.positions['CAP/USDT']


def test_profit_display_is_disabled_even_before_migration():
    from core.services.exits.profit_lock_display import profit_lock_display
    p=position();p['peak_trailing_state']={'profit_stop_price':102.}
    assert profit_lock_display(p,0,0)=={'profit_lock_amount':None,'profit_lock_status':'已停用'}
