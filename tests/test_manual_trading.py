import pytest
import pandas as pd

import core.paper_account as paper_module


def _valid_long_entry_frame():
    frame = pd.DataFrame([
        {
            'timestamp': (index + 1) * 60_000,
            'open': 100.0,
            'high': 101.0,
            'low': 99.0,
            'close': 100.0,
            'atr': 1.5,
            'ma5': 90.0 + index,
            'ma15': 89.0 + index,
            'kc_middle': 90.0 + index,
            'kc_upper': 100.0 + index,
            'kc_lower': 80.0 + index,
            'is_closed': True,
        }
        for index in range(15)
    ])
    frame.attrs['timeframe_ms'] = 60_000
    return frame


@pytest.mark.anyio
async def test_manual_open_and_close_obey_hard_ma_gates_and_account_flow(
    tmp_path, monkeypatch,
):
    monkeypatch.setattr(paper_module, 'STATE_FILE', str(tmp_path / 'paper.json'))
    account = paper_module.PaperAccount()
    account.balance = 1_000.0

    opened = await account.open_position(
        symbol='DOGE/USDT', side='LONG', price=100.0,
        amount_usdt=25.0, sl=98.0, tp=104.0,
        reason='手動開倉_LONG', atr=1.5, leverage=5,
        signal_score=100,
        entry_context={
            'entry_mode': 'CHANNEL_SWING',
            'manual_entry': True,
            'source': 'MANUAL',
            'entry_gate_frame': _valid_long_entry_frame(),
            'entry_gate_quote': 100.0,
        },
    )

    assert opened is True
    assert account.positions['DOGE/USDT']['manual_entry'] is True
    assert account.positions['DOGE/USDT']['side'] == 'LONG'

    closed = await account.close_position(
        'DOGE/USDT', 99.0, '手動平倉', is_manual=True,
    )

    assert closed is True
    assert 'DOGE/USDT' not in account.positions
    assert account.trades[0]['action'] == 'CLOSE_LONG'


@pytest.mark.anyio
async def test_manual_open_fails_closed_without_market_structure_data(
    tmp_path, monkeypatch,
):
    monkeypatch.setattr(paper_module, 'STATE_FILE', str(tmp_path / 'paper.json'))
    account = paper_module.PaperAccount()
    account.balance = 1_000.0

    with pytest.raises(ValueError, match="BLOCKED_INSUFFICIENT_DATA"):
        await account.open_position(
            symbol='DOGE/USDT', side='LONG', price=100.0,
            amount_usdt=25.0, sl=98.0, tp=104.0,
            reason='手動開倉_LONG', atr=1.5, leverage=5,
            signal_score=100,
            entry_context={
                'entry_mode': 'CHANNEL_SWING',
                'manual_entry': True,
                'source': 'MANUAL',
            },
        )
