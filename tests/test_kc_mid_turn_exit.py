"""Closed KC slope exits, fixed-TP retirement, and production dispatch."""
import asyncio
import copy
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pandas as pd
import pytest

from core.services.exits.profit_protection_service import protection, ProfitProtectionExitStrategy
from core.services.symbol_runner import process_single_symbol_runner


@pytest.fixture(autouse=True)
def isolated_logs(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / 'data').mkdir()


def position(side='LONG'):
    return dict(symbol='TEST/USDT', side=side, entry_price=100., qty=.1,
                open_timestamp=1, entry_atr=1.)


def frame(mids, price=100.):
    return pd.DataFrame([dict(timestamp=i*60000, open=price, close=price,
                              high=price, low=price, kc_middle=mid,
                              kc_upper=mid+10, kc_lower=mid-10, atr=1.)
                         for i, mid in enumerate(mids)])




@pytest.mark.parametrize('side,mids,price', [('LONG', [100, 101, 90], 103),
                                          ('SHORT', [100, 99, 110], 97),
                                          ('LONG', [100, 101, 90], 100),
                                          ('SHORT', [100, 99, 110], 100),
                                          ('LONG', [100, 100, 90], 100),
                                          ('SHORT', [100, 100, 110], 100)])
def test_no_fixed_tp_price_cross_or_live_mid_exit(side, mids, price):
    assert ProfitProtectionExitStrategy(fee=0, slippage=0).evaluate_exit(
        position(side), frame(mids, price), price) is None


@pytest.mark.parametrize('mids', [[100], [100, 99], [100, float('nan'), 100],
                                [100, float('inf'), 100], [100, 0, 100]])
def test_invalid_mid_does_not_create_exit(mids):
    assert protection(position(), 100, 0, 0, frame(mids)) is None














def test_staged_position_still_bypasses_legacy():
    p = position()
    p['use_staged_risk_engine'] = True
    assert ProfitProtectionExitStrategy().evaluate_exit(p, frame([100, 99, 100]), 96) is None
    assert 'channel_profit_protection' not in p


