"""Immediate same-color bodies confirm dojis; fresh quotes can revoke permission."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from core.services.entry_contract import evaluate_entry_contract
from core.services.entry_firewall import validate_account_entry
from test_doji_entry_cleanup import set_body_ratio
from test_entry_without_wick_filter import frame_for

@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
@pytest.mark.parametrize('index', [2, 3])
def test_confirmed_doji_passes_account(side, index):
    frame = set_body_ratio(frame_for(side), index, .09)
    decision = evaluate_entry_contract(frame)
    assert decision and decision['side'] == side
    account = SimpleNamespace(entry_frame_provider=AsyncMock(return_value=frame))
    context = dict(entry_signal_code=decision['type'], channel_confirmation_bar_id=decision['confirmation_bar_id'])
    assert asyncio.run(validate_account_entry(account, 'TEST', side, context))['side'] == side

@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
@pytest.mark.parametrize('fault', ['opposite', 'flat', 'next_doji', 'chase'])
def test_confirmation_revoked_at_account(side, fault):
    frame = set_body_ratio(frame_for(side), 3, .09)
    decision = evaluate_entry_contract(frame)
    assert decision
    sign = 1 if side == 'LONG' else -1
    opening = float(frame.iloc[-1].open)
    quote = opening + sign * {'opposite': -.05, 'flat': 0, 'next_doji': .05, 'chase': .2}[fault]
    frame.loc[4, 'close'] = quote
    frame.loc[4, 'high'] = max(opening, quote) + .01
    frame.loc[4, 'low'] = min(opening, quote) - .01
    if fault == 'next_doji':
        set_body_ratio(frame, 4, .09)
    reason = {'opposite': 'BLOCKED_CLOSED_DOJI', 'flat': 'BLOCKED_LIVE_DOJI', 'next_doji': 'BLOCKED_LIVE_DOJI', 'chase': 'BLOCKED_OPEN_CHASE'}[fault]
    diagnostics = {}
    assert evaluate_entry_contract(frame, quote, diagnostics=diagnostics) is None
    assert diagnostics['reason'] == reason
    account = SimpleNamespace(entry_frame_provider=AsyncMock(return_value=frame))
    context = dict(entry_signal_code=decision['type'], channel_confirmation_bar_id=decision['confirmation_bar_id'])
    with pytest.raises(ValueError, match=reason):
        asyncio.run(validate_account_entry(account, 'TEST', side, context))

@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_consecutive_dojis_cannot_skip_successor(side):
    frame = frame_for(side)
    for index in (2, 3):
        set_body_ratio(frame, index, .09)
    assert evaluate_entry_contract(frame) is None

@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_latest_quote_can_revoke_and_restore_confirmation(side):
    frame = set_body_ratio(frame_for(side), 3, .09)
    opening = float(frame.iloc[-1].open)
    sign = 1 if side == 'LONG' else -1
    assert evaluate_entry_contract(frame, opening + sign * .05)
    assert evaluate_entry_contract(frame, opening - sign * .05) is None
    assert evaluate_entry_contract(frame, opening + sign * .05)
