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
def test_touch_is_not_break_and_live_candle_cannot_exit(side):
    f,p=case(side);p['ma3_trend_hold']=True
    f.loc[5,'close']=f.loc[5,'ma15']
    assert DualTrackExitStrategy().evaluate_exit(p,f) is None
    live=f.iloc[-1].copy();live['timestamp']+=60000;live['is_closed']=False
    live['close']=91. if side=='LONG' else 109.
    f=pd.concat([f,pd.DataFrame([live])],ignore_index=True)
    assert DualTrackExitStrategy().evaluate_exit(p,f) is None






def test_fresh_process_imports():
    result=subprocess.run([sys.executable,'-c',
        'from core.services.exits.profit_protection_service import assess_market_regime; import core.services.entry_firewall; import core.services.strategies.unified_entry_strategy; import core.engine'],capture_output=True,text=True)
    assert result.returncode==0,result.stderr


