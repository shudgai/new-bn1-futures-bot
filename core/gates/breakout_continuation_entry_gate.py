"""Closed-candle detector for consolidation breakouts and outside continuations."""

import math
from collections.abc import Mapping, Sequence
from typing import Any, Optional


class BreakoutContinuationEntryGate:
    """Detect the two additional breakout/continuation entry patterns."""

    @staticmethod
    def _number(value: Any) -> Optional[float]:
        try:
            number = float(value)
        except (TypeError, ValueError, OverflowError):
            return None
        return number if math.isfinite(number) else None

    @classmethod
    def evaluate(
        cls,
        klines: Sequence[Mapping[str, Any]],
        kc_upper: Sequence[float],
        kc_lower: Sequence[float],
        kc_basis: Sequence[float],
        has_position: bool = False,
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
        if (
            upper is None
            or lower is None
            or basis is None
            or upper <= lower
            or not lower <= basis <= upper
        ):
            return None

        k0, k1, k2, k3 = candles[::-1]

        def candle_info(candle: Mapping[str, float]) -> tuple[float, bool, bool]:
            body = abs(candle["close"] - candle["open"])
            return body, candle["close"] > candle["open"], candle["close"] < candle["open"]

        body0, green0, red0 = candle_info(k0)
        body1, green1, red1 = candle_info(k1)
        body2, green2, red2 = candle_info(k2)
        body3, _, _ = candle_info(k3)
        is_big_candle = body0 > ((body1 + body2 + body3) / 3.0) * 1.4

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
