"""Status responses use the real engine methods and an isolated paper account."""
import json

import pytest
from fastapi import Response

import core.paper_account as paper_module
from core.engine import TradingEngine


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_status_response_preserves_shadow_contract(tmp_path, monkeypatch):
    from services import api

    monkeypatch.setattr(paper_module, "STATE_FILE", str(tmp_path / "status-account.json"))
    monkeypatch.setattr(api.engine, "account", paper_module.PaperAccount())
    monkeypatch.setattr(api.engine, "_btc_lead_shadow_active", {"direction": "LONG"})
    monkeypatch.setattr(api.engine, "_btc_lead_shadow_events", [
        {"delay_sec": 2., "chop_locked": False},
        {"delay_sec": 4., "chop_locked": False},
        {"delay_sec": 50., "chop_locked": True},
    ])
    response = Response()
    result = await api.get_status(response)
    assert result["btc_lead_shadow"] == {
        "active": {"direction": "LONG"},
        "events": api.engine._btc_lead_shadow_events,
        "total_events": 3, "eligible_events": 2, "average_delay_sec": 3.,
    }
    assert response.headers["Cache-Control"].startswith("no-store")
    json.dumps(result)


def test_empty_shadow_status_is_read_only():
    engine = object.__new__(TradingEngine)
    assert engine.btc_lead_shadow_status() == {
        "active": {}, "events": [], "total_events": 0,
        "eligible_events": 0, "average_delay_sec": None,
    }
    assert vars(engine) == {}
