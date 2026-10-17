"""Mathematical entry detector for WILD_SWING breakout and continuation modes."""

import math
from collections.abc import Mapping, Sequence
from typing import Any, Optional


INITIAL_2BAR_BREAKOUT = "INITIAL_2BAR_BREAKOUT"
CONTINUATION_OUTSIDE_KC = "CONTINUATION_OUTSIDE_KC"
INSTANT_BREAKOUT_INSIDE = "INSTANT_BREAKOUT_INSIDE"
INSTANT_BREAKOUT_OUTSIDE = "INSTANT_BREAKOUT_OUTSIDE"
CONFIRMED_2BAR_BREAKOUT = "CONFIRMED_2BAR_BREAKOUT"
MA_CROSSOVER = "MA_CROSSOVER"
INSTANT_BREAKOUT_CONFIRMATIONS = frozenset({
    INSTANT_BREAKOUT_INSIDE,
    INSTANT_BREAKOUT_OUTSIDE,
})
PURE_MATH_ENTRY_CONFIRMATIONS = frozenset({
    INSTANT_BREAKOUT_INSIDE,
    INSTANT_BREAKOUT_OUTSIDE,
    CONFIRMED_2BAR_BREAKOUT,
    CONTINUATION_OUTSIDE_KC,
    MA_CROSSOVER,
})
class BreakoutContinuationEntryGate:
    """Detect the approved mathematical WILD_SWING entry patterns."""

    @staticmethod
    def _number(value: Any) -> Optional[float]:
        try:
            number = float(value)
        except (TypeError, ValueError, OverflowError):
            return None
        return number if math.isfinite(number) else None

    @classmethod
    def evaluate_pure_math(
        cls,
        closed_klines: Sequence[Mapping[str, Any]],
        kc_upper: Sequence[float],
        kc_lower: Sequence[float],
        kc_basis: Sequence[float],
        ma5_series: Sequence[float],
        ma15_series: Sequence[float],
        *,
        tick_price: Optional[float] = None,
        live_candle: Optional[Mapping[str, Any]] = None,
    ) -> Optional[dict[str, Any]]:
        """Evaluate the five MA5-directed modes from closed data and a live quote."""
        count = len(closed_klines)
        if count < 2 or any(
            len(values) != count
            for values in (kc_upper, kc_lower, kc_basis, ma5_series, ma15_series)
        ):
            return None

        def valid_candle(candle: Mapping[str, Any]) -> Optional[dict[str, float]]:
            if not isinstance(candle, Mapping):
                return None
            values = {
                name: cls._number(candle.get(name))
                for name in ("open", "high", "low", "close")
            }
            if any(value is None or value <= 0 for value in values.values()):
                return None
            result = {name: float(value) for name, value in values.items()}
            if (
                result["high"] < max(
                    result["open"], result["close"], result["low"],
                )
                or result["low"] > min(
                    result["open"], result["close"], result["high"],
                )
            ):
                return None
            return result

        recent_closed = [
            valid_candle(candle)
            for candle in closed_klines[-min(count, 4):]
        ]
        if any(candle is None for candle in recent_closed):
            return None
        previous, current = recent_closed[-2:]
        if previous is None or current is None:
            return None

        upper_values = [cls._number(value) for value in kc_upper[-2:]]
        lower_values = [cls._number(value) for value in kc_lower[-2:]]
        basis_values = [cls._number(value) for value in kc_basis[-2:]]
        ma5_values = [cls._number(value) for value in ma5_series[-2:]]
        ma15_values = [cls._number(value) for value in ma15_series[-2:]]
        if any(
            value is None
            for series in (
                upper_values, lower_values, basis_values, ma5_values, ma15_values,
            )
            for value in series
        ):
            return None
        previous_upper, upper = (float(value) for value in upper_values)
        previous_lower, lower = (float(value) for value in lower_values)
        basis = float(basis_values[-1])
        previous_ma5, closed_ma5 = (float(value) for value in ma5_values)
        previous_ma15, current_ma15 = (float(value) for value in ma15_values)
        effective_ma5 = closed_ma5
        effective_previous_ma5 = previous_ma5
        if (
            upper <= lower
            or previous_upper <= float(previous_lower)
            or not lower <= basis <= upper
        ):
            return None

        def signal(
            action: str,
            entry_type: str,
            price: float,
            reason: str,
            *,
            current_ma5: Optional[float] = None,
            current_basis: Optional[float] = None,
            is_intrabar: bool = False,
        ) -> dict[str, Any]:
            return {
                "action": action,
                "type": entry_type,
                "price": price,
                "is_intrabar": is_intrabar,
                "ma5_current": (
                    effective_ma5 if current_ma5 is None else current_ma5
                ),
                "ma5_previous": effective_previous_ma5,
                "ma15_current": current_ma15,
                "ma15_previous": previous_ma15,
                "kc_upper": upper,
                "kc_lower": lower,
                "kc_basis": basis if current_basis is None else current_basis,
                "reason": reason,
            }

        if tick_price is not None and live_candle is not None:
            live = valid_candle(live_candle)
            tick = cls._number(tick_price)
            live_upper = cls._number(live_candle.get("kc_upper"))
            live_lower = cls._number(live_candle.get("kc_lower"))
            live_basis = cls._number(live_candle.get("kc_middle"))
            live_timestamp = cls._number(live_candle.get("timestamp"))
            closed_timestamp = cls._number(closed_klines[-1].get("timestamp"))
            live_is_closed = live_candle.get("is_closed")
            if (
                live is not None
                and tick is not None
                and tick > 0
                and live_upper is not None
                and live_lower is not None
                and live_basis is not None
                and live_upper > live_lower
                and live_lower <= live_basis <= live_upper
                and live_timestamp is not None
                and closed_timestamp is not None
                and live_timestamp > closed_timestamp
                and live_is_closed is not None
                and not bool(live_is_closed)
                and live["low"] <= tick <= live["high"]
            ):
                close_history = [
                    candle["close"] for candle in recent_closed[-4:]
                ]
                live_ma5 = (
                    (sum(close_history) + tick) / 5.0
                    if count >= 4
                    and all(value is not None for value in close_history)
                    else None
                )
                if live_ma5 is not None:
                    effective_ma5 = live_ma5
                    effective_previous_ma5 = closed_ma5
                ma5_up = live_ma5 is not None and live_ma5 > closed_ma5
                ma5_down = live_ma5 is not None and live_ma5 < closed_ma5
                live_basis = float(live_basis)
                live_bullish = tick > live["open"]
                live_bearish = tick < live["open"]
                if (
                    live_bullish and ma5_up
                    and live["open"] <= float(live_upper)
                    and tick > float(live_upper)
                ):
                    return signal(
                        "OPEN_LONG",
                        INSTANT_BREAKOUT_INSIDE,
                        tick,
                        "即時突破：當根開盤在上軌內且最新價突破上軌，MA5 向上",
                        current_ma5=live_ma5,
                        current_basis=live_basis,
                        is_intrabar=True,
                    )
                if (
                    live_bullish and ma5_up
                    and live["open"] > float(live_upper)
                    and tick > float(live_upper)
                    and tick > current["high"]
                ):
                    return signal(
                        "OPEN_LONG",
                        INSTANT_BREAKOUT_OUTSIDE,
                        tick,
                        "外軌加速：開盤在上軌外且最新價突破前 K 高點，MA5 向上",
                        current_ma5=live_ma5,
                        current_basis=live_basis,
                        is_intrabar=True,
                    )
                if (
                    live_bearish and ma5_down
                    and live["open"] >= float(live_lower)
                    and tick < float(live_lower)
                ):
                    return signal(
                        "OPEN_SHORT",
                        INSTANT_BREAKOUT_INSIDE,
                        tick,
                        "即時跌破：當根開盤在下軌內且最新價跌破下軌，MA5 向下",
                        current_ma5=live_ma5,
                        current_basis=live_basis,
                        is_intrabar=True,
                    )
                if (
                    live_bearish and ma5_down
                    and live["open"] < float(live_lower)
                    and tick < float(live_lower)
                    and tick < current["low"]
                ):
                    return signal(
                        "OPEN_SHORT",
                        INSTANT_BREAKOUT_OUTSIDE,
                        tick,
                        "外軌加速：開盤在下軌外且最新價跌破前 K 低點，MA5 向下",
                        current_ma5=live_ma5,
                        current_basis=live_basis,
                        is_intrabar=True,
                    )
                if live_bullish and ma5_up and tick > float(live_upper):
                    return signal(
                        "OPEN_LONG",
                        CONTINUATION_OUTSIDE_KC,
                        tick,
                        "KC 上軌外即時延續且 MA5 向上",
                        current_ma5=live_ma5,
                        current_basis=live_basis,
                        is_intrabar=True,
                    )
                if live_bearish and ma5_down and tick < float(live_lower):
                    return signal(
                        "OPEN_SHORT",
                        CONTINUATION_OUTSIDE_KC,
                        tick,
                        "KC 下軌外即時延續且 MA5 向下",
                        current_ma5=live_ma5,
                        current_basis=live_basis,
                        is_intrabar=True,
                    )

        previous_bullish = previous["close"] > previous["open"]
        current_bullish = current["close"] > current["open"]
        previous_bearish = previous["close"] < previous["open"]
        current_bearish = current["close"] < current["open"]
        ma5_up = effective_ma5 > effective_previous_ma5
        ma5_down = effective_ma5 < effective_previous_ma5

        if (
            previous_bullish
            and current_bullish
            and previous["open"] <= previous_upper < previous["close"]
            and current["close"] > upper
        ):
            return signal(
                "OPEN_LONG",
                CONFIRMED_2BAR_BREAKOUT,
                current["close"],
                "兩根收線確認：首根實體突破上軌，次根同色收在上軌外",
            )
        if (
            previous_bearish
            and current_bearish
            and previous["open"] >= float(previous_lower) > previous["close"]
            and current["close"] < lower
        ):
            return signal(
                "OPEN_SHORT",
                CONFIRMED_2BAR_BREAKOUT,
                current["close"],
                "兩根收線確認：首根實體跌破下軌，次根同色收在下軌外",
            )
        if current_bullish and current["close"] > upper and ma5_up:
            return signal(
                "OPEN_LONG",
                CONTINUATION_OUTSIDE_KC,
                current["close"],
                "KC 上軌外收線且 MA5 嚴格向上",
            )
        if current_bearish and current["close"] < lower and ma5_down:
            return signal(
                "OPEN_SHORT",
                CONTINUATION_OUTSIDE_KC,
                current["close"],
                "KC 下軌外收線且 MA5 嚴格向下",
            )

        golden_cross = (
            previous_ma5 <= previous_ma15
            and closed_ma5 > current_ma15
            and current["close"] > basis
            and ma5_up
        )
        death_cross = (
            previous_ma5 >= previous_ma15
            and closed_ma5 < current_ma15
            and current["close"] < basis
            and ma5_down
        )
        if golden_cross:
            return signal(
                "OPEN_LONG",
                MA_CROSSOVER,
                current["close"],
                "MA5 金叉 MA15，收盤在 KC 中軌上且 MA5 向上",
            )
        if death_cross:
            return signal(
                "OPEN_SHORT",
                MA_CROSSOVER,
                current["close"],
                "MA5 死叉 MA15，收盤在 KC 中軌下且 MA5 向下",
            )
        return None

    @classmethod
    def validate_closed_outer_entry(
        cls,
        closed_klines: Sequence[Mapping[str, Any]],
        side: str,
        confirmation: str,
    ) -> Optional[dict[str, Any]]:
        """Re-derive a confirmed breakout or continuation from closed candles."""
        if confirmation not in {
            CONFIRMED_2BAR_BREAKOUT,
            CONTINUATION_OUTSIDE_KC,
        }:
            return None
        try:
            ma5_key = (
                "_wild_ma5"
                if closed_klines and "_wild_ma5" in closed_klines[-1]
                else "ma5"
            )
            candidate = cls.evaluate_pure_math(
                closed_klines,
                [row["kc_upper"] for row in closed_klines],
                [row["kc_lower"] for row in closed_klines],
                [row["kc_middle"] for row in closed_klines],
                [row[ma5_key] for row in closed_klines],
                [row["ma15"] for row in closed_klines],
            )
        except (KeyError, TypeError, IndexError):
            return None
        if (
            candidate is None
            or candidate["action"] != f"OPEN_{str(side).upper()}"
            or candidate["type"] != confirmation
        ):
            return None
        return candidate

    @classmethod
    def _evaluate_legacy(
        cls,
        klines: Sequence[Mapping[str, Any]],
        kc_upper: Sequence[float],
        kc_lower: Sequence[float],
        kc_basis: Sequence[float],
        has_position: bool = False,
        ma5_series: Optional[Sequence[float]] = None,
    ) -> Optional[dict[str, str]]:
        if has_position or len(klines) < 5:
            return None

        if not (
            len(kc_upper) == len(klines)
            and len(kc_lower) == len(klines)
            and len(kc_basis) == len(klines)
        ):
            return None

        candles: list[dict[str, float]] = []
        for candle in klines[-4:]:
            if not isinstance(candle, Mapping):
                return None
            values = {
                name: cls._number(candle.get(name))
                for name in ("open", "high", "low", "close")
            }
            if any(value is None for value in values.values()):
                return None
            open_price = values["open"]
            high = values["high"]
            low = values["low"]
            close = values["close"]
            if (
                open_price is None
                or high is None
                or low is None
                or close is None
                or min(open_price, high, low, close) <= 0
                or high < max(open_price, close, low)
                or low > min(open_price, close, high)
            ):
                return None
            candles.append({
                "open": open_price,
                "high": high,
                "low": low,
                "close": close,
            })

        upper = cls._number(kc_upper[-1])
        lower = cls._number(kc_lower[-1])
        basis = cls._number(kc_basis[-1])
        previous_upper = cls._number(kc_upper[-2])
        previous_lower = cls._number(kc_lower[-2])
        if (
            upper is None
            or lower is None
            or basis is None
            or previous_upper is None
            or previous_lower is None
            or upper <= lower
            or previous_upper <= previous_lower
            or not lower <= basis <= upper
        ):
            return None

        k0, k1, k2, k3 = candles[::-1]
        ma5_current = None
        ma5_previous = None
        if ma5_series is not None:
            if len(ma5_series) != len(klines):
                return None
            ma5_current = cls._number(ma5_series[-1])
            ma5_previous = cls._number(ma5_series[-2])
            if ma5_current is None or ma5_previous is None:
                return None

        def candle_info(candle: Mapping[str, float]) -> tuple[float, bool, bool]:
            body = abs(candle["close"] - candle["open"])
            return body, candle["close"] > candle["open"], candle["close"] < candle["open"]

        body0, green0, red0 = candle_info(k0)
        body1, green1, red1 = candle_info(k1)
        body2, green2, red2 = candle_info(k2)
        body3, _, _ = candle_info(k3)
        is_big_candle = body0 > ((body1 + body2 + body3) / 3.0) * 1.4

        k0_body_low = min(k0["open"], k0["close"])
        k0_body_high = max(k0["open"], k0["close"])
        k1_body_low = min(k1["open"], k1["close"])
        k1_body_high = max(k1["open"], k1["close"])

        if (
            k1_body_low <= previous_upper < k1_body_high
            and k0_body_low > upper
        ):
            return {
                "action": "OPEN_LONG",
                "type": INITIAL_2BAR_BREAKOUT,
                "reason": (
                    f"兩根確認破上軌：前 K 實體高點 "
                    f"{k1_body_high:.5f} > {previous_upper:.5f}，"
                    f"當前 K 實體低點 {k0_body_low:.5f} > {upper:.5f}"
                ),
            }

        if (
            k1_body_low < previous_lower <= k1_body_high
            and k0_body_high < lower
        ):
            return {
                "action": "OPEN_SHORT",
                "type": INITIAL_2BAR_BREAKOUT,
                "reason": (
                    f"兩根確認破下軌：前 K 實體低點 "
                    f"{k1_body_low:.5f} < {previous_lower:.5f}，"
                    f"當前 K 實體高點 {k0_body_high:.5f} < {lower:.5f}"
                ),
            }

        if (
            ma5_current is not None
            and ma5_previous is not None
            and k0["close"] > upper
            and ma5_current > ma5_previous
        ):
            return {
                "action": "OPEN_LONG",
                "type": CONTINUATION_OUTSIDE_KC,
                "reason": (
                    f"KC 上軌外續推：收盤 {k0['close']:.5f} > {upper:.5f}，"
                    f"MA5 向上 {ma5_current:.5f} > {ma5_previous:.5f}"
                ),
            }

        if (
            ma5_current is not None
            and ma5_previous is not None
            and k0["close"] < lower
            and ma5_current < ma5_previous
        ):
            return {
                "action": "OPEN_SHORT",
                "type": CONTINUATION_OUTSIDE_KC,
                "reason": (
                    f"KC 下軌外續推：收盤 {k0['close']:.5f} < {lower:.5f}，"
                    f"MA5 向下 {ma5_current:.5f} < {ma5_previous:.5f}"
                ),
            }

        if (
            k0["open"] <= upper
            and k0["close"] > upper
            and green0
            and is_big_candle
            and (red1 or red2)
        ):
            return {
                "action": "OPEN_LONG",
                "type": "CHANNEL_CONSOLIDATION_BREAKOUT",
                "reason": (
                    f"通道內蓄勢大綠破軌: Open={k0['open']:.5f} <= 上軌, "
                    f"Close={k0['close']:.5f} 破軌"
                ),
            }

        if (
            k0["open"] >= lower
            and k0["close"] < lower
            and red0
            and is_big_candle
            and (green1 or green2)
        ):
            return {
                "action": "OPEN_SHORT",
                "type": "CHANNEL_CONSOLIDATION_BREAKOUT",
                "reason": (
                    f"通道內蓄勢大紅破軌: Open={k0['open']:.5f} >= 下軌, "
                    f"Close={k0['close']:.5f} 破軌"
                ),
            }

        pullback_high = max(k1["high"], k2["high"])
        pullback_low = min(k1["low"], k2["low"])
        outside_long_base = k1["close"] > basis and k2["close"] > basis
        if (
            k0["open"] > basis
            and outside_long_base
            and green0
            and is_big_candle
            and k0["close"] > pullback_high
            and k0["close"] > upper
        ):
            return {
                "action": "OPEN_LONG",
                "type": "OUTSIDE_CHANNEL_CONTINUATION",
                "reason": (
                    f"KC外小陰回踩不破中軌 + 大綠創新高續推: "
                    f"收盤={k0['close']:.5f} 突破回踩高點={pullback_high:.5f}"
                ),
            }

        outside_short_base = k1["close"] < basis and k2["close"] < basis
        if (
            k0["open"] < basis
            and outside_short_base
            and red0
            and is_big_candle
            and k0["close"] < pullback_low
            and k0["close"] < lower
        ):
            return {
                "action": "OPEN_SHORT",
                "type": "OUTSIDE_CHANNEL_CONTINUATION",
                "reason": (
                    f"KC外小陽反彈不破中軌 + 大紅破底續推: "
                    f"收盤={k0['close']:.5f} 跌破回踩低點={pullback_low:.5f}"
                ),
            }

        return None

    @classmethod
    def evaluate(
        cls,
        klines: Sequence[Mapping[str, Any]],
        kc_upper: Sequence[float],
        kc_lower: Sequence[float],
        kc_basis: Sequence[float],
        has_position: bool = False,
        ma5_series: Optional[Sequence[float]] = None,
        ma15_series: Optional[Sequence[float]] = None,
    ) -> Optional[dict[str, Any]]:
        """Evaluate the closed-candle subset of the five authorized modes."""
        if has_position or ma5_series is None or ma15_series is None:
            return None
        return cls.evaluate_pure_math(
            klines,
            kc_upper,
            kc_lower,
            kc_basis,
            ma5_series,
            ma15_series,
        )
