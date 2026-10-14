import pytest
from gate_strict_bot import CONFIG, PositionState, StrategyEvaluator

def create_bar(close=100.0, open_p=99.0, ma5=101.0, ma15=99.0, kc_middle=100.0, kc_upper=102.0, kc_lower=98.0, atr=1.0):
    return {
        'close': close,
        'open': open_p,
        'ma5': ma5,
        'ma15': ma15,
        'kc_middle': kc_middle,
        'kc_upper': kc_upper,
        'kc_lower': kc_lower,
        'atr': atr
    }

def test_ma5_flat_rejected():
    cfg = CONFIG.copy()
    evaluator = StrategyEvaluator(cfg)
    state = PositionState()

    # ma5_slope = 0 (flat)
    closed = create_bar(ma5=100.0)
    prev = create_bar(ma5=100.0)
    res = evaluator.evaluate_entry(closed, prev, state, 1000)
    assert res is None

def test_ma_separation_rejected():
    cfg = CONFIG.copy()
    evaluator = StrategyEvaluator(cfg)
    state = PositionState()

    # ma5_slope is valid, but ma_sep < 0.0015
    closed = create_bar(close=100.0, ma5=100.08, ma15=100.0) # sep = 0.08 / 100 = 0.0008 < 0.0015
    prev = create_bar(close=100.0, ma5=100.0)
    res = evaluator.evaluate_entry(closed, prev, state, 1000)
    assert res is None

def test_kc_flat_rejected():
    cfg = CONFIG.copy()
    evaluator = StrategyEvaluator(cfg)
    state = PositionState()

    # ma5 slope and sep valid, but kc_slope = 0
    closed = create_bar(close=100.0, ma5=101.0, ma15=100.0, kc_middle=100.0)
    prev = create_bar(close=100.0, ma5=100.0, ma15=100.0, kc_middle=100.0)
    res = evaluator.evaluate_entry(closed, prev, state, 1000)
    assert res is None

def test_bandwidth_squeeze_rejected():
    cfg = CONFIG.copy()
    evaluator = StrategyEvaluator(cfg)
    state = PositionState()

    # kc_bandwidth < 0.008
    closed = create_bar(close=100.0, ma5=101.0, ma15=100.0, kc_middle=100.1, kc_upper=100.3, kc_lower=99.9) # bw = 0.4 / 100 = 0.004 < 0.008
    prev = create_bar(close=100.0, ma5=100.0, ma15=100.0, kc_middle=100.0)
    res = evaluator.evaluate_entry(closed, prev, state, 1000)
    assert res is None

def test_open_long_at_bottom_success():
    cfg = CONFIG.copy()
    evaluator = StrategyEvaluator(cfg)
    state = PositionState()

    # price = 98.2, kc_lower = 98.0, dist = 0.2 <= 0.5 * 1.0 (atr)
    # close >= open, ma5 > ma15, ma5 up, kc up, bw >= 0.008
    closed = create_bar(close=98.2, open_p=98.0, ma5=101.0, ma15=99.0, kc_middle=100.1, kc_upper=102.0, kc_lower=98.0, atr=1.0)
    prev = create_bar(close=98.0, ma5=100.0, ma15=99.0, kc_middle=100.0)
    res = evaluator.evaluate_entry(closed, prev, state, 1000)
    assert res == 'OPEN_LONG'

def test_open_short_at_top_success():
    cfg = CONFIG.copy()
    evaluator = StrategyEvaluator(cfg)
    state = PositionState()

    # price = 101.8, kc_upper = 102.0, dist = 0.2 <= 0.5 * 1.0 (atr)
    # close <= open, ma5 < ma15, ma5 down, kc down, bw >= 0.008
    closed = create_bar(close=101.8, open_p=102.0, ma5=99.0, ma15=101.0, kc_middle=99.9, kc_upper=102.0, kc_lower=98.0, atr=1.0)
    prev = create_bar(close=102.0, ma5=100.0, ma15=101.0, kc_middle=100.0)
    res = evaluator.evaluate_entry(closed, prev, state, 1000)
    assert res == 'OPEN_SHORT'

def test_exit_hard_stop():
    cfg = CONFIG.copy()
    evaluator = StrategyEvaluator(cfg)
    state = PositionState()
    state.open('LONG', 100.0, 1000)

    # Price dropped by 2% -> net_pnl = -0.02 - 0.0012 = -0.0212 <= -0.015
    closed = create_bar(close=98.0, ma15=99.0)
    exit_action = evaluator.evaluate_exit(closed, 98.0, 1000, state)
    assert exit_action == 'EXIT_HARD_STOP'

def test_same_bar_protection():
    cfg = CONFIG.copy()
    evaluator = StrategyEvaluator(cfg)
    state = PositionState()
    state.open('LONG', 100.0, 1000)

    # Same bar, profit only 0.5% (less than 0.8% threshold), broken trend should be blocked
    closed = create_bar(close=99.5, ma15=100.0)
    exit_action = evaluator.evaluate_exit(closed, 100.5, 1000, state)
    assert exit_action is None

    # Same bar, profit reaches 0.95% (net > 0.8%) -> EXIT_SAME_BAR_TP
    exit_action = evaluator.evaluate_exit(closed, 101.0, 1000, state)
    assert exit_action == 'EXIT_SAME_BAR_TP'

def test_ratchet_profit_lock():
    cfg = CONFIG.copy()
    evaluator = StrategyEvaluator(cfg)
    state = PositionState()
    state.open('LONG', 100.0, 1000)

    closed = create_bar(close=112.0, ma15=105.0)
    # Peak profit reaches 15% (net = 0.15 - 0.0012 = 0.1488)
    evaluator.evaluate_exit(closed, 115.0, 2000, state)
    assert state.ratchet_active is True

    # Pullback 20% from peak
    # Peak net pnl ~ 0.1488, pullback line = 0.1488 * 0.8 = 0.119
    # If price pulls back to 110.0 (net ~ 0.0988 <= 0.119) -> Ratchet triggered
    exit_action = evaluator.evaluate_exit(closed, 110.0, 2000, state)
    assert exit_action == 'EXIT_NET_PROFIT_RATCHET'

def test_trend_immunity_veto_and_break():
    cfg = CONFIG.copy()
    evaluator = StrategyEvaluator(cfg)
    state = PositionState()
    state.open('LONG', 100.0, 1000)

    # Next bar, not hitting TP or hard stop.
    # Close above MA15 -> VETO (None)
    closed = create_bar(close=101.0, ma15=100.5)
    exit_action = evaluator.evaluate_exit(closed, 101.0, 2000, state)
    assert exit_action is None

    # Close below MA15 -> EXIT_TREND_BROKEN
    closed = create_bar(close=99.8, ma15=100.5)
    exit_action = evaluator.evaluate_exit(closed, 99.8, 2000, state)
    assert exit_action == 'EXIT_TREND_BROKEN'
