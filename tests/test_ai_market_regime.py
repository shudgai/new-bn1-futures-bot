import asyncio
import json
import time
from types import SimpleNamespace

import pandas as pd
import pytest

from core.ai_advisor import LocalAIAdvisor
from core.gates.pipeline import pipeline
from core.services.entry_contract import evaluate_entry_contract
from core.services.entry_firewall import validate_account_entry
from core import engine as engine_module


def _completed_candles(count=30):
    return [
        {
            "timestamp": 1_700_000_000_000 + index * 60_000,
            "open": 100.0 + index,
            "high": 101.2 + index,
            "low": 99.8 + index,
            "close": 101.0 + index,
            "volume": 10.0,
            "is_closed": True,
        }
        for index in range(count)
    ]


def test_local_ai_market_regime_classifies_one_symbol_and_keeps_fresh_state():
    calls = []

    def fake_request(payload):
        calls.append(payload)
        user_data = json.loads(payload["messages"][1]["content"])
        assert user_data["symbol"] == "LOBSTER/USDT"
        assert user_data["timeframe"] == "1m"
        assert len(user_data["candles"]) == 30
        return {
            "model": "local-qwen",
            "choices": [{"message": {"content": '{"regime":"TRENDING","reason":"directional"}'}}],
        }

    advisor = LocalAIAdvisor("http://127.0.0.1:8888/v1/chat/completions", request_fn=fake_request)
    result = asyncio.run(advisor.analyze_market_regime(
        "LOBSTER/USDT", _completed_candles(),
    ))

    assert result["state"] == "TRENDING"
    assert advisor.market_regime_for("LOBSTER/USDT") == "TRENDING"
    assert advisor.market_regime_for("CAP/USDT") == "UNKNOWN"
    assert len(calls) == 1


def test_local_ai_choppy_unknown_invalid_and_stale_states_fail_closed():
    advisor = LocalAIAdvisor(
        "http://127.0.0.1:8888/v1/chat/completions",
        request_fn=lambda _payload: {
            "choices": [{"message": {"content": '{"regime":"CHOPPY"}'}}],
        },
    )
    choppy = asyncio.run(advisor.analyze_market_regime("CAP/USDT", _completed_candles()))
    assert choppy["state"] == "CHOPPY"
    assert advisor.market_regime_for("CAP/USDT") == "CHOPPY"

    invalid = asyncio.run(advisor.analyze_market_regime(
        "BAD/USDT", _completed_candles()[:-1],
    ))
    assert invalid["state"] == "UNKNOWN"
    assert advisor.market_regime_for("BAD/USDT") == "UNKNOWN"

    advisor._market_regimes["CAP/USDT"]["updated_at"] = time.time() - 181
    assert advisor.market_regime_for("CAP/USDT") == "UNKNOWN"


@pytest.mark.parametrize(
    "code",
    [
        None,
        "TRIGGER_C_CONTINUATION",
        "AUTHORIZED_REALTIME_BREAKOUT",
        "KC_LIVE_BODY_BREAKOUT_SHORT",
    ],
)
def test_entry_pipeline_blocks_choppy_or_missing_ai_regime(monkeypatch, code):
    frame = pd.DataFrame([{"open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0}])
    frame.attrs["timeframe_ms"] = 60_000
    monkeypatch.setattr(pipeline, "market_regime_provider", lambda _symbol: "CHOPPY")
    diagnostics = {}
    assert pipeline.authorize(
        None, frame, 1.0, symbol="CAP/USDT", diagnostics=diagnostics,
    ) is None
    assert diagnostics["reason"] == "BLOCKED_BY_AI_CHOP_REGIME"
    assert evaluate_entry_contract(
        frame, 1.0, code=code, symbol="CAP/USDT", diagnostics=diagnostics,
    ) is None
    assert diagnostics["reason"] == "BLOCKED_BY_AI_CHOP_REGIME"

    monkeypatch.setattr(pipeline, "market_regime_provider", None)
    assert pipeline.market_regime_problem("CAP/USDT") == "BLOCKED_BY_AI_CHOP_REGIME"


def test_account_firewall_blocks_choppy_ai_even_for_pipeline_ttl_grace(monkeypatch):
    async def check():
        monkeypatch.setattr(pipeline, "market_regime_provider", lambda _symbol: "CHOPPY")
        account = SimpleNamespace()
        try:
            await validate_account_entry(
                account,
                "CAP/USDT",
                "LONG",
                {"pipeline_ttl_grace": {"authorized_at_monotonic": time.monotonic()}},
            )
        except ValueError as exc:
            assert "BLOCKED_BY_AI_CHOP_REGIME" in str(exc)
        else:
            raise AssertionError("AI CHOPPY must veto account-level entry")

    asyncio.run(check())


@pytest.mark.parametrize(
    ("channel_state", "ma_gap", "expected_reason"),
    [
        ("KC方向不明", 1.0, "BLOCKED_BY_CHOPPY_UNKNOWN_DIRECTION"),
        ("UP", 0.1, "BLOCKED_BY_MA_TANGLING"),
    ],
)
def test_account_firewall_blocks_chop_even_with_trend_ai(
    monkeypatch, channel_state, ma_gap, expected_reason,
):
    async def check():
        rows = [
            {
                "timestamp": 1_700_000_000_000 + index * 60_000,
                "open": 100.0,
                "high": 101.0,
                "low": 99.0,
                "close": 100.2,
                "atr": 1.0,
                "kc_lower": 98.0,
                "kc_middle": 100.0,
                "kc_upper": 102.0,
                "ma5": 100.0 + ma_gap,
                "ma15": 100.0,
                "kc_upper_slope": 0.2,
                "kc_lower_slope": 0.2,
                "channel_state": channel_state,
                "is_closed": True,
            }
            for index in range(3)
        ]
        frame = pd.DataFrame(rows)
        frame.attrs.update(timeframe_ms=60_000, entry_finality_verified=True)
        account = SimpleNamespace(
            entry_frame_provider=lambda _symbol: asyncio.sleep(0, result=frame),
        )
        monkeypatch.setattr(pipeline, "market_regime_provider", lambda _symbol: "TRENDING")
        with pytest.raises(ValueError, match=expected_reason):
            await validate_account_entry(
                account,
                "CAP/USDT",
                "SHORT",
                {"entry_signal_code": "KC_LIVE_BODY_BREAKOUT_SHORT"},
            )

    asyncio.run(check())


def test_engine_refreshes_symbol_regimes_in_background_from_closed_1m_bars(monkeypatch):
    async def check():
        symbols = ["CAP/USDT"]
        calls = []
        sleeps = []

        class FakeAdvisor:
            def set_market_regime_unavailable(self, symbol, error):
                raise AssertionError(f"unexpected unavailable state for {symbol}: {error}")

            async def analyze_market_regime(self, symbol, candles):
                calls.append((symbol, candles))
                return {
                    "state": "TRENDING",
                    "status": "ok",
                    "model": "local-test",
                    "error": "",
                    "reason": "",
                }

        frame = pd.DataFrame(_completed_candles())
        frame.attrs["timeframe_ms"] = 60_000

        async def fetch_klines(symbol, timeframe, limit, keep_live):
            assert (symbol, timeframe, limit, keep_live) == (
                "CAP/USDT", "1m", 31, False,
            )
            return frame

        async def stop_after_cycle(delay):
            sleeps.append(delay)
            engine.is_running = False

        engine = SimpleNamespace(
            is_running=True,
            symbol_rotation=SimpleNamespace(
                ai=FakeAdvisor(), entry_scan_symbols=symbols,
            ),
            fetch_klines=fetch_klines,
            account=SimpleNamespace(log=lambda *_args: None),
        )
        monkeypatch.setattr(engine_module, "DEFAULT_SYMBOLS", ())
        monkeypatch.setattr(engine_module.asyncio, "sleep", stop_after_cycle)

        await engine_module.TradingEngine._market_regime_loop(engine)
        assert len(calls) == 1
        assert calls[0][0] == "CAP/USDT"
        assert len(calls[0][1]) == 30
        assert all(candle["is_closed"] for candle in calls[0][1])
        assert sleeps == [pytest.approx(180.0, abs=0.1)]

    asyncio.run(check())


def test_local_ai_malformed_response_and_bad_candles_fail_closed():
    malformed = LocalAIAdvisor(
        "http://127.0.0.1:8888/v1/chat/completions",
        request_fn=lambda _payload: {
            "choices": [{"message": {"content": '{"regime":"UNCERTAIN"}'}}],
        },
    )
    result = asyncio.run(malformed.analyze_market_regime(
        "CAP/USDT", _completed_candles(),
    ))
    assert result["state"] == "UNKNOWN"
    assert malformed.market_regime_for("CAP/USDT") == "UNKNOWN"

    invalid_candles = _completed_candles()
    invalid_candles[-1]["low"] = invalid_candles[-1]["high"] + 1
    invalid = asyncio.run(malformed.analyze_market_regime(
        "CAP/USDT", invalid_candles,
    ))
    assert invalid["state"] == "UNKNOWN"
    assert "invalid_completed_candle" in invalid["error"]
