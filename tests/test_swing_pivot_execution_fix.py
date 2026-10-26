import asyncio
import pandas as pd

from core.services.math_execution_gate import validate_entry_at_execution


class DummyAccount:
    def __init__(self):
        self.entry_filter_last = {}
        self.logs = []

    def log(self, msg, level="INFO"):
        self.logs.append((level, msg))


def test_swing_pivot_execution_with_stop_loss_resolution():
    async def _run():
        account = DummyAccount()
        bars = [
            # left bar
            dict(timestamp=60_000, open=100.0, high=102.0, low=99.0, close=101.0, atr=1.0,
                 ma5=100.5, ma15=100.0, kc_upper=105.0, kc_middle=100.0, kc_lower=95.0, is_closed=True),
            # pivot bar (valley)
            dict(timestamp=120_000, open=100.5, high=101.0, low=97.0, close=98.0, atr=1.0,
                 ma5=99.0, ma15=99.5, kc_upper=105.0, kc_middle=100.0, kc_lower=95.0, is_closed=True),
            # confirm bar (bounce up, close > pivot_mid 99.25, body >= 0.35)
            dict(timestamp=180_000, open=98.5, high=101.5, low=98.0, close=101.0, atr=1.0,
                 ma5=100.0, ma15=99.5, kc_upper=105.0, kc_middle=100.0, kc_lower=95.0, is_closed=True),
        ]
        df = pd.DataFrame(bars)
        df.attrs["timeframe_ms"] = 60_000.0
        df.attrs["snapshot_ms"] = 240_000.0
        df.attrs["macro_trend"] = "BULLISH"
        df["macro_trend"] = "BULLISH"

        payload = {
            "entry_type": "SWING_PIVOT",
            "candidate_signal_type": "SWING_PIVOT_LONG",
            "initial_sl": 97.0,
            "entry_gate_frame": df,
            "source": "UNIFIED_PIPELINE",
            "upstream_entry_gate_authorized": True,
            "macro_trend": "BULLISH",
        }

        allowed, reason = await validate_entry_at_execution(
            account=account,
            symbol="TESTUSDT",
            side="LONG",
            context=payload,
            execution_price=101.0,
        )

        assert allowed is True, f"Swing pivot entry should be allowed, got: {reason}"
        assert "CENTRAL_GATE" not in reason or "APPROVED" in reason

    asyncio.run(_run())
