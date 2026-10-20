"""
AntiFakeoutExitGuard (防假突破與回踩震盪持倉保護服務)

核心鐵律：
要做到「回踩不平倉」與「假突破不平倉」，切斷任何以「盤中影線穿透」或「微幅回吐」為條件的平倉邏輯，
建立嚴格的實體收盤（Candle Close）確認機制與緩衝防護帶（Buffer Zone = 0.15%）。

一、多空雙向防護規則設計：
1. 多單（持多保護）：
   - 回踩不平倉：盤中收陰、回測 EMA5/EMA13 或 KC 中軌，只要收盤未實質跌破 13EMA，一律嚴禁平倉。
   - 假跌破不平倉：盤中下影線短暫跌破 13EMA，但收盤收腳彈回 13EMA 之上，判定為假摔洗盤，禁止平倉。
   - 實質破位平倉：必須等待 K 棒收盤確認，且收盤價低於 EMA13 * (1 - 0.0015)，結構確認破壞才離場。

2. 空單（持空保護）：
   - 反彈不平倉：盤中收陽、反彈摸 EMA5/EMA13 或 KC 中軌，只要收盤未實質衝破 13EMA，一律嚴禁平倉。
   - 假突破不平倉：盤中上影線短暫衝破 13EMA，但收盤壓回 13EMA 之下，判定為假抽誘多，禁止平倉。
   - 實質破位平倉：必須等待 K 棒收盤確認，且收盤價高於 EMA13 * (1 + 0.0015)，結構確認破壞才離場。

3. 極端暴利鎖利守衛：
   - 浮盈曾達 5U 以上，動態回撤超過 30%（利潤回落至 70%）時，動態回撤鎖利離場。
"""

from __future__ import annotations

import math
from typing import Any, Dict, Optional, Tuple
import pandas as pd


def check_exit_with_anti_fakeout(
    candle: Dict[str, Any],
    kc: Dict[str, Any],
    ema5: float,
    ema13: float,
    position: Optional[Dict[str, Any]],
    current_pnl_usdt: Optional[float] = None,
    buffer_ratio: float = 0.0015,
) -> Tuple[str, str]:
    """
    持倉風控與平倉函式：
    - 免疫回踩震盪
    - 免疫假突破/假跌破 (需收盤確認 + 緩衝帶)
    - 僅在結構實質破壞或大額暴利保護時平倉
    """
    if position is None:
        return "HOLD", "無持倉"

    side = str(position.get("side", "")).lower()
    if side not in ("long", "short"):
        return "HOLD", "持倉方向無效"

    close_p = float(candle.get("close", 0.0))
    low_p = float(candle.get("low", close_p))
    high_p = float(candle.get("high", close_p))

    # 設定防假突破過濾緩衝 (0.15% 容許區間，避免貼線摩擦誤平)
    long_defense_line = ema13 * (1.0 - buffer_ratio)
    short_defense_line = ema13 * (1.0 + buffer_ratio)

    # 計算或更新未實現盈虧與峰值利潤
    if current_pnl_usdt is None:
        entry_p = float(position.get("entry_price", 0.0) or 0.0)
        pos_amt = abs(float(position.get("amount", 0.0) or position.get("size", 0.0) or 0.0))
        if entry_p > 0 and pos_amt > 0 and close_p > 0:
            if side == "long":
                current_pnl_usdt = (close_p - entry_p) * pos_amt
            else:
                current_pnl_usdt = (entry_p - close_p) * pos_amt
        else:
            current_pnl_usdt = 0.0

    max_profit = float(position.get("max_profit", 0.0) or 0.0)
    if current_pnl_usdt > max_profit:
        max_profit = current_pnl_usdt
        position["max_profit"] = max_profit

    # =========================================================================
    # 0. 極端暴利鎖利守衛 (防天地針插針利潤全吐光)
    # =========================================================================
    if max_profit >= 5.0:
        if current_pnl_usdt <= max_profit * 0.70:
            return "CLOSE_POSITION", "浮盈曾達 5U 以上，動態回撤鎖利離場"

    # =========================================================================
    # 1. 多單平倉判定 (持多)
    # =========================================================================
    if side == "long":
        # --- 防護 A：回踩不平倉守衛 ---
        # 只要收盤價依然高於 EMA13，無論中間陰線多深、下影線下刺多長，強制不平倉
        if close_p >= ema13:
            return "HOLD", "【多頭回踩有守】收盤仍在EMA13之上，禁止平多"

        # --- 防護 B：假跌破過濾 ---
        # 雖然收盤微破 EMA13，但未跌破緩衝容忍線，視為假跌破震盪
        if close_p >= long_defense_line:
            return "HOLD", "【疑似假跌破】跌破幅度未達 0.15% 緩衝，維持持倉"

        # --- 真正平多條件：實體確認破位 ---
        if close_p < long_defense_line:
            return "CLOSE_LONG", "【實質跌破離場】收盤實體跌破13EMA與緩衝帶，多頭結構瓦解"

    # =========================================================================
    # 2. 空單平倉判定 (持空)
    # =========================================================================
    elif side == "short":
        # --- 防護 A：反彈不平倉守衛 ---
        # 只要收盤價依然低於 EMA13，無論中間陽線反彈多高、上影線上刺多長，強制不平倉
        if close_p <= ema13:
            return "HOLD", "【空頭反彈遇阻】收盤仍在EMA13之下，禁止平空"

        # --- 防護 B：假突破過濾 ---
        # 雖然收盤微破 EMA13，但未突破緩衝容忍線，視為假突破誘多
        if close_p <= short_defense_line:
            return "HOLD", "【疑似假突破】突破幅度未達 0.15% 緩衝，維持持倉"

        # --- 真正平空條件：實體確認破位 ---
        if close_p > short_defense_line:
            return "CLOSE_SHORT", "【實質突破離場】收盤實體衝破13EMA與緩衝帶，空頭結構瓦解"

    return "HOLD", "持倉觀望中"


class AntiFakeoutExitGuard:
    """Anti-fakeout and structural exit guard manager."""

    BUFFER_RATIO = 0.0015  # 0.15% 防假突破緩衝區間

    @classmethod
    def evaluate(
        cls,
        position: Dict[str, Any],
        frame: Optional[pd.DataFrame],
        quote: Optional[float] = None,
        current_pnl_usdt: Optional[float] = None,
    ) -> Tuple[bool, str, Dict[str, Any]]:
        """
        全視角持倉防禦與平倉評估：
        - 盤中影線穿透 100% 免疫
        - 必須以最新收盤價或已收盤棒確認
        - 返回: (should_exit: bool, reason: str, details: dict)
        """
        if not position:
            return False, "HOLD_NO_POSITION", {}

        side = str(position.get("side", "")).lower()
        if side not in ("long", "short"):
            return False, "HOLD_INVALID_SIDE", {}

        # 讀取 K 線與技術指標
        if frame is None or frame.empty:
            return False, "HOLD_NO_FRAME", {}

        # 尋找已收盤的最新 K 棒或當前 K 棒
        try:
            from core.services.candle_data import closed_entry_candles
            closed = closed_entry_candles(frame)
            if not closed.empty:
                eval_candle = closed.iloc[-1].to_dict()
            else:
                eval_candle = frame.iloc[-1].to_dict()
        except Exception:
            eval_candle = frame.iloc[-1].to_dict()

        close_p = float(eval_candle.get("close", quote or 0.0))
        if close_p <= 0 and quote and quote > 0:
            close_p = float(quote)

        # 均線指標取得：優先使用 ema13 或 ma15
        ema13 = float(
            eval_candle.get("ema13", eval_candle.get("ma15", eval_candle.get("kc_middle", 0.0))) or 0.0
        )
        ema5 = float(
            eval_candle.get("ema5", eval_candle.get("ma5", eval_candle.get("_wild_ma5", 0.0))) or 0.0
        )
        kc = {
            "mid": float(eval_candle.get("kc_middle", eval_candle.get("kc_basis", 0.0)) or 0.0),
            "upper": float(eval_candle.get("kc_upper", 0.0) or 0.0),
            "lower": float(eval_candle.get("kc_lower", 0.0) or 0.0),
        }

        if ema13 <= 0:
            # 若無均線數據，暫不平倉保護
            return False, "HOLD_EMA13_UNAVAILABLE", {}

        action, reason = check_exit_with_anti_fakeout(
            candle=eval_candle,
            kc=kc,
            ema5=ema5,
            ema13=ema13,
            position=position,
            current_pnl_usdt=current_pnl_usdt,
            buffer_ratio=cls.BUFFER_RATIO,
        )

        details = {
            "side": side.upper(),
            "close": close_p,
            "ema13": ema13,
            "ema5": ema5,
            "kc": kc,
            "buffer_ratio": cls.BUFFER_RATIO,
            "action": action,
            "reason": reason,
        }

        if action in ("CLOSE_POSITION", "CLOSE_LONG", "CLOSE_SHORT"):
            return True, reason, details

        return False, reason, details
