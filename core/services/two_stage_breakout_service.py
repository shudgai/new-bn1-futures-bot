"""
Two-Stage Breakout Entry Service (破軌兩段式進場服務)

【核心規則與權責】
1. 強突破 (一根就進: BREAK_STRONG):
   - 收盤在 KC 軌道外 (多單 close > kc_upper, 空單 close < kc_lower)
   - 同色 K 棒 (多單 close > open, 空單 close < open)
   - 新一輪突破: 前一根收盤在軌道內 (多單 prev_close <= prev_kc_upper, 空單 prev_close >= prev_kc_lower)
   - 實體夠大: 實體 >= 前 10 根平均實體的 1.5 倍 (參數可由 config 覆蓋)
   - 實體占比: body / rng >= 60% (參數可由 config 覆蓋)
   - 收盤靠近極值: 多單 (high - close) <= 0.25 * rng; 空單 (close - low) <= 0.25 * rng (上/下影線極短)
   - 通道擴張: kc_width[i] > kc_width[i-3]
   - 不追過頭: 距軌道 <= 1.0 * ATR
   -> 開 50% 倉位 (size_ratio = 0.50), entry_source = 'BREAK_STRONG'
   -> 初始止損: 突破棒另一端外加 0.3 * ATR

2. 普通突破 (等第二根確認: BREAK_CONFIRM):
   - 第一根破軌 (out_prev and is_prev_color and not out_prev_prev)
   - 第二根同色延續且站在軌外 (out_now and is_color)
   -> 開 100% 倉位 (size_ratio = 1.0), entry_source = 'BREAK_CONFIRM'
   -> 初始止損: 突破區間另一端外加 0.3 * ATR

3. 強突破加倉 (BREAK_STRONG_ADD):
   - 若已持有 BREAK_STRONG 50% 倉位，次根收盤仍在軌外且同色延續
   -> 補至 100% 倉位, entry_source = 'BREAK_STRONG_ADD'
"""

from __future__ import annotations

import logging
import math
from typing import Any, Dict, Optional, Tuple
import pandas as pd

from core.config import get_breakout_two_stage_config

logger = logging.getLogger("two_stage_breakout")

BREAK_STRONG = "BREAK_STRONG"
BREAK_CONFIRM = "BREAK_CONFIRM"
BREAK_CONFIRM_OPP = "BREAK_CONFIRM_OPP"
BREAK_STRONG_ADD = "BREAK_STRONG_ADD"


class TwoStageBreakoutService:
    """破軌兩段式進場評估器"""

    @classmethod
    def evaluate(
        cls,
        df: pd.DataFrame,
        side: str,
        *,
        symbol: str = "",
        position: Optional[Dict[str, Any]] = None,
        mode: str = "HYBRID",  # HYBRID, ONLY_CONFIRM, ALWAYS_SINGLE
        config_override: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        評估破軌進場訊號
        :param df: 技術指標已計算之 K 線 DataFrame (要求包含 open, high, low, close, kc_upper, kc_lower, kc_middle, atr 等)
        :param side: 'LONG' 或 'SHORT'
        :param symbol: 交易幣種 (選填)
        :param position: 當前活躍持倉 (選填，用於判定加倉)
        :param mode: 'HYBRID' (強突破一根+普通第二根), 'ONLY_CONFIRM' (僅第二根), 'ALWAYS_SINGLE' (一律一根)
        :param config_override: 參數覆蓋 (選填)
        :return: 信號字典或 None
        """
        side = str(side).upper()
        if side not in ("LONG", "SHORT"):
            return None

        cfg = dict(get_breakout_two_stage_config())
        if config_override:
            cfg.update(config_override)

        if not cfg.get("ENABLED", True):
            return None

        # 需要至少足夠的回溯根數 (lookback + 3 根)
        lookback = int(cfg.get("STRONG_AVG_BODY_LOOKBACK", 10))
        min_bars = max(lookback + 5, 20)
        if df is None or len(df) < min_bars:
            return None

        # 只看已收盤 K 棒進行訊號確認
        if "is_closed" in df.columns:
            closed_df = df[df["is_closed"] == True]
            if len(closed_df) < min_bars:
                closed_df = df
        else:
            closed_df = df

        bars = closed_df.copy()
        curr = bars.iloc[-1]
        prev = bars.iloc[-2]
        prev_prev = bars.iloc[-3] if len(bars) >= 3 else prev

        # 數值萃取
        c_open = float(curr["open"])
        c_close = float(curr["close"])
        c_high = float(curr["high"])
        c_low = float(curr["low"])
        c_upper = float(curr.get("kc_upper", 0.0) or 0.0)
        c_lower = float(curr.get("kc_lower", 0.0) or 0.0)
        c_atr = float(curr.get("atr", 0.0001) or 0.0001)

        p_open = float(prev["open"])
        p_close = float(prev["close"])
        p_upper = float(prev.get("kc_upper", 0.0) or 0.0)
        p_lower = float(prev.get("kc_lower", 0.0) or 0.0)

        pp_close = float(prev_prev["close"])
        pp_upper = float(prev_prev.get("kc_upper", 0.0) or 0.0)
        pp_lower = float(prev_prev.get("kc_lower", 0.0) or 0.0)

        if min(c_upper, p_upper, c_atr) <= 0:
            return None

        d = 1.0 if side == "LONG" else -1.0
        band_curr = c_upper if side == "LONG" else c_lower
        band_prev = p_upper if side == "LONG" else p_lower
        band_prev_prev = pp_upper if side == "LONG" else pp_lower

        out_now = (d * (c_close - band_curr)) > 0
        out_prev = (d * (p_close - band_prev)) > 0
        out_prev_prev = (d * (pp_close - band_prev_prev)) > 0

        is_color = (d * (c_close - c_open)) > 0
        is_prev_color = (d * (p_close - p_open)) > 0

        rng = max(1e-9, c_high - c_low)
        body = abs(c_close - c_open)

        # 計算前 N 根實體平均值
        recent_bodies = (bars["close"].iloc[-lookback-1:-1] - bars["open"].iloc[-lookback-1:-1]).abs()
        avg_body = float(recent_bodies.mean()) if len(recent_bodies) > 0 else body

        # KC 寬度計算
        w_lookback = int(cfg.get("STRONG_KC_WIDTH_LOOKBACK", 3))
        idx_prev_w = max(0, len(bars) - 1 - w_lookback)
        kc_w_curr = c_upper - c_lower
        kc_w_past = float(bars["kc_upper"].iloc[idx_prev_w] - bars["kc_lower"].iloc[idx_prev_w])
        kc_expanding = kc_w_curr > kc_w_past

        # ── 加倉判定：若已持有 BREAK_STRONG 倉位 ──
        if position and str(position.get("side", "")).upper() == side:
            pos_source = str(position.get("entry_source", "") or position.get("entry_mode", "")).upper()
            pos_added = bool(position.get("has_scaled_in", False))
            if "BREAK_STRONG" in pos_source and not pos_added:
                # 次根仍在軌外且同色
                if out_now and is_color:
                    sl_buffer = float(cfg.get("STRONG_SL_ATR_BUFFER", 0.3))
                    initial_sl = (c_low - sl_buffer * c_atr) if side == "LONG" else (c_high + sl_buffer * c_atr)
                    add_ratio = float(cfg.get("STRONG_ADD_SIZE_RATIO", 0.50))
                    return {
                        "side": side,
                        "action": f"OPEN_{side}",
                        "entry_source": BREAK_STRONG_ADD,
                        "structure_confirmation": BREAK_STRONG_ADD,
                        "size_ratio": add_ratio,
                        "price": c_close,
                        "initial_sl": initial_sl,
                        "is_scale_in": True,
                        "reason": f"強突破次根同色延續加倉至100% (BREAK_STRONG_ADD, 比例={add_ratio*100:.0f}%)",
                        "bar_id": float(curr.get("timestamp", 0.0) or curr.get("time", 0.0)),
                    }
            # 已有常規倉位，不再重複開新倉
            return None

        # ── 條件 1：強突破 (一根就進: BREAK_STRONG) ──
        body_mult = float(cfg.get("STRONG_BODY_MULTIPLIER", 1.5))
        body_ratio_threshold = float(cfg.get("STRONG_BODY_RATIO", 0.60))
        max_atr_dist = float(cfg.get("STRONG_MAX_ATR_DIST", 1.0))

        # 收盤靠近極值 (上/下影線短)
        if side == "LONG":
            close_near_extreme = (c_high - c_close) <= (0.25 * rng)
            dist_to_band = c_close - band_curr
        else:
            close_near_extreme = (c_close - c_low) <= (0.25 * rng)
            dist_to_band = band_curr - c_close

        is_strong = (
            out_now
            and is_color
            and not out_prev  # 新一輪突破：前一根收盤仍在軌道內
            and (body >= body_mult * avg_body)
            and ((body / rng) >= body_ratio_threshold)
            and (dist_to_band <= max_atr_dist * c_atr)
            and kc_expanding
            and close_near_extreme
        )

        # ── 條件 2：普通突破第二根確認 ──
        opp_max_body_ratio = float(cfg.get("CONFIRM_OPP_MAX_BODY_RATIO", 0.60))
        is_first_break = out_prev and is_prev_color and not out_prev_prev

        # 2a. 第二根同色站在軌外: BREAK_CONFIRM (100% 倉位)
        is_confirm = is_first_break and out_now and is_color

        # 2b. 第二根反色但實體 <= 0.6*avg_body 且仍在軌外: BREAK_CONFIRM_OPP (50% 倉位)
        is_confirm_opp = is_first_break and out_now and (not is_color) and (body <= opp_max_body_ratio * avg_body)

        # 根據運作模式決定觸發行為
        if mode == "ALWAYS_SINGLE":
            # 一律一根就進模式
            if out_now and is_color and not out_prev:
                sl_buffer = float(cfg.get("STRONG_SL_ATR_BUFFER", 0.3))
                initial_sl = (c_low - sl_buffer * c_atr) if side == "LONG" else (c_high + sl_buffer * c_atr)
                return {
                    "side": side,
                    "action": f"OPEN_{side}",
                    "entry_source": "BREAK_SINGLE",
                    "structure_confirmation": "BREAK_SINGLE",
                    "size_ratio": 1.0,
                    "price": c_close,
                    "initial_sl": initial_sl,
                    "reason": "一律一根突破開倉 (BREAK_SINGLE: 100%倉位)",
                    "bar_id": float(curr.get("timestamp", 0.0) or curr.get("time", 0.0)),
                }
            return None

        elif mode == "ONLY_CONFIRM":
            # 僅第二根確認模式
            if is_confirm:
                sl_buffer = float(cfg.get("STRONG_SL_ATR_BUFFER", 0.3))
                sl_base = min(c_low, float(prev["low"])) if side == "LONG" else max(c_high, float(prev["high"]))
                initial_sl = (sl_base - sl_buffer * c_atr) if side == "LONG" else (sl_base + sl_buffer * c_atr)
                return {
                    "side": side,
                    "action": f"OPEN_{side}",
                    "entry_source": BREAK_CONFIRM,
                    "structure_confirmation": BREAK_CONFIRM,
                    "size_ratio": float(cfg.get("CONFIRM_INITIAL_SIZE_RATIO", 1.0)),
                    "price": c_close,
                    "initial_sl": initial_sl,
                    "reason": "普通突破第二根同色確認進場 (BREAK_CONFIRM: 100%倉位)",
                    "bar_id": float(curr.get("timestamp", 0.0) or curr.get("time", 0.0)),
                }
            if is_confirm_opp:
                sl_buffer = float(cfg.get("STRONG_SL_ATR_BUFFER", 0.3))
                sl_base = min(c_low, float(prev["low"])) if side == "LONG" else max(c_high, float(prev["high"]))
                initial_sl = (sl_base - sl_buffer * c_atr) if side == "LONG" else (sl_base + sl_buffer * c_atr)
                opp_ratio = float(cfg.get("CONFIRM_OPP_SIZE_RATIO", 0.50))
                return {
                    "side": side,
                    "action": f"OPEN_{side}",
                    "entry_source": BREAK_CONFIRM_OPP,
                    "structure_confirmation": BREAK_CONFIRM_OPP,
                    "size_ratio": opp_ratio,
                    "price": c_close,
                    "initial_sl": initial_sl,
                    "reason": f"普通突破第二根反色弱實體進場 (BREAK_CONFIRM_OPP: {opp_ratio*100:.0f}%倉位)",
                    "bar_id": float(curr.get("timestamp", 0.0) or curr.get("time", 0.0)),
                }
            return None

        else:
            # HYBRID 模式 (建議版：強突破一根 + 普通突破第二根)
            if is_strong:
                sl_buffer = float(cfg.get("STRONG_SL_ATR_BUFFER", 0.3))
                initial_sl = (c_low - sl_buffer * c_atr) if side == "LONG" else (c_high + sl_buffer * c_atr)
                init_ratio = float(cfg.get("STRONG_INITIAL_SIZE_RATIO", 0.50))
                return {
                    "side": side,
                    "action": f"OPEN_{side}",
                    "entry_source": BREAK_STRONG,
                    "structure_confirmation": BREAK_STRONG,
                    "size_ratio": init_ratio,
                    "price": c_close,
                    "initial_sl": initial_sl,
                    "reason": f"強突破一根進場 (BREAK_STRONG: {init_ratio*100:.0f}%倉位)",
                    "bar_id": float(curr.get("timestamp", 0.0) or curr.get("time", 0.0)),
                }

            if is_confirm:
                sl_buffer = float(cfg.get("STRONG_SL_ATR_BUFFER", 0.3))
                sl_base = min(c_low, float(prev["low"])) if side == "LONG" else max(c_high, float(prev["high"]))
                initial_sl = (sl_base - sl_buffer * c_atr) if side == "LONG" else (sl_base + sl_buffer * c_atr)
                conf_ratio = float(cfg.get("CONFIRM_INITIAL_SIZE_RATIO", 1.0))
                return {
                    "side": side,
                    "action": f"OPEN_{side}",
                    "entry_source": BREAK_CONFIRM,
                    "structure_confirmation": BREAK_CONFIRM,
                    "size_ratio": conf_ratio,
                    "price": c_close,
                    "initial_sl": initial_sl,
                    "reason": f"普通突破第二根同色確認進場 (BREAK_CONFIRM: {conf_ratio*100:.0f}%倉位)",
                    "bar_id": float(curr.get("timestamp", 0.0) or curr.get("time", 0.0)),
                }

            if is_confirm_opp:
                sl_buffer = float(cfg.get("STRONG_SL_ATR_BUFFER", 0.3))
                sl_base = min(c_low, float(prev["low"])) if side == "LONG" else max(c_high, float(prev["high"]))
                initial_sl = (sl_base - sl_buffer * c_atr) if side == "LONG" else (sl_base + sl_buffer * c_atr)
                opp_ratio = float(cfg.get("CONFIRM_OPP_SIZE_RATIO", 0.50))
                return {
                    "side": side,
                    "action": f"OPEN_{side}",
                    "entry_source": BREAK_CONFIRM_OPP,
                    "structure_confirmation": BREAK_CONFIRM_OPP,
                    "size_ratio": opp_ratio,
                    "price": c_close,
                    "initial_sl": initial_sl,
                    "reason": f"普通突破第二根反色弱實體進場 (BREAK_CONFIRM_OPP: {opp_ratio*100:.0f}%倉位)",
                    "bar_id": float(curr.get("timestamp", 0.0) or curr.get("time", 0.0)),
                }

            return None
