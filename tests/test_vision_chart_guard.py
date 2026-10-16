import asyncio
from types import SimpleNamespace

import pandas as pd
import pytest

from core.vision import chart_guard as cg
from core.services.symbol_runner import _process_wild_swing_symbol
from test_wild_swing_strategy import _frame


@pytest.fixture(autouse=True)
def disable_live_local_vision(monkeypatch):
    monkeypatch.setenv("USE_LOCAL_QWEN", "false")
    monkeypatch.setenv("VISION_FAIL_OPEN", "1")


def bars(n=50):
    return [
        SimpleNamespace(
            open=100 + i * 0.1, high=101 + i * 0.1, low=99 + i * 0.1,
            close=100.5 + i * 0.1, volume=100.0 + i, ma5=100.0,
            ma15=100.0, kc_upper=110.0, kc_lower=90.0,
        )
        for i in range(n)
    ]


def test_render_is_png_bytes_and_writes_no_files(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    out = cg.render_chart_bytes(bars())
    assert out[:8] == b"\x89PNG\r\n\x1a\n" and len(out) > 1000
    assert list(tmp_path.iterdir()) == []


def test_render_accepts_closed_candle_dataframe():
    frame = _frame("LONG")
    out = cg.render_chart_bytes(frame)
    assert out[:8] == b"\x89PNG\r\n\x1a\n"


def test_parse_verdict():
    assert cg.parse_verdict('{"verdict": "REAL", "reason": "放量"}') == (True, "放量")
    assert cg.parse_verdict('```json\n{"verdict":"trap","reason":"x"}\n```') == (False, "x")
    with pytest.raises(ValueError):
        cg.parse_verdict("maybe")
    with pytest.raises(ValueError):
        cg.parse_verdict('{"verdict": "???"}')


def run(side="LONG"):
    return asyncio.run(cg.evaluate_breakout_vision(bars(), side))


def test_trap_vetoes_and_real_approves(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    monkeypatch.setattr(cg, "_call_gemini", lambda img, side, key, mode=None: '{"verdict":"TRAP","reason":"長影線"}')
    assert run() == (False, "長影線")
    monkeypatch.setattr(cg, "_call_gemini", lambda img, side, key, mode=None: '{"verdict":"REAL","reason":"放量"}')
    assert run() == (True, "放量")


def test_outage_fails_open_by_default_and_closed_when_configured(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    ok, why = run()
    assert ok and why.startswith("VISION_UNAVAILABLE")
    monkeypatch.setenv("GEMINI_API_KEY", "k")

    def boom(*a):
        raise RuntimeError("api down")

    monkeypatch.setattr(cg, "_call_gemini", boom)
    assert run()[0] is True
    monkeypatch.setenv("VISION_FAIL_OPEN", "0")
    assert run()[0] is False


@pytest.mark.parametrize("approved,expected_entries", [(True, 1), (False, 0)])
@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_wild_swing_runner_vision_gates_entry(monkeypatch, approved, expected_entries, side):
    calls = []

    async def fake(frame, proposed_side, mode=None):
        calls.append((frame, proposed_side, mode))
        return approved, "test verdict"

    class Account:
        def __init__(self):
            self.positions = {}
            self.closing_lock = set()
            self.pending_limit_orders = {}

    class Engine:
        def __init__(self):
            self.account = Account()
            self.entries = []

        async def execute_dual_tf_entry(self, **kwargs):
            self.entries.append(kwargs)
            self.account.positions[kwargs["symbol"]] = {"side": kwargs["side"]}
            return True

    monkeypatch.setattr(cg, "evaluate_breakout_vision", fake)
    engine = Engine()
    frame = _frame(side)
    frame["timestamp"] = [index * 60_000 for index in range(len(frame))]
    asyncio.run(_process_wild_swing_symbol(
        engine, "LOBSTER/USDT", frame, float(frame.iloc[-1]["close"]), False, False,
    ))
    next_bar = frame.iloc[-1].copy()
    next_bar["timestamp"] = float(frame.iloc[-1]["timestamp"]) + 60_000
    if side == "SHORT":
        next_bar.update({
            "open": 99.2, "high": 99.3, "low": 98.8, "close": 98.9,
            "kc_middle": 99.8, "ma15": 100.0, "ma5": 99.7,
        })
    else:
        next_bar.update({
            "open": 100.8, "high": 101.3, "low": 100.7, "close": 101.2,
            "kc_middle": 100.2, "ma15": 100.0, "ma5": 100.1,
        })
    next_frame = pd.concat([frame, next_bar.to_frame().T], ignore_index=True)
    asyncio.run(_process_wild_swing_symbol(
        engine, "LOBSTER/USDT", next_frame, float(next_bar["close"]), False, False,
    ))

    assert len(engine.entries) == expected_entries
    assert len(calls) == 1
    assert calls[0][0].iloc[-1]["timestamp"] == next_bar["timestamp"]
    assert calls[0][1] == side
    if expected_entries:
        assert engine.entries[0]["entry_mode"] == "WILD_SWING"
    else:
        asyncio.run(_process_wild_swing_symbol(
            engine, "LOBSTER/USDT", next_frame, float(next_bar["close"]), False, False,
        ))
        assert len(calls) == 1


def test_prompt_adapts_to_ma_cross_mode(monkeypatch):
    seen = {}

    def fake(img, side, key, mode=None):
        seen["mode"] = mode
        return '{"verdict":"TRAP","reason":"纏繞"}'

    monkeypatch.setenv("GEMINI_API_KEY", "k")
    monkeypatch.setattr(cg, "_call_gemini", fake)
    out = asyncio.run(cg.evaluate_breakout_vision(bars(), "LONG", mode="MA_SWING"))
    assert out == (False, "纏繞") and seen["mode"] == "MA_SWING"
    assert "MA5/MA15 交叉" in cg.PROMPT.format(side="LONG", signal_desc=cg.SIGNAL_DESC["MA_SWING"])


class _Err(Exception):
    def __init__(self, code):
        super().__init__(str(code))
        self.code = code


def test_transient_503_is_retried_then_succeeds(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    monkeypatch.setattr(cg, "RETRY_DELAY_SEC", 0)
    calls = []

    def flaky(img, side, key, mode=None):
        calls.append(1)
        if len(calls) < 3:
            raise _Err(503)
        return '{"verdict":"TRAP","reason":"x"}'

    monkeypatch.setattr(cg, "_call_gemini", flaky)
    approved, _ = asyncio.run(cg.evaluate_breakout_vision(bars(), "LONG"))
    assert len(calls) == 3 and approved is False


def test_non_transient_error_not_retried(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    calls = []

    def bad(img, side, key, mode=None):
        calls.append(1)
        raise _Err(401)

    monkeypatch.setattr(cg, "_call_gemini", bad)
    approved, reason = asyncio.run(cg.evaluate_breakout_vision(bars(), "LONG"))
    assert len(calls) == 1 and approved is True and reason.startswith("VISION_UNAVAILABLE")


def test_quota_429_not_retried(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    calls = []

    def quota(img, side, key, mode=None):
        calls.append(1)
        raise _Err(429)

    monkeypatch.setattr(cg, "_call_gemini", quota)
    asyncio.run(cg.evaluate_breakout_vision(bars(), "LONG"))
    assert len(calls) == 1
