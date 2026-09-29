import asyncio
import copy
import subprocess
import sys
import pandas as pd
import pytest
from core.services.exits.dual_track_exit_service import DualTrackExitStrategy
from core.services.exits.profit_protection_service import assess_market_regime
from test_entry_e2e_20260928 import candles as entry_candles
from core.services.entry_firewall import validate_entry_frame
from core.services.strategies.unified_entry_strategy import evaluate_closed_entry


def case(side='LONG'):
    f=entry_candles()
    f['high']=110.;f['low']=90.;f['atr']=2.
    f.loc[3,['close','ma3','ma15','kc_upper','kc_middle','kc_lower']]=[106.,106.,102.,105.,101.,97.]
    f.loc[4,['close','ma3','ma15','kc_upper','kc_middle','kc_lower']]=[107.,107.,103.,106.,102.,98.]
    f.loc[5,['close','ma3','ma15','kc_upper','kc_middle','kc_lower']]=[104.,106.,103.5,102.9,102.5,99.]
    # Pullback may narrow the latest upper rail; prior trend still protects it.
    if side=='SHORT':
        original=f.copy()
        for a,b in [('open','open'),('close','close'),('high','low'),('low','high'),('ma3','ma3'),('ma15','ma15'),('kc_upper','kc_lower'),('kc_lower','kc_upper'),('kc_middle','kc_middle')]:f[a]=200-original[b]
    p=dict(side=side,entry_price=100.,open_timestamp=1.,sl=90. if side=='LONG' else 110.)
    return f,p


@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_small_pullback_holds_and_survives_restart(side):
    f,p=case(side);strategy=DualTrackExitStrategy()
    assert strategy.evaluate_exit(p,f) is None
    assert p['swing_breakeven_armed'] is True
    f.loc[4,'ma15']=f.loc[3,'ma15']  # recent trend no longer passes detector
    assert strategy.evaluate_exit(copy.deepcopy(p),f) is None


@pytest.mark.parametrize('side',['LONG','SHORT'])
@pytest.mark.parametrize('level',['ma15','kc_middle'])
def test_confirmed_break_allows_exit(side,level):
    f,p=case(side);p['ma3_trend_hold']=True;p['entry_atr']=4.
    sign=1 if side=='LONG' else -1
    f.loc[5,'close']=f.loc[5,level]-sign*.1
    assert DualTrackExitStrategy().evaluate_exit(p,f)=='EXIT_MA15_DEFENSE_CLOSED'


@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_touch_is_not_break_and_live_candle_cannot_exit(side):
    f,p=case(side);p['ma3_trend_hold']=True
    f.loc[5,'close']=f.loc[5,'ma15']
    assert DualTrackExitStrategy().evaluate_exit(p,f) is None
    live=f.iloc[-1].copy();live['timestamp']+=60000;live['is_closed']=False
    live['close']=91. if side=='LONG' else 109.
    f=pd.concat([f,pd.DataFrame([live])],ignore_index=True)
    assert DualTrackExitStrategy().evaluate_exit(p,f) is None


@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_hard_stop_still_exits(side):
    f,p=case(side);p['ma3_trend_hold']=True
    p['stop_loss']=105. if side=='LONG' else 95.
    assert DualTrackExitStrategy().evaluate_exit(p,f)=='EXIT_BREAKEVEN'


@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_firewall_has_no_missing_import_and_strong_signal_override(side):
    f=entry_candles(side)
    assert assess_market_regime(f,side) in ('UNKNOWN','CHOPPY','STACKED','SMOOTH')
    code=evaluate_closed_entry(f,side)[1]
    assert validate_entry_frame(f,side,code)['action']=='ENTER'


def test_fresh_process_imports():
    result=subprocess.run([sys.executable,'-c',
        'from core.services.exits.profit_protection_service import assess_market_regime; import core.services.entry_firewall; import core.services.strategies.unified_entry_strategy; import core.engine'],capture_output=True,text=True)
    assert result.returncode==0,result.stderr


@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_existing_position_after_two_pullbacks_recovers_trend_context(side):
    f,p=case(side)
    keys=['close','ma3','ma15','kc_upper','kc_middle','kc_lower']
    a,b=f.loc[3,keys].copy(),f.loc[4,keys].copy()
    f.loc[2,keys]=a;f.loc[3,keys]=b
    f.loc[4,keys]=f.loc[5,keys]
    assert DualTrackExitStrategy().evaluate_exit(p,f) is None
    assert p['swing_breakeven_armed'] is True
