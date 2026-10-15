import copy
import pandas as pd
from core.services.exits.dual_track_exit_service import DualTrackExitStrategy, observe_breakeven
from test_emergency_ma3_trend_hold import case


def setup():
    f,p=case('LONG')
    p.update(entry_atr=2.)
    f.loc[3:5,'low']=[102.,103.,103.5]
    f.loc[3:5,'open']=[104.,105.,104.]
    return f,p


def append(f, close, low, ma15=103.):
    row=f.iloc[-1].copy();row['timestamp']+=60000
    row['open']=close+.1;row['close']=close;row['low']=low
    row['high']=max(close+.2,105.);row['ma15']=ma15
    return pd.concat([f,pd.DataFrame([row])],ignore_index=True)








