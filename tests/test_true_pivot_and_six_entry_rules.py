import pytest
from core.services.execution_manager import check_pivot_status, ExecutionManager
from core.services.scalping_state_machine import ScalpingStateMachine


def test_true_valley_pivot_exit_detection():
    """驗證空單波段獲利後的真谷底三點樞紐探底反轉（無需穿越 MA15 即可及時平空）"""
    # 模擬 1000PEPE 谷底：前一根探底陰線，當根收陽站上 MA3，突破實體中點，MA3 上拐
    prev_k = {
        "open": 0.00505,
        "close": 0.00498,
        "high": 0.00506,
        "low": 0.00497,
    }
    curr_k = {
        "open": 0.00498,
        "close": 0.00503,  # 突破前棒中點 (0.005015)
        "high": 0.00504,
        "low": 0.00498,
    }
    ma3_prev = 0.00500
    ma3_curr = 0.00502  # MA3 上拐且當前 close 0.00503 >= ma3_curr
    ma15_prev = 0.00518
    ma15_curr = 0.00516  # 價格仍在 MA15 遠下方 (空頭浪底部)
    kc_upper = 0.00525
    kc_lower = 0.00500
    prev_kc_upper = 0.00526
    prev_kc_lower = 0.00501
    atr_val = 0.00005

    is_bottom, is_peak = check_pivot_status(
        curr_k=curr_k,
        prev_k=prev_k,
        ma3_curr=ma3_curr,
        ma3_prev=ma3_prev,
        ma15_curr=ma15_curr,
        ma15_prev=ma15_prev,
        kc_upper=kc_upper,
        kc_lower=kc_lower,
        prev_kc_upper=prev_kc_upper,
        prev_kc_lower=prev_kc_lower,
        current_atr=atr_val,
    )
    assert is_bottom is True
    assert is_peak is False


def test_true_peak_pivot_exit_detection():
    """驗證多單波段獲利後的真峰頂三點樞紐衝頂反轉（無需跌破 MA15 即可及時平多）"""
    # 模擬多頭衝頂：前一根衝頂陽線，當根收陰跌破 MA3，跌破實體中點，MA3 下拐
    prev_k = {
        "open": 0.00520,
        "close": 0.00528,
        "high": 0.00529,
        "low": 0.00519,
    }
    curr_k = {
        "open": 0.00528,
        "close": 0.00522,  # 跌破前棒中點 (0.00524)
        "high": 0.00528,
        "low": 0.00521,
    }
    ma3_prev = 0.00526
    ma3_curr = 0.00524  # MA3 下拐且當前 close 0.00522 <= ma3_curr
    ma15_prev = 0.00510
    ma15_curr = 0.00512  # 價格仍在 MA15 遠上方 (多頭浪頂部)
    kc_upper = 0.00527
    kc_lower = 0.00505
    prev_kc_upper = 0.00526
    prev_kc_lower = 0.00504
    atr_val = 0.00005

    is_bottom, is_peak = check_pivot_status(
        curr_k=curr_k,
        prev_k=prev_k,
        ma3_curr=ma3_curr,
        ma3_prev=ma3_prev,
        ma15_curr=ma15_curr,
        ma15_prev=ma15_prev,
        kc_upper=kc_upper,
        kc_lower=kc_lower,
        prev_kc_upper=prev_kc_upper,
        prev_kc_lower=prev_kc_lower,
        current_atr=atr_val,
    )
    assert is_peak is True
    assert is_bottom is False


def test_entry_ma3_ma15_position_rule():
    """驗證開倉鐵律：MA3 <= MA15 嚴禁開多，MA3 >= MA15 嚴禁開空"""
    # 開多時 MA3 < MA15 -> 否決
    res_long = ScalpingStateMachine.evaluate_dict(
        position_state="FLAT",
        macro_trend="BULLISH",
        current_price=101.0,
        open_price=100.0,
        ma3=99.5,
        ma15=100.0,
        ma3_slope=0.02,
    )
    assert res_long["action"] == "WAIT"
    assert "位置否決" in res_long["reason"]

    # 開空時 MA3 > MA15 -> 否決
    res_short = ScalpingStateMachine.evaluate_dict(
        position_state="FLAT",
        macro_trend="BEARISH",
        current_price=99.0,
        open_price=100.0,
        ma3=100.5,
        ma15=100.0,
        ma3_slope=-0.02,
    )
    assert res_short["action"] == "WAIT"
    assert "位置否決" in res_short["reason"]


def test_entry_ma3_slope_flat_rule():
    """驗證開倉鐵律：MA3 走平或逆向 100% 否決開倉"""
    # 開多但 MA3 斜率 <= 0
    res_long_flat = ScalpingStateMachine.evaluate_dict(
        position_state="FLAT",
        macro_trend="BULLISH",
        current_price=101.0,
        open_price=100.0,
        ma3=100.5,
        ma15=100.0,
        ma3_slope=0.0,
    )
    assert res_long_flat["action"] == "WAIT"
    assert "斜率否決" in res_long_flat["reason"]

    # 開空但 MA3 斜率 >= 0
    res_short_flat = ScalpingStateMachine.evaluate_dict(
        position_state="FLAT",
        macro_trend="BEARISH",
        current_price=99.0,
        open_price=100.0,
        ma3=99.5,
        ma15=100.0,
        ma3_slope=0.0,
    )
    assert res_short_flat["action"] == "WAIT"
    assert "斜率否決" in res_short_flat["reason"]



def test_entry_pullback_veto_hold():
    """驗證開倉後成本區間回踩絕不平倉鐵律 (peak_roe < 5% 且虧損在呼吸區)"""
    # 模擬多單開倉在 100，價格小回踩至 99.5 (-0.5% 呼吸區，未達 5% ROE)
    peak_roe_pct = 2.0
    favorable_gain_atr = 0.3
    pos_entry = 100.0
    curr_quote = 99.5
    pos_side = 'LONG'
    atr_v = 1.0

    is_absolute_safety = False
    reason = 'CLOSE_LONG_ON_BEARISH_REVERSAL'
    is_stagnation_exit = (reason == 'EXIT_BY_KC_MIDDLE_FLAT_EXHAUSTION')

    is_profit_unexpanded = bool(peak_roe_pct < 5.0 and favorable_gain_atr < 0.8)
    current_loss_pct = (pos_entry - curr_quote) / pos_entry
    is_within_normal_breathing = bool(current_loss_pct < 0.020 and (current_loss_pct * pos_entry) < 2.5 * atr_v)

    should_exit = True
    if should_exit and not is_absolute_safety and not is_stagnation_exit:
        if is_profit_unexpanded and is_within_normal_breathing:
            should_exit = False

    assert should_exit is False, '開倉後回踩應觸發 VETO_HOLD，絕不平倉！'
