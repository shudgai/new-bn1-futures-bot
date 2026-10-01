"""The third candle authorizes entry before closure and is rechecked at submission."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from core.services.entry_contract import evaluate_entry_contract
from core.services.entry_firewall import validate_account_entry
from test_entry_without_wick_filter import frame_for

@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_two_closed_plus_live_third_accepts_either_color(side):
    f = frame_for(side).tail(3).copy()
    decision = evaluate_entry_contract(f)
    assert decision and decision['side'] == side
    assert not bool(f.iloc[-1].is_closed)
    assert decision['breakout_bar_id'] == float(f.iloc[0].timestamp)
    context = dict(entry_signal_code=decision['type'],
                   channel_confirmation_bar_id=decision['confirmation_bar_id'])
    account = SimpleNamespace(entry_frame_provider=AsyncMock(return_value=f))
    assert asyncio.run(validate_account_entry(account, 'TEST', side, context))
    opening = float(f.iloc[-1].open)
    sign = 1 if side == 'LONG' else -1
    quote = opening - sign * .05
    assert evaluate_entry_contract(f, quote)['side'] == side
    f.loc[f.index[-1], 'close'] = quote
    f.loc[f.index[-1], 'high'] = max(opening, quote) + .01
    f.loc[f.index[-1], 'low'] = min(opening, quote) - .01
    assert asyncio.run(validate_account_entry(account, 'TEST', side, context))['side'] == side
    # A flat third candle remains a doji even though color is unrestricted.
    f.loc[f.index[-1], 'close'] = opening
    with pytest.raises(ValueError, match='BLOCKED_LIVE_DOJI'):
        asyncio.run(validate_account_entry(account, 'TEST', side, context))

@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_closed_only_or_one_confirmation_cannot_replace_live_third(side):
    f = frame_for(side).tail(3).copy()
    assert evaluate_entry_contract(f.iloc[-2:].copy()) is None
    f['is_closed'] = True
    assert evaluate_entry_contract(f) is None

@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_candle_before_pair_does_not_delay_entry(side):
    f = frame_for(side)
    f.loc[1, 'close'] = f.loc[1, 'open']
    assert evaluate_entry_contract(f)
