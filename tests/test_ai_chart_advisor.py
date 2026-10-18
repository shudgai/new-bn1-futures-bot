import asyncio

import pandas as pd

from core.services.ai_chart_advisor import AIChartAdvisor


def _closed_frame(bar_id=60_000):
    frame = pd.DataFrame({
        "timestamp": [bar_id],
        "is_closed": [True],
    })
    frame.attrs["timeframe_ms"] = 60_000
    return frame


def _labels(**overrides):
    result = {
        "is_chop": False,
        "is_exhausted": False,
        "allow_long": True,
        "allow_short": True,
        "reason": "trend",
    }
    result.update(overrides)
    return result


def test_advisor_publishes_fresh_labels_for_the_analyzed_bar():
    advisor = AIChartAdvisor(endpoint="http://advisor.invalid/analyze")

    async def fake_render(frame):
        return b"chart"

    async def fake_request(image):
        assert image == b"chart"
        return _labels(is_chop=True)

    advisor._render_chart = fake_render
    advisor._request_labels = fake_request

    assert asyncio.run(
        advisor.update_chart_async("LOBSTER/USDT", 60_000, _closed_frame())
    )
    state = advisor.inspect_state("LOBSTER/USDT", 60_000)

    assert state is not None
    assert state["is_chop"] is True
    assert state["bar_id"] == 60_000
    assert advisor.inspect_state("LOBSTER/USDT", 120_000) is not None
    assert advisor.inspect_state("LOBSTER/USDT", 150_001) is None
    assert advisor.inspect_state("CAP/USDT", 60_000) is None


def test_advisor_fails_open_without_publishing_failed_inference():
    advisor = AIChartAdvisor(endpoint="http://advisor.invalid/analyze")

    async def failed_request(_image):
        raise TimeoutError("inference timeout")

    advisor._render_chart = lambda _frame: asyncio.sleep(0, result=b"chart")
    advisor._request_labels = failed_request

    assert not asyncio.run(
        advisor.update_chart_async("CAP/USDT", 60_000, _closed_frame())
    )
    assert advisor.inspect_state("CAP/USDT", 60_000) is None


def test_advisor_rejects_malformed_flags_and_expired_labels(monkeypatch):
    try:
        AIChartAdvisor._parse_labels(_labels(allow_long="false"))
    except ValueError as exc:
        assert "invalid flags" in str(exc)
    else:
        raise AssertionError("String values must not be treated as boolean flags")

    advisor = AIChartAdvisor(endpoint="http://advisor.invalid/analyze")
    advisor._cache["CAP/USDT"] = {
        **_labels(),
        "bar_id": 60_000.0,
        "completed_at": 0.0,
        "expires_at": 10.0,
    }
    monkeypatch.setattr("core.services.ai_chart_advisor.time.monotonic", lambda: 10.0)

    assert advisor.inspect_state("CAP/USDT", 60_000) is None


def test_advisor_schedules_once_per_new_verified_closed_bar():
    advisor = AIChartAdvisor(endpoint="http://advisor.invalid/analyze")
    calls = []

    async def fake_update(symbol, bar_id, frame):
        calls.append((symbol, bar_id, len(frame)))
        return True

    advisor.update_chart_async = fake_update

    async def run():
        assert advisor.schedule_update("LOBSTER/USDT", 60_000, _closed_frame())
        assert not advisor.schedule_update("LOBSTER/USDT", 60_000, _closed_frame())
        await asyncio.gather(*list(advisor._tasks.values()))
        await asyncio.sleep(0)

    asyncio.run(run())

    assert calls == [("LOBSTER/USDT", 60_000.0, 1)]
