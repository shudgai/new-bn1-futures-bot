import pytest
import os
import json
import logging
import copy
from unittest.mock import patch, MagicMock
from core.services.exits.peak_trailing_exit import evaluate_peak_trailing
from core.services.exits.profit_exit_telemetry import ProfitExitTelemetry

@pytest.fixture(autouse=True)
def setup_teardown():
    # File isolation (tmp_path redirect + production-path guard) and logger/ENABLED
    # restoration are provided by the global fixture in tests/conftest.py.
    ProfitExitTelemetry.ENABLED = True
    logger = logging.getLogger('ProfitExitTelemetry')
    logger.handlers.clear()
    yield

def extract_logs(caplog, event_type):
    res = []
    for record in caplog.records:
        if record.name == 'ProfitExitTelemetry':
            try:
                data = json.loads(record.message)
                if data.get('event_type') == event_type:
                    res.append(data)
            except:
                pass
    return res

def create_pos(side='LONG', entry=100, atr=1):
    return {'id': 't1', 'symbol': 'BTCUSDT', 'side': side, 'entry_price': entry, 'qty': 1, 'entry_atr': atr, 'sl': 90 if side == 'LONG' else 110, 'open_timestamp': 1}

# =================================================================================
# 2. ARM BOUNDARY
# =================================================================================
def test_arm_boundary_2_999(caplog):
    pos = create_pos()
    evaluate_peak_trailing(pos, 102.999, {'quote_ms': 1000}, atr=1.0)
    assert len(extract_logs(caplog, 'PARABOLIC_ARM_REACHED')) == 0

def test_arm_boundary_3_000(caplog):
    pos = create_pos()
    evaluate_peak_trailing(pos, 103.0, {'quote_ms': 1000}, atr=1.0)
    assert len(extract_logs(caplog, 'PARABOLIC_ARM_REACHED')) == 1

def test_arm_boundary_once(caplog):
    pos = create_pos()
    evaluate_peak_trailing(pos, 103.0, {'quote_ms': 1000}, atr=1.0)
    evaluate_peak_trailing(pos, 104.0, {'quote_ms': 1001}, atr=1.0)
    assert len(extract_logs(caplog, 'PARABOLIC_ARM_REACHED')) == 1

# =================================================================================
# 3. PULLBACK BOUNDARY
# =================================================================================
@patch('core.services.exits.trend_hold_evaluator.evaluate_trend_hold', return_value=('RELEASED', ''))
def test_pullback_boundary_0_999(mock_hold, caplog):
    pos = create_pos()
    evaluate_peak_trailing(pos, 105.0, {'quote_ms': 1000}, atr=1.0)
    dec = evaluate_peak_trailing(pos, 104.001, {'quote_ms': 1001}, atr=1.0)
    assert dec is None

@patch('core.services.exits.trend_hold_evaluator.evaluate_trend_hold', return_value=('RELEASED', ''))
def test_pullback_boundary_1_000(mock_hold, caplog):
    pos = create_pos()
    evaluate_peak_trailing(pos, 105.0, {'quote_ms': 1000}, atr=1.0)
    dec = evaluate_peak_trailing(pos, 104.0, {'quote_ms': 1001}, atr=1.0)
    assert dec is not None
    assert dec['trigger'] == 'EXIT_PARABOLIC_PULLBACK_1_ATR'

# =================================================================================
# 4. SHORT SYMMETRY
# =================================================================================
@patch('core.services.exits.trend_hold_evaluator.evaluate_trend_hold', return_value=('RELEASED', ''))
def test_short_symmetry(mock_hold, caplog):
    pos = create_pos(side='SHORT', entry=100, atr=1)
    # Peak gain = 3 ATR -> price goes to 97
    evaluate_peak_trailing(pos, 97.0, {'quote_ms': 1000}, atr=1.0)
    assert len(extract_logs(caplog, 'PARABOLIC_ARM_REACHED')) == 1
    
    # New peak -> 96
    evaluate_peak_trailing(pos, 96.0, {'quote_ms': 1001}, atr=1.0)
    peak_logs = extract_logs(caplog, 'NEW_RUNTIME_PEAK')
    assert len(peak_logs) >= 1
    assert peak_logs[-1]['data']['runtime_peak_price'] == 96.0
    
    # Rebound 1 ATR -> price goes to 97
    dec = evaluate_peak_trailing(pos, 97.0, {'quote_ms': 1002}, atr=1.0)
    assert dec is not None
    assert dec['trigger'] == 'EXIT_PARABOLIC_PULLBACK_1_ATR'
    assert dec['action'] == 'FULL_CLOSE'

# =================================================================================
# 5. MA-TURN PATH
# =================================================================================
@patch('core.services.exits.trend_hold_evaluator.evaluate_trend_hold', return_value=('RELEASED', ''))
def test_ma_turn_allowed(mock_hold, caplog):
    pos = create_pos()
    # Drawdown < 1 ATR (peak 105, price 104.5 -> drawdown 0.5)
    evaluate_peak_trailing(pos, 105.0, {'quote_ms': 1000, 'ma5': 104, 'last_ma5': 103}, atr=1.0)
    # MA5 downward turn: last_ma5 > ma5
    dec = evaluate_peak_trailing(pos, 104.5, {'quote_ms': 1001, 'ma5': 103.0, 'last_ma5': 104.0}, atr=1.0)
    assert dec is not None
    assert dec['trigger'] == 'EXIT_PARABOLIC_MA3_TURN'

@patch('core.services.exits.trend_hold_evaluator.evaluate_trend_hold', return_value=('HOLD', 'TREND'))
def test_ma_turn_vetoed(mock_hold, caplog):
    pos = create_pos()
    evaluate_peak_trailing(pos, 105.0, {'quote_ms': 1000, 'ma5': 104, 'last_ma5': 103}, atr=1.0)
    dec = evaluate_peak_trailing(pos, 104.5, {'quote_ms': 1001, 'ma5': 103.0, 'last_ma5': 104.0}, atr=1.0)
    assert dec is None

# =================================================================================
# 6. DECISION EQUIVALENCE MATRIX
# =================================================================================
def evaluate_both(setup_func, eval_func):
    ProfitExitTelemetry.ENABLED = False
    pos1, snap1 = setup_func()
    dec_disabled = eval_func(pos1, snap1)
    
    ProfitExitTelemetry.ENABLED = True
    pos2, snap2 = setup_func()
    dec_enabled = eval_func(pos2, snap2)
    return dec_disabled, dec_enabled

def test_no_exit_equivalence():
    def setup():
        return create_pos(), {'quote_ms': 1000}
    def eval(pos, snap):
        return evaluate_peak_trailing(pos, 101, snap, atr=1.0)
    d, e = evaluate_both(setup, eval)
    assert d == e == None

@patch('core.services.exits.trend_hold_evaluator.evaluate_trend_hold', return_value=('RELEASED', ''))
def test_pullback_allow_equivalence(mock_hold):
    def setup():
        pos = create_pos()
        evaluate_peak_trailing(pos, 105, {'quote_ms': 1000}, atr=1.0)
        return pos, {'quote_ms': 1001}
    def eval(pos, snap):
        return evaluate_peak_trailing(pos, 104, snap, atr=1.0)
    d, e = evaluate_both(setup, eval)
    assert d == e
    assert d['trigger'] == 'EXIT_PARABOLIC_PULLBACK_1_ATR'

@patch('core.services.exits.trend_hold_evaluator.evaluate_trend_hold', return_value=('HOLD', ''))
def test_pullback_veto_equivalence(mock_hold):
    def setup():
        pos = create_pos()
        evaluate_peak_trailing(pos, 105, {'quote_ms': 1000}, atr=1.0)
        return pos, {'quote_ms': 1001}
    def eval(pos, snap):
        return evaluate_peak_trailing(pos, 104, snap, atr=1.0)
    d, e = evaluate_both(setup, eval)
    assert d == e == None

@patch('core.services.exits.trend_hold_evaluator.evaluate_trend_hold', return_value=('RELEASED', ''))
def test_ma_turn_allow_equivalence(mock_hold):
    def setup():
        pos = create_pos()
        evaluate_peak_trailing(pos, 105, {'quote_ms': 1000, 'ma5': 104, 'last_ma5': 103}, atr=1.0)
        return pos, {'quote_ms': 1001, 'ma5': 103, 'last_ma5': 104}
    def eval(pos, snap):
        return evaluate_peak_trailing(pos, 104.5, snap, atr=1.0)
    d, e = evaluate_both(setup, eval)
    assert d == e
    assert d['trigger'] == 'EXIT_PARABOLIC_MA3_TURN'

@patch('core.services.exits.trend_hold_evaluator.evaluate_trend_hold', return_value=('HOLD', ''))
def test_ma_turn_veto_equivalence(mock_hold):
    def setup():
        pos = create_pos()
        evaluate_peak_trailing(pos, 105, {'quote_ms': 1000, 'ma5': 104, 'last_ma5': 103}, atr=1.0)
        return pos, {'quote_ms': 1001, 'ma5': 103, 'last_ma5': 104}
    def eval(pos, snap):
        return evaluate_peak_trailing(pos, 104.5, snap, atr=1.0)
    d, e = evaluate_both(setup, eval)
    assert d == e == None

MATURE_BAR = {'o': 101, 'h': 103, 'l': 100.5, 'c': 102.5, 'ma3': 101, 'ma5': 100}  # LONG: l > ma5

def mature_snapshot(rev_bar):
    # Production gate: live_bar_ms == floor(quote_ms/60000)*60000,
    # closed_bar_ms == live_bar_ms - 60000, positive live_open & atr.
    # live_open == price -> waterfall body 0 (cannot override Mature).
    history = [dict(MATURE_BAR, ms=60000 * i) for i in range(1, 5)]
    history.append(dict(rev_bar, ms=300000))
    return {'quote_ms': 360000, 'live_bar_ms': 360000, 'closed_bar_ms': 300000,
            'live_open': 102.0, 'atr': 1.0, 'history_5': history}

# Pinbar: upper_wick 2.0 > 0.5*ATR and > 2*body(0.2); close_pos 0.12 < 0.40.
# Not doji: c(101.8) >= ma3(101).
MATURE_PINBAR_REV = {'o': 102.0, 'h': 104.0, 'l': 101.5, 'c': 101.8, 'ma3': 101, 'ma5': 100}
# Negative control: bullish full-body bar -> neither pinbar nor doji.
MATURE_NO_REV = {'o': 101.8, 'h': 102.6, 'l': 101.7, 'c': 102.5, 'ma3': 101, 'ma5': 100}

def test_mature_equivalence():
    def setup():
        return create_pos(), mature_snapshot(MATURE_PINBAR_REV)
    def eval(pos, snap):
        return evaluate_peak_trailing(pos, 102, snap, atr=1.0)
    d, e = evaluate_both(setup, eval)
    assert d == e
    assert d is not None
    assert d['action'] == 'FULL_CLOSE'
    assert d['reason'] == 'EXIT_ADVERSE_ABNORMAL_BODY'
    assert d['trigger'] == 'MATURE_REVERSAL_PINBAR'

def test_mature_negative_control():
    def setup():
        return create_pos(), mature_snapshot(MATURE_NO_REV)
    def eval(pos, snap):
        return evaluate_peak_trailing(pos, 102, snap, atr=1.0)
    d, e = evaluate_both(setup, eval)
    assert d == e == None

def waterfall_snapshot():
    # quote 120000 -> bar 120000; closed 60000; live_open 102, prior ATR 1.0
    return {'quote_ms': 120000, 'live_bar_ms': 120000, 'closed_bar_ms': 60000,
            'live_open': 102.0, 'atr': 1.0}

def test_waterfall_equivalence():
    # Price 100: body 2.0 >= 1.2*ATR, and above hard stop 98.5 (entry 100 - 1.5 ATR)
    def setup():
        return create_pos(), waterfall_snapshot()
    def eval(pos, snap):
        return evaluate_peak_trailing(pos, 100.0, snap, atr=1.0)
    d, e = evaluate_both(setup, eval)
    assert d == e
    assert d is not None
    assert d['action'] == 'FULL_CLOSE'
    assert d['reason'] == 'EXIT_ADVERSE_ABNORMAL_BODY'
    assert d['trigger'] == 'WATERFALL_DROP'

def test_waterfall_negative_control():
    # Price 101: body 1.0 < 1.2*ATR -> no waterfall, no hard stop
    def setup():
        return create_pos(), waterfall_snapshot()
    def eval(pos, snap):
        return evaluate_peak_trailing(pos, 101.0, snap, atr=1.0)
    d, e = evaluate_both(setup, eval)
    assert d == e == None

def test_hard_safety_equivalence():
    def setup():
        return create_pos(), {'quote_ms': 1000}
    def eval(pos, snap):
        return evaluate_peak_trailing(pos, 80, snap, atr=1.0)
    d, e = evaluate_both(setup, eval)
    assert d == e
    assert d['reason'] == 'EXIT_INITIAL_ATR_HARD_STOP'

# =================================================================================
# 7. LOGGER FAILURE
# =================================================================================
@patch('core.services.exits.profit_exit_telemetry.get_telemetry_logger')
@patch('core.services.exits.trend_hold_evaluator.evaluate_trend_hold', return_value=('RELEASED', ''))
def test_logger_failure_exact_equivalence(mock_hold, mock_logger):
    def setup():
        pos = create_pos()
        evaluate_peak_trailing(pos, 105, {'quote_ms': 1000}, atr=1.0)
        return pos, {'quote_ms': 1001}
    def eval(pos, snap):
        return evaluate_peak_trailing(pos, 104, snap, atr=1.0)
    
    ProfitExitTelemetry.ENABLED = False
    pos1, snap1 = setup()
    dec_disabled = eval(pos1, snap1)
    
    ProfitExitTelemetry.ENABLED = True
    mock_logger.side_effect = Exception("Disk full")
    pos2, snap2 = setup()
    dec_enabled = eval(pos2, snap2)
    
    assert dec_disabled == dec_enabled
    assert dec_disabled is not None

# =================================================================================
# 8. TELEMETRY STATE KEYS
# =================================================================================
@patch('core.services.exits.trend_hold_evaluator.evaluate_trend_hold', return_value=('RELEASED', ''))
def test_telemetry_state_keys_decision_neutral(mock_hold):
    pos_clean = create_pos()
    pos_dirty = create_pos()
    pos_dirty['state'] = {
        'telemetry_arm_logged': True,
        'last_logged_peak_price': 999.0,
        'last_logged_peak_atr': 9.0,
        'telemetry_candidate_logged': True
    }
    
    evaluate_peak_trailing(pos_clean, 105, {'quote_ms': 1000}, atr=1.0)
    evaluate_peak_trailing(pos_dirty, 105, {'quote_ms': 1000}, atr=1.0)
    
    dec_clean = evaluate_peak_trailing(pos_clean, 104, {'quote_ms': 1001}, atr=1.0)
    dec_dirty = evaluate_peak_trailing(pos_dirty, 104, {'quote_ms': 1001}, atr=1.0)
    
    assert dec_clean == dec_dirty
    assert dec_clean is not None

