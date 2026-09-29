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


def test_thresholds_and_both_sides():
    _,p=setup()
    observe_breakeven(p,102.4,2.)
    assert p['swing_breakeven_armed'] and not p.get('swing_trailing_armed')
    observe_breakeven(p,103.6,2.)
    assert not p.get('swing_trailing_armed')
    observe_breakeven(p,103.61,2.)
    assert p['swing_trailing_armed']
    q=dict(side='SHORT',entry_price=100.,sl=110.)
    observe_breakeven(q,96.,2.)
    assert q.get('swing_trailing_armed')


def test_quote_touch_does_not_exit_and_close_uses_prior_lows():
    f,p=setup();s=DualTrackExitStrategy()
    assert s.evaluate_exit(p,f,current_price=104.) is None
    assert p['swing_trailing_line']==102.
    assert s.evaluate_exit(p,None,current_price=101.) is None
    f=append(f,102.9,102.8)
    assert s.evaluate_exit(p,f,current_price=104.)=='EXIT_TWO_BAR_LOW_TRAILING_CLOSED'
    assert p['swing_trailing_line']==103.
    assert s.evaluate_exit(copy.deepcopy(p),None,current_price=104.)=='EXIT_TWO_BAR_LOW_TRAILING_CLOSED'


def test_trailing_replaces_ma15_and_never_lowers():
    f,p=setup();s=DualTrackExitStrategy()
    assert s.evaluate_exit(p,f,current_price=104.) is None
    f=append(f,104.,101.,ma15=104.5)
    assert s.evaluate_exit(p,f,current_price=104.) is None
    assert p['swing_trailing_line']==103.
    f=append(f,103.,100.5)
    assert s.evaluate_exit(p,f,current_price=104.) is None
    assert p['swing_trailing_line']==103.
    f=append(f,102.9,102.)
    assert s.evaluate_exit(p,f,current_price=104.)=='EXIT_TWO_BAR_LOW_TRAILING_CLOSED'


def test_no_forming_bar_or_pre_entry_lows():
    f,p=setup();p['open_timestamp']=float(f.iloc[-1].timestamp)/1000
    s=DualTrackExitStrategy()
    assert s.evaluate_exit(p,f,current_price=104.) is None
    assert not p.get('swing_trailing_line')
    live=append(f,101.,100.);live.loc[len(live)-1,'is_closed']=False
    assert s.evaluate_exit(p,live,current_price=104.) is None
    assert not p.get('swing_trailing_line')
