"""Shared net-ROE profit lock used by scan-time and final-exit validation.

Primary policy: arm at 10% peak net ROE and close after a 30% giveback
from that peak. Net ROE includes estimated round-trip fees and slippage.
Explicit legacy configs remain supported for offline callers.
"""
from dataclasses import dataclass
import math
from typing import Optional, Tuple, Dict, Any


@dataclass(frozen=True)
class NetRoeLockConfig:
    """Legacy custom tier parameters; production uses the prompt ratio rule."""
    tier1_start: float = 10.0     # 啟動門檻 (淨 ROE +10%)
    tier1_floor: float = 7.0      # 相容欄位；預設策略用峰值比例計算
    tier2_start: float = 15.0     # 舊版自訂階梯相容欄位
    tier2_giveback: float = 4.0   # 舊版自訂階梯相容欄位
    tier3_start: float = 30.0     # 舊版自訂階梯相容欄位
    tier3_giveback: float = 5.0   # 舊版自訂階梯相容欄位


DEFAULT_LOCK_CONFIG = NetRoeLockConfig()
PROMPT_LOCK_GIVEBACK_RATIO = 0.30

# 向後相容之全域別名（指向預設配置之第一階段啟動門檻）
NET_ROE_LOCK_START_PCT = DEFAULT_LOCK_CONFIG.tier1_start
NET_ROE_LOCK_GIVEBACK_PCT = DEFAULT_LOCK_CONFIG.tier2_giveback
BREAKEVEN_NET_ROE_TRIGGER_PCT = 10.0
BREAKEVEN_FAVORABLE_ATR_MULTIPLE = 0.5


def should_arm_breakeven_lock(peak_net_roe_pct, favorable_move_atr):
    """Arm only after both the net-ROE and favorable ATR thresholds are met."""
    try:
        net_roe = float(peak_net_roe_pct)
        favorable_atr = float(favorable_move_atr)
        if not math.isfinite(net_roe) or not math.isfinite(favorable_atr):
            return False
        return (
            net_roe >= BREAKEVEN_NET_ROE_TRIGGER_PCT
            and favorable_atr >= BREAKEVEN_FAVORABLE_ATR_MULTIPLE
        )
    except (TypeError, ValueError, OverflowError):
        return False


def tiered_net_roe_floor(
    peak_net_roe_pct: float,
    config: Optional[NetRoeLockConfig] = None,
) -> Tuple[Optional[float], int]:
    """
    Default: arm at +10% peak net ROE and floor at 70% of the peak.
    An explicitly supplied config retains the historical tier behavior.
    """
    # Default production policy: retain 70% of each peak once 10% is reached.
    # Passing an explicit config opts into the legacy tiered behavior.
    if config is None:
        try:
            peak = float(peak_net_roe_pct)
            if not math.isfinite(peak) or peak < DEFAULT_LOCK_CONFIG.tier1_start:
                return None, 0
            return round(peak * (1.0 - PROMPT_LOCK_GIVEBACK_RATIO), 6), 1
        except (TypeError, ValueError, OverflowError):
            return None, 0

    cfg = config
    try:
        peak = float(peak_net_roe_pct)
        if not math.isfinite(peak):
            return None, 0

        # 浮點數精度保護
        if peak < cfg.tier1_start and not math.isclose(
            peak, cfg.tier1_start, rel_tol=1e-9, abs_tol=1e-9
        ):
            return None, 0

        if peak < cfg.tier2_start and not math.isclose(
            peak, cfg.tier2_start, rel_tol=1e-9, abs_tol=1e-9
        ):
            # 第一階段
            return round(cfg.tier1_floor, 6), 1

        if peak < cfg.tier3_start and not math.isclose(
            peak, cfg.tier3_start, rel_tol=1e-9, abs_tol=1e-9
        ):
            # 第二階段：允許回吐 tier2_giveback，底線至少為 tier1_floor
            floor = max(cfg.tier1_floor, peak - cfg.tier2_giveback)
            return round(floor, 6), 2

        # 第三階段：允許回吐 tier3_giveback，且底線絕不低於第二階段在 tier3_start 建立的頂部防守線
        tier2_max_floor = cfg.tier3_start - cfg.tier2_giveback
        floor = max(tier2_max_floor, peak - cfg.tier3_giveback)
        return round(floor, 6), 3

    except (TypeError, ValueError, OverflowError):
        return None, 0


def has_reached_net_roe_floor(current_net_roe_pct: float, floor_net_roe_pct: float) -> bool:
    """Treat touching or falling below the lock floor as a trigger, with float tolerance."""
    try:
        if current_net_roe_pct is None or floor_net_roe_pct is None:
            return False
        current, floor = float(current_net_roe_pct), float(floor_net_roe_pct)
        if not math.isfinite(current) or not math.isfinite(floor):
            return False
        return current < floor or math.isclose(
            current, floor, rel_tol=1e-9, abs_tol=1e-9,
        )
    except (TypeError, ValueError, OverflowError):
        return False


def calculate_net_roe_pct(
    position: Dict[str, Any],
    price: float,
    side: Optional[str] = None,
) -> Optional[float]:
    """
    計算單筆持倉在特定價格下的真實淨 ROE 百分比 (Net ROE%)。
    扣除雙邊手續費與滑價，與全系統估算淨損益完全對齊。
    """
    try:
        if not position or price is None:
            return None
        pos_side = str(side or position.get('side', '')).upper()
        if pos_side not in ('LONG', 'SHORT'):
            return None
        entry = float(position.get('entry_price') or 0.0)
        curr_price = float(price)
        if (
            not math.isfinite(entry)
            or not math.isfinite(curr_price)
            or entry <= 0
            or curr_price <= 0
        ):
            return None

        raw_qty = position.get('qty', position.get('quantity'))
        if raw_qty is None:
            return None
        qty = abs(float(raw_qty))
        if not math.isfinite(qty) or qty <= 0:
            return None
        leverage = float(position.get('leverage') or 5.0)
        margin = float(position.get('margin') or 0.0)
        if not math.isfinite(leverage) or leverage <= 0 or not math.isfinite(margin):
            return None
        if margin <= 0:
            margin = (entry * qty) / max(leverage, 1.0)

        from core.config import SLIPPAGE_PCT, TAKER_FEE_RATE
        from core.services.exits.peak_trailing_exit import estimated_display_net_pnl

        sign = 1 if pos_side == 'LONG' else -1
        net_pnl = estimated_display_net_pnl(
            entry, curr_price, qty, sign, TAKER_FEE_RATE, SLIPPAGE_PCT,
        )
        if margin <= 0:
            return None
        net_roe = (net_pnl / margin) * 100.0
        return net_roe if math.isfinite(net_roe) else None
    except (AttributeError, TypeError, ValueError, OverflowError):
        return None


def evaluate_net_roe_profit_lock(
    position: Dict[str, Any],
    current_price: float,
    side: Optional[str] = None,
    config: Optional[NetRoeLockConfig] = None,
) -> Tuple[bool, Optional[str], Dict[str, Any]]:
    """
    單筆持倉之淨 ROE 鎖利唯一裁決官：
    - 多單 LONG 與空單 SHORT 統一規則。
    - 首次達淨 ROE +10% 啟動鎖利。
    - 持續追蹤最高淨 ROE 並創新高時更新。
    - 預設底線為峰值淨 ROE 的 70%，即峰值回吐 30% 時平倉。
    - 剛達啟動門檻時不因啟動動作立即平倉。
    - 回傳 (should_exit, reason, meta)。
    """
    if not position or current_price is None:
        return False, None, {}

    cfg = config or DEFAULT_LOCK_CONFIG
    pos_side = str(side or position.get("side", "")).upper()
    if pos_side not in ("LONG", "SHORT"):
        return False, None, {}

    current_roe = calculate_net_roe_pct(position, current_price, pos_side)
    if current_roe is None or not math.isfinite(current_roe):
        return False, None, {}

    # 持倉綁定之狀態字典
    state = position.get("three_tier_net_roe_lock_state")
    if not isinstance(state, dict):
        state = {}
        position["three_tier_net_roe_lock_state"] = state

    # 讀取既有最高 ROE
    saved_peak = float(
        state.get("peak_net_roe_pct")
        or position.get("highest_net_roe")
        or position.get("peak_net_roe_pct")
        or -9999.0
    )

    # 結合歷史最高價/最低價（若有）推算峰值
    entry_p = float(position.get("entry_price") or 0.0)
    if entry_p > 0:
        favorable_p = (
            max(float(position.get("highest_price") or entry_p), current_price)
            if pos_side == "LONG"
            else min(float(position.get("lowest_price") or entry_p), current_price)
        )
        favorable_roe = calculate_net_roe_pct(position, favorable_p, pos_side)
        if favorable_roe is not None and math.isfinite(favorable_roe):
            saved_peak = max(saved_peak, favorable_roe)

    peak_roe = max(saved_peak, current_roe)

    # 創新高即刻更新
    position["highest_net_roe"] = peak_roe
    position["peak_net_roe_pct"] = peak_roe
    state["peak_net_roe_pct"] = peak_roe

    # 檢查是否啟動（達 tier1_start）
    is_armed = bool(
        state.get("armed")
        or position.get("profit_lock_armed")
        or (peak_roe >= cfg.tier1_start)
        or math.isclose(peak_roe, cfg.tier1_start, rel_tol=1e-9, abs_tol=1e-9)
    )
    state["armed"] = is_armed
    position["profit_lock_armed"] = is_armed

    meta: Dict[str, Any] = {
        "current_net_roe_pct": round(current_roe, 4),
        "peak_net_roe_pct": round(peak_roe, 4),
        "armed": is_armed,
        "floor_net_roe_pct": None,
        "lock_tier": 0,
        "profit_lock_rule": "PEAK_NET_ROE_10PCT_ARM_30PCT_GIVEBACK",
    }

    if not is_armed:
        return False, None, meta

    # 已啟動鎖利：計算當前階梯底線
    floor, tier = tiered_net_roe_floor(peak_roe, config=config)
    state["floor_net_roe_pct"] = floor
    state["lock_tier"] = tier
    meta["floor_net_roe_pct"] = floor
    meta["lock_tier"] = tier

    if floor is not None and has_reached_net_roe_floor(current_roe, floor):
        meta["cause"] = (
            f"淨 ROE 鎖利觸發 (EXIT_BY_RATCHET_PROFIT_LOCK): "
            f"最高 ROE {peak_roe:.2f}% (第 {tier} 階段) 跌破鎖利底線 {floor:.2f}%，"
            f"當前淨 ROE {current_roe:.2f}%"
        )
        return True, "EXIT_BY_RATCHET_PROFIT_LOCK", meta

    return False, None, meta
