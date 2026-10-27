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
