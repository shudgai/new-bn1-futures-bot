import pytest
import pandas as pd
import time

import core.paper_account as paper_module


def _valid_long_entry_frame():
    current_minute = int(time.time() // 60) * 60_000
    last_closed_bar = current_minute - 60_000
    frame = pd.DataFrame([
        {
            'timestamp': last_closed_bar - (29 - index) * 60_000,
            'open': 98.9 + index * 0.3,
            'high': 99.65 + index * 0.3,
            'low': 98.85 + index * 0.3,
            'close': 99.5 + index * 0.3,
            'atr': 1.5,
            'ma5': 99.0 + index * 0.31,
            'ma15': 99.0 + index * 0.26,
            'kc_middle': 97.8 + index * 0.2,
            'kc_upper': 100.0 + index * 0.2 + (0.3 if index == 29 else 0.0),
            'kc_lower': 95.6 + index * 0.2 - (0.3 if index == 29 else 0.0),
            'volume': 130.0 if index == 29 else 100.0,
            'is_closed': True,
        }
        for index in range(30)
    ])
    frame.attrs.update(
        timeframe_ms=60_000,
        snapshot_ms=current_minute,
        entry_finality_verified=True,
    )
    return frame


@pytest.mark.anyio
async def test_manual_open_and_close_obey_hard_ma_gates_and_account_flow(
    tmp_path, monkeypatch,
):
    monkeypatch.setattr(paper_module, 'STATE_FILE', str(tmp_path / 'paper.json'))
    account = paper_module.PaperAccount()
    account.balance = 1_000.0

    opened = await account.open_position(
        symbol='DOGE/USDT', side='LONG', price=108.3,
        amount_usdt=25.0, sl=106.3, tp=112.3,
        reason='手動開倉_LONG', atr=1.5, leverage=5,
        signal_score=100,
        entry_context={
            'entry_mode': 'CHANNEL_SWING',
            'manual_entry': True,
            'source': 'MANUAL',
            'entry_gate_frame': _valid_long_entry_frame(),
            'entry_gate_quote': 108.3,
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
