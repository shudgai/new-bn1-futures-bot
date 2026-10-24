"""
core/services/quant_risk_engine.py
===================================
量化合約機器人風控與信號判斷核心模組
嚴格落實四大核心鐵律：
1. 趨勢保護原則：未破趨勢結構 100% HOLD 續抱，不被微幅雜訊洗出場。
2. 10% 啟動 + 30% 回吐鎖利狀態機：Max_Profit >= 10% 啟動，回吐跌破 70% 水位時市價全平。
3. 嚴防追高殺跌：KC 乖離過濾、回踩中軌支撐/阻力確認。
4. K 線收盤確認 (Bar Close)：完全排除未定型盤中跳動數據。
"""
from __future__ import annotations

import math
import logging
from dataclasses import dataclass, field
from typing import Dict, Any, Optional, Tuple, Literal

logger = logging.getLogger(__name__)


@dataclass
class PositionState:
    """持倉狀態資料模型"""
    symbol: str
    side: Literal["LONG", "SHORT"]
    entry_price: float
    qty: float = 1.0
    leverage: float = 5.0
    fee_estimate: float = 0.001       # 預估雙邊手續費與滑點 0.1% (0.001)
    max_profit_pct: float = 0.0       # 持倉期間歷史最高淨利率
    is_lock_active: bool = False      # 是否已激活 10% 鎖利機制
    entry_timestamp: Optional[float] = None


@dataclass
class KCChannels:
    """肯特納通道（KC）與均線指標數據"""
    middle: float                     # KC 中軌 (EMA20)
    upper: float                      # KC 上軌 (middle + mult * atr)
    lower: float                      # KC 下軌 (middle - mult * atr)
    middle_slope: float               # 中軌動態斜率
    atr: float                        # 平均真實波動幅度
    trend: Literal["KC向上", "KC向下", "震盪觀察"]


class QuantRiskEngine:
    """
    量化風控與信號裁決狀態機
    """

    # 鎖利觸發門檻常數
    PROFIT_LOCK_ACTIVATION_THRESHOLD = 0.10  # 10.0% 啟動鎖利
    PROFIT_LOCK_PRESERVATION_RATIO = 0.70    # 保留 70% 利潤 (即回吐 30% 強制平倉)

    @classmethod
    def calculate_current_net_pnl(cls, pos: PositionState, current_price: float) -> float:
        """
        精準計算當前淨利率 (扣除預估手續費與滑點)
        """
        if pos.entry_price <= 0:
            return 0.0

        if pos.side == "LONG":
            raw_ret = (current_price - pos.entry_price) / pos.entry_price
        else:  # SHORT
            raw_ret = (pos.entry_price - current_price) / pos.entry_price

        current_net_profit = (raw_ret * pos.leverage) - pos.fee_estimate
        return current_net_profit

    @classmethod
    def update_profit_metrics(cls, pos: PositionState, current_price: float) -> Tuple[float, float, bool]:
        """
        更新歷史最高淨利率並判定是否啟動 10% 鎖利模組
        回傳: (current_net_pnl, max_profit_pct, is_lock_active)
        """
        current_net = cls.calculate_current_net_pnl(pos, current_price)

        # 記錄並更新歷史最高淨利
        if current_net > pos.max_profit_pct:
            pos.max_profit_pct = current_net

        # 檢查 10% 鎖利機制門檻
        if pos.max_profit_pct >= cls.PROFIT_LOCK_ACTIVATION_THRESHOLD:
            pos.is_lock_active = True

        return current_net, pos.max_profit_pct, pos.is_lock_active

    @classmethod
    def check_exit_rules(
        cls,
        pos: PositionState,
        current_price: float,
        kc: KCChannels,
        last_closed_bar: Optional[Dict[str, float]] = None,
    ) -> Dict[str, Any]:
        """
        嚴格執行平倉與鎖利判斷
        回傳結構:
        {
            "action": "CLOSE" or "HOLD",
            "reason": str,
            "metrics": { ... }
        }
        """
        current_net, max_profit, is_locked = cls.update_profit_metrics(pos, current_price)
        lock_exit_line = max_profit * cls.PROFIT_LOCK_PRESERVATION_RATIO if is_locked else 0.0

        metrics = {
            "symbol": pos.symbol,
            "side": pos.side,
            "entry_price": pos.entry_price,
            "current_price": current_price,
            "current_net_pnl_pct": current_net,
            "max_profit_pct": max_profit,
            "is_lock_active": is_locked,
            "lock_exit_line_pct": lock_exit_line,
            "trend": kc.trend,
        }

        # ---------------- 規則 1：【最高優先級】達標 10% 且利潤自最高點回吐 30% -> 強制鎖利平倉 ----------------
        if is_locked:
            if current_net <= lock_exit_line:
                reason = (
                    f"【觸發30%回吐鎖利】最高淨利: {max_profit * 100:.2f}%, "
                    f"鎖利線: {lock_exit_line * 100:.2f}%, 當前淨利: {current_net * 100:.2f}%"
                )
                return {"action": "CLOSE", "reason": reason, "metrics": metrics}

        # ---------------- 規則 2：利潤未達 10%，趨勢不破絕不平倉 (趨勢保護原則) ----------------
        # 趨勢反轉防守：以收盤價實體破位為準，盤中毛刺不觸發
        c_close = float(last_closed_bar.get("close", current_price)) if last_closed_bar else current_price

        if pos.side == "SHORT":
            # 空單防守：只有在 KC 趨勢翻多，或收盤價明確站回中軌之上破壞空頭結構時才出場
            if kc.trend == "KC向上" or (c_close > kc.middle and kc.middle_slope > 0):
                return {
                    "action": "CLOSE",
                    "reason": f"【趨勢反轉平倉】KC空頭結構破壞 (收盤={c_close:.4f} > 中軌={kc.middle:.4f}, 趨勢: {kc.trend})",
                    "metrics": metrics,
                }
            # 空頭結構完好，死抱主跌浪
            return {
                "action": "HOLD",
                "reason": f"空頭趨勢持續 (中軌下傾={kc.middle_slope:.6f}, 價格受壓於中軌下)，未達回吐門檻，堅定持倉續抱",
                "metrics": metrics,
            }

        elif pos.side == "LONG":
            # 多單防守：只有在 KC 趨勢翻空，或收盤價明確跌破中軌之下破壞多頭結構時才出場
            if kc.trend == "KC向下" or (c_close < kc.middle and kc.middle_slope < 0):
                return {
                    "action": "CLOSE",
                    "reason": f"【趨勢反轉平倉】KC多頭結構破壞 (收盤={c_close:.4f} < 中軌={kc.middle:.4f}, 趨勢: {kc.trend})",
                    "metrics": metrics,
                }
            # 多頭結構完好，死抱主升浪
            return {
                "action": "HOLD",
                "reason": f"多頭趨勢持續 (中軌上傾={kc.middle_slope:.6f}, 價格守穩於中軌上)，未達回吐門檻，堅定持倉續抱",
                "metrics": metrics,
            }

        return {"action": "HOLD", "reason": "未觸發任何平倉條件，維持持倉", "metrics": metrics}

    @classmethod
    def check_entry_rules(
        cls,
        symbol: str,
        kc: KCChannels,
        last_closed_bar: Dict[str, float],
        is_bar_closed: bool = True,
        has_existing_position: bool = False,
    ) -> Dict[str, Any]:
        """
        開倉過濾規則（解決追高殺跌與雙巴）：
        1. 嚴格限定僅用已收線 K 棒 (Bar Close)。
        2. 禁止乖離追單 (天花板不追多、地板不殺空)。
        3. 順勢回踩確認進場。
        """
        if has_existing_position:
            return {"action": "NONE", "reason": "已有持倉，禁止重複開倉"}

        if not is_bar_closed:
            return {"action": "NONE", "reason": "盤中動態跳動中，等待 K 線收盤確認 (Bar Close)"}

        c_open = float(last_closed_bar["open"])
        c_close = float(last_closed_bar["close"])
        c_high = float(last_closed_bar["high"])
        c_low = float(last_closed_bar["low"])
        atr = max(kc.atr, 1e-6)

        # ---------------- 做多開倉審查 (LONG) ----------------
        if kc.trend == "KC向上" and kc.middle_slope > 0:
            # 1. 乖離過濾：嚴禁在大幅高出上軌天花板處追多 (距中軌 > 1.5 ATR 或高出上軌 0.3 ATR)
            gap_from_mid = c_close - kc.middle
            if gap_from_mid > 1.5 * atr or c_close > (kc.upper + 0.3 * atr):
                return {"action": "NONE", "reason": f"【禁止天花板追多】乖離過大 (距中軌={gap_from_mid/atr:.2f} ATR)，等待回踩"}

            # 2. 順勢回踩有支撐進場：收盤站穩在 KC 中軌之上，且當根為實體收紅陽線
            if c_close > kc.middle and c_close > c_open and (c_close - c_open) >= 0.25 * atr:
                return {
                    "action": "OPEN_LONG",
                    "symbol": symbol,
                    "reason": f"【多單進場訊號】KC向上順勢，回踩中軌守穩且收紅陽線 (收盤={c_close:.4f} > 中軌={kc.middle:.4f})",
                }

        # ---------------- 做空開倉審查 (SHORT) ----------------
        elif kc.trend == "KC向下" and kc.middle_slope < 0:
            # 1. 乖離過濾：嚴禁在深破下軌地板處殺空 (距中軌 > 1.5 ATR 或低於下軌 0.3 ATR)
            gap_from_mid = kc.middle - c_close
            if gap_from_mid > 1.5 * atr or c_close < (kc.lower - 0.3 * atr):
                return {"action": "NONE", "reason": f"【禁止地板殺空】乖離過大 (距中軌={gap_from_mid/atr:.2f} ATR)，等待反彈"}

            # 2. 順勢反抽遇阻進場：收盤壓制在 KC 中軌之下，且當根為實體收黑陰線
            if c_close < kc.middle and c_close < c_open and (c_open - c_close) >= 0.25 * atr:
                return {
                    "action": "OPEN_SHORT",
                    "symbol": symbol,
                    "reason": f"【空單進場訊號】KC向下順勢，反抽中軌遇阻且收黑陰線 (收盤={c_close:.4f} < 中軌={kc.middle:.4f})",
                }

        return {"action": "NONE", "reason": f"盤面無順勢突破或結構處於震盪觀察 (當前趨勢: {kc.trend})"}

    @classmethod
    def format_status_output(cls, decision: Dict[str, Any]) -> str:
        """
        標準化狀態輸出格式化器
        """
        m = decision.get("metrics")
        if not m:
            return f"🤖 [狀態快照] 動作: {decision.get('action')} | 理由: {decision.get('reason')}"

        lock_status_text = (
            f"已啟動 (回吐70%鎖利線: {m['lock_exit_line_pct']*100:.2f}%)"
            if m["is_lock_active"]
            else f"待啟動 (距10%目標差: {max(0.0, (cls.PROFIT_LOCK_ACTIVATION_THRESHOLD - m['max_profit_pct'])*100):.2f}%)"
        )

        output_lines = [
            "=" * 60,
            f"🤖 【量化風控與執行狀態快照 - {m['symbol']}】",
            f"• 持倉狀態: {m['side']} @ {m['entry_price']:.4f} (現價: {m['current_price']:.4f})",
            f"• 當前淨利率 / 歷史最高: {m['current_net_pnl_pct']*100:+.2f}% / {m['max_profit_pct']*100:+.2f}%",
            f"• 10% 鎖利狀態: {lock_status_text}",
            f"• 通道趨勢狀態: {m['trend']}",
            f"• 執行指令: 【{decision.get('action')}】",
            f"• 觸發依據: {decision.get('reason')}",
            "=" * 60,
        ]
        return "\n".join(output_lines)
