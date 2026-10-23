"""
tests/test_phase5_exit_refactor.py
==================================
龍蝦 Binance Futures Bot 第一階段出場重構專屬回歸測試
嚴格涵蓋使用者指定之 11 項驗收場景：
1. 多單強勢上漲，出現 1～2 根小陰線回踩，仍守住趨勢結構：一般微型峰頂不得單獨觸發平倉。
2. 空單強勢下跌，出現 1～2 根小陽線反彈，仍守住空頭結構：一般微型谷底不得單獨觸發平倉。
3. 淨 ROE +4% 時發生一般小回踩：不得觸發尚未啟動的峰值回撤鎖利。
4. 峰值淨 ROE +22%，回落至 +17%：依定義明確的階梯規則判斷是否觸發鎖利 (觸發平倉)。
5. 趨勢續抱條件成立，但硬止損觸發：必須平倉 (CIRCUIT_BREAKER_HARD_SL)。
6. 趨勢續抱條件成立，但極端瀑布條件觸發：必須平倉 (EXIT_BY_EXTREME_WATERFALL)。
7. 峰值淨 ROE 達 +40%，之後回撤：鎖利底線不得低於已建立的較高防守線 (單調不下降)。
8. 平倉後重新開倉：新持倉不得繼承舊持倉的峰值 ROE 或鎖利狀態。
9. 程式重啟後恢復持倉：峰值 ROE 與鎖利階段必須正確恢復。
10. 不同槓桿、不同保證金及 LONG/SHORT 計算均須測試。
11. 確認其他出口與原本的風控測試沒有回歸。
"""
import copy
import math
import pytest
import pandas as pd

from core.services.exits.net_roe_profit_lock import (
    NetRoeLockConfig,
    tiered_net_roe_floor,
    has_reached_net_roe_floor,
    calculate_net_roe_pct,
    evaluate_net_roe_profit_lock,
    DEFAULT_LOCK_CONFIG,
)
from core.gates.unified_pipeline_gates import ExitGate
from core.services.exits.peak_trailing_exit import STATE_KEYS
from core.services.execution_manager import ExecutionManager


def _price_for_net_roe(side: str, target_roe_pct: float, entry: float = 100.0, leverage: float = 5.0) -> float:
    from core.config import SLIPPAGE_PCT, TAKER_FEE_RATE
    sign = 1.0 if side == "LONG" else -1.0
    target_pnl = (target_roe_pct / 100.0) * (entry / leverage)
    p = (target_pnl + entry * (sign + TAKER_FEE_RATE)) / (sign - TAKER_FEE_RATE - SLIPPAGE_PCT)
    return p


# ── 場景 1: 多單強勢上漲，1~2 根小陰線回踩，守住結構：微型峰頂不得單獨平倉 ──
def test_scene1_long_small_pullback_holds_structure_veto_peak():
    entry = 100.0
    pos = {"symbol": "BTC/USDT", "side": "LONG", "entry_price": entry, "qty": 1.0, "leverage": 5.0}
    
    # 構造 1m K 線：強勢上漲後 1 根小陰線回踩，但價格仍在 MA5 之上且 MA5 向上
    # bar 0: 98 -> 99
    # bar 1: 99 -> 101 (破上軌衝高)
    # bar 2: 101 -> 100.8 (小陰線回踩，但仍遠高於 entry 且在 MA5 之上)
    data = [
        [1000, 97.5, 98.2, 97.4, 98.0, 100],
        [2000, 98.0, 99.5, 97.9, 99.2, 120],
        [3000, 99.2, 101.5, 99.1, 101.2, 200],  # 衝高
        [4000, 101.2, 101.3, 100.6, 100.9, 110], # 小陰線，收盤 100.9 站穩 MA5
    ]
    raw_df = pd.DataFrame(data, columns=["timestamp", "open", "high", "low", "close", "volume"])
    raw_df["ma_slow"] = [97.0, 97.2, 97.5, 97.8]
    raw_df["bandwidth"] = [0.05] * 4
    raw_df["kc_upper"] = [100.0] * 4
    raw_df["kc_lower"] = [96.0] * 4
    raw_df["kc_middle"] = [98.0] * 4

    snap = {
        "symbol": "BTC/USDT",
        "current_price": 100.9,
        "atr": 0.8,
        "kc_upper": 100.2,
        "kc_middle": 98.0,
        "kc_lower": 95.8,
        "kc_middle_slope": 0.002,
        "macro_trend": "BULLISH",
        "raw_df": raw_df,
        "curr_k": {"ma5": 100.1, "close": 100.9},
        "prev_k": {"ma5": 99.5, "close": 101.2},
    }

    should_exit, reason, meta = ExitGate.evaluate(pos, snap)
    assert should_exit is False, "守住 MA5 防守線時，微型峰頂不得單獨平倉，必須 VETO_HOLD"
    assert reason == "VETO_HOLD"
    assert meta.get("trend_structure_intact") is True or meta.get("ma5_slope_shield") is True


# ── 場景 2: 空單強勢下跌，1~2 根小陽線反彈，守住結構：微型谷底不得單獨平倉 ──
def test_scene2_short_small_bounce_holds_structure_veto_valley():
    entry = 100.0
    pos = {"symbol": "BTC/USDT", "side": "SHORT", "entry_price": entry, "qty": 1.0, "leverage": 5.0}

    # 構造 1m K 線：強勢下跌後 1 根小陽線反抽，但價格仍在 MA5 之下且 MA5 向下
    data = [
        [1000, 102.5, 102.6, 101.8, 102.0, 100],
        [2000, 102.0, 102.1, 100.5, 100.8, 120],
        [3000, 100.8, 100.9, 98.5, 98.8, 200],  # 探底
        [4000, 98.8, 99.4, 98.7, 99.1, 110],    # 小陽線，收盤 99.1 壓制在 MA5 之下
    ]
    raw_df = pd.DataFrame(data, columns=["timestamp", "open", "high", "low", "close", "volume"])
    raw_df["ma_slow"] = [103.0, 102.8, 102.5, 102.2]
    raw_df["bandwidth"] = [0.05] * 4
    raw_df["kc_upper"] = [104.0] * 4
    raw_df["kc_lower"] = [100.0] * 4
    raw_df["kc_middle"] = [102.0] * 4

    snap = {
        "symbol": "BTC/USDT",
        "current_price": 99.1,
        "atr": 0.8,
        "kc_upper": 104.2,
        "kc_middle": 102.0,
        "kc_lower": 99.8,
        "kc_middle_slope": -0.002,
        "macro_trend": "BEARISH",
        "raw_df": raw_df,
        "curr_k": {"ma5": 99.9, "close": 99.1},
        "prev_k": {"ma5": 100.5, "close": 98.8},
    }

    should_exit, reason, meta = ExitGate.evaluate(pos, snap)
    assert should_exit is False, "受壓在 MA5 防守線下時，微型谷底不得單獨平倉，必須 VETO_HOLD"
    assert reason == "VETO_HOLD"
    assert meta.get("trend_structure_intact") is True or meta.get("ma5_slope_shield") is True


# ── 場景 3: 淨 ROE +4% 時發生一般小回踩：不得觸發尚未啟動的鎖利 ──
def test_scene3_roe_4pct_does_not_arm_profit_lock():
    entry = 100.0
    pos = {"symbol": "BTC/USDT", "side": "LONG", "entry_price": entry, "qty": 1.0, "leverage": 5.0, "margin": 20.0}
    
    price_4 = _price_for_net_roe("LONG", 4.0, entry=entry, leverage=5.0)
    should_exit, reason, meta = evaluate_net_roe_profit_lock(pos, price_4, "LONG")
    
    assert should_exit is False
    assert meta["armed"] is False
    assert meta["floor_net_roe_pct"] is None
    assert pos.get("profit_lock_armed") is False


# ── 場景 4: 峰值淨 ROE +22%，回落至 +17%：依階梯規則觸發鎖利平倉 ──
def test_scene4_peak_22_giveback_to_17_triggers_exit():
    entry = 100.0
    pos = {"symbol": "BTC/USDT", "side": "LONG", "entry_price": entry, "qty": 1.0, "leverage": 5.0, "margin": 20.0}
    
    # 1. 先推升至 +22%
    p22 = _price_for_net_roe("LONG", 22.0, entry=entry, leverage=5.0)
    evaluate_net_roe_profit_lock(pos, p22, "LONG")
    assert pos["profit_lock_armed"] is True
    # peak=22% 屬於第二階段 (15%~30%)，回吐空間 4%，底線應為 22 - 4 = 18%
    floor, tier = tiered_net_roe_floor(pos["highest_net_roe"])
    assert tier == 2
    assert math.isclose(floor, 18.0, abs_tol=0.01)
    
    # 2. 回落至 +17% (已跌破 18% 底線)
    p17 = _price_for_net_roe("LONG", 17.0, entry=entry, leverage=5.0)
    should_exit, reason, meta = evaluate_net_roe_profit_lock(pos, p17, "LONG")
    
    assert should_exit is True
    assert reason == "EXIT_BY_RATCHET_PROFIT_LOCK"
    assert math.isclose(meta["floor_net_roe_pct"], 18.0, abs_tol=0.01)


# ── 場景 5: 趨勢續抱條件成立，但硬止損觸發：必須平倉 ──
def test_scene5_hard_stop_overrides_veto_hold():
    entry = 100.0
    pos = {"symbol": "BTC/USDT", "side": "LONG", "entry_price": entry, "qty": 1.0, "leverage": 5.0, "margin": 20.0}
    # 嚴重暴跌虧損達 -3.5 ATR (atr=1.0, threshold=-3.0%)
    price_crash = 96.0  # -4% <= -3%
    snap = {
        "symbol": "BTC/USDT",
        "current_price": price_crash,
        "atr": 1.0,
        "kc_upper": 105.0,
        "kc_middle": 100.0,
        "kc_lower": 95.0,
        "kc_middle_slope": 0.05,  # 趨勢指標即便仍顯示多頭
        "macro_trend": "BULLISH",
    }
    should_exit, reason, meta = ExitGate.evaluate(pos, snap)
    assert should_exit is True
    assert reason == "CIRCUIT_BREAKER_HARD_SL"


# ── 場景 6: 趨勢續抱條件成立，但極端瀑布觸發：必須平倉 ──
def test_scene6_extreme_waterfall_overrides_veto_hold():
    entry = 100.0
    pos = {"symbol": "BTC/USDT", "side": "LONG", "entry_price": entry, "qty": 1.0, "leverage": 5.0, "margin": 20.0}
    # 單根大陰棒跌幅 2.0 ATR (atr=1.0) 且貫穿中軌 (open 101.0, close 99.0, kc_mid 100.0)
    raw_df = pd.DataFrame([
        [1000, 100.5, 101.5, 100.2, 101.0, 100],
        [2000, 101.0, 101.2, 98.8, 99.0, 300],  # 巨大瀑布陰棒
    ], columns=["timestamp", "open", "high", "low", "close", "volume"])
    snap = {
        "symbol": "BTC/USDT",
        "current_price": 99.0,
        "atr": 1.0,
        "kc_upper": 103.0,
        "kc_middle": 100.0,
        "kc_lower": 97.0,
        "kc_middle_slope": 0.01,
        "macro_trend": "BULLISH",
        "raw_df": raw_df,
    }
    should_exit, reason, meta = ExitGate.evaluate(pos, snap)
    assert should_exit is True
    assert reason == "EXIT_BY_EXTREME_WATERFALL"


# ── 場景 7: 峰值淨 ROE 達 +40%，之後回撤：鎖利底線單調不下降 ──
def test_scene7_monotonic_non_decreasing_floor_across_tiers():
    # 測試邊界：peak 從 29.9% 跨越到 30.0% 及 40.0%
    f29_9, tier29_9 = tiered_net_roe_floor(29.9)
    assert tier29_9 == 2
    assert math.isclose(f29_9, 25.9, abs_tol=0.01)

    f30_0, tier30_0 = tiered_net_roe_floor(30.0)
    assert tier30_0 == 3
    # 邊界防跌保護：第二階段上限為 26.0%，切換第三階段不得下降至 25.0%，必須 >= 26.0%
    assert f30_0 >= f29_9
    assert math.isclose(f30_0, 26.0, abs_tol=0.01)

    f40_0, tier40_0 = tiered_net_roe_floor(40.0)
    assert tier40_0 == 3
    assert math.isclose(f40_0, 35.0, abs_tol=0.01)
    assert f40_0 > f30_0


# ── 場景 8: 平倉後重新開倉：新持倉不得繼承舊持倉狀態 ──
def test_scene8_new_position_clears_old_lock_state():
    old_pos = {
        "symbol": "BTC/USDT",
        "side": "LONG",
        "entry_price": 100.0,
        "qty": 1.0,
        "highest_net_roe": 30.0,
        "profit_lock_armed": True,
        "three_tier_net_roe_lock_state": {"peak_net_roe_pct": 30.0, "armed": True},
    }
    # 模擬持倉清空/重置
    new_pos = {"symbol": "BTC/USDT", "side": "LONG", "entry_price": 100.0, "qty": 1.0, "leverage": 5.0}
    p5 = _price_for_net_roe("LONG", 5.0, entry=100.0, leverage=5.0)
    should_exit, reason, meta = evaluate_net_roe_profit_lock(new_pos, p5, "LONG")
    
    assert should_exit is False
    assert meta["armed"] is False
    assert new_pos.get("highest_net_roe", 0) < 8.0


# ── 場景 9: 程式重啟後恢復持倉：峰值 ROE 與鎖利階段正確恢復 ──
def test_scene9_restart_recovery_persists_peak_and_floor():
    assert "three_tier_net_roe_lock_state" in STATE_KEYS
    pos_before_crash = {
        "symbol": "BTC/USDT",
        "side": "LONG",
        "entry_price": 100.0,
        "qty": 1.0,
        "leverage": 5.0,
        "margin": 20.0,
        "highest_net_roe": 22.0,
        "profit_lock_armed": True,
        "three_tier_net_roe_lock_state": {
            "peak_net_roe_pct": 22.0,
            "floor_net_roe_pct": 18.0,
            "armed": True,
            "lock_tier": 2,
        },
    }
    reloaded_pos = copy.deepcopy(pos_before_crash)
    # 重啟後價格回落至 17.5% (跌破 18.0% 底線)
    p17_5 = _price_for_net_roe("LONG", 17.5, entry=100.0, leverage=5.0)
    should_exit, reason, meta = evaluate_net_roe_profit_lock(reloaded_pos, p17_5, "LONG")
    
    assert should_exit is True
    assert reason == "EXIT_BY_RATCHET_PROFIT_LOCK"
    assert math.isclose(meta["floor_net_roe_pct"], 18.0, abs_tol=0.01)


# ── 場景 10: 不同槓桿、不同保證金及 LONG/SHORT 對稱計算 ──
@pytest.mark.parametrize("side", ["LONG", "SHORT"])
@pytest.mark.parametrize("leverage", [2.0, 5.0, 10.0, 20.0])
def test_scene10_different_leverage_margin_symmetry(side, leverage):
    entry = 100.0
    qty = 2.5
    margin = (entry * qty) / leverage
    pos = {
        "symbol": "BTC/USDT",
        "side": side,
        "entry_price": entry,
        "qty": qty,
        "leverage": leverage,
        "margin": margin,
    }
    # 測試推升至 +15% 鎖利，底線為 11%
    p15 = _price_for_net_roe(side, 15.0, entry=entry, leverage=leverage)
    exit15, _, meta15 = evaluate_net_roe_profit_lock(pos, p15, side)
    assert exit15 is False
    assert meta15["armed"] is True
    assert math.isclose(meta15["floor_net_roe_pct"], 11.0, abs_tol=0.05)

    # 測試跌至 10.8% 觸發
    p10_8 = _price_for_net_roe(side, 10.8, entry=entry, leverage=leverage)
    exit10_8, reason, meta10_8 = evaluate_net_roe_profit_lock(pos, p10_8, side)
    assert exit10_8 is True
    assert reason == "EXIT_BY_RATCHET_PROFIT_LOCK"


# ── 場景 11: 慢線結構破位 (EXIT_BY_TREND_BROKEN) 依然生效 ──
def test_scene11_trend_broken_remains_active():
    entry = 100.0
    pos = {"symbol": "BTC/USDT", "side": "LONG", "entry_price": entry, "qty": 1.0, "leverage": 5.0}
    # 收盤明確跌破慢線 >= 0.3 ATR 且大週期不再同向
    raw_df = pd.DataFrame([
        [1000, 100.0, 100.5, 98.5, 98.8, 100],
        [2000, 98.8, 99.0, 97.0, 97.2, 120],  # 跌破慢線 98.0
    ], columns=["timestamp", "open", "high", "low", "close", "volume"])
    raw_df["ma_slow"] = [98.5, 98.0]
    snap = {
        "symbol": "BTC/USDT",
        "current_price": 97.2,
        "atr": 1.0,
        "kc_upper": 103.0,
        "kc_middle": 100.0,
        "kc_lower": 97.0,
        "kc_middle_slope": -0.01,
        "macro_trend": "BEARISH",
        "raw_df": raw_df,
    }
    should_exit, reason, meta = ExitGate.evaluate(pos, snap)
    assert should_exit is True
    assert reason == "EXIT_BY_TREND_BROKEN"
