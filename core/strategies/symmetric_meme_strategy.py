"""
對稱式妖幣趨勢追蹤量化策略引擎 (Symmetric Meme Trend Strategy)
- 適用標的：妖幣、高波動合約 (如 MEME / LOBSTER / CAP 等)
- 特性：
  1. 多空完全對稱 (Long / Short Symmetrical)
  2. K 棒收盤確認 (is_kline_closed 杜絕盤中影線假突破)
  3. 突破動能開倉 (KC 方向 + 突破前 N 根極值 + 實體動能 + 中軌 ATR 乖離防過度追單)
  4. 回踩/回抽續抱 (KC 趨勢維持且在中軌正向側時，忽略微觀均線抖動與短暫回調，一票否決平倉)
  5. 雙重衰退確認平倉 (收盤跌破/突破中軌，或 MA3 逆轉 + KC 中軌同步衰退)
  6. 分階段停利停損 (初始固定 ATR 防守 + 浮盈達標啟動 Trailing Stop 移動鎖利)
  7. 狀態機管理 (IDLE, HOLD_LONG, HOLD_SHORT) 與平倉冷卻
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple


class TradeState(str, Enum):
    IDLE = "IDLE"              # 空手觀望
    HOLD_LONG = "HOLD_LONG"    # 持有多單
    HOLD_SHORT = "HOLD_SHORT"  # 持有空單


class OrderAction(str, Enum):
    OPEN_LONG = "OPEN_LONG"
    OPEN_SHORT = "OPEN_SHORT"
    CLOSE_LONG = "CLOSE_LONG"
    CLOSE_SHORT = "CLOSE_SHORT"


@dataclass
class Bar:
    """1 分鐘 (或指定週期) K 棒數據"""
    timestamp: float
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0

    @property
    def is_bullish(self) -> bool:
        return self.close > self.open

    @property
    def is_bearish(self) -> bool:
        return self.close < self.open

    @property
    def body(self) -> float:
        return abs(self.close - self.open)

    @property
    def upper_wick(self) -> float:
        return self.high - max(self.open, self.close)

    @property
    def lower_wick(self) -> float:
        return min(self.open, self.close) - self.low


@dataclass
class Position:
    """持倉狀態追蹤物件"""
    state: TradeState
    entry_price: float
    entry_time: float
    size: float
    highest_price: float        # 持倉期間最高價 (多單移動停利基準)
    lowest_price: float         # 持倉期間最低價 (空單移動停利基準)
    initial_stop_loss: float    # 初始硬停損價格
    profit_lock_active: bool = False  # 是否已觸發浮盈鎖定 (例如浮盈 >= 10%)
    trailing_stop: float = 0.0  # 移動追蹤保護價


@dataclass
class StrategyConfig:
    """策略參數設定"""
    # 肯特納通道 (KC)
    kc_ema_period: int = 20
    kc_atr_multiplier: float = 1.5
    atr_period: int = 20

    # 極短線動能均線
    ma_fast_period: int = 3

    # 動能突破過濾
    breakout_lookback: int = 10       # 突破前 N 根最高/最低價
    min_body_atr_ratio: float = 0.35  # 當根收盤實體至少達 0.35 ATR
    max_wick_ratio: float = 0.70      # 逆向影線不得超過實體的 70%

    # 中軌乖離防追高追低 (Bias / ATR)
    max_mid_bias_atr: float = 2.8     # 與中軌距離超過 2.8 ATR 禁止過度追價

    # 風險控制與分階段停利停損
    initial_sl_atr: float = 1.5       # 初始停損距離 (1.5 * ATR)
    profit_lock_threshold: float = 0.10  # 浮盈達到 10% 啟動利潤鎖定
    trailing_sl_atr: float = 1.2      # 啟動鎖利後的追蹤停利緩衝 (1.2 * ATR)

    # 平倉冷卻保護
    cooldown_bars_after_exit: int = 3  # 平倉後冷卻 K 棒數，防邊界反覆雙巴


class SymmetricMemeTrendStrategy:
    """對稱式妖幣趨勢追蹤量化策略"""

    def __init__(self, config: Optional[StrategyConfig] = None):
        self.config = config or StrategyConfig()
        self.state: TradeState = TradeState.IDLE
        self.position: Optional[Position] = None
        self.bars: List[Bar] = []
        self.cooldown_remaining: int = 0

    def on_kline(self, bar: Bar, is_kline_closed: bool) -> Optional[Tuple[OrderAction, str]]:
        """
        K 棒驅動入口 (嚴格保證只在收盤確認時執行)
        :param bar: 當前 K 棒
        :param is_kline_closed: 是否為收盤確認棒
        :return: (OrderAction, 觸發原因) 或 None
        """
        # 鐵律：未收線毛刺一律不計算
        if not is_kline_closed:
            return None

        self.bars.append(bar)
        if len(self.bars) > 100:
            self.bars.pop(0)

        # 基礎歷史長度檢查
        if len(self.bars) < max(self.config.kc_ema_period, self.config.breakout_lookback) + 5:
            return None

        # 計算即時指標
        indicators = self._calculate_indicators()
        if not indicators:
            return None

        # 處理平倉冷卻計數
        if self.cooldown_remaining > 0:
            self.cooldown_remaining -= 1
            if self.state == TradeState.IDLE:
                return None

        # 狀態機分流
        if self.state == TradeState.IDLE:
            return self._evaluate_entries(bar, indicators)
        elif self.state == TradeState.HOLD_LONG:
            return self._evaluate_exit_long(bar, indicators)
        elif self.state == TradeState.HOLD_SHORT:
            return self._evaluate_exit_short(bar, indicators)

        return None

    def _evaluate_entries(self, bar: Bar, ind: Dict[str, Any]) -> Optional[Tuple[OrderAction, str]]:
        # 多頭開倉審查
        long_signal, long_reason = self._check_long_entry(bar, ind)
        if long_signal:
            self._enter_position(TradeState.HOLD_LONG, bar.close, ind["atr"], bar.timestamp)
            return OrderAction.OPEN_LONG, long_reason

        # 空頭開倉審查
        short_signal, short_reason = self._check_short_entry(bar, ind)
        if short_signal:
            self._enter_position(TradeState.HOLD_SHORT, bar.close, ind["atr"], bar.timestamp)
            return OrderAction.OPEN_SHORT, short_reason

        return None

    def _check_long_entry(self, bar: Bar, ind: Dict[str, Any]) -> Tuple[bool, str]:
        kc_mid = ind["kc_mid"]
        prev_kc_mid = ind["prev_kc_mid"]
        atr = ind["atr"]

        # 1. 趨勢與位置
        kc_trending_up = kc_mid >= prev_kc_mid
        above_mid = bar.close > kc_mid
        if not (kc_trending_up and above_mid):
            return False, "KC_NOT_BULLISH_OR_BELOW_MID"

        # 2. 動能破位確認
        highest_prev_n = ind["highest_prev_n"]
        if bar.close <= highest_prev_n:
            return False, "NOT_BREAKOUT_N_HIGH"

        if not bar.is_bullish or bar.body < (self.config.min_body_atr_ratio * atr):
            return False, "INSUFFICIENT_BULLISH_BODY"

        if bar.upper_wick > (self.config.max_wick_ratio * bar.body):
            return False, "UPPER_WICK_TOO_LONG"

        # 3. 中軌乖離防過度追高
        mid_bias_atr = (bar.close - kc_mid) / atr
        if mid_bias_atr > self.config.max_mid_bias_atr:
            return False, f"LONG_CHASE_EXCESSIVE_BIAS({mid_bias_atr:.2f} ATR)"

        return True, "LONG_MOMENTUM_BREAKOUT_CONFIRMED"

    def _check_short_entry(self, bar: Bar, ind: Dict[str, Any]) -> Tuple[bool, str]:
        kc_mid = ind["kc_mid"]
        prev_kc_mid = ind["prev_kc_mid"]
        atr = ind["atr"]

        # 1. 趨勢與位置
        kc_trending_down = kc_mid <= prev_kc_mid
        below_mid = bar.close < kc_mid
        if not (kc_trending_down and below_mid):
            return False, "KC_NOT_BEARISH_OR_ABOVE_MID"

        # 2. 動能破位確認
        lowest_prev_n = ind["lowest_prev_n"]
        if bar.close >= lowest_prev_n:
            return False, "NOT_BREAKDOWN_N_LOW"

        if not bar.is_bearish or bar.body < (self.config.min_body_atr_ratio * atr):
            return False, "INSUFFICIENT_BEARISH_BODY"

        if bar.lower_wick > (self.config.max_wick_ratio * bar.body):
            return False, "LOWER_WICK_TOO_LONG"

        # 3. 中軌負乖離防過度追低
        mid_bias_atr = (kc_mid - bar.close) / atr
        if mid_bias_atr > self.config.max_mid_bias_atr:
            return False, f"SHORT_CHASE_EXCESSIVE_BIAS({mid_bias_atr:.2f} ATR)"

        return True, "SHORT_MOMENTUM_BREAKDOWN_CONFIRMED"

    def _evaluate_exit_long(self, bar: Bar, ind: Dict[str, Any]) -> Optional[Tuple[OrderAction, str]]:
        pos = self.position
        if not pos:
            return None

        pos.highest_price = max(pos.highest_price, bar.high)

        # 1. 浮盈鎖定與移動追蹤
        unrealized_pnl_pct = (bar.close - pos.entry_price) / pos.entry_price
        if unrealized_pnl_pct >= self.config.profit_lock_threshold:
            if not pos.profit_lock_active:
                pos.profit_lock_active = True
                pos.trailing_stop = max(pos.entry_price * 1.01, pos.highest_price - (self.config.trailing_sl_atr * ind["atr"]))
            else:
                pos.trailing_stop = max(pos.trailing_stop, pos.highest_price - (self.config.trailing_sl_atr * ind["atr"]))

        # 2. 停損與移動停利
        if bar.close <= pos.initial_stop_loss:
            self._close_position()
            return OrderAction.CLOSE_LONG, "EXIT_LONG_HARD_SL"

        if pos.profit_lock_active and bar.close <= pos.trailing_stop:
            self._close_position()
            return OrderAction.CLOSE_LONG, f"EXIT_LONG_TRAILING_LOCK({unrealized_pnl_pct*100:.1f}%)"

        # 3. 正常回踩續抱 (Veto Hold)
        kc_trending_up = ind["kc_mid"] >= ind["prev_kc_mid"]
        above_mid = bar.close >= ind["kc_mid"]
        if kc_trending_up and above_mid:
            return None

        # 4. 平多條件雙重確認
        broken_mid = bar.close < ind["kc_mid"]
        ma3_turning_down = ind["ma3"] < ind["prev_ma3"]
        kc_decaying = ind["kc_mid"] <= ind["prev_kc_mid"]

        if broken_mid or (ma3_turning_down and kc_decaying):
            self._close_position()
            return OrderAction.CLOSE_LONG, "EXIT_LONG_DOUBLE_CONFIRMATION_DECAY"

        return None

    def _evaluate_exit_short(self, bar: Bar, ind: Dict[str, Any]) -> Optional[Tuple[OrderAction, str]]:
        pos = self.position
        if not pos:
            return None

        pos.lowest_price = min(pos.lowest_price, bar.low)

        # 1. 浮盈鎖定與移動追蹤
        unrealized_pnl_pct = (pos.entry_price - bar.close) / pos.entry_price
        if unrealized_pnl_pct >= self.config.profit_lock_threshold:
            if not pos.profit_lock_active:
                pos.profit_lock_active = True
                pos.trailing_stop = min(pos.entry_price * 0.99, pos.lowest_price + (self.config.trailing_sl_atr * ind["atr"]))
            else:
                pos.trailing_stop = min(pos.trailing_stop, pos.lowest_price + (self.config.trailing_sl_atr * ind["atr"]))

        # 2. 停損與移動停利
        if bar.close >= pos.initial_stop_loss:
            self._close_position()
            return OrderAction.CLOSE_SHORT, "EXIT_SHORT_HARD_SL"

        if pos.profit_lock_active and bar.close >= pos.trailing_stop:
            self._close_position()
            return OrderAction.CLOSE_SHORT, f"EXIT_SHORT_TRAILING_LOCK({unrealized_pnl_pct*100:.1f}%)"

        # 3. 正常回抽續抱 (Veto Hold)
        kc_trending_down = ind["kc_mid"] <= ind["prev_kc_mid"]
        below_mid = bar.close <= ind["kc_mid"]
        if kc_trending_down and below_mid:
            return None

        # 4. 平空條件雙重確認
        broken_mid = bar.close > ind["kc_mid"]
        ma3_turning_up = ind["ma3"] > ind["prev_ma3"]
        kc_reversing = ind["kc_mid"] >= ind["prev_kc_mid"]

        if broken_mid or (ma3_turning_up and kc_reversing):
            self._close_position()
            return OrderAction.CLOSE_SHORT, "EXIT_SHORT_DOUBLE_CONFIRMATION_REVERSAL"

        return None

    def _enter_position(self, state: TradeState, price: float, atr: float, timestamp: float):
        self.state = state
        if state == TradeState.HOLD_LONG:
            initial_sl = price - (self.config.initial_sl_atr * atr)
            self.position = Position(
                state=state,
                entry_price=price,
                entry_time=timestamp,
                size=1.0,
                highest_price=price,
                lowest_price=price,
                initial_stop_loss=initial_sl,
            )
        else:
            initial_sl = price + (self.config.initial_sl_atr * atr)
            self.position = Position(
                state=state,
                entry_price=price,
                entry_time=timestamp,
                size=1.0,
                highest_price=price,
                lowest_price=price,
                initial_stop_loss=initial_sl,
            )

    def _close_position(self):
        self.state = TradeState.IDLE
        self.position = None
        self.cooldown_remaining = self.config.cooldown_bars_after_exit

    def _calculate_indicators(self) -> Optional[Dict[str, Any]]:
        n_bars = len(self.bars)
        closes = [b.close for b in self.bars]

        # EMA 20 (KC 中軌)
        period = self.config.kc_ema_period
        multiplier = 2.0 / (period + 1.0)
        ema_values = []
        val = closes[0]
        for c in closes:
            val = (c - val) * multiplier + val
            ema_values.append(val)

        curr_kc_mid = ema_values[-1]
        prev_kc_mid = ema_values[-2]

        # MA 3
        ma_fast_period = self.config.ma_fast_period
        curr_ma3 = sum(closes[-ma_fast_period:]) / ma_fast_period
        prev_ma3 = sum(closes[-ma_fast_period - 1 : -1]) / ma_fast_period

        # ATR
        trs = []
        for i in range(1, n_bars):
            h, l, prev_c = self.bars[i].high, self.bars[i].low, self.bars[i - 1].close
            tr = max(h - l, abs(h - prev_c), abs(l - prev_c))
            trs.append(tr)

        atr_period = self.config.atr_period
        if len(trs) < atr_period:
            return None
        atr = sum(trs[-atr_period:]) / atr_period

        # 前 N 根極值 (不含當根已收線 K 棒)
        lookback = self.config.breakout_lookback
        prev_n_bars = self.bars[-lookback - 1 : -1]
        highest_prev_n = max(b.high for b in prev_n_bars)
        lowest_prev_n = min(b.low for b in prev_n_bars)

        return {
            "kc_mid": curr_kc_mid,
            "prev_kc_mid": prev_kc_mid,
            "ma3": curr_ma3,
            "prev_ma3": prev_ma3,
            "atr": atr,
            "highest_prev_n": highest_prev_n,
            "lowest_prev_n": lowest_prev_n,
        }
