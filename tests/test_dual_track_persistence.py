"""Exit observations survive account synchronization and process restarts."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pandas as pd
import pytest

import core.paper_account as paper_module
import core.testnet_account as testnet_module
from core.services.symbol_runner import process_single_symbol_runner
from test_close_deduplication import account_for


@pytest.fixture
def anyio_backend():
    return "asyncio"


