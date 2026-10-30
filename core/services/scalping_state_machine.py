"""
core/services/scalping_state_machine.py
=======================================
AI 極速決策引擎：有限狀態機 (Finite State Machine) 核心規格
嚴格遵守【2026-10-30 最新 System Prompt 規範】：

【核心職責】
1. 實施「早期收盤突破進場」
2. 發揮「大週期順勢一票否決 (Macro Trend Veto)」與「大週期結束即刻一鍵轉向 (Structure Break Reversal)」
3. 透過「MA3 斜率與位置雙重硬性審查」，徹底杜絕阿呆谷追空與下殺接飛刀
4. 全面廢除「過 KC 中軌平倉」，改為「大結構打破」或「收盤價實體站穩/跌破 MA15」才平倉
5. 平倉後強制進場冷卻期 (Cooldown 3 根 K 棒 / 180 秒)，杜絕雙頭磨損

狀態有且僅有 3 種：
1. "SHORT" (持有空單)
2. "LONG"  (持有多單)
3. "FLAT"  (無持倉觀望)
"""
import logging
from typing import Dict, Any, Tuple, Optional

logger = logging.getLogger("ScalpingStateMachine")


class ScalpingStateMachine:
    """極短線量化交易策略 AI 極速決策引擎"""

    @classmethod
    def evaluate(
        cls,
        position_state: str,
        macro_trend: str,
        current_price: float,
        ma3: float,
        ma15: float,
        ma3_slope: float = 0.0,
        cooldown_active: bool = False,
        open_price: float = 0.0,
        entry_price: float = 0.0,
        highest_price: float = 0.0,
        lowest_price: float = 0.0,
        peak_roe_pct: float = 0.0,
        is_structure_break: bool = False,
    ) -> Tuple[str, str]:
        """
        每根 K 棒收盤 (Bar Close) 或即時報價更新時執行的核心裁決
        :return: (action, reason)
        action 可選:
          - "HOLD"
          - "CLOSE_SHORT"
          - "CLOSE_LONG"
          - "CLOSE_SHORT_AND_OPEN_LONG"
          - "CLOSE_LONG_AND_OPEN_SHORT"
          - "OPEN_LONG"
          - "OPEN_SHORT"
          - "WAIT"
        """
        res = cls.evaluate_dict(
            position_state=position_state,
            macro_trend=macro_trend,
            current_price=current_price,
            ma3=ma3,
            ma15=ma15,
            ma3_slope=ma3_slope,
            cooldown_active=cooldown_active,
            open_price=open_price,
            entry_price=entry_price,
            highest_price=highest_price,
            lowest_price=lowest_price,
            peak_roe_pct=peak_roe_pct,
            is_structure_break=is_structure_break,
        )
        return res["action"], res["reason"]

    @classmethod
    def evaluate_dict(
        cls,
        position_state: str,
        macro_trend: str,
        current_price: float,
        ma3: float,
        ma15: float,
        ma3_slope: float = 0.0,
        cooldown_active: bool = False,
        open_price: float = 0.0,
        entry_price: float = 0.0,
        highest_price: float = 0.0,
        lowest_price: float = 0.0,
        peak_roe_pct: float = 0.0,
        is_structure_break: bool = False,
    ) -> Dict[str, Any]:
        """
        回傳符合 System Prompt 規範的完整 JSON 結構字典
        """
        pos = str(position_state or "FLAT").upper()
        if pos not in ("SHORT", "LONG"):
            pos = "FLAT"

        macro = str(macro_trend or "NEUTRAL").upper()
        curr_p = float(current_price or 0.0)
        open_p = float(open_price or curr_p)
        m3 = float(ma3 or 0.0)
        m15 = float(ma15 or 0.0)
        slope = float(ma3_slope or 0.0)
        cooldown = bool(cooldown_active)
        entry_p = float(entry_price or 0.0)
        high_p = float(highest_price or entry_p or curr_p)
        low_p = float(lowest_price or entry_p or curr_p)
        roe_pct = float(peak_roe_pct or 0.0)
        struct_break = bool(is_structure_break)

        # 狀態標籤
        if macro == "BULLISH":
            status_label = "大週期牛市 (順勢做多)"
        elif macro == "BEARISH":
            status_label = "大週期熊市 (順勢做空)"
        else:
            status_label = "中性震盪 (等待大結構確認)"

        # =====================================================================
        # 🛑 規則一：大週期結束即刻一鍵轉向 (Structure Break Reversal)
        # =====================================================================
        if struct_break:
            if macro == "BULLISH":
                # 熊轉牛確立 (BEARISH -> BULLISH)
                if pos == "SHORT":
                    return {
                        "macro_trend": macro,
                        "status_label": status_label,
                        "position_state": pos,
                        "ma3_slope": slope,
                        "cooldown_active": cooldown,
                        "action": "CLOSE_SHORT_AND_OPEN_LONG",
                        "reason": "大週期結構強勢打破轉為牛市 (突破 Lower High 且站上 MA15)，持有空單立刻一鍵翻多！",
                    }
                elif pos == "FLAT":
                    if cooldown:
                        return {
                            "macro_trend": macro,
                            "status_label": status_label,
                            "position_state": pos,
                            "ma3_slope": slope,
                            "cooldown_active": cooldown,
                            "action": "WAIT",
                            "reason": "大週期結構轉牛，但當前處於平倉後冷卻期，暫緩進場！",
                        }
                    return {
                        "macro_trend": macro,
                        "status_label": status_label,
                        "position_state": pos,
                        "ma3_slope": slope,
                        "cooldown_active": cooldown,
                        "action": "OPEN_LONG",
                        "reason": "大週期結構打破確立轉牛，無持倉立刻開多卡位新牛市起漲點！",
                    }

            elif macro == "BEARISH":
                # 牛轉熊確立 (BULLISH -> BEARISH)
                if pos == "LONG":
                    return {
                        "macro_trend": macro,
                        "status_label": status_label,
                        "position_state": pos,
                        "ma3_slope": slope,
                        "cooldown_active": cooldown,
                        "action": "CLOSE_LONG_AND_OPEN_SHORT",
                        "reason": "大週期結構強勢跌破轉為熊市 (跌破 Higher Low 且失守 MA15)，持有多單立刻一鍵翻空！",
                    }
                elif pos == "FLAT":
                    if cooldown:
                        return {
                            "macro_trend": macro,
                            "status_label": status_label,
                            "position_state": pos,
                            "ma3_slope": slope,
                            "cooldown_active": cooldown,
                            "action": "WAIT",
                            "reason": "大週期結構轉熊，但當前處於平倉後冷卻期，暫緩進場！",
                        }
                    return {
                        "macro_trend": macro,
                        "status_label": status_label,
                        "position_state": pos,
                        "ma3_slope": slope,
                        "cooldown_active": cooldown,
                        "action": "OPEN_SHORT",
                        "reason": "大週期結構跌破確立轉熊，無持倉立刻開空卡位新熊市起跌點！",
                    }

        # =====================================================================
        # 🔴 狀態 1：目前持有空單 (SHORT) 時的離場/翻單判斷
        # =====================================================================
        if pos == "SHORT":
            # 規則四：廢除過中軌平倉；僅在收盤價實體突破站穩 MA15 (收盤價 > MA15 且 收紅K) 方可平倉
            is_bull_reversal_exit = bool(curr_p > m15 and curr_p > open_p)

            # 方案 1 鎖利條件：浮盈 >= 10% 且自最低谷底回吐 >= 20% 且 MA3 > MA15
            is_take_profit = False
            if entry_p > 0 and low_p > 0:
                max_gain = (entry_p - low_p) / entry_p
                cur_gain = (entry_p - curr_p) / entry_p
                drawdown = (max_gain - cur_gain) / max_gain if max_gain > 0 else 0.0
                if (max_gain >= 0.10 or roe_pct >= 10.0) and drawdown >= 0.20 and (m3 > m15):
                    is_take_profit = True

            if is_bull_reversal_exit or is_take_profit:
                exit_why = "單根 K 棒實體強勢突破站穩 MA15" if is_bull_reversal_exit else "命中方案 1 鎖利門檻 (回吐>=20% 且 MA3>MA15)"
                if macro == "BULLISH":
                    return {
                        "macro_trend": macro,
                        "status_label": status_label,
                        "position_state": pos,
                        "ma3_slope": slope,
                        "cooldown_active": cooldown,
                        "action": "CLOSE_SHORT_AND_OPEN_LONG",
                        "reason": f"{exit_why}；當前為大週期牛市，一鍵翻單開多！",
                    }
                else:
                    return {
                        "macro_trend": macro,
                        "status_label": status_label,
                        "position_state": pos,
                        "ma3_slope": slope,
                        "cooldown_active": cooldown,
                        "action": "CLOSE_SHORT",
                        "reason": f"{exit_why}；平空落袋觀望，啟動 3 根 K 棒防追冷卻。",
                    }

            return {
                "macro_trend": macro,
                "status_label": status_label,
                "position_state": pos,
                "ma3_slope": slope,
                "cooldown_active": cooldown,
                "action": "HOLD",
                "reason": "大週期熊市空單續抱中 (未實體站穩 MA15，微幅過線視為噪聲不平倉)",
            }

        # =====================================================================
        # 🟢 狀態 2：目前持有多單 (LONG) 時的離場/翻單判斷
        # =====================================================================
        elif pos == "LONG":
            # 規則四：廢除過中軌平倉；僅在收盤價實體跌破失守 MA15 (收盤價 < MA15 且 收黑K) 方可平倉
            is_bear_reversal_exit = bool(curr_p < m15 and curr_p < open_p)

            # 方案 1 鎖利條件：浮盈 >= 10% 且自最高點回吐 >= 20% 且 MA3 < MA15
            is_take_profit = False
            if entry_p > 0 and high_p > 0:
                max_gain = (high_p - entry_p) / entry_p
                cur_gain = (curr_p - entry_p) / entry_p
                drawdown = (max_gain - cur_gain) / max_gain if max_gain > 0 else 0.0
                if (max_gain >= 0.10 or roe_pct >= 10.0) and drawdown >= 0.20 and (m3 < m15):
                    is_take_profit = True

            if is_bear_reversal_exit or is_take_profit:
                exit_why = "單根 K 棒實體跌破失守 MA15" if is_bear_reversal_exit else "命中方案 1 鎖利門檻 (回吐>=20% 且 MA3<MA15)"
                if macro == "BEARISH":
                    return {
                        "macro_trend": macro,
                        "status_label": status_label,
                        "position_state": pos,
                        "ma3_slope": slope,
                        "cooldown_active": cooldown,
                        "action": "CLOSE_LONG_AND_OPEN_SHORT",
                        "reason": f"{exit_why}；當前為大週期熊市，一鍵翻單開空！",
                    }
                else:
                    return {
                        "macro_trend": macro,
                        "status_label": status_label,
                        "position_state": pos,
                        "ma3_slope": slope,
                        "cooldown_active": cooldown,
                        "action": "CLOSE_LONG",
                        "reason": f"{exit_why}；平多落袋觀望，啟動 3 根 K 棒防追冷卻。",
                    }

            return {
                "macro_trend": macro,
                "status_label": status_label,
                "position_state": pos,
                "ma3_slope": slope,
                "cooldown_active": cooldown,
                "action": "HOLD",
                "reason": "大週期牛市多單續抱中 (守穩 MA15，微幅過線視為噪聲不平倉)",
            }

        # =====================================================================
        # ⚪ 狀態 3：目前【無持倉 (FLAT)】時的進場裁決
        # =====================================================================
        else:
            # 🛑 規則四.3：平倉後強制進場冷卻期 (Cooldown) 審查
            if cooldown:
                return {
                    "macro_trend": macro,
                    "status_label": status_label,
                    "position_state": pos,
                    "ma3_slope": slope,
                    "cooldown_active": cooldown,
                    "action": "WAIT",
                    "reason": "處於平倉後 3 根 K 棒冷卻期內，100% 嚴禁重複開倉，防雙頭抽耳光！",
                }

            # 🟢 規則二：大週期牛市做多審查
            if macro == "BULLISH":
                # 順勢鐵律：大週期牛市 100% 一票否決任何做空指令！
                # 早期開多條件：單根 K 棒實體突破 MA15 (收盤價 > MA15 且 收紅陽線)
                is_breakout_long = bool(curr_p > m15 and curr_p >= open_p)

                if is_breakout_long:
                    # 雙重一票否決審查 (杜絕下殺接飛刀)
                    # 1. 位置否決：MA3 < MA15 (空頭排列)
                    if m3 < m15:
                        return {
                            "macro_trend": macro,
                            "status_label": status_label,
                            "position_state": pos,
                            "ma3_slope": slope,
                            "cooldown_active": cooldown,
                            "action": "WAIT",
                            "reason": f"位置否決：MA3 ({m3:.6f}) < MA15 ({m15:.6f}) 空頭排列，一票否決開多！",
                        }
                    # 2. 斜率否決：MA3 斜率向下 (MA3_Slope < 0)
                    if slope < 0:
                        return {
                            "macro_trend": macro,
                            "status_label": status_label,
                            "position_state": pos,
                            "ma3_slope": slope,
                            "cooldown_active": cooldown,
                            "action": "WAIT",
                            "reason": f"斜率否決：MA3 斜率向下 ({slope:.6f} < 0)，一票否決開多接飛刀！",
                        }

                    # 通過雙重否決，授權早期開多
                    return {
                        "macro_trend": macro,
                        "status_label": status_label,
                        "position_state": pos,
                        "ma3_slope": slope,
                        "cooldown_active": cooldown,
                        "action": "OPEN_LONG",
                        "reason": "大週期牛市早期進場：實體收盤價突破 MA15，且通過 MA3 位置與斜率雙重審查，立即開多卡位起漲點！",
                    }
                else:
                    return {
                        "macro_trend": macro,
                        "status_label": status_label,
                        "position_state": pos,
                        "ma3_slope": slope,
                        "cooldown_active": cooldown,
                        "action": "WAIT",
                        "reason": "大週期牛市觀望中：尚未出現實體收盤價突破 MA15 之起漲訊號。",
                    }

            # 🔴 規則三：大週期熊市做空審查
            elif macro == "BEARISH":
                # 順勢鐵律：大週期熊市 100% 一票否決任何做多指令！
                # 早期開空條件：單根 K 棒實體跌破 MA15 (收盤價 < MA15 且 收黑陰線)
                is_breakdown_short = bool(curr_p < m15 and curr_p <= open_p)

                if is_breakdown_short:
                    # 雙重一票否決審查 (杜絕阿呆谷追空)
                    # 1. 位置否決：MA3 > MA15 (多頭排列)
                    if m3 > m15:
                        return {
                            "macro_trend": macro,
                            "status_label": status_label,
                            "position_state": pos,
                            "ma3_slope": slope,
                            "cooldown_active": cooldown,
                            "action": "WAIT",
                            "reason": f"位置否決：MA3 ({m3:.6f}) > MA15 ({m15:.6f}) 多頭排列，一票否決開空！",
                        }
                    # 2. 斜率否決：MA3 斜率向上 (MA3_Slope > 0)
                    if slope > 0:
                        return {
                            "macro_trend": macro,
                            "status_label": status_label,
                            "position_state": pos,
                            "ma3_slope": slope,
                            "cooldown_active": cooldown,
                            "action": "WAIT",
                            "reason": f"斜率否決：MA3 斜率向上 ({slope:.6f} > 0)，一票否決開空防軋空！",
                        }

                    # 通過雙重否決，授權早期開空
                    return {
                        "macro_trend": macro,
                        "status_label": status_label,
                        "position_state": pos,
                        "ma3_slope": slope,
                        "cooldown_active": cooldown,
                        "action": "OPEN_SHORT",
                        "reason": "大週期熊市早期進場：實體收盤價跌破 MA15，且通過 MA3 位置與斜率雙重審查，立即開空卡位起跌點！",
                    }
                else:
                    return {
                        "macro_trend": macro,
                        "status_label": status_label,
                        "position_state": pos,
                        "ma3_slope": slope,
                        "cooldown_active": cooldown,
                        "action": "WAIT",
                        "reason": "大週期熊市觀望中：尚未出現實體收盤價跌破 MA15 之起跌訊號。",
                    }

            # ⚪ 中性行情
            return {
                "macro_trend": macro,
                "status_label": status_label,
                "position_state": pos,
                "ma3_slope": slope,
                "cooldown_active": cooldown,
                "action": "WAIT",
                "reason": "目前無明確大週期結構趨勢，保持空倉觀望，嚴禁無序開單！",
            }
