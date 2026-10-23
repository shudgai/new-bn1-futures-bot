"""
tests/test_unified_net_roe_profit_lock.py
=========================================
驗證 Binance Futures Bot 淨 ROE 階梯鎖利規則（第一階段重構版本）：
1. 啟動前：淨 ROE 未達 +8%，不觸發一般峰值回撤鎖利。
2. 第一階段：峰值淨 ROE 達 +8% 後，鎖利底線固定為 +4%。
3. 第二階段：峰值淨 ROE 達 +15% 後，允許回吐 4 個百分點。
4. 第三階段：峰值淨 ROE 達 +30% 後，允許回吐 5 個百分點（且底線單調不下降，不低於第二階段建立的 26% 防守線）。
5. 驗證項目包含：
   - +7.9% 不啟動鎖利
   - 首次達到 +8% 時啟動但不平倉
   - 階梯基準案例（+8%->+4%, +10%->+4%, +15%->+11%, +20%->+16%, +22%->+18%, +30%->+26%, +40%->+35%, +50%->+45%）
   - 最高 +30%，目前 +28% 不得觸發
   - 創新高後立即推升鎖利線
   - 多單 LONG 與空單 SHORT 完全對稱符合
   - 重啟後能恢復同一筆持倉的鎖利狀態
   - 新交易不繼承舊交易的最高 ROE
   - 硬止損（-3 ATR）優先觸發
   - 浮點數精度與邊界值測試
   - ExitGate.evaluate 主交易流程整合驗證
"""
import copy
import math
import pytest
import pandas as pd

from core.services.exits.net_roe_profit_lock import (
    tiered_net_roe_floor,
    has_reached_net_roe_floor,
    calculate_net_roe_pct,
    evaluate_net_roe_profit_lock,
    NET_ROE_LOCK_START_PCT,
    DEFAULT_LOCK_CONFIG,
)
from core.gates.unified_pipeline_gates import ExitGate
from core.services.data_pipeline import MarketDataSnapshot
from core.services.exits.peak_trailing_exit import STATE_KEYS
from core.services.execution_manager import ExecutionManager


def _price_for_net_roe(side: str, target_roe_pct: float, entry: float = 100.0, leverage: float = 5.0) -> float:
    from core.config import SLIPPAGE_PCT, TAKER_FEE_RATE
    sign = 1.0 if side == "LONG" else -1.0
    target_pnl = (target_roe_pct / 100.0) * (entry / leverage)
    p = (target_pnl + entry * (sign + TAKER_FEE_RATE)) / (sign - TAKER_FEE_RATE - SLIPPAGE_PCT)
    return p


# ── 1. +7.9% 不啟動鎖利 ──
@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_sub_threshold_7_9_does_not_arm(side):
    entry = 100.0
    pos = {
        "symbol": "BTC/USDT",
        "side": side,
        "entry_price": entry,
        "qty": 1.0,
        "leverage": 5.0,
        "margin": 20.0,
    }
    price_7_9 = _price_for_net_roe(side, 7.9, entry=entry, leverage=5.0)
    should_exit, reason, meta = evaluate_net_roe_profit_lock(pos, price_7_9, side)
    
    assert should_exit is False
    assert reason is None
    assert meta["armed"] is False
    assert meta["floor_net_roe_pct"] is None
    assert math.isclose(meta["current_net_roe_pct"], 7.9, abs_tol=0.05)


# ── 2. 首次達到 +8% 時啟動鎖利但不立即平倉 ──
@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_first_hit_8_percent_arms_without_exit(side):
    entry = 100.0
    pos = {
        "symbol": "BTC/USDT",
        "side": side,
        "entry_price": entry,
        "qty": 1.0,
        "leverage": 5.0,
        "margin": 20.0,
    }
    price_8_0 = _price_for_net_roe(side, 8.0, entry=entry, leverage=5.0)
    should_exit, reason, meta = evaluate_net_roe_profit_lock(pos, price_8_0, side)
    
    assert should_exit is False, "剛達 +8% 時不得因啟動動作本身立即平倉"
    assert meta["armed"] is True
    assert math.isclose(meta["floor_net_roe_pct"], 4.0, abs_tol=0.05)
    assert pos["profit_lock_armed"] is True
    assert math.isclose(pos["highest_net_roe"], 8.0, abs_tol=0.05)


# ── 3. 規範表格各階段案例：達到最高 ROE 後回吐至底線觸發平倉 ──
@pytest.mark.parametrize(
    ("peak_roe", "expected_floor"),
    [
        (8.0, 4.0),
        (10.0, 4.0),
        (12.0, 4.0),
        (15.0, 11.0),
        (20.0, 16.0),
        (22.0, 18.0),
        (25.0, 21.0),
        (30.0, 26.0),
        (40.0, 35.0),
        (50.0, 45.0),
    ],
)
@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_table_cases_trigger_at_exact_giveback(peak_roe, expected_floor, side):
    entry = 100.0
    pos = {
        "symbol": "BTC/USDT",
        "side": side,
        "entry_price": entry,
        "qty": 1.0,
        "leverage": 5.0,
        "margin": 20.0,
    }
    price_peak = _price_for_net_roe(side, peak_roe, entry=entry, leverage=5.0)
    evaluate_net_roe_profit_lock(pos, price_peak, side)
    
    assert pos["profit_lock_armed"] is True
    assert math.isclose(pos["highest_net_roe"], peak_roe, abs_tol=0.05)
    
    # 價格回吐至底線 expected_floor
    price_trigger = _price_for_net_roe(side, expected_floor, entry=entry, leverage=5.0)
    should_exit, reason, meta = evaluate_net_roe_profit_lock(pos, price_trigger, side)
    
    assert should_exit is True, f"最高 +{peak_roe}% 降至 +{expected_floor}% 必須觸發平倉"
    assert reason == "EXIT_BY_RATCHET_PROFIT_LOCK"
    assert math.isclose(meta["floor_net_roe_pct"], expected_floor, abs_tol=0.05)


# ── 4. 最高 +30%，目前 +28% 時不得觸發 ──
@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_highest_30_current_28_does_not_trigger(side):
    entry = 100.0
    pos = {
        "symbol": "BTC/USDT",
        "side": side,
        "entry_price": entry,
        "qty": 1.0,
        "leverage": 5.0,
        "margin": 20.0,
    }
    price_30 = _price_for_net_roe(side, 30.0, entry=entry, leverage=5.0)
    evaluate_net_roe_profit_lock(pos, price_30, side)
    
    price_28 = _price_for_net_roe(side, 28.0, entry=entry, leverage=5.0)
    should_exit, reason, meta = evaluate_net_roe_profit_lock(pos, price_28, side)
    
    assert should_exit is False, "最高 +30% 目前 +28%（底線為 26%）不得觸發"
    assert meta["armed"] is True
    assert math.isclose(meta["floor_net_roe_pct"], 26.0, abs_tol=0.05)


# ── 5. 創新高後，鎖利線立即依新高更新 ──
def test_new_high_immediately_updates_floor():
    entry = 100.0
    pos = {"symbol": "ETH/USDT", "side": "LONG", "entry_price": entry, "qty": 1.0, "leverage": 5.0, "margin": 20.0}
    
    # 1. 達 10% -> 鎖定 4%
    p10 = _price_for_net_roe("LONG", 10.0, entry=entry, leverage=5.0)
    evaluate_net_roe_profit_lock(pos, p10)
    assert math.isclose(pos["highest_net_roe"], 10.0, abs_tol=0.05)
    floor1, _ = tiered_net_roe_floor(pos["highest_net_roe"])
    assert math.isclose(floor1, 4.0, abs_tol=0.05)
    
    # 2. 創高達 16% -> 進入第二階段，鎖定立即上推至 12% (16 - 4)
    p16 = _price_for_net_roe("LONG", 16.0, entry=entry, leverage=5.0)
    evaluate_net_roe_profit_lock(pos, p16)
    assert math.isclose(pos["highest_net_roe"], 16.0, abs_tol=0.05)
    floor2, _ = tiered_net_roe_floor(pos["highest_net_roe"])
    assert math.isclose(floor2, 12.0, abs_tol=0.05)
    
    # 3. 回吐至 12.5%（高於 12%）不觸發
    p12_5 = _price_for_net_roe("LONG", 12.5, entry=entry, leverage=5.0)
    exit_12_5, _, _ = evaluate_net_roe_profit_lock(pos, p12_5)
    assert exit_12_5 is False
    
    # 4. 回吐至 12.0% 觸發
    p12_0 = _price_for_net_roe("LONG", 12.0, entry=entry, leverage=5.0)
    exit_12_0, reason, meta = evaluate_net_roe_profit_lock(pos, p12_0)
    assert exit_12_0 is True
    assert reason == "EXIT_BY_RATCHET_PROFIT_LOCK"


# ── 6. 浮點數精度與邊界值測試 ──
def test_floating_point_precision_boundaries():
    # 7.9999999% 不啟動
    assert tiered_net_roe_floor(7.9999999) == (None, 0)
    # 8.0% 啟動 -> 4.0%
    assert tiered_net_roe_floor(8.0) == (4.0, 1)
    # 邊界值相等判定
    assert has_reached_net_roe_floor(4.000000001, 4.0) is True
    assert has_reached_net_roe_floor(4.0, 4.0) is True
    assert has_reached_net_roe_floor(4.0001, 4.0) is False


# ── 7. 重啟後能恢復同一筆持倉的鎖利狀態 ──
def test_restart_state_restoration():
    assert "three_tier_net_roe_lock_state" in STATE_KEYS
    assert "highest_net_roe" in STATE_KEYS
    assert "profit_lock_armed" in STATE_KEYS
    
    original_pos = {
        "symbol": "BTC/USDT",
        "side": "LONG",
        "entry_price": 100.0,
        "qty": 1.0,
        "leverage": 5.0,
        "highest_net_roe": 22.0,
        "profit_lock_armed": True,
        "three_tier_net_roe_lock_state": {
            "peak_net_roe_pct": 22.0,
            "floor_net_roe_pct": 18.0,
            "armed": True,
            "lock_tier": 2,
        },
    }
    
    reloaded_pos = copy.deepcopy(original_pos)
    p18 = _price_for_net_roe("LONG", 18.0, entry=100.0, leverage=5.0)
    should_exit, reason, meta = evaluate_net_roe_profit_lock(reloaded_pos, p18)
    
    assert should_exit is True
    assert reason == "EXIT_BY_RATCHET_PROFIT_LOCK"
    assert math.isclose(meta["peak_net_roe_pct"], 22.0, abs_tol=0.05)
    assert math.isclose(meta["floor_net_roe_pct"], 18.0, abs_tol=0.05)


# ── 8. 新交易不繼承舊交易的最高 ROE ──
def test_new_trade_does_not_inherit_old_trade_peak():
    class DummyAccount:
        def __init__(self):
            self.position_meta = {
                "BTC/USDT": {
                    "three_tier_net_roe_lock_state": {"peak_net_roe_pct": 30.0, "armed": True},
                    "highest_net_roe": 30.0,
                    "profit_lock_armed": True,
                }
            }
            self.positions = {}

    account = DummyAccount()
    pos_meta = account.position_meta.get("BTC/USDT", {})
    pos_meta.pop("three_tier_net_roe_lock_state", None)
    pos_meta.pop("highest_net_roe", None)
    pos_meta.pop("profit_lock_armed", None)
    
    new_pos = {
        "symbol": "BTC/USDT",
        "side": "LONG",
        "entry_price": 100.0,
        "qty": 1.0,
        "leverage": 5.0,
    }
    account.positions["BTC/USDT"] = new_pos
    
    p5 = _price_for_net_roe("LONG", 5.0, entry=100.0, leverage=5.0)
    should_exit, reason, meta = evaluate_net_roe_profit_lock(new_pos, p5)
    
    assert should_exit is False
    assert meta["armed"] is False
    assert new_pos.get("highest_net_roe", 0) < 8.0


# ── 9. ExitGate.evaluate 主交易流程整合驗證 ──
def test_exit_gate_evaluate_integration_triggers_profit_lock():
    entry = 100.0
    pos = {
        "symbol": "BTC/USDT",
        "side": "LONG",
        "entry_price": entry,
        "qty": 1.0,
        "leverage": 5.0,
        "margin": 20.0,
    }
    # 1. 價格推升至 20% -> 底線為 16%
    p20 = _price_for_net_roe("LONG", 20.0, entry=entry, leverage=5.0)
    snap20 = {
        "symbol": "BTC/USDT",
        "current_price": p20,
        "atr": 1.0,
        "kc_upper": 110.0,
        "kc_middle": 100.0,
        "kc_lower": 90.0,
        "kc_middle_slope": 0.01,
        "macro_trend": "BULLISH",
    }
    should_exit_20, _, meta20 = ExitGate.evaluate(pos, snap20)
    assert should_exit_20 is False
    assert meta20.get("armed") is True
    assert math.isclose(meta20["floor_net_roe_pct"], 16.0, abs_tol=0.05)
    
    # 2. 價格回吐至 16%
    p16 = _price_for_net_roe("LONG", 16.0, entry=entry, leverage=5.0)
    snap16 = {
        "symbol": "BTC/USDT",
        "current_price": p16,
        "atr": 1.0,
        "kc_upper": 110.0,
        "kc_middle": 100.0,
        "kc_lower": 90.0,
        "kc_middle_slope": 0.01,
        "macro_trend": "BULLISH",
    }
    should_exit_16, reason_16, meta16 = ExitGate.evaluate(pos, snap16)
    
    assert should_exit_16 is True, "ExitGate.evaluate 必須直接觸發鎖利平倉"
    assert reason_16 == "EXIT_BY_RATCHET_PROFIT_LOCK"
    assert math.isclose(meta16["floor_net_roe_pct"], 16.0, abs_tol=0.05)


# ── 10. 硬止損優先級不受影響 ──
def test_hard_stop_loss_has_absolute_priority():
    entry = 100.0
    pos = {
        "symbol": "BTC/USDT",
        "side": "LONG",
        "entry_price": entry,
        "qty": 1.0,
        "leverage": 5.0,
        "margin": 20.0,
    }
    loss_price = 96.0
    snap = {
        "symbol": "BTC/USDT",
        "current_price": loss_price,
        "atr": 1.0,
        "kc_upper": 105.0,
        "kc_middle": 100.0,
        "kc_lower": 95.0,
        "kc_middle_slope": -0.01,
        "macro_trend": "BEARISH",
    }
    should_exit, reason, meta = ExitGate.evaluate(pos, snap)
    assert should_exit is True
    assert reason == "CIRCUIT_BREAKER_HARD_SL"
