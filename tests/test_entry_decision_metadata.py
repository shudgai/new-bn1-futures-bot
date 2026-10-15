"""A valid pipeline decision must survive execution and account revalidation."""
import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pandas as pd
import pytest

from core.engine import TradingEngine
from core.services import entry_contract
from core.services.entry_firewall import validate_account_entry


