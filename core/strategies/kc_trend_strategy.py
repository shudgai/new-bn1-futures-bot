"""
KC + MA 趨勢訊號生成核心模組 (Signal Generator Only)
Path: core/strategies/kc_trend_strategy.py
"""

from __future__ import annotations

import math
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Deque, Dict, List, Literal, Optional, Set, Tuple

Side = Literal["long", "short"]


@dataclass
class Bar:
    timestamp: float
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0

    @property
    def body(self) -> float:
        return abs(self.close - self.open)

    @property
    def bullish(self) -> bool:
        return self.close > self.open

    @property
    def bearish(self) -> bool:
        return self.close < self.open

    @property
    def range(self) -> float:
        return max(0.0, self.high - self.low)


@dataclass
class EntrySignal:
    side: Side
    reason: str
    timestamp: float
    price: float
    strength: float = 1.0
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ExitSignal:
    side: Side
    reason: str
    timestamp: float
    price: float
    fraction: float = 1.0
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class StrategyConfig:
    # 指標參數
    kc_ema_period: int = 20
    atr_period: int = 20
    kc_atr_multiplier: float = 1.5
    ma_fast_period: int = 3
    ma_slow_period: int = 15

    # K 棒型態過濾 (相對於 ATR)
    small_body_atr_max: float = 0.25
    large_body_atr_min: float = 0.80
    pattern_small_bars_min: int = 1
    pattern_small_bars_max: int = 2

    # 斜率與盤整過濾
    ma3_flat_slope_atr: float = 0.035
    ma15_flat_slope_atr: float = 0.018
    slope_lookback: int = 3
    consolidation_kc_width_atr_max: float = 1.60
    consolidation_range_atr_max: float = 2.40
    consolidation_lookback: int = 12
    ma3_oscillation_lookback: int = 8
    ma3_oscillation_crosses_min: int = 3
    ma3_oscillation_slope_flip_min: int = 4

    # 大趨勢方向過濾
    require_ma15_direction: bool = True
    ma15_direction_slope_atr: float = 0.015

    # 延續追單與開倉限制
    continuation_enabled: bool = True
    continuation_max_entries_per_breakout: int = 3
    continuation_require_candle_direction: bool = True
    cooldown_bars_after_exit: int = 1

    # 疑似爆倉急跌做空代理
    liquidation_proxy_enabled: bool = True
    liquidation_return_atr_min: float = 1.25
    liquidation_volume_multiple_min: float = 2.5

    # 退場參數
    middle_band_exit_buffer_atr: float = 0.05
    emergency_single_bar_move_atr: float = 3.0
    emergency_volume_multiple: float = 4.0
    partial_take_profit_fraction: float = 0.50
    partial_take_profit_atr: float = 1.5

    # 資料新鮮度過濾
    max_bar_age_seconds: float = 180.0
    min_bars_for_indicators: int = 25


class KCTrendStrategy:
    def __init__(self, config: Optional[StrategyConfig] = None):
        self.cfg = config or StrategyConfig()
        self.bars: Deque[Bar] = deque(maxlen=500)
        self.current_bar: Optional[Bar] = None

        # 單調遞增計數器與歷史快照
        self._bar_counter: int = 0
        self._last_closed_snapshot: Optional[Dict[str, float]] = None

        # 狀態控制
        self._signal_keys: Set[Tuple[int, str, str]] = set()
        self.last_entry_bar_index: Dict[Side, int] = {"long": -10**9, "short": -10**9}
        self.last_exit_bar_index: Dict[Side, int] = {"long": -10**9, "short": -10**9}
        self.breakout_side: Optional[Side] = None
        self.continuation_count: int = 0

        # 持倉同步追蹤 (嚴格由撮合回報驅動)
        self.partial_taken: Dict[Side, bool] = {"long": False, "short": False}
        self.position_entry_price: Dict[Side, Optional[float]] = {"long": None, "short": None}
        self.position_entry_atr: Dict[Side, Optional[float]] = {"long": None, "short": None}

        # 待提取進場訊號佇列
        self._pending_entries: Deque[EntrySignal] = deque()
        self._last_emergency_bar_timestamp: Optional[float] = None

    @property
    def bar_index(self) -> int:
        return self._bar_counter

    @staticmethod
    def _normalize_timestamp(ts: float) -> float:
        return ts / 1000.0 if ts > 1e11 else ts

    @staticmethod
    def _sma(values: List[float], period: int) -> Optional[float]:
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    @staticmethod
    def _ema(values: List[float], period: int) -> Optional[float]:
        if len(values) < period:
            return None
        alpha = 2.0 / (period + 1.0)
        value = sum(values[:period]) / period
        for x in values[period:]:
            value = alpha * x + (1.0 - alpha) * value
        return value

    def _indicator_snapshot(self, include_current: bool = False) -> Optional[Dict[str, float]]:
        bars = list(self.bars)
        if include_current and self.current_bar is not None:
            if not bars or bars[-1].timestamp != self.current_bar.timestamp:
                bars.append(self.current_bar)
            else:
                bars[-1] = self.current_bar

        need = max(
            self.cfg.kc_ema_period,
            self.cfg.atr_period + 1,
            self.cfg.ma_slow_period,
            self.cfg.consolidation_lookback,
        )
        if len(bars) < max(need, self.cfg.min_bars_for_indicators):
            return None

        closes = [b.close for b in bars]
        volumes = [b.volume for b in bars]

        true_ranges: List[float] = []
        for i, b in enumerate(bars):
            if i == 0:
                tr = b.high - b.low
            else:
                prev_close = bars[i - 1].close
                tr = max(b.high - b.low, abs(b.high - prev_close), abs(b.low - prev_close))
            true_ranges.append(max(0.0, tr))

        atr = self._sma(true_ranges, self.cfg.atr_period)
        kc_mid = self._ema(closes, self.cfg.kc_ema_period)
        ma3 = self._sma(closes, self.cfg.ma_fast_period)
        ma15 = self._sma(closes, self.cfg.ma_slow_period)

        if atr is None or kc_mid is None or ma3 is None or ma15 is None or atr <= 0:
            return None

        kc_upper = kc_mid + self.cfg.kc_atr_multiplier * atr
        kc_lower = kc_mid - self.cfg.kc_atr_multiplier * atr

        slope_n = max(1, self.cfg.slope_lookback)
        ma3_now = self._sma(closes, self.cfg.ma_fast_period)
        ma15_now = self._sma(closes, self.cfg.ma_slow_period)
        ma3_old = self._sma(closes[:-slope_n], self.cfg.ma_fast_period)
        ma15_old = self._sma(closes[:-slope_n], self.cfg.ma_slow_period)
        ma3_slope = 0.0 if ma3_old is None or ma3_now is None else (ma3_now - ma3_old) / atr
        ma15_slope = 0.0 if ma15_old is None or ma15_now is None else (ma15_now - ma15_old) / atr

        lookback = min(self.cfg.consolidation_lookback, len(bars))
        recent = bars[-lookback:]
        recent_range = max(b.high for b in recent) - min(b.low for b in recent)
        kc_width_atr = (kc_upper - kc_lower) / atr
        range_atr = recent_range / atr

        osc_n = min(self.cfg.ma3_oscillation_lookback, len(closes) - self.cfg.ma_fast_period + 1)
        ma3_series = []
        for end in range(len(closes) - osc_n, len(closes)):
            sub = closes[end - self.cfg.ma_fast_period + 1 : end + 1]
            ma3_series.append(sum(sub) / self.cfg.ma_fast_period)

        slopes = [ma3_series[i] - ma3_series[i - 1] for i in range(1, len(ma3_series))]
        signs = [1 if s > 0 else -1 if s < 0 else 0 for s in slopes]
        slope_flips = sum(1 for a, b in zip(signs, signs[1:]) if a and b and a != b)

        crosses = 0
        start = max(1, len(bars) - self.cfg.ma3_oscillation_lookback)
        for i in range(start, len(bars)):
            prev_m = self._sma(closes[:i], self.cfg.ma_fast_period)
            curr_m = self._sma(closes[: i + 1], self.cfg.ma_fast_period)
            if prev_m is not None and curr_m is not None:
                if (closes[i - 1] - prev_m) * (closes[i] - curr_m) < 0:
                    crosses += 1

        avg_vol = self._sma(volumes[:-1], min(20, max(1, len(volumes) - 1))) if len(volumes) > 1 else None

        return {
            "atr": atr, "kc_mid": kc_mid, "kc_upper": kc_upper, "kc_lower": kc_lower,
            "ma3": ma3, "ma15": ma15, "ma3_slope_atr": ma3_slope,
            "ma15_slope_atr": ma15_slope, "kc_width_atr": kc_width_atr,
            "range_atr": range_atr, "ma3_slope_flips": float(slope_flips),
            "ma3_price_crosses": float(crosses),
            "avg_volume": float(avg_vol or 0.0),
        }

    def _global_filter_reason(self, side: Side, snap: Dict[str, float]) -> Optional[str]:
        if (snap["kc_width_atr"] <= self.cfg.consolidation_kc_width_atr_max and
                snap["range_atr"] <= self.cfg.consolidation_range_atr_max):
            return "blocked_consolidation"

        if abs(snap["ma3_slope_atr"]) <= self.cfg.ma3_flat_slope_atr:
            return "blocked_ma3_flat"
        if abs(snap["ma15_slope_atr"]) <= self.cfg.ma15_flat_slope_atr:
            return "blocked_ma15_flat"

        # MA3 短線順勢限制：做多禁下拐，做空禁上揚
        if side == "long" and snap["ma3_slope_atr"] < -self.cfg.ma3_flat_slope_atr:
            return "blocked_ma3_hooking_down"
        if side == "short" and snap["ma3_slope_atr"] > self.cfg.ma3_flat_slope_atr:
            return "blocked_ma3_hooking_up"

        if (snap["ma3_slope_flips"] >= self.cfg.ma3_oscillation_slope_flip_min and
                snap["ma3_price_crosses"] >= self.cfg.ma3_oscillation_crosses_min):
            return "blocked_ma3_oscillation"

        if self.cfg.require_ma15_direction:
            if side == "long" and snap["ma15_slope_atr"] < self.cfg.ma15_direction_slope_atr:
                return "blocked_ma15_not_rising"
            if side == "short" and snap["ma15_slope_atr"] > -self.cfg.ma15_direction_slope_atr:
                return "blocked_ma15_not_falling"

        return None

    def _is_fresh(self, timestamp: float) -> bool:
        ts = self._normalize_timestamp(timestamp)
        return (time.time() - ts) <= self.cfg.max_bar_age_seconds

    def _make_entry(
        self, side: Side, reason: str, price: float, timestamp: float,
        snap: Dict[str, float], strength: float = 1.0, metadata: Optional[Dict[str, Any]] = None
    ) -> Optional[EntrySignal]:
        if not self._is_fresh(timestamp):
            return None

        blocked = self._global_filter_reason(side, snap)
        if blocked:
            return None

        key = (self.bar_index, side, reason)
        if key in self._signal_keys:
            return None

        if self.bar_index - self.last_exit_bar_index[side] <= self.cfg.cooldown_bars_after_exit:
            return None

        self._signal_keys.add(key)
        self.last_entry_bar_index[side] = self.bar_index
        return EntrySignal(side, reason, self._normalize_timestamp(timestamp), price, strength, metadata or {})

    def _candle_pattern_signal(self, price: float, timestamp: float) -> Optional[EntrySignal]:
        snap = self._indicator_snapshot(include_current=True)
        if snap is None or self.current_bar is None:
            return None

        cur = self.current_bar
        atr = snap["atr"]
        if cur.body < self.cfg.large_body_atr_min * atr:
            return None

        previous = list(self.bars)
        small_count = 0
        for b in reversed(previous[-self.cfg.pattern_small_bars_max:]):
            if b.body <= self.cfg.small_body_atr_max * atr:
                small_count += 1
            else:
                break
        if not (self.cfg.pattern_small_bars_min <= small_count <= self.cfg.pattern_small_bars_max):
            return None

        if cur.bullish and price > snap["kc_upper"] and price > cur.open and price > snap["ma3"] and price > snap["ma15"]:
            return self._make_entry(
                "long", "immediate_large_bull_body_kc_break", price, timestamp, snap,
                metadata={"small_bars": small_count, "atr": atr, "kc_upper": snap["kc_upper"]}
            )
        if cur.bearish and price < snap["kc_lower"] and price < cur.open and price < snap["ma3"] and price < snap["ma15"]:
            return self._make_entry(
                "short", "immediate_large_bear_body_kc_break", price, timestamp, snap,
                metadata={"small_bars": small_count, "atr": atr, "kc_lower": snap["kc_lower"]}
            )
        return None

    def _continuation_signal(self, price: float, timestamp: float) -> Optional[EntrySignal]:
        if not self.cfg.continuation_enabled or self.breakout_side is None or self.current_bar is None:
            return None

        snap = self._indicator_snapshot(include_current=True)
        if snap is None:
            return None

        side = self.breakout_side

        # 硬性中斷：只要價格回到軌道內或站上/跌破 MA，立即強制清空破軌狀態與計數
        if side == "long" and (price <= snap["kc_upper"] or price <= snap["ma3"] or price <= snap["ma15"]):
            self.breakout_side = None
            self.continuation_count = 0
            return None

        if side == "short" and (price >= snap["kc_lower"] or price >= snap["ma3"] or price >= snap["ma15"]):
            self.breakout_side = None
            self.continuation_count = 0
            return None

        if self.continuation_count >= self.cfg.continuation_max_entries_per_breakout:
            return None

        if side == "long":
            if self.cfg.continuation_require_candle_direction and price <= self.current_bar.open:
                return None
            signal = self._make_entry(
                "long", "kc_outside_continuation", price, timestamp, snap,
                metadata={"continuation_index": self.continuation_count + 1}
            )
        else:  # side == "short"
            if self.cfg.continuation_require_candle_direction and price >= self.current_bar.open:
                return None
            signal = self._make_entry(
                "short", "kc_outside_continuation", price, timestamp, snap,
                metadata={"continuation_index": self.continuation_count + 1}
            )

        if signal:
            self.continuation_count += 1
        return signal

    def _liquidation_proxy_signal(self, price: float, volume: float, timestamp: float) -> Optional[EntrySignal]:
        if not self.cfg.liquidation_proxy_enabled or self.current_bar is None:
            return None
        snap = self._indicator_snapshot(include_current=True)
        if snap is None:
            return None

        previous_close = self.bars[-1].close if self.bars else self.current_bar.open
        drop = previous_close - price
        avg_volume = snap["avg_volume"]
        volume_spike = avg_volume > 0 and volume >= avg_volume * self.cfg.liquidation_volume_multiple_min
        sharp_drop = drop >= snap["atr"] * self.cfg.liquidation_return_atr_min

        if sharp_drop and volume_spike:
            return self._make_entry(
                "short", "suspected_liquidation_drop_proxy", price, timestamp, snap,
                strength=0.7,
                metadata={"confirmed_liquidation": False, "drop": drop, "atr": snap["atr"]}
            )
        return None

    def on_tick(self, price: float, volume: float, timestamp: Optional[float] = None) -> Optional[EntrySignal]:
        now_ts = self._normalize_timestamp(timestamp if timestamp is not None else time.time())
        if not math.isfinite(price) or price <= 0:
            return None

        if self.current_bar is None:
            self.current_bar = Bar(now_ts, price, price, price, price, volume)
        else:
            self.current_bar.high = max(self.current_bar.high, price)
            self.current_bar.low = min(self.current_bar.low, price)
            self.current_bar.close = price
            self.current_bar.volume += max(0.0, volume)

        sig = self._candle_pattern_signal(price, now_ts)
        if sig:
            self.breakout_side = sig.side
            self.continuation_count = 0
            return sig

        sig = self._continuation_signal(price, now_ts)
        if sig:
            return sig

        return self._liquidation_proxy_signal(price, volume, now_ts)

    def on_bar_close(self, bar: Bar) -> List[ExitSignal]:
        bar.timestamp = self._normalize_timestamp(bar.timestamp)
        self.bars.append(bar)
        self._bar_counter += 1
        self.current_bar = None
        exits: List[ExitSignal] = []

        # 淘汰 5 根 Bar 以前的過期訊號 key
        expire_limit = self.bar_index - 5
        self._signal_keys = {k for k in self._signal_keys if k[0] >= expire_limit}

        snap = self._indicator_snapshot()
        if snap is None:
            return exits

        # 1. 兩根確認機制 (使用 _last_closed_snapshot 防視角偏差)
        if len(self.bars) >= 2 and self._last_closed_snapshot is not None:
            bar1 = self.bars[-2]
            bar2 = self.bars[-1]
            prev_snap = self._last_closed_snapshot

            long_bar1_break = bar1.close > prev_snap["kc_upper"]
            long_bar2_valid = (
                bar2.bullish and
                bar2.close > snap["kc_upper"] and
                bar2.close > snap["ma3"] and
                bar2.close > snap["ma15"]
            )

            if long_bar1_break and long_bar2_valid:
                self.breakout_side = "long"
                self.continuation_count = 0
                sig = self._make_entry(
                    "long", "two_bar_kc_upper_breakout_confirmed",
                    bar.close, bar.timestamp, snap
                )
                if sig:
                    self._pending_entries.append(sig)

            short_bar1_break = bar1.close < prev_snap["kc_lower"]
            short_bar2_valid = (
                bar2.bearish and
                bar2.close < snap["kc_lower"] and
                bar2.close < snap["ma3"] and
                bar2.close < snap["ma15"]
            )

            if short_bar1_break and short_bar2_valid:
                self.breakout_side = "short"
                self.continuation_count = 0
                sig = self._make_entry(
                    "short", "two_bar_kc_lower_breakout_confirmed",
                    bar.close, bar.timestamp, snap
                )
                if sig:
                    self._pending_entries.append(sig)

        # 破軌延續狀態收盤重置：只要收盤回到軌道內，即刻解除延續開倉資格
        if self.breakout_side == "long" and bar.close <= snap["kc_upper"]:
            self.breakout_side = None
            self.continuation_count = 0
        elif self.breakout_side == "short" and bar.close >= snap["kc_lower"]:
            self.breakout_side = None
            self.continuation_count = 0

        # 2. MA3 / MA15 交叉確認
        if len(self.bars) >= 2:
            closes = [b.close for b in self.bars]
            ma3_prev = self._sma(closes[:-1], self.cfg.ma_fast_period)
            ma15_prev = self._sma(closes[:-1], self.cfg.ma_slow_period)
            ma3_now = snap["ma3"]
            ma15_now = snap["ma15"]

            if None not in (ma3_prev, ma15_prev, ma3_now, ma15_now):
                if ma3_prev <= ma15_prev and ma3_now > ma15_now:
                    sig = self._make_entry("long", "ma3_cross_above_ma15", bar.close, bar.timestamp, snap)
                    if sig:
                        self._pending_entries.append(sig)
                elif ma3_prev >= ma15_prev and ma3_now < ma15_now:
                    sig = self._make_entry("short", "ma3_cross_below_ma15", bar.close, bar.timestamp, snap)
                    if sig:
                        self._pending_entries.append(sig)

        # 3. KC 中線緩衝退場
        for side in ("long", "short"):
            entry_p = self.position_entry_price[side]
            if entry_p is None:
                continue

            if side == "long" and bar.close < snap["kc_mid"] - self.cfg.middle_band_exit_buffer_atr * snap["atr"]:
                exits.append(ExitSignal(side, "kc_middle_band_close_exit", bar.timestamp, bar.close))
                self._mark_exit(side)
                continue

            if side == "short" and bar.close > snap["kc_mid"] + self.cfg.middle_band_exit_buffer_atr * snap["atr"]:
                exits.append(ExitSignal(side, "kc_middle_band_close_exit", bar.timestamp, bar.close))
                self._mark_exit(side)
                continue

            entry_atr = self.position_entry_atr[side] or snap["atr"]
            if not self.partial_taken[side]:
                if side == "long" and bar.close >= entry_p + self.cfg.partial_take_profit_atr * entry_atr:
                    exits.append(ExitSignal(side, "partial_take_profit", bar.timestamp, bar.close,
                                            self.cfg.partial_take_profit_fraction))
                    self.partial_taken[side] = True
                elif side == "short" and bar.close <= entry_p - self.cfg.partial_take_profit_atr * entry_atr:
                    exits.append(ExitSignal(side, "partial_take_profit", bar.timestamp, bar.close,
                                            self.cfg.partial_take_profit_fraction))
                    self.partial_taken[side] = True

        # 4. 異常波動緊急警示退場
        if self._last_emergency_bar_timestamp != bar.timestamp:
            prev_c = self.bars[-2].close if len(self.bars) >= 2 else bar.open
            move = abs(bar.close - prev_c)
            avg_vol = snap["avg_volume"]
            vol_spike = avg_vol > 0 and bar.volume >= avg_vol * self.cfg.emergency_volume_multiple
            if move >= self.cfg.emergency_single_bar_move_atr * snap["atr"] or vol_spike:
                for side in ("long", "short"):
                    if self.position_entry_price[side] is not None:
                        exits.append(ExitSignal(
                            side, "emergency_volatility_or_volume_alert",
                            bar.timestamp, bar.close, 1.0,
                            {"move": move, "atr": snap["atr"], "volume_spike": vol_spike}
                        ))
                        self._mark_exit(side)
                self._last_emergency_bar_timestamp = bar.timestamp

        # 更新已收盤快照
        self._last_closed_snapshot = snap
        return exits

    def pop_pending_entry(self) -> Optional[EntrySignal]:
        if self._pending_entries:
            return self._pending_entries.popleft()
        return None

    def notify_position_opened(self, side: Side, avg_price: float, atr_at_entry: Optional[float] = None) -> None:
        self.position_entry_price[side] = avg_price
        snap = self._indicator_snapshot()
        self.position_entry_atr[side] = atr_at_entry or (snap["atr"] if snap else None)
        self.partial_taken[side] = False

    def notify_position_closed(self, side: Side, timestamp: Optional[float] = None) -> None:
        self._mark_exit(side)

    def _mark_exit(self, side: Side) -> None:
        self.position_entry_price[side] = None
        self.position_entry_atr[side] = None
        self.partial_taken[side] = False
        self.last_exit_bar_index[side] = self.bar_index
