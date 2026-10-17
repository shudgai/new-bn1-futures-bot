import asyncio
from types import SimpleNamespace

import pandas as pd
import pytest

from core.vision import chart_guard as cg
from core.services.symbol_runner import _process_wild_swing_symbol
from test_wild_swing_strategy import _frame, _pivot_cross_frame


@pytest.fixture(autouse=True)
def disable_live_local_vision(monkeypatch):
    monkeypatch.setenv("USE_LOCAL_QWEN", "false")
    monkeypatch.setenv("VISION_FAIL_OPEN", "0")


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


@pytest.mark.parametrize(
    "response,expected",
    [
        ('{"verdict":"APPROVED","reason":"結構通過"}', (True, "結構通過")),
        ('{"verdict":"REJECTED","reason":"影線風險"}', (False, "影線風險")),
        ('{"verdict":"REAL","reason":"legacy"}', (False, "AI_EXPLICIT_APPROVAL_REQUIRED")),
        ('{"verdict":"approved","reason":"lowercase"}', (False, "AI_EXPLICIT_APPROVAL_REQUIRED")),
        ("not json", (False, "AI_INVALID_RESPONSE")),
    ],
)
def test_qwen_entry_approval_requires_explicit_verdict(response, expected):
    assert cg.parse_qwen_entry_approval(response) == expected


def test_qwen_entry_vision_is_local_only_and_ignores_fail_open(monkeypatch):
    monkeypatch.setenv("ENABLE_VISION_GUARD", "true")
    monkeypatch.setenv("USE_LOCAL_QWEN", "true")
    monkeypatch.setenv("VISION_FAIL_OPEN", "1")
    monkeypatch.setenv("GEMINI_API_KEY", "must-not-be-called")
    calls = []

    async def local(*args, **kwargs):
        calls.append((args, kwargs))
        return '{"verdict":"REAL","reason":"not explicit"}'

    monkeypatch.setattr(cg, "_call_local_qwen", local)
    approved, reason = asyncio.run(
        cg.evaluate_qwen_entry_vision(bars(), "LONG", mode="BREAKOUT"),
    )
    assert not approved
    assert reason == "AI_EXPLICIT_APPROVAL_REQUIRED"
    assert len(calls) == 1
    assert calls[0][1]["require_explicit_approval"] is True


def test_qwen_entry_vision_rejects_when_local_qwen_is_not_enabled(monkeypatch):
    monkeypatch.setenv("ENABLE_VISION_GUARD", "true")
    monkeypatch.setenv("USE_LOCAL_QWEN", "false")
    monkeypatch.setenv("VISION_FAIL_OPEN", "1")
    approved, reason = asyncio.run(
        cg.evaluate_qwen_entry_vision(bars(), "LONG", mode="BREAKOUT"),
    )
    assert not approved
    assert reason == "LOCAL_QWEN_REQUIRED"


def test_qwen_entry_vision_failure_cannot_fall_back_to_gemini(monkeypatch):
    monkeypatch.setenv("ENABLE_VISION_GUARD", "true")
    monkeypatch.setenv("USE_LOCAL_QWEN", "true")
    monkeypatch.setenv("VISION_FAIL_OPEN", "1")
    monkeypatch.setenv("GEMINI_API_KEY", "must-not-be-called")
    gemini_calls = []

    async def local_failure(*args, **kwargs):
        raise RuntimeError("local service unavailable")

    def gemini(*args, **kwargs):
        gemini_calls.append(args)
        return '{"verdict":"REAL","reason":"fallback"}'

    monkeypatch.setattr(cg, "_call_local_qwen", local_failure)
    monkeypatch.setattr(cg, "_call_gemini", gemini)
    approved, reason = asyncio.run(
        cg.evaluate_qwen_entry_vision(bars(), "LONG", mode="BREAKOUT"),
    )
    assert not approved
    assert reason == "QWEN_REVIEW_FAILED:RuntimeError"
    assert gemini_calls == []


def run(side="LONG"):
    return asyncio.run(cg.evaluate_breakout_vision(bars(), side))


def test_trap_vetoes_and_real_approves(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    monkeypatch.setattr(cg, "_call_gemini", lambda img, side, key, mode=None: '{"verdict":"TRAP","reason":"長影線"}')
    assert run() == (False, "長影線")
    monkeypatch.setattr(cg, "_call_gemini", lambda img, side, key, mode=None: '{"verdict":"REAL","reason":"放量"}')
    assert run() == (True, "放量")


def test_outage_fails_closed_by_default_and_only_opens_when_explicitly_configured(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    ok, why = run()
    assert not ok and why.startswith("VISION_UNAVAILABLE")
    monkeypatch.setenv("VISION_FAIL_OPEN", "1")
    ok, why = run()
    assert ok and why.startswith("VISION_UNAVAILABLE")
    monkeypatch.setenv("GEMINI_API_KEY", "k")

    def boom(*a):
        raise RuntimeError("api down")

    monkeypatch.setattr(cg, "_call_gemini", boom)
    assert run()[0] is True
    monkeypatch.setenv("VISION_FAIL_OPEN", "0")
    assert run()[0] is False


def test_disabled_vision_guard_rejects_entry(monkeypatch):
    monkeypatch.setenv("ENABLE_VISION_GUARD", "false")
    assert run() == (False, "VISION_GUARD_DISABLED")


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_wild_swing_runner_uses_math_gate_without_qwen(monkeypatch, side):
    from core.gates.macro_candle_structure_gate import MacroCandleStructureGate

    calls = []

    async def fake(frame, proposed_side, mode=None):
        calls.append((frame, proposed_side, mode))
        return False, "unexpected Qwen call"

    class Account:
        def __init__(self):
            self.positions = {}
            self.closing_lock = set()
            self.pending_limit_orders = {}
            self.trades = []

    class Engine:
        def __init__(self):
            self.account = Account()
            self.entries = []

        async def execute_dual_tf_entry(self, **kwargs):
            self.entries.append(kwargs)
            self.account.positions[kwargs["symbol"]] = {"side": kwargs["side"]}
            return True

    monkeypatch.setattr(cg, "evaluate_qwen_entry_vision", fake)
    monkeypatch.setattr(
        MacroCandleStructureGate,
        "can_open",
        lambda *args, **kwargs: (True, "test macro pass"),
    )
    engine = Engine()
    frame = _pivot_cross_frame(side)
    timestamp_offset = 1_740_000_000_000
    frame["timestamp"] += timestamp_offset
    frame["atr"] = 2.0
    if side == "LONG":
        frame.loc[10, "kc_middle"] = 100.0
        frame.loc[11, "kc_middle"] = 100.1
    else:
        frame.loc[10, "kc_middle"] = 100.0
        frame.loc[11, "kc_middle"] = 99.9
    asyncio.run(_process_wild_swing_symbol(
        engine, "LOBSTER/USDT", frame.iloc[:-1], float(frame.iloc[-2]["close"]),
        False, False,
    ))
    asyncio.run(_process_wild_swing_symbol(
        engine, "LOBSTER/USDT", frame, float(frame.iloc[-1]["close"]), False, False,
    ))

    assert len(engine.entries) == 1
    assert calls == []
    assert engine.entries[0]["entry_mode"] == "WILD_SWING"
    assert engine.entries[0]["meta_dict"]["entry_pattern"] == "CROSSOVER"
    assert engine.entries[0]["meta_dict"]["entry_signal_mode"] == "CROSSOVER"


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_mid_channel_pullback_without_pivot_cross_or_profit_lock_is_not_sent_to_ai(
    monkeypatch, side,
):
    calls = []

    async def approve(*args, **kwargs):
        calls.append((args, kwargs))
        return True, "would approve"

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
            return True

    monkeypatch.setattr(cg, "evaluate_qwen_entry_vision", approve)
    engine = Engine()
    frame = _frame(side)
    frame["atr"] = 2.0
    frame["timestamp"] = [index * 60_000 for index in range(len(frame))]
    asyncio.run(_process_wild_swing_symbol(
        engine, "LOBSTER/USDT", frame, float(frame.iloc[-1]["close"]), False, False,
    ))

    next_bar = frame.iloc[-1].copy()
    next_bar["timestamp"] = float(frame.iloc[-1]["timestamp"]) + 60_000
    if side == "SHORT":
        next_bar.update({
            "open": 99.6, "high": 99.7, "low": 99.2, "close": 99.3,
            "kc_upper": 102.2, "kc_middle": 99.8, "kc_lower": 97.4,
            "ma15": 100.0, "ma5": 99.7,
        })
    else:
        next_bar.update({
            "open": 100.4, "high": 100.9, "low": 100.3, "close": 100.8,
            "kc_upper": 102.2, "kc_middle": 100.2, "kc_lower": 98.2,
            "ma15": 100.0, "ma5": 100.1,
        })
    next_frame = pd.concat([frame, next_bar.to_frame().T], ignore_index=True)
    asyncio.run(_process_wild_swing_symbol(
        engine, "LOBSTER/USDT", next_frame, float(next_bar["close"]), False, False,
    ))

    assert calls == []
    assert engine.entries == []


def test_prompt_adapts_to_ma_cross_mode(monkeypatch):
    seen = {}

    def fake(img, side, key, mode=None):
        seen["mode"] = mode
        return '{"verdict":"TRAP","reason":"纏繞"}'

    monkeypatch.setenv("GEMINI_API_KEY", "k")
    monkeypatch.setattr(cg, "_call_gemini", fake)
    out = asyncio.run(cg.evaluate_breakout_vision(bars(), "LONG", mode="MA_SWING"))
    assert out == (False, "纏繞") and seen["mode"] == "MA_SWING"
    assert "MA5/MA15 交叉" in cg._vision_prompt("LONG", "MA_SWING")


@pytest.mark.parametrize(
    "mode,expected",
    [
        ("BREAKOUT", "通道寬度擴張"),
        ("CONTINUATION", "0.35-0.65 中軌回踩區"),
        ("CROSSOVER", "MA5/MA15 真正交叉"),
        ("REVERSAL", "確認三點峰谷"),
    ],
)
def test_prompt_describes_each_entry_signal_mode(mode, expected):
    assert expected in cg._vision_prompt("LONG", mode)


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
    assert len(calls) == 1 and approved is False and reason.startswith("VISION_UNAVAILABLE")


def test_quota_429_not_retried(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    calls = []

    def quota(img, side, key, mode=None):
        calls.append(1)
        raise _Err(429)

    monkeypatch.setattr(cg, "_call_gemini", quota)
    approved, reason = asyncio.run(cg.evaluate_breakout_vision(bars(), "LONG"))
    assert len(calls) == 1
    assert approved is False and reason.startswith("VISION_UNAVAILABLE")
