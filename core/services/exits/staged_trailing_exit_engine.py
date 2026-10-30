"""
Staged Trailing Exit Engine (多階段階梯保本 + 動態移動止盈引擎)
適用於 1000PEPE/USDT (做多/牛市) 與 龍蝦/USDT (做空/熊市)

【四階段規則 (防守價只進不退)】：
- 階段 1 (零風險保本): 浮盈 >= +4% -> 防守價移至 開倉價 ± 0.3% (涵蓋手續費)
- 階段 2 (小利鎖定):   浮盈 >= +10% -> 防守價移至 開倉價 ± 5%
- 階段 3 (中利鎖定):   浮盈 >= +15% -> 防守價移至 開倉價 ± 10%
- 階段 4 (大波段移動止盈): 浮盈 >= +20% -> 啟動 Trailing Stop，自最高價回吐 5% (多單) 或 自最低價反彈 5% (空單) 立即市價全平

【大週期反轉強制平倉】：
- 多單持有中，若大週期轉為 BEARISH -> 立即市價平多清場
- 空單持有中，若大週期轉為 BULLISH -> 立即市價平空清場
"""
from typing import Dict, Any, Tuple, Optional
import math


def evaluate_staged_trailing_exit(
    position: Dict[str, Any],
    current_price: float,
    macro_trend: str,
) -> Tuple[bool, Optional[str], Dict[str, Any]]:
    """
    裁決已建立之持倉是否觸發階梯保本、移動止盈或大週期反轉平倉。
    回傳: (should_exit, reason, meta)
    """
    if not position or current_price is None or current_price <= 0:
        return False, None, {}

    side = str(position.get("side", "")).upper()
    if side not in ("LONG", "SHORT"):
        return False, None, {}

    entry_p = float(position.get("entry_price", 0.0) or 0.0)
    if entry_p <= 0:
        return False, None, {}

    macro = str(macro_trend or "NEUTRAL").upper()

    # ══════════════════════════════════════════════════════════════════
    # 規則 1：大週期反轉強制清場 (Macro Trend Reversal Mandatory Exit)
    # ══════════════════════════════════════════════════════════════════
    if side == "LONG" and macro == "BEARISH":
        return True, "EXIT_BY_MACRO_TREND_REVERSAL_TO_BEAR", {
            "side": side,
            "entry_price": entry_p,
            "current_price": current_price,
            "macro_trend": macro,
            "detail": "大週期由牛轉熊，多單無條件立即市價全平！",
        }
    elif side == "SHORT" and macro == "BULLISH":
        return True, "EXIT_BY_MACRO_TREND_REVERSAL_TO_BULL", {
            "side": side,
            "entry_price": entry_p,
            "current_price": current_price,
            "macro_trend": macro,
            "detail": "大週期由熊轉牛，空單無條件立即市價全平！",
        }

    # ══════════════════════════════════════════════════════════════════
    # 規則 2：多階段階梯保本與防守價只進不退 (Monotonic Trailing Ladder)
    # ══════════════════════════════════════════════════════════════════
    # 計算當前浮盈比例 (百分比)
    if side == "LONG":
        cur_gain_pct = ((current_price - entry_p) / entry_p) * 100.0
        # 追蹤歷史最高價
        peak_p = max(float(position.get("peak_price", entry_p) or entry_p), current_price)
        position["peak_price"] = peak_p
        peak_gain_pct = ((peak_p - entry_p) / entry_p) * 100.0

        current_stage = int(position.get("trailing_stage", 0) or 0)
        curr_stop_p = float(position.get("trailing_stop_price", 0.0) or 0.0)

        # 階梯只進不退
        if peak_gain_pct >= 20.0:
            new_stage = 4
            new_stop_p = max(curr_stop_p, entry_p * 1.10)
        elif peak_gain_pct >= 15.0:
            new_stage = max(current_stage, 3)
            new_stop_p = max(curr_stop_p, entry_p * 1.10)
        elif peak_gain_pct >= 10.0:
            new_stage = max(current_stage, 2)
            new_stop_p = max(curr_stop_p, entry_p * 1.05)
        elif peak_gain_pct >= 4.0:
            new_stage = max(current_stage, 1)
            new_stop_p = max(curr_stop_p, entry_p * 1.003)
        else:
            new_stage = current_stage
            new_stop_p = curr_stop_p

        position["trailing_stage"] = new_stage
        position["trailing_stop_price"] = new_stop_p

        # 階段 4：啟動移動止盈 (Trailing Stop)，自最高價回吐達 5% 立即平倉
        if new_stage == 4:
            drawback_pct = ((peak_p - current_price) / peak_p) * 100.0
            if drawback_pct >= 5.0 or (new_stop_p > 0 and current_price <= new_stop_p):
                return True, "EXIT_BY_STAGE4_TRAILING_STOP_5PCT", {
                    "side": side,
                    "stage": 4,
                    "peak_price": peak_p,
                    "current_price": current_price,
                    "drawback_pct": round(drawback_pct, 2),
                    "detail": f"多單觸發階段4移動止盈：最高價 {peak_p:.6g}，自高點回吐 {drawback_pct:.2f}% >= 5%，落袋平多！",
                }

        # 階段 1~3：跌破階梯防守價立即平倉
        if new_stop_p > 0 and current_price <= new_stop_p:
            stage_names = {1: "階段1(保本+0.3%)", 2: "階段2(小利+5%)", 3: "階段3(中利+10%)"}
            return True, f"EXIT_BY_STAGE{new_stage}_LADDER_STOP", {
                "side": side,
                "stage": new_stage,
                "stop_price": new_stop_p,
                "current_price": current_price,
                "detail": f"多單觸發{stage_names.get(new_stage, '')}防守價 {new_stop_p:.6g}，保利平倉！",
            }

    elif side == "SHORT":
        cur_gain_pct = ((entry_p - current_price) / entry_p) * 100.0
        # 追蹤歷史最低價
        trough_p = min(float(position.get("trough_price", entry_p) or entry_p), current_price)
        position["trough_price"] = trough_p
        peak_gain_pct = ((entry_p - trough_p) / entry_p) * 100.0

        current_stage = int(position.get("trailing_stage", 0) or 0)
        curr_stop_p = float(position.get("trailing_stop_price", float("inf")) or float("inf"))

        # 階梯只進不退（空單止損向下移動）
        if peak_gain_pct >= 20.0:
            new_stage = 4
            new_stop_p = min(curr_stop_p, entry_p * 0.90)
        elif peak_gain_pct >= 15.0:
            new_stage = max(current_stage, 3)
            new_stop_p = min(curr_stop_p, entry_p * 0.90)
        elif peak_gain_pct >= 10.0:
            new_stage = max(current_stage, 2)
            new_stop_p = min(curr_stop_p, entry_p * 0.95)
        elif peak_gain_pct >= 4.0:
            new_stage = max(current_stage, 1)
            new_stop_p = min(curr_stop_p, entry_p * 0.997)
        else:
            new_stage = current_stage
            new_stop_p = curr_stop_p

        position["trailing_stage"] = new_stage
        position["trailing_stop_price"] = new_stop_p

        # 階段 4：啟動移動止盈 (Trailing Stop)，自最低價反彈達 5% 立即平倉
        if new_stage == 4:
            rebound_pct = ((current_price - trough_p) / trough_p) * 100.0
            if rebound_pct >= 5.0 or (new_stop_p < float("inf") and current_price >= new_stop_p):
                return True, "EXIT_BY_STAGE4_TRAILING_STOP_5PCT", {
                    "side": side,
                    "stage": 4,
                    "trough_price": trough_p,
                    "current_price": current_price,
                    "rebound_pct": round(rebound_pct, 2),
                    "detail": f"空單觸發階段4移動止盈：最低價 {trough_p:.6g}，自低點反彈 {rebound_pct:.2f}% >= 5%，落袋平空！",
                }

        # 階段 1~3：突破階梯防守價立即平倉
        if new_stop_p < float("inf") and current_price >= new_stop_p:
            stage_names = {1: "階段1(保本-0.3%)", 2: "階段2(小利-5%)", 3: "階段3(中利-10%)"}
            return True, f"EXIT_BY_STAGE{new_stage}_LADDER_STOP", {
                "side": side,
                "stage": new_stage,
                "stop_price": new_stop_p,
                "current_price": current_price,
                "detail": f"空單觸發{stage_names.get(new_stage, '')}防守價 {new_stop_p:.6g}，保利平倉！",
            }

    return False, None, {}

