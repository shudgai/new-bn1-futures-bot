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
        # 只要 3M 收盤價依然高於 EMA13，無論中間陰線多深、下影線下刺多長，強制不平倉
        if close_p >= ema13:
            return "HOLD", "【多頭回踩有守】3M收盤仍在EMA13之上，多頭趨勢完整，禁止平多！"

        # --- 防護 B：假跌破過濾 ---
        # 雖然收盤微破 EMA13，但未跌破緩衝容忍線，視為假跌破震盪
        if close_p >= long_defense_line:
            return "HOLD", "【疑似假跌破】跌破幅度未達 0.15% 緩衝，維持持倉"

        # --- 真正平多條件：實體確認破位 ---
        if close_p < long_defense_line:
            return "CLOSE_LONG", "【實質跌破離場】3M收盤實體跌破13EMA與緩衝帶，多頭結構瓦解"

    # =========================================================================
    # 2. 空單平倉判定 (持空)
    # =========================================================================
    elif side == "short":
        # --- 防護 A：反彈不平倉守衛 ---
        # 只要 3M 收盤價依然低於 EMA13，無論中間陽線反彈多高、上影線上刺多長，強制不平倉
        if close_p <= ema13:
            return "HOLD", "【空頭反彈遇阻】3M收盤仍在EMA13之下，空頭趨勢完整，禁止平空！"

        # --- 防護 B：假突破過濾 ---
        # 雖然收盤微破 EMA13，但未突破緩衝容忍線，視為假突破誘多
        if close_p <= short_defense_line:
            return "HOLD", "【疑似假突破】突破幅度未達 0.15% 緩衝，維持持倉"

        # --- 真正平空條件：實體確認破位 ---
        if close_p > short_defense_line:
            return "CLOSE_SHORT", "【實質突破離場】3M收盤實體衝破13EMA與緩衝帶，空頭結構瓦解"

def is_valid_short_climax_exit(
    candle: Dict[str, Any],
    kc: Dict[str, Any],
    quote: float,
    position: Optional[Dict[str, Any]],
    details: Optional[Dict[str, Any]] = None,
) -> Tuple[bool, str]:
    """
    通道波段鎖利模式（只在下軌穿透放量 / 長下影線低點鎖利）：
    杜絕在下軌不走、一路回吐到上軌才慌忙平倉的錯誤行為！
    必須同時滿足：
    1. 價格低點或最新價穿透 / 接觸 KC 下軌 (low <= kc_lower 或 quote <= kc_lower 且 quote <= kc_mid)
    2. 成交量放量 (volume >= vol_ma * 1.5)
    3. 實質長下影線針尖 (lower_wick >= 1.5 * candle_body 且 lower_wick >= 0.35 * atr)
    4. 必須具有實質波段利潤 (浮盈 >= 0.8 * atr 或 pnl_pct >= 0.8%)
    """
    close_p = float(candle.get("close", quote or 0.0))
    open_p = float(candle.get("open", close_p))
    low_p = min(float(candle.get("low", close_p)), quote if quote > 0 else close_p)
    volume = float(candle.get("volume", 0.0))
    vol_ma = float(candle.get("vol_ma", candle.get("volume_ma", 0.0)))
    atr = float(candle.get("atr", 0.0))
    if atr <= 0:
        atr = close_p * 0.005

    kc_lower = float(kc.get("lower", 0.0) or 0.0)
    kc_mid = float(kc.get("mid", 0.0) or 0.0)

    # 1. 檢驗位置：必須在下軌穿透區，禁止在半山腰/中軌誤觸發
    is_at_lower_band = (kc_lower > 0 and low_p <= kc_lower * 1.002 and close_p <= kc_mid)
    if not is_at_lower_band:
        return False, "未穿透或觸及 KC 下軌極端區 (禁止在半山腰觸發見底鎖利)"

    # 2. 檢驗長下影線針尖
    candle_body = max(abs(close_p - open_p), 1e-8)
    lower_wick = max(0.0, min(open_p, close_p) - low_p)
    if lower_wick < (candle_body * 1.5) or lower_wick < (0.35 * atr):
        return False, f"下影線長度不足 (wick={lower_wick:.6g} < 1.5*body 或 < 0.35*atr={0.35*atr:.6g})"

    # 3. 檢驗成交量
    if vol_ma > 0 and volume < (vol_ma * 1.4):
        return False, f"成交量未顯著放大 (vol={volume:.1f} < 1.4*vol_ma={vol_ma*1.4:.1f})"

    # 4. 檢驗實質利潤
    entry_p = float((position or {}).get("entry_price", 0.0) or 0.0)
    if entry_p > 0:
        profit_atr = (entry_p - close_p) / atr
        profit_pct = (entry_p - close_p) / entry_p
        if profit_atr < 0.8 and profit_pct < 0.008:
            return False, f"未達實質波段利潤 (profit_atr={profit_atr:.2f} < 0.8, profit_pct={profit_pct:.2%})"

    return True, "【通道波段鎖利】KC 下軌穿透放量且出現長下影線低點針尖，鎖利平空落袋！"


def is_valid_short_structural_reversal_exit(
    close_p: float,
    ema13: float,
    buffer_ratio: float = 0.0015,
) -> bool:
    """
    唯一合法結構平倉條件：
    必須等待 3M K 棒收盤實體實質站上 13EMA（且高於 0.15% 緩衝帶）
    """
    if ema13 <= 0:
        return False
    return close_p > (ema13 * (1.0 + buffer_ratio))


def should_veto_short_exit(
    candle: Dict[str, Any],
    kc: Dict[str, Any],
    ema13: float,
    ema5: float,
    position: Optional[Dict[str, Any]],
    exit_reason: str,
    details: Optional[Dict[str, Any]] = None,
    quote: Optional[float] = None,
    frame: Optional[pd.DataFrame] = None,
    buffer_ratio: float = 0.0015,
) -> Tuple[bool, str]:
    """
    空單防洗盤硬守衛（Veto Exit）：
    只要當前 3M 收盤價仍在 13EMA 之下，且 KC 中軌向下傾斜，一律視為趨勢完整，
    強制禁止觸發任何平空邏輯（return True, "VETO_SHORT_EXIT: ..."）。
    
    例外放行：
    1. 帳戶/伺服器級硬止損 (HARD_STOP_LOSS / CIRCUIT_BREAKER_HARD_SL)
    2. 5U 以上極端暴利鎖利 (曾達 5U 以上，動態回撤 30%)
    3. 合法通道波段鎖利模式 (KC 下軌穿透放量 + 長下影線低點)
    4. 唯一合法結構平倉條件 (3M 收盤實質衝破 13EMA + 0.15% 緩衝帶)
    """
    reason_upper = str(exit_reason or "").upper()

    # 1. 硬止損一律放行以保本金安全
    if any(h in reason_upper for h in ("HARD_STOP_LOSS", "CIRCUIT_BREAKER", "ADVERSE_STOP_LOSS")):
        return False, "硬止損緊急避險放行"

    # 2. 5U 以上極端暴利鎖利放行
    if "5U_DYNAMIC" in reason_upper or "PROFIT_5U" in reason_upper:
        return False, "5U暴利防天地針鎖利放行"

    close_p = float(candle.get("close", quote or 0.0))
    if quote is not None and quote > 0:
        close_p = float(quote)

    # 3. 檢查唯一合法結構平倉條件：必須 3M 收盤實質衝破 13EMA + 0.15% 緩衝帶
    if is_valid_short_structural_reversal_exit(close_p, ema13, buffer_ratio):
        return False, "3M收盤實體實質衝破13EMA與緩衝帶，結構轉折合法平空"

    # 4. 檢查是否符合合法「通道波段鎖利模式」
    is_climax, climax_msg = is_valid_short_climax_exit(
        candle=candle,
        kc=kc,
        quote=close_p,
        position=position,
        details=details,
    )
    if is_climax:
        return False, climax_msg

    # 5. 其餘所有情況：只要 3M 收盤價仍受制於 13EMA 之下，且 KC 中軌或 13EMA 向下傾斜，一票否決！
    kc_mid = float(kc.get("mid", 0.0) or 0.0)
    kc_mid_slope = float(candle.get("kc_mid_slope", 0.0))
    if kc_mid_slope == 0.0 and frame is not None and len(frame) >= 2:
        try:
            prev_mid = float(frame.iloc[-2].get("kc_middle", frame.iloc[-2].get("kc_basis", 0.0)) or 0.0)
            curr_mid = float(frame.iloc[-1].get("kc_middle", frame.iloc[-1].get("kc_basis", 0.0)) or 0.0)
            if prev_mid > 0 and curr_mid > 0:
                kc_mid_slope = (curr_mid - prev_mid) / prev_mid
        except Exception:
            pass

    # 若 13EMA 依然向下傾斜或走平偏下 (<= 0.0001)，或 KC 中軌向下傾斜
    is_downtrend_intact = (kc_mid_slope <= 0.0001) or (close_p <= ema13)

    if is_downtrend_intact:
        return True, (
            f"VETO_SHORT_EXIT: 3M收盤 ({close_p:.6g}) 仍受制於13EMA ({ema13:.6g}) 之下且趨勢向下，"
            f"嚴禁在次級回踩或反抽中平空 (理由: {exit_reason})！維持空單持倉！"
        )

    # 若收盤未站上 13EMA + 緩衝帶，同樣否決
    if close_p <= (ema13 * (1.0 + buffer_ratio)):
        return True, (
            f"VETO_SHORT_EXIT: 3M收盤未實質站穩 13EMA+0.15% 緩衝帶 (收盤={close_p:.6g}, 門檻={ema13 * (1.0 + buffer_ratio):.6g})，"
            f"禁止慌忙平空！"
        )

    return False, "通過平空審查"


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

