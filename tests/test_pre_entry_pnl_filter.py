"""Removed PnL veto must not return; entry data and hard-stop ATR remain valid."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from core.services.strategies.pure_trend_v2 import PureTrendStrategyV2, evaluate_v2_frame
from core.services.entry_firewall import validate_account_entry
from test_v2_execution_boundary import candles, context










@pytest.mark.parametrize('side', ['LONG','SHORT'])
def test_closed_candidate_rechecks_latest_quote(side):
    f=candles(side,False)
    decision=evaluate_v2_frame(f)
    assert decision is None
    assert evaluate_v2_frame(f,103. if side=='LONG' else 97.) is None


