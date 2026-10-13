import pytest
from core import config
from core.services.entry_contract import evaluate_entry_contract
from core.gates.pipeline import is_meme_breakout_symbol, fast_cross_ma15_slope_problem
from core.services.entry_contract import CHOP_FILTER_SYMBOLS
from core.services.exits.peak_trailing_exit import evaluate_peak_trailing,PIVOT_ONLY_CHANNEL_SYMBOLS
from test_strict_entry_gates_live import frame_for
from test_kc_outer_pivot_strategy import position,snapshot


def test_active_symbols_switched_to_cap():
    assert config.DEFAULT_SYMBOLS == ['龙虾/USDT','CAP/USDT']
    assert 'CAP/USDT' in config.DEFAULT_SYMBOLS


@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_cap_and_lobster_share_entry_and_pivot_policy(side):
    f=frame_for(side)
    decisions=[evaluate_entry_contract(f,symbol=s) for s in ['CAP/USDT','龙虾/USDT']]
    assert (decisions[0] is None) == (decisions[1] is None)
    if decisions[0] is not None:
        assert decisions[0]['type']==decisions[1]['type']
    exits=[evaluate_peak_trailing(position(side,s),101.,snapshot(side),fee=0.,slippage=0.)
           for s in ['CAP/USDT','龙虾/USDT']]
    assert exits == [None, None]


@pytest.mark.parametrize('symbol', [
    'CAP/USDT', 'CAP/USDT:USDT', '龙虾/USDT', '龍蝦/USDT',
    'LOBSTER/USDT', 'LOBSTER/USDT:USDT',
])
def test_meme_symbol_aliases_share_fast_cross_and_consolidation_policy(symbol):
    assert is_meme_breakout_symbol(symbol)
    assert symbol in CHOP_FILTER_SYMBOLS
    # Meme coin bullish fast crosses share the same live-MA15 slope exemption.
    assert fast_cross_ma15_slope_problem(
        None, 1.0, 'LONG', symbol=symbol,
    ) is None


@pytest.mark.parametrize('symbol', [
    'CAP/USDT', 'CAP/USDT:USDT', 'cap/usdt',
    '龙虾/USDT', '龙虾/USDT:USDT', '龍蝦/USDT', '龍蝦/USDT:USDT',
    'LOBSTER/USDT', 'LOBSTER/USDT:USDT', 'lobster/usdt',
    'SUI/USDT', 'SUI/USDT:USDT', 'sui/usdt',
])
def test_all_symbol_aliases_are_pivot_only_and_chop_protected(symbol):
    from core.symbol_aliases import (
        is_pivot_only_channel_symbol,
        is_chop_filter_symbol,
        PIVOT_ONLY_CHANNEL_SYMBOLS,
    )
    assert is_pivot_only_channel_symbol(symbol)
    assert is_chop_filter_symbol(symbol)
    # The upper-case normalized version is present in the shared frozen sets
    assert symbol.strip().upper() in PIVOT_ONLY_CHANNEL_SYMBOLS


@pytest.mark.parametrize('symbol', [
    'CAP/USDT', 'CAP/USDT:USDT',
    '龙虾/USDT', '龍蝦/USDT', 'LOBSTER/USDT', 'LOBSTER/USDT:USDT',
])
def test_all_lobster_and_cap_aliases_share_pivot_exit_protection(symbol):
    pos = position('LONG', symbol)
    snap = snapshot('LONG')
    # Under Channel Swing, peak trailing exit preserves position unless a 3-point pivot forms
    decision = evaluate_peak_trailing(pos, 101.0, snap, fee=0.0, slippage=0.0)
    assert decision is None

