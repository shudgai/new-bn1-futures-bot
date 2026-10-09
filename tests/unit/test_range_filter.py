import pytest
import math
from core.services.strategies.entry_v2_strategy import ClosedBar1m, _is_range, EntryV2Strategy, SEARCH, VETO_WAIT, WAIT_FRESH_CONFIRM
from core.services.context_5m import LONG, SHORT, Closed5mContext

def create_bar(sep, slope, atr=1.0, ma15_base=100.0, side=LONG):
    # sep = abs(ma5 - ma15) / atr -> abs(ma5 - ma15) = sep * atr
    # slope = abs(ma15 - ma15_prev5) / atr -> abs(ma15 - ma15_prev5) = slope * atr
    ma15 = ma15_base
    ma15_prev5 = ma15 - slope * atr
    ma5 = ma15 + sep * atr if side == LONG else ma15 - sep * atr
    return ClosedBar1m(
        bar_id=1000, open=100, high=105, low=95, close=100,
        ma5=ma5, ma15=ma15, prev_ma5=ma5, prev_ma15=ma15, kc_mid=100,
        atr14=atr, ma15_prev5=ma15_prev5
    )

def test_3_small_sep_strong_slope():
    # sep < 0.30, slope >= 0.20 => NOT range
    bar = create_bar(sep=0.29, slope=0.20, atr=1.0)
    assert _is_range(bar) is False

def test_4_flat_ma15_large_sep():
    # sep >= 0.30, slope < 0.20 => NOT range
    bar = create_bar(sep=0.30, slope=0.19, atr=1.0)
    assert _is_range(bar) is False

def test_5_both_below_thresholds():
    # sep < 0.30, slope < 0.20 => range
    bar = create_bar(sep=0.29, slope=0.19, atr=1.0)
    assert _is_range(bar) is True

def test_6_exactly_030_sep():
    bar = create_bar(sep=0.30, slope=0.10, atr=1.0)
    assert _is_range(bar) is False
    
def test_7_exactly_020_slope():
    bar = create_bar(sep=0.10, slope=0.20, atr=1.0)
    assert _is_range(bar) is False

def test_10_long_short_symmetry():
    bar_long = create_bar(sep=0.29, slope=0.19, side=LONG)
    bar_short = create_bar(sep=0.29, slope=0.19, side=SHORT)
    assert _is_range(bar_long) is True
    assert _is_range(bar_short) is True

def test_12_insufficient_history():
    bar = create_bar(sep=0.1, slope=0.1, atr=float('nan'))
    assert _is_range(bar) is True
    bar2 = create_bar(sep=0.1, slope=0.1, atr=0.0)
    assert _is_range(bar2) is True
    bar3 = ClosedBar1m(1000, 100, 105, 95, 100, 100, 100, 100, 100, 100, 1.0, float('nan'))
    assert _is_range(bar3) is True

def test_integration_range_blocks_candidate():
    strat = EntryV2Strategy()
    strat._symbols["LOBSTER"] = strat._symbols.get("LOBSTER") or __import__("core.services.strategies.entry_v2_strategy", fromlist=["_SymbolState"])._SymbolState(startup_watermark_bar_id=1, last_processed_bar_id=1)
    st = strat._symbols["LOBSTER"]
    
    # 1. cross
    bar1 = ClosedBar1m(bar_id=60000, open=10, high=10, low=10, close=10,
                       ma5=101, ma15=100, prev_ma5=99, prev_ma15=100, kc_mid=100,
                       atr14=1.0, ma15_prev5=99)
    strat.on_closed_bar("LOBSTER", bar1, None)
    assert st.formation is not None
    assert st.formation.side == LONG
    
    # 2. pullback
    bar2 = ClosedBar1m(bar_id=120000, open=10, high=105, low=95, close=102,
                       ma5=102, ma15=100, prev_ma5=101, prev_ma15=100, kc_mid=100,
                       atr14=1.0, ma15_prev5=99)
    strat.on_closed_bar("LOBSTER", bar2, None)
    assert st.formation.pullback_bar_id == 120000
    
    # 3. resume (RANGE) -> blocked!
    # sep = 0.20, slope = 0.10
    bar3 = ClosedBar1m(bar_id=180000, open=10, high=110, low=100, close=106, # close > pullback_high (105)
                       ma5=100.2, ma15=100.0, prev_ma5=102, prev_ma15=100, kc_mid=100,
                       atr14=1.0, ma15_prev5=99.9)
    cand = strat.on_closed_bar("LOBSTER", bar3, None)
    assert cand is None
    assert st.state == SEARCH
    assert st.formation.pullback_bar_id is None # it cleared the confirmation!
    assert strat.counters.get("RANGE_BLOCKED", 0) == 1

def test_integration_recovery_range_discard():
    # 8. recovery fresh confirmation while range => NO ENTRY
    # 9. after range ends, stale confirmation may NOT be reused
    strat = EntryV2Strategy()
    strat._symbols["LOBSTER"] = strat._symbols.get("LOBSTER") or __import__("core.services.strategies.entry_v2_strategy", fromlist=["_SymbolState"])._SymbolState(startup_watermark_bar_id=1, last_processed_bar_id=1)
    st = strat._symbols["LOBSTER"]
    
    # 1. cross
    bar1 = ClosedBar1m(bar_id=60000, open=10, high=10, low=10, close=10,
                       ma5=101, ma15=100, prev_ma5=99, prev_ma15=100, kc_mid=100,
                       atr14=1.0, ma15_prev5=90) # slope = 10
    strat.on_closed_bar("LOBSTER", bar1, None)
    
    # 2. pullback
    bar2 = ClosedBar1m(bar_id=120000, open=10, high=105, low=95, close=102,
                       ma5=102, ma15=100, prev_ma5=101, prev_ma15=100, kc_mid=100,
                       atr14=1.0, ma15_prev5=90)
    strat.on_closed_bar("LOBSTER", bar2, None)
    
    # 3. resume (NOT RANGE) -> veto blocked
    bar3 = ClosedBar1m(bar_id=180000, open=10, high=110, low=100, close=106,
                       ma5=102, ma15=100, prev_ma5=102, prev_ma15=100, kc_mid=100,
                       atr14=1.0, ma15_prev5=90)
    ctx = Closed5mContext(decision_timestamp=180000, open_timestamp=0, close_timestamp=170000, open=100, high=100, low=100, close=100, bar_count=5,
                          ma5=90, ma15=100, prev_open_timestamp=0, prev_close_timestamp=140000, prev_ma5=95) # opposite slope
    cand = strat.on_closed_bar("LOBSTER", bar3, ctx)
    assert cand is None
    assert st.state == VETO_WAIT
    
    # 4. Veto release
    bar4 = ClosedBar1m(bar_id=240000, open=100, high=100, low=100, close=100,
                       ma5=102, ma15=100, prev_ma5=102, prev_ma15=100, kc_mid=100,
                       atr14=1.0, ma15_prev5=90)
    ctx_good = Closed5mContext(decision_timestamp=240000, open_timestamp=0, close_timestamp=230000, open=100, high=100, low=100, close=100, bar_count=5,
                               ma5=101, ma15=100, prev_open_timestamp=0, prev_close_timestamp=200000, prev_ma5=99) # same slope
    strat.on_closed_bar("LOBSTER", bar4, ctx_good)
    assert st.state == WAIT_FRESH_CONFIRM
    
    # 5. Fresh confirmation WHILE RANGE
    bar5 = ClosedBar1m(bar_id=300000, open=10, high=120, low=100, close=110, # close > pullback_high (105)
                       ma5=100.2, ma15=100.0, prev_ma5=102, prev_ma15=100, kc_mid=100,
                       atr14=1.0, ma15_prev5=99.9) # range!
    strat.on_closed_bar("LOBSTER", bar5, ctx_good)
    assert st.state == VETO_WAIT # fallback to VETO_WAIT
    assert strat.counters.get("RANGE_BLOCKED", 0) == 1
    
    # 6. Next bar: NOT range anymore, but it doesn't automatically resume!
    bar6 = ClosedBar1m(bar_id=360000, open=10, high=130, low=100, close=110,
                       ma5=102, ma15=100, prev_ma5=102, prev_ma15=100, kc_mid=100,
                       atr14=1.0, ma15_prev5=90)
    cand2 = strat.on_closed_bar("LOBSTER", bar6, ctx_good)
    assert cand2 is None
    # Still VETO_WAIT because bar6 released veto, now it's WAIT_FRESH_CONFIRM!
    assert st.state == WAIT_FRESH_CONFIRM
    
    # 7. Finally a fresh confirmation after range ends
    bar7 = ClosedBar1m(bar_id=420000, open=10, high=140, low=100, close=110,
                       ma5=102, ma15=100, prev_ma5=102, prev_ma15=100, kc_mid=100,
                       atr14=1.0, ma15_prev5=90)
    cand3 = strat.on_closed_bar("LOBSTER", bar7, ctx_good)
    assert cand3 is not None
    assert cand3.is_recovered is True
