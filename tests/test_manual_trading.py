import pytest

import core.paper_account as paper_module


@pytest.mark.anyio
async def test_manual_open_and_close_skip_strategy_gates_but_execute_account_flow(
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
