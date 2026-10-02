import pytest
from swing_replay import SwingReplayEngine, aggregate_replay_results

def test_lobster_fixture():
    # 11:03 Lobster F1 deterministic geometric proof
    engine = SwingReplayEngine(entry_price=0.0520148, side='SHORT', initial_stop=0.05543, entry_atr=0.002277)
    engine.process_kline({'open': 0.05161, 'high': 0.05373, 'low': 0.05077, 'close': 0.05355, 'kc_middle': 0.05478}, '11:03')
    
    # F1 Geometric Proof: Arm=0.05087. Floor=0.05201.
    # Open(0.05161) is un-armed. Must go down to Low(0.05077) to arm.
    # From Low to Close(0.05355), must cross Floor(0.05201) upward.
    # Therefore hit AFTER arm.
    f1 = engine.floors['F1']
    assert f1['active'] == False
    assert f1['exit_reason'] == 'PROFIT_FLOOR'
    assert f1['resolution'] == 'DETERMINISTIC'

def test_high_first_low_later_no_tick_unknown():
    engine = SwingReplayEngine(entry_price=100, side='SHORT', initial_stop=110, entry_atr=10)
    engine.process_kline({'open': 105, 'high': 110, 'low': 80, 'close': 105, 'kc_middle': 105}, 't1')
    assert engine.floors['F3']['resolution'] == 'INTRABAR_UNKNOWN'

def test_low_first_high_later_no_tick_unknown():
    engine = SwingReplayEngine(entry_price=100, side='LONG', initial_stop=90, entry_atr=10)
    engine.process_kline({'open': 95, 'high': 120, 'low': 90, 'close': 95, 'kc_middle': 95}, 't1')
    assert engine.floors['F3']['resolution'] == 'INTRABAR_UNKNOWN'

def test_floor_previously_armed_next_hit_deterministic():
    engine = SwingReplayEngine(entry_price=100, side='SHORT', initial_stop=110, entry_atr=10)
    engine.process_kline({'open': 95, 'high': 95, 'low': 85, 'close': 85, 'kc_middle': 100}, 't1')
    engine.process_kline({'open': 85, 'high': 105, 'low': 80, 'close': 105, 'kc_middle': 100}, 't2')
    assert engine.floors['F3']['resolution'] == 'DETERMINISTIC'

def test_floor_and_sl_same_bar_unprovable_unknown():
    engine = SwingReplayEngine(entry_price=100, side='SHORT', initial_stop=110, entry_atr=10)
    engine.process_kline({'open': 100, 'high': 100, 'low': 85, 'close': 85, 'kc_middle': 100}, 't1')
    engine.process_kline({'open': 105, 'high': 110, 'low': 85, 'close': 105, 'kc_middle': 100}, 't2')
    assert engine.floors['F3']['resolution'] == 'INTRABAR_UNKNOWN'

def test_same_bar_arm_and_hit_unprovable_unknown():
    engine = SwingReplayEngine(entry_price=100, side='SHORT', initial_stop=110, entry_atr=10)
    engine.process_kline({'open': 105, 'high': 105, 'low': 85, 'close': 85, 'kc_middle': 100}, 't1')
    assert engine.floors['F3']['resolution'] == 'INTRABAR_UNKNOWN'

def test_tick_path_resolves_floor_first():
    engine = SwingReplayEngine(entry_price=100, side='SHORT', initial_stop=110, entry_atr=10)
    # Ticks: open->arm->floor->SL
    ticks = [{'price': 105}, {'price': 85}, {'price': 105}, {'price': 115}]
    engine.process_kline({'open': 105, 'high': 115, 'low': 85, 'close': 115, 'kc_middle': 100}, 't1', ticks=ticks)
    
    assert engine.floors['F3']['resolution'] == 'TICK_RESOLVED'
    assert engine.floors['F3']['exit_reason'] == 'PROFIT_FLOOR'

def test_tick_path_resolves_sl_first():
    engine = SwingReplayEngine(entry_price=100, side='SHORT', initial_stop=110, entry_atr=10)
    # Ticks: open->SL->arm->floor
    ticks = [{'price': 105}, {'price': 115}, {'price': 85}, {'price': 105}]
    engine.process_kline({'open': 105, 'high': 115, 'low': 85, 'close': 115, 'kc_middle': 100}, 't1', ticks=ticks)
    
    assert engine.floors['F3']['resolution'] == 'TICK_RESOLVED'
    assert engine.floors['F3']['exit_reason'] == 'INITIAL_ATR_STOP'

def test_unknown_excluded_from_deterministic_aggregate():
    results = [
        {'resolution': 'DETERMINISTIC', 'realized_R': 1.0},
        {'resolution': 'DETERMINISTIC', 'realized_R': -1.0},
        {'resolution': 'TICK_RESOLVED', 'realized_R': 2.0},
        {'resolution': 'TICK_RESOLVED', 'realized_R': -1.0},
        {'resolution': 'INTRABAR_UNKNOWN', 'best_case_R': 3.0, 'worst_case_R': -2.0},
        {'resolution': 'INTRABAR_UNKNOWN', 'best_case_R': 2.0, 'worst_case_R': -1.0},
        {'resolution': 'INTRABAR_UNKNOWN'},
    ]
    stats = aggregate_replay_results(results)
    
    assert stats['trade_count'] == 4  # 2 det + 2 tick
    assert stats['unknown_count'] == 3
    assert stats['unknown_rate'] == 3 / 7
    assert stats['total_R'] == 1.0
    assert stats['average_R'] == 0.25
    assert stats['expectancy_R'] == 0.25
    assert stats['median_R'] == 0.0
    assert stats['profit_factor'] == 1.5  # 3.0 / 2.0
    assert stats['max_drawdown_R'] == 1.0  # From +1.0 down to 0.0, or +2.0 to +1.0
    assert stats['unknown_best_case_R'] == 2.5
    assert stats['unknown_worst_case_R'] == -1.5

def test_f0_semantics():
    engine = SwingReplayEngine(entry_price=100, side='SHORT', initial_stop=110, entry_atr=10)
    engine.process_kline({'open': 100, 'high': 100, 'low': 70, 'close': 70, 'kc_middle': 100}, 't1')
    
    f0 = engine.floors['F0']
    assert f0['active'] == True
    assert f0['armed_at'] is None
    assert f0['floor_price'] is None

def test_swing_released_exit_triggered():
    engine = SwingReplayEngine(entry_price=100, side='SHORT', initial_stop=110, entry_atr=10)
    engine.process_kline({'open': 100, 'high': 100, 'low': 70, 'close': 70, 'kc_middle': 100, 'ma5': 90, 'ma15': 95}, 't1')
    assert engine.swing_state == 'SWING_HOLD'
    
    # Trigger B: MA5 >= MA15
    engine.process_kline({'open': 70, 'high': 105, 'low': 70, 'close': 105, 'kc_middle': 100, 'ma5': 96, 'ma15': 95}, 't2')
    assert engine.swing_state == 'SWING_RELEASED'
    
    f0 = engine.floors['F0']
    assert f0['active'] == False
    assert f0['resolution'] == 'DETERMINISTIC'
    assert f0['exit_reason'] == 'SWING_RELEASED'
    assert f0['exit_price'] == 105

def test_no_lookahead():
    # Prove that the outcome of Bar 1 does not depend on Bar 2
    engine1 = SwingReplayEngine(entry_price=100, side='SHORT', initial_stop=110, entry_atr=10)
    engine1.process_kline({'open': 100, 'high': 115, 'low': 90, 'close': 90, 'kc_middle': 100}, 't1')
    r1 = engine1.floors['F3']['resolution']
    
    engine2 = SwingReplayEngine(entry_price=100, side='SHORT', initial_stop=110, entry_atr=10)
    engine2.process_kline({'open': 100, 'high': 115, 'low': 90, 'close': 90, 'kc_middle': 100}, 't1')
    # Change future bar completely
    engine2.process_kline({'open': 10, 'high': 10, 'low': 10, 'close': 10, 'kc_middle': 10}, 't2')
    r2 = engine2.floors['F3']['resolution']
    
    assert r1 == r2 == 'INTRABAR_UNKNOWN'
