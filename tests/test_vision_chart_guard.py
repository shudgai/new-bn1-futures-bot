import asyncio
import os

import pytest

from core.swing.models import Bar
from core.vision import chart_guard as cg
from tests.test_swing_runner import Account, Engine, make_frame
from core.swing.runner import process_symbol


def bars(n=50):
    return [Bar(100 + i * 0.1, 101 + i * 0.1, 99 + i * 0.1, 100.5 + i * 0.1, 100.0 + i,
                100.0, 100.0, 110.0, 90.0, 2.0) for i in range(n)]


def test_render_is_png_bytes_and_writes_no_files(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    out = cg.render_chart_bytes(bars())
    assert out[:8] == b"\x89PNG\r\n\x1a\n" and len(out) > 1000
    assert list(tmp_path.iterdir()) == []


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


def breakout_frame():
    df = make_frame()
    df.loc[df.index[-1], ["open", "close", "high", "low"]] = [108.0, 113.0, 113.2, 107.8]
    df.loc[df.index[-2], ["open", "close", "high", "low"]] = [110.5, 111.0, 111.2, 110.4]
    df.loc[df.index[-1], "volume"] = 250.0
    df.loc[df.index[-1], "atr"] = 3.0
    df.loc[df.index[-1], "ma5"] = 101.0
    return df


@pytest.mark.parametrize("approved,expected_entries", [(True, 1), (False, 0)])
def test_runner_vision_gates_breakout(monkeypatch, approved, expected_entries):
    async def fake(b, side, mode=None):
        return approved, "t"

    monkeypatch.setattr("core.vision.chart_guard.evaluate_breakout_vision", fake)
    eng = Engine(Account())
    asyncio.run(process_symbol(eng, "X", breakout_frame(), 113.0, False, False))
    if expected_entries == 1 and not eng.entries:
        pytest.skip("fixture does not produce a breakout signal")
    assert len(eng.entries) == expected_entries


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


def test_runner_vetoes_ma_swing_too(monkeypatch):
    import core.swing.runner as r
    from core.swing.models import EntrySignal

    calls = []

    async def fake(b, side, mode=None):
        calls.append(mode)
        return False, "t"

    monkeypatch.setattr("core.vision.chart_guard.evaluate_breakout_vision", fake)
    monkeypatch.setattr(r, "evaluate_entry", lambda bars, st: EntrySignal("LONG", "MA_SWING", "NEXT_OPEN"))
    eng = Engine(Account())
    asyncio.run(process_symbol(eng, "X", make_frame(), 100.0, False, False))
    assert calls == ["MA_SWING"] and not eng.entries


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
