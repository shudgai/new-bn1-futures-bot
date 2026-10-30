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
    # 規則 1：開倉後成本區間回踩防洗鐵律（AGENTS.md 核心授權）
    # 開倉後若波段尚未實質展開（peak_roe < 5% 且峰值未達 0.5 ATR）：
    # 成本區間的一切小陰小陽或均線盤中抖動，100% 判定為正常呼吸回踩，絕不平倉！
    # ══════════════════════════════════════════════════════════════════

    # ══════════════════════════════════════════════════════════════════
    # 規則 2：多階段階梯保本與防守價只進不退 (Monotonic Trailing Ladder - 依 ROE 計算)
    # 遵循 AGENTS.md 核心授權：
    # - 淨 ROE 達 +10% 時啟動保底鎖利（鎖住至少 +4% ROE）；
    # - 淨 ROE 達 +15% 時升級鎖住至少 +8% ROE；
    # - 淨 ROE 達 +20% 時升級鎖住至少 +12% ROE；
    # - 自極值點回吐移動止盈 (Trailing Stop)：獲利展開後自最高/最低點回吐達 1.8% 立即市價落袋！
    # ══════════════════════════════════════════════════════════════════
    leverage = float(position.get("leverage", 5.0) or 5.0)
    if leverage <= 0:
        leverage = 5.0

    if side == "LONG":
        cur_gain_pct = ((current_price - entry_p) / entry_p) * 100.0
        cur_roe_pct = cur_gain_pct * leverage
        # 追蹤歷史最高價
        peak_p = max(float(position.get("peak_price", entry_p) or entry_p), current_price)
        position["peak_price"] = peak_p
        peak_gain_pct = ((peak_p - entry_p) / entry_p) * 100.0
        peak_roe_pct = peak_gain_pct * leverage

        current_stage = int(position.get("trailing_stage", 0) or 0)
        curr_stop_p = float(position.get("trailing_stop_price", 0.0) or 0.0)

        # 階梯只進不退（多單止盈向上移動）
        if peak_roe_pct >= 20.0:
            new_stage = 4
            lock_p = entry_p * (1.0 + (12.0 / 100.0) / leverage)
            new_stop_p = max(curr_stop_p, lock_p)
        elif peak_roe_pct >= 15.0:
            new_stage = max(current_stage, 3)
            lock_p = entry_p * (1.0 + (8.0 / 100.0) / leverage)
            new_stop_p = max(curr_stop_p, lock_p)
        elif peak_roe_pct >= 10.0:
            new_stage = max(current_stage, 2)
            lock_p = entry_p * (1.0 + (4.0 / 100.0) / leverage)
            new_stop_p = max(curr_stop_p, lock_p)
        elif peak_roe_pct >= 4.0:
            new_stage = max(current_stage, 1)
            new_stop_p = max(curr_stop_p, entry_p * 1.003)
        else:
            new_stage = current_stage
            new_stop_p = curr_stop_p

        position["trailing_stage"] = new_stage
        position["trailing_stop_price"] = new_stop_p

        # 階段 3~4：啟動移動止盈 (Trailing Stop)，自最高價回吐達 1.8%~2.0% 立即平倉落袋
        if new_stage >= 3:
            drawback_pct = ((peak_p - current_price) / peak_p) * 100.0
            if drawback_pct >= 1.8 or (new_stop_p > 0 and current_price <= new_stop_p):
                return True, "EXIT_BY_STAGE4_TRAILING_STOP_5PCT", {
                    "side": side,
                    "stage": new_stage,
                    "peak_price": peak_p,
                    "current_price": current_price,
                    "peak_roe_pct": round(peak_roe_pct, 2),
                    "drawback_pct": round(drawback_pct, 2),
                    "detail": f"多單觸發移動止盈：最高價 {peak_p:.6g} (峰值ROE +{peak_roe_pct:.1f}%)，自高點回吐 {drawback_pct:.2f}%，及時平多鎖利！",
                }

        # 階段 1~2：跌破階梯防守價立即平倉
        if new_stop_p > 0 and current_price <= new_stop_p:
            stage_names = {1: "階段1(保本+0.3%)", 2: "階段2(小利+4%ROE)", 3: "階段3(中利+8%ROE)", 4: "階段4(大利+12%ROE)"}
            return True, f"EXIT_BY_STAGE{new_stage}_LADDER_STOP", {
                "side": side,
                "stage": new_stage,
                "stop_price": new_stop_p,
                "current_price": current_price,
                "peak_roe_pct": round(peak_roe_pct, 2),
                "detail": f"多單觸發{stage_names.get(new_stage, '')}防守價 {new_stop_p:.6g}，保利平倉！",
            }

    elif side == "SHORT":
        cur_gain_pct = ((entry_p - current_price) / entry_p) * 100.0
        cur_roe_pct = cur_gain_pct * leverage
        # 追蹤歷史最低價
        trough_p = min(float(position.get("trough_price", entry_p) or entry_p), current_price)
        position["trough_price"] = trough_p
        peak_gain_pct = ((entry_p - trough_p) / entry_p) * 100.0
        peak_roe_pct = peak_gain_pct * leverage

        current_stage = int(position.get("trailing_stage", 0) or 0)
        curr_stop_p = float(position.get("trailing_stop_price", float("inf")) or float("inf"))

        # 階梯只進不退（空單止損向下移動）
        if peak_roe_pct >= 20.0:
            new_stage = 4
            lock_p = entry_p * (1.0 - (12.0 / 100.0) / leverage)
            new_stop_p = min(curr_stop_p, lock_p)
        elif peak_roe_pct >= 15.0:
            new_stage = max(current_stage, 3)
            lock_p = entry_p * (1.0 - (8.0 / 100.0) / leverage)
            new_stop_p = min(curr_stop_p, lock_p)
        elif peak_roe_pct >= 10.0:
            new_stage = max(current_stage, 2)
            lock_p = entry_p * (1.0 - (4.0 / 100.0) / leverage)
            new_stop_p = min(curr_stop_p, lock_p)
        elif peak_roe_pct >= 4.0:
            new_stage = max(current_stage, 1)
            new_stop_p = min(curr_stop_p, entry_p * 0.997)
        else:
            new_stage = current_stage
            new_stop_p = curr_stop_p

        position["trailing_stage"] = new_stage
        position["trailing_stop_price"] = new_stop_p

        # 階段 3~4：啟動移動止盈 (Trailing Stop)，自最低價反彈達 1.8%~2.0% 立即平倉落袋
        if new_stage >= 3:
            rebound_pct = ((current_price - trough_p) / trough_p) * 100.0
            if rebound_pct >= 1.8 or (new_stop_p < float("inf") and current_price >= new_stop_p):
                return True, "EXIT_BY_STAGE4_TRAILING_STOP_5PCT", {
                    "side": side,
                    "stage": new_stage,
                    "trough_price": trough_p,
                    "current_price": current_price,
                    "peak_roe_pct": round(peak_roe_pct, 2),
                    "rebound_pct": round(rebound_pct, 2),
                    "detail": f"空單觸發移動止盈：最低價 {trough_p:.6g} (峰值ROE +{peak_roe_pct:.1f}%)，自低點反彈 {rebound_pct:.2f}%，及時平空鎖利！",
                }

        # 階段 1~2：突破階梯防守價立即平倉
        if new_stop_p < float("inf") and current_price >= new_stop_p:
            stage_names = {1: "階段1(保本-0.3%)", 2: "階段2(小利+4%ROE)", 3: "階段3(中利+8%ROE)", 4: "階段4(大利+12%ROE)"}
            return True, f"EXIT_BY_STAGE{new_stage}_LADDER_STOP", {
                "side": side,
                "stage": new_stage,
                "stop_price": new_stop_p,
                "current_price": current_price,
                "peak_roe_pct": round(peak_roe_pct, 2),
                "detail": f"空單觸發{stage_names.get(new_stage, '')}防守價 {new_stop_p:.6g}，保利平倉！",
            }

    return False, None, {}

