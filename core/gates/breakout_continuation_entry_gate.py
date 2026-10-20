"""Mathematical two-closed-candle KC breakout entry gate."""

import math
from collections.abc import Mapping, Sequence
from typing import Any, Optional


INITIAL_2BAR_BREAKOUT = "INITIAL_2BAR_BREAKOUT"
CONTINUATION_OUTSIDE_KC = "CONTINUATION_OUTSIDE_KC"
INSTANT_BREAKOUT_INSIDE = "INSTANT_BREAKOUT_INSIDE"
INSTANT_BREAKOUT_OUTSIDE = "INSTANT_BREAKOUT_OUTSIDE"
CONFIRMED_2BAR_BREAKOUT = "CONFIRMED_2BAR_BREAKOUT"
MA_CROSSOVER = "MA_CROSSOVER"
FIVE_LINE_CONTINUATION = "FIVE_LINE_CONTINUATION"
INSTANT_BURST_BREAKOUT = "INSTANT_BURST_BREAKOUT"
DIP_SNIPER = "DIP_SNIPER"
MEME_PEAK_HUNTER = "MEME_PEAK_HUNTER"
MID_LINE_BREAKDOWN = "MID_LINE_BREAKDOWN"
MID_LINE_BREAKOUT = "MID_LINE_BREAKOUT"
INSTANT_BREAKOUT_CONFIRMATIONS = frozenset({
    INSTANT_BREAKOUT_INSIDE,
    INSTANT_BREAKOUT_OUTSIDE,
    INSTANT_BURST_BREAKOUT,
})
PURE_MATH_ENTRY_CONFIRMATIONS = frozenset({
    INSTANT_BREAKOUT_INSIDE,
    INSTANT_BREAKOUT_OUTSIDE,
    CONFIRMED_2BAR_BREAKOUT,
    CONTINUATION_OUTSIDE_KC,
    MA_CROSSOVER,
    FIVE_LINE_CONTINUATION,
    INSTANT_BURST_BREAKOUT,
    DIP_SNIPER,
    MEME_PEAK_HUNTER,
    MID_LINE_BREAKDOWN,
    MID_LINE_BREAKOUT,
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
        """Confirm the second same-color KC-break candle, including while live."""
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
            ma15_current: Optional[float] = None,
            channel_expanding: bool = False,
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
                "ma15_current": (
                    current_ma15 if ma15_current is None else ma15_current
                ),
                "ma15_previous": previous_ma15,
                "kc_upper": upper,
                "kc_lower": lower,
                "kc_basis": basis if current_basis is None else current_basis,
                "channel_expanding": channel_expanding,
                "reason": reason,
            }

        previous_bullish = previous["close"] > previous["open"]
        current_bullish = current["close"] > current["open"]
        previous_bearish = previous["close"] < previous["open"]
        current_bearish = current["close"] < current["open"]
        ma5_up = effective_ma5 > effective_previous_ma5
        ma5_down = effective_ma5 < effective_previous_ma5

        open_live_bar_after_closed = False
        if tick_price is not None and live_candle is not None:
            live = valid_candle(live_candle)
            tick = cls._number(tick_price)
            live_upper = cls._number(live_candle.get("kc_upper"))
            if live_upper is None:
                live_upper = upper
            live_lower = cls._number(live_candle.get("kc_lower"))
            if live_lower is None:
                live_lower = lower
            live_middle = cls._number(live_candle.get("kc_middle"))
            if live_middle is None:
                live_middle = basis
            live_ma15 = cls._number(live_candle.get("ma15"))
            if live_ma15 is None:
                live_ma15 = current_ma15
            live_timestamp = cls._number(live_candle.get("timestamp"))
            closed_timestamp = cls._number(closed_klines[-1].get("timestamp"))
            live_is_open = live_candle.get("is_closed") is False or (
                type(live_candle.get("is_closed")).__name__ == "bool_"
                and not bool(live_candle.get("is_closed"))
            )
            open_live_bar_after_closed = bool(
                live_is_open
                and live_timestamp is not None
                and closed_timestamp is not None
                and live_timestamp > closed_timestamp
            )
            if (
                live is not None
                and tick is not None
                and live_upper is not None
                and live_lower is not None
                and live_middle is not None
                and live_ma15 is not None
                and live_timestamp is not None
                and closed_timestamp is not None
                and live_timestamp > closed_timestamp
                and live_is_open
                and live_lower < live_middle < live_upper
                and live["low"] <= tick <= live["high"]
                and count >= 4
            ):
                average_bars = [
                    valid_candle(row) for row in closed_klines[-14:]
                ]
                average_body = (
                    sum(
                        abs(candle["close"] - candle["open"])
                        for candle in average_bars if candle is not None
                    ) / len(average_bars)
                    if len(average_bars) == 14 and all(
                        candle is not None for candle in average_bars
                    ) else 0.0
                )
                live_body = abs(tick - live["open"])

                def is_small(candle: Optional[dict[str, float]]) -> bool:
                    return bool(
                        candle is not None
                        and average_body > 0
                        and abs(candle["close"] - candle["open"])
                        <= average_body * 0.85
                    )

                prior_one = recent_closed[-1] if recent_closed else None
                prior_two = recent_closed[-2] if len(recent_closed) >= 2 else None
                prior_three = recent_closed[-3] if len(recent_closed) >= 3 else None
                small_red_one = bool(
                    is_small(prior_one) and prior_one["close"] < prior_one["open"]
                )
                small_green_one = bool(
                    is_small(prior_one) and prior_one["close"] > prior_one["open"]
                )
                long_setup = small_red_one or bool(
                    prior_two and prior_two["close"] < prior_two["open"]
                    and is_small(prior_two)
                    and prior_one and prior_one["close"] > prior_one["open"]
                    and is_small(prior_one)
                ) or bool(
                    prior_three and prior_three["close"] < prior_three["open"]
                    and is_small(prior_three)
                    and prior_two and prior_two["close"] > prior_two["open"]
                    and is_small(prior_two)
                    and prior_one and prior_one["close"] > prior_one["open"]
                    and is_small(prior_one)
                )
                short_setup = small_green_one or bool(
                    prior_two and prior_two["close"] > prior_two["open"]
                    and is_small(prior_two)
                    and prior_one and prior_one["close"] < prior_one["open"]
                    and is_small(prior_one)
                ) or bool(
                    prior_three and prior_three["close"] > prior_three["open"]
                    and is_small(prior_three)
                    and prior_two and prior_two["close"] < prior_two["open"]
                    and is_small(prior_two)
                    and prior_one and prior_one["close"] < prior_one["open"]
                    and is_small(prior_one)
                )

                recent_closes = [
                    cls._number(row.get("close"))
                    for row in closed_klines[-4:]
                ]
                live_ma5 = (
                    (sum(recent_closes) + tick) / 5.0
                    if all(value is not None for value in recent_closes)
                    else None
                )
                live_bullish = tick > live["open"]
                live_bearish = tick < live["open"]
                ma5_up = live_ma5 is not None and live_ma5 > closed_ma5
                ma5_down = live_ma5 is not None and live_ma5 < closed_ma5

                if (
                    live_bullish
                    and live["open"] <= float(live_upper)
                    and tick > float(live_upper)
                    and (live_ma5 is None or ma5_up)
                ):
                    result = signal(
                        "OPEN_LONG",
                        INSTANT_BREAKOUT_INSIDE,
                        tick,
                        "即時突破：當根開盤在上軌內且最新價突破上軌，MA5 向上",
                        current_ma5=live_ma5,
                        current_basis=live_middle,
                        ma15_current=live_ma15,
                        is_intrabar=True,
                    )
                    result["bar_id"] = live_timestamp
                    return result
                if (
                    live_bullish
                    and live["open"] > float(live_upper)
                    and tick > float(live_upper)
                    and tick > current["high"]
                    and (live_ma5 is None or ma5_up)
                ):
                    result = signal(
                        "OPEN_LONG",
                        INSTANT_BREAKOUT_OUTSIDE,
                        tick,
                        "外軌加速：開盤在上軌外且最新價突破前 K 高點，MA5 向上",
                        current_ma5=live_ma5,
                        current_basis=live_middle,
                        ma15_current=live_ma15,
                        is_intrabar=True,
                    )
                    result["bar_id"] = live_timestamp
                    return result
                if (
                    live_bearish
                    and live["open"] >= float(live_lower)
                    and tick < float(live_lower)
                    and (live_ma5 is None or ma5_down)
                ):
                    result = signal(
                        "OPEN_SHORT",
                        INSTANT_BREAKOUT_INSIDE,
                        tick,
                        "即時跌破：當根開盤在下軌內且最新價跌破下軌，MA5 向下",
                        current_ma5=live_ma5,
                        current_basis=live_middle,
                        ma15_current=live_ma15,
                        is_intrabar=True,
                    )
                    result["bar_id"] = live_timestamp
                    return result
                if (
                    live_bearish
                    and live["open"] < float(live_lower)
                    and tick < float(live_lower)
                    and tick < current["low"]
                    and (live_ma5 is None or ma5_down)
                ):
                    result = signal(
                        "OPEN_SHORT",
                        INSTANT_BREAKOUT_OUTSIDE,
                        tick,
                        "外軌加速：開盤在下軌外且最新價跌破前 K 低點，MA5 向下",
                        current_ma5=live_ma5,
                        current_basis=live_middle,
                        ma15_current=live_ma15,
                        is_intrabar=True,
                    )
                    result["bar_id"] = live_timestamp
                    return result
                # ── 模式 C：即時破軌爆發開倉 (INSTANT_BURST_BREAKOUT) ──
                # 解除中長線中軌與 MA15 滯後束縛，捕捉貫穿破軌或回踩蓄勢後的暴力穿軌爆發
                if live_ma5 is not None:
                    live_atr = float(current.get("atr", 0.0001)) if current is not None else 0.0001
                    burst_body = abs(tick - float(live["open"]))
                    has_base_long = (
                        live["open"] <= live_upper  # 盤中從軌道內實體貫穿破上軌
                        or (prior_one is not None and prior_one.get("low", 0.0) >= live_middle * 0.998)
                        or (prior_two is not None and prior_two.get("low", 0.0) >= live_middle * 0.998)
                        or (current is not None and current.get("low", 0.0) >= basis * 0.998)
                    )
                    long_upper_wick = max(0.0, float(live.get("high", tick)) - tick)
                    is_valid_long_burst = (
                        tick > live_upper
                        and live_ma5 > closed_ma5
                        and tick > live["open"]
                        and burst_body >= 0.25 * live_atr
                        and (long_upper_wick <= 0.75 * burst_body or long_upper_wick <= 0.2 * live_atr)
                        and has_base_long
                    )
                    if is_valid_long_burst:
                        result = signal(
                            "OPEN_LONG", INSTANT_BURST_BREAKOUT, tick,
                            "即時穿透 KC 上軌，短線動能爆發開多",
                            current_ma5=live_ma5, current_basis=live_middle,
                            ma15_current=live_ma15, is_intrabar=True,
                        )
                        result["bar_id"] = live_timestamp
                        return result

                    has_base_short = (
                        live["open"] >= live_lower  # 盤中從軌道內實體貫穿破下軌
                        or (prior_one is not None and prior_one.get("high", 0.0) <= live_middle * 1.002)
                        or (prior_two is not None and prior_two.get("high", 0.0) <= live_middle * 1.002)
                        or (current is not None and current.get("high", 0.0) <= basis * 1.002)
                    )
                    short_lower_wick = max(0.0, tick - float(live.get("low", tick)))
                    is_valid_short_burst = (
                        tick < live_lower
                        and live_ma5 < closed_ma5
                        and tick < live["open"]
                        and burst_body >= 0.25 * live_atr
                        and (short_lower_wick <= 0.75 * burst_body or short_lower_wick <= 0.2 * live_atr)
                        and has_base_short
                    )
                    if is_valid_short_burst:
                        result = signal(
                            "OPEN_SHORT", INSTANT_BURST_BREAKOUT, tick,
                            "即時摜破 KC 下軌，短線動能爆發開空",
                            current_ma5=live_ma5, current_basis=live_middle,
                            ma15_current=live_ma15, is_intrabar=True,
                        )
                        result["bar_id"] = live_timestamp
                        return result

                # Continue an already established outer-band move only when
                # price pushes farther outside and MA5/KC remain aligned.
                if (
                    live_ma5 is not None
                    and tick > live_upper
                    and tick > live["open"]
                    and live_ma5 > closed_ma5
                    and live_ma5 > live_middle
                    and tick >= live_ma5
                ):
                    result = signal(
                        "OPEN_LONG", CONTINUATION_OUTSIDE_KC, tick,
                        "上軌外同向續推，MA5/KC 同向，評估延續開多",
                        current_ma5=live_ma5, current_basis=live_middle,
                        ma15_current=live_ma15, is_intrabar=True,
                    )
                    result["bar_id"] = live_timestamp
                    return result
                if (
                    live_ma5 is not None
                    and tick < live_lower
                    and tick < live["open"]
                    and live_ma5 < closed_ma5
                    and live_ma5 < live_middle
                    and tick <= live_ma5
                ):
                    result = signal(
                        "OPEN_SHORT", CONTINUATION_OUTSIDE_KC, tick,
                        "下軌外同向續跌，MA5/KC 同向，評估延續開空",
                        current_ma5=live_ma5, current_basis=live_middle,
                        ma15_current=live_ma15, is_intrabar=True,
                    )
                    result["bar_id"] = live_timestamp
                    return result
                live_width = live_upper - live_lower
                closed_width = upper - lower
                expanding_live = live_width > closed_width
                long_first_break = (
                    current_bullish
                    and current["close"] > upper
                )
                short_first_break = (
                    current_bearish
                    and current["close"] < lower
                )
                if (
                    live_ma5 is not None
                    and long_first_break
                    and tick > live["open"]
                    and tick > live_upper
                    and live_ma5 > closed_ma5
                    and live_middle >= basis - 1e-6
                ):
                    result = signal(
                        "OPEN_LONG",
                        CONFIRMED_2BAR_BREAKOUT,
                        tick,
                        "第二根當根確認：首根實體破上軌，次根同色且最新價站在上軌外",
                        current_ma5=live_ma5,
                        current_basis=live_middle,
                        ma15_current=live_ma15,
                        channel_expanding=expanding_live,
                        is_intrabar=True,
                    )
                    result["bar_id"] = live_timestamp
                    return result
                if (
                    live_ma5 is not None
                    and short_first_break
                    and tick < live["open"]
                    and tick < live_lower
                    and live_ma5 < closed_ma5
                    and live_middle <= basis + 1e-6
                ):
                    result = signal(
                        "OPEN_SHORT",
                        CONFIRMED_2BAR_BREAKOUT,
                        tick,
                        "第二根當根確認：首根實體跌破下軌，次根同色且最新價站在下軌外",
                        current_ma5=live_ma5,
                        current_basis=live_middle,
                        ma15_current=live_ma15,
                        is_intrabar=True,
                    )
                    result["bar_id"] = live_timestamp
                    return result

                # ── 即時當根模式 A：MA5 / MA15 交叉開倉 (MA_CROSSOVER) ──
                if live_ma5 is not None and live_ma15 is not None and expanding_live:
                    is_safe_long_zone = tick < (live_upper * 1.003)
                    if (
                        closed_ma5 <= current_ma15
                        and live_ma5 > live_ma15
                        and live_ma5 > closed_ma5
                        and tick > live_middle
                        and is_safe_long_zone
                    ):
                        result = signal(
                            "OPEN_LONG", MA_CROSSOVER, tick,
                            "即時 MA5/MA15 金叉 + 站穩 KC 中軌起漲",
                            current_ma5=live_ma5, current_basis=live_middle,
                            ma15_current=live_ma15, channel_expanding=True,
                            is_intrabar=True,
                        )
                        result["bar_id"] = live_timestamp
                        return result

                    is_safe_short_zone = tick > (live_lower * 0.997)
                    if (
                        closed_ma5 >= current_ma15
                        and live_ma5 < live_ma15
                        and live_ma5 < closed_ma5
                        and tick < live_middle
                        and is_safe_short_zone
                    ):
                        result = signal(
                            "OPEN_SHORT", MA_CROSSOVER, tick,
                            "即時 MA5/MA15 死叉 + 跌破 KC 中軌起跌",
                            current_ma5=live_ma5, current_basis=live_middle,
                            ma15_current=live_ma15, channel_expanding=True,
                            is_intrabar=True,
                        )
                        result["bar_id"] = live_timestamp
                        return result

                # ── 即時當根模式 B：五線共振回踩/反彈有守次級發動延續開倉 (FIVE_LINE_CONTINUATION) ──
                if live_ma5 is not None and live_ma15 is not None:
                    five_lines_up = (
                        live_middle > basis
                        and live_ma15 >= current_ma15
                        and live_ma5 > live_ma15
                        and tick > live_middle
                    )
                    five_lines_down = (
                        live_middle < basis
                        and live_ma15 <= current_ma15
                        and live_ma5 < live_ma15
                        and tick < live_middle
                    )

                    is_safe_long_zone = tick < (live_upper * 1.003)
                    if (
                        five_lines_up
                        and is_safe_long_zone
                        and current["close"] < current["open"]
                        and tick > live["open"]
                        and tick > live_ma5
                    ):
                        result = signal(
                            "OPEN_LONG", FIVE_LINE_CONTINUATION, tick,
                            "五線向上，回踩有守陽線延續開多",
                            current_ma5=live_ma5, current_basis=live_middle,
                            ma15_current=live_ma15, channel_expanding=expanding_live,
                            is_intrabar=True,
                        )
                        result["bar_id"] = live_timestamp
                        return result

                    is_safe_short_zone = tick > (live_lower * 0.997)
                    if (
                        five_lines_down
                        and is_safe_short_zone
                        and current["close"] > current["open"]
                        and tick < live["open"]
                        and tick < live_ma5
                    ):
                        result = signal(
                            "OPEN_SHORT", FIVE_LINE_CONTINUATION, tick,
                            "五線向下，反彈遇阻陰線延續開空",
                            current_ma5=live_ma5, current_basis=live_middle,
                            ma15_current=live_ma15, channel_expanding=expanding_live,
                            is_intrabar=True,
                        )
                        result["bar_id"] = live_timestamp
                        return result

        if open_live_bar_after_closed:
            return None

        if (
            previous_bullish
            and current_bullish
            and previous["close"] > previous_upper
            and current["close"] > upper
        ):
            return signal(
                "OPEN_LONG",
                CONFIRMED_2BAR_BREAKOUT,
                current["close"],
                "兩根收線確認：首根實體突破上軌，次根同色收在上軌外",
                channel_expanding=True,
            )
        if (
            previous_bearish
            and current_bearish
            and previous["close"] < float(previous_lower)
            and current["close"] < lower
        ):
            return signal(
                "OPEN_SHORT",
                CONFIRMED_2BAR_BREAKOUT,
                current["close"],
                "兩根收線確認：首根實體跌破下軌，次根同色收在下軌外",
            )
        if (
            current_bullish
            and current["close"] > upper
            and closed_ma5 > basis
            and ma5_up
            and basis >= float(basis_values[0])
        ):
            return signal(
                "OPEN_LONG", CONTINUATION_OUTSIDE_KC, current["close"],
                "收線上軌外順勢續推：MA5 高於中軌延續開多",
            )
        if (
            current_bearish
            and current["close"] < lower
            and closed_ma5 < basis
            and ma5_down
            and basis <= float(basis_values[0])
        ):
            return signal(
                "OPEN_SHORT", CONTINUATION_OUTSIDE_KC, current["close"],
                "收線下軌外順勢續跌：MA5 低於中軌延續開空",
            )

        # ── 收線確認模式 A：MA5 / MA15 交叉開倉 (MA_CROSSOVER) ──
        channel_expanding_closed = (upper - lower >= previous_upper - previous_lower)
        is_safe_short_zone_closed = current["close"] > (lower * 0.997)
        if (
            previous_ma5 >= previous_ma15
            and closed_ma5 < current_ma15
            and ma5_down
            and current["close"] < basis
            and is_safe_short_zone_closed
            and channel_expanding_closed
        ):
            return signal(
                "OPEN_SHORT", MA_CROSSOVER, current["close"],
                "MA5/MA15 死叉收線 + 跌破 KC 中軌起跌",
                channel_expanding=True,
            )

        is_safe_long_zone_closed = current["close"] < (upper * 1.003)
        if (
            previous_ma5 <= previous_ma15
            and closed_ma5 > current_ma15
            and ma5_up
            and current["close"] > basis
            and is_safe_long_zone_closed
            and channel_expanding_closed
        ):
            return signal(
                "OPEN_LONG", MA_CROSSOVER, current["close"],
                "MA5/MA15 金叉收線 + 站穩 KC 中軌起漲",
                channel_expanding=True,
            )

        # ── 收線確認模式 B：五線共振回踩/反彈延續開倉 (FIVE_LINE_CONTINUATION) ──
        kc_mid_slope = basis - float(basis_values[0])
        ma15_slope = current_ma15 - previous_ma15
        five_lines_up_closed = (
            kc_mid_slope > 0
            and ma15_slope >= 0
            and closed_ma5 > current_ma15
            and current["close"] > basis
        )
        five_lines_down_closed = (
            kc_mid_slope < 0
            and ma15_slope <= 0
            and closed_ma5 < current_ma15
            and current["close"] < basis
        )

        if (
            five_lines_up_closed
            and is_safe_long_zone_closed
            and previous["close"] < previous["open"]
            and current["close"] > current["open"]
            and current["close"] > closed_ma5
        ):
            return signal(
                "OPEN_LONG", FIVE_LINE_CONTINUATION, current["close"],
                "五線向上，回踩有守陽線延續開多",
                channel_expanding=channel_expanding_closed,
            )

        if (
            five_lines_down_closed
            and is_safe_short_zone_closed
            and previous["close"] > previous["open"]
            and current["close"] < current["open"]
            and current["close"] < closed_ma5
        ):
            return signal(
                "OPEN_SHORT", FIVE_LINE_CONTINUATION, current["close"],
                "五線向下，反彈遇阻陰線延續開空",
                channel_expanding=channel_expanding_closed,
            )

        return None

    @classmethod
    def validate_closed_outer_entry(
        cls,
        closed_klines: Sequence[Mapping[str, Any]],
        side: str,
        confirmation: str,
        *,
        tick_price: Optional[float] = None,
        live_candle: Optional[Mapping[str, Any]] = None,
    ) -> Optional[dict[str, Any]]:
        """Re-derive the two-bar break, including a live second candle."""
        if confirmation not in {
            CONFIRMED_2BAR_BREAKOUT,
            INSTANT_BREAKOUT_INSIDE,
            CONTINUATION_OUTSIDE_KC,
            MA_CROSSOVER,
            FIVE_LINE_CONTINUATION,
            INSTANT_BURST_BREAKOUT,
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
                tick_price=tick_price,
                live_candle=live_candle,
            )
        except (KeyError, TypeError, IndexError):
            return None
        outer_breakout_types = {
            CONFIRMED_2BAR_BREAKOUT,
            INSTANT_BREAKOUT_INSIDE,
            CONTINUATION_OUTSIDE_KC,
            INSTANT_BURST_BREAKOUT,
        }
        if (
            candidate is None
            or candidate["action"] != f"OPEN_{str(side).upper()}"
        ):
            return None
        if candidate["type"] != confirmation:
            if not (confirmation in outer_breakout_types and candidate["type"] in outer_breakout_types):
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
        """Evaluate the closed-candle two-bar KC breakout gate."""
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
