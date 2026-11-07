"""
Unit tests for Scheme A Holding Breathing Protection (方案 A 成本呼吸區持倉保護)
- 未達 0.5 ATR 且 peak_roe < 5%: 成本區間正常回踩/反彈，一票否決平倉 (VETO_HOLD)
- 實質展開 (>= 0.5 ATR 或 peak_roe >= 5%): 捕獲真峰谷放行平倉獲利了結
"""

import pytest
from core.services.execution_manager import should_exit_long, should_exit_short


def test_holding_breathing_protection_blocks_early_exit_long():
    """驗證多單剛開倉在成本區間回踩時，即便出現毛刺或MA下拐，100% 拒絕平倉"""
    curr_k = {
        "open": 100.0,
        "close": 99.8,   # 成本價 100.0，微跌 -0.2 (0.2 ATR)
        "high": 100.2,
        "low": 99.6,
    }
    prev_k = {
        "open": 100.2,
        "close": 100.0,
        "high": 100.4,
        "low": 99.9,
    }
    # 剛開倉未展開部位
    position = {
        "entry_price": 100.0,
        "highest_price": 100.2,  # gain = 0.2 < 0.5 ATR
        "peak_roe": 1.0,         # 1% < 5%
        "leverage": 5,
        "side": "LONG",
    }
    exit_decision = should_exit_long(
        curr_k=curr_k,
        prev_k=prev_k,
        ma3_curr=99.9,
        ma3_prev=100.3,          # MA3 死叉下拐
        ma15_curr=100.1,
        ma15_prev=100.1,
        kc_upper=102.0,
        prev_kc_upper=102.0,
        current_atr=1.0,         # 0.5 ATR = 0.5 > gain(0.2)
        position=position,
    )
    # 必須一票否決平多，續抱！
    assert exit_decision is False


def test_holding_breathing_protection_blocks_early_exit_short():
    """驗證空單剛開倉在成本區間反彈時，即便出現毛刺或MA上拐，100% 拒絕平倉"""
    curr_k = {
        "open": 100.0,
        "close": 100.2,   # 成本價 100.0，微漲 +0.2 (0.2 ATR)
        "high": 100.4,
        "low": 99.8,
    }
    prev_k = {
        "open": 99.8,
        "close": 100.0,
        "high": 100.1,
        "low": 99.6,
    }
    position = {
        "entry_price": 100.0,
        "lowest_price": 99.8,    # drop = 0.2 < 0.5 ATR
        "peak_roe": 1.0,         # 1% < 5%
        "leverage": 5,
        "side": "SHORT",
    }
    exit_decision = should_exit_short(
        curr_k=curr_k,
        prev_k=prev_k,
        ma3_curr=100.1,
        ma3_prev=99.7,           # MA3 金叉上拐
        ma15_curr=99.9,
        ma15_prev=99.9,
        kc_lower=98.0,
        prev_kc_lower=98.0,
        current_atr=1.0,         # 0.5 ATR = 0.5 > drop(0.2)
        position=position,
    )
    # 必須一票否決平空，續抱！
    assert exit_decision is False


def test_holding_allows_exit_after_profit_expansion():
    """驗證當部位波段已實質展開 (gain >= 0.5 ATR 且捕獲真峰頂) 時，放行平倉獲利了結"""
    # 頂部收陰且出現長上影針或大陰線
    curr_k = {
        "open": 105.0,
        "close": 103.0,
        "high": 106.0,
        "low": 102.8,
    }
    prev_k = {
        "open": 102.0,
        "close": 105.0,
        "high": 105.5,
        "low": 101.8,
    }
    position = {
        "entry_price": 100.0,
        "highest_price": 106.0,  # gain = 6.0 >= 0.5 ATR (0.5)
        "peak_roe": 30.0,        # 30% >= 5%
        "leverage": 5,
        "side": "LONG",
    }
    exit_decision = should_exit_long(
        curr_k=curr_k,
        prev_k=prev_k,
        ma3_curr=103.5,
        ma3_prev=105.0,          # 死叉且實體吞噬
        ma15_curr=103.8,
        ma15_prev=103.6,
        kc_upper=104.0,
        prev_kc_upper=104.0,
        current_atr=1.0,
        position=position,
    )
    # 實質展開後真峰頂放行平多
    assert exit_decision is True


def test_no_per_position_hard_stop_only_account_waterfall_and_circuit_breaker():
    """
    驗證「不要硬止損，只留帳戶硬止損，大瀑布及市場熔斷！」：
    1. 單筆部位虧損超過 3 ATR (DISASTER_LOSS_3ATR) 或反側外軌 (DISASTER_RAIL) 且未構成結構反轉時，不觸發單筆硬止損。
    2. check_waterfall_emergency_exit 廢除 0.5 ATR 偽硬止損 (penetrates_mid_ma15)，僅在單根 K 棒極速崩跌/暴拉 >= 1.2 ATR 且貫穿 KC 中軌的大瀑布時才觸發。
    """
    import time
    import pandas as pd
    from core.gates.ultimate_short_exit_gatekeeper import UltimateShortExitGatekeeper
    from core.gates.unified_pipeline_gates import ExitGate

    assert "CIRCUIT_BREAKER_HARD_SL" not in ExitGate.ABSOLUTE_SAFETY_REASONS
    assert "HARD_STOP_LOSS" not in ExitGate.ABSOLUTE_SAFETY_REASONS
    assert "OPPOSITE_KC_BAND_BREACH" not in ExitGate.ABSOLUTE_SAFETY_REASONS
    assert "ACCOUNT_HARD_STOP" in ExitGate.ABSOLUTE_SAFETY_REASONS
    assert "EXIT_BY_EXTREME_WATERFALL" in ExitGate.ABSOLUTE_SAFETY_REASONS
    assert "CIRCUIT_BREAKER" in ExitGate.ABSOLUTE_SAFETY_REASONS

    now_min = int(time.time() // 60)
    # 模擬盤中小紅 K 僅低於中軌與 MA15 0.6 ATR，但當根開盤至現價跌幅僅 0.2 ATR（非大瀑布）
    df_minor_dip = pd.DataFrame([
        {
            "timestamp": (now_min - 1) * 60_000,
            "open": 100.0, "high": 100.5, "low": 99.5, "close": 100.0,
            "kc_upper": 103.0, "kc_middle": 100.0, "kc_lower": 97.0,
            "ma5": 100.0, "ma15": 100.0, "atr": 1.0, "is_closed": True,
        },
        {
            "timestamp": now_min * 60_000,
            "open": 99.5, "high": 99.6, "low": 99.2, "close": 99.3,
            "kc_upper": 103.0, "kc_middle": 100.0, "kc_lower": 97.0,
            "ma5": 99.6, "ma15": 100.0, "atr": 1.0, "is_closed": False,
        },
    ])
    pos_long = {
        "symbol": "龙虾/USDT",
        "side": "LONG",
        "entry_price": 100.2,
        "open_timestamp": (now_min - 5) * 60,
    }
    wf_hit, wf_reason, _ = UltimateShortExitGatekeeper.check_waterfall_emergency_exit(
        "龙虾/USDT", pos_long, {}, 99.3, frame=df_minor_dip,
    )
    assert wf_hit is False, f"非大瀑布不得因跌破中軌 0.5 ATR 觸發偽硬止損: {wf_reason}"

    # 真正大瀑布：單根盤中下殺 >= 1.2 ATR (99.5 -> 98.1 = 1.4 ATR) 且跌破 KC 中軌 -> 允許大瀑布避險平倉
    wf_hit_true, wf_reason_true, _ = UltimateShortExitGatekeeper.check_waterfall_emergency_exit(
        "龙虾/USDT", pos_long, {}, 98.1, frame=df_minor_dip,
    )
    assert wf_hit_true is True
    assert wf_reason_true == "EXIT_LONG_EMERGENCY_WATERFALL"

