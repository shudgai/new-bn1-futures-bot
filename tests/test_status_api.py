"""Status responses use the real engine methods and an isolated paper account."""
import json

import pytest
from fastapi import Response

import core.paper_account as paper_module
from core.engine import TradingEngine


@pytest.fixture
def anyio_backend():
    return "asyncio"




def test_empty_shadow_status_is_read_only():
    engine = object.__new__(TradingEngine)
    assert engine.btc_lead_shadow_status() == {
        "active": {}, "events": [], "total_events": 0,
        "eligible_events": 0, "average_delay_sec": None,
    }
    assert vars(engine) == {}
