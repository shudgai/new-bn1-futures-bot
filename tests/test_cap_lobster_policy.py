import pytest
from core import config
from core.services.entry_contract import evaluate_entry_contract
from core.services.exits.peak_trailing_exit import evaluate_peak_trailing,PIVOT_ONLY_CHANNEL_SYMBOLS
from test_strict_entry_gates_live import frame_for
from test_kc_outer_pivot_strategy import position,snapshot


def test_active_symbols_switched_to_cap():
    assert config.DEFAULT_SYMBOLS == ['龙虾/USDT','CAP/USDT']
    assert 'CAP/USDT' in PIVOT_ONLY_CHANNEL_SYMBOLS


@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_cap_and_lobster_share_entry_and_pivot_policy(side):
    f=frame_for(side)
    decisions=[evaluate_entry_contract(f,symbol=s) for s in ['CAP/USDT','龙虾/USDT']]
    assert decisions[0]['type']==decisions[1]['type']
    assert decisions[0]['strict_gate_evidence']==decisions[1]['strict_gate_evidence']
    exits=[evaluate_peak_trailing(position(side,s),101.,snapshot(side),fee=0.,slippage=0.)
           for s in ['CAP/USDT','龙虾/USDT']]
    assert exits[0]['trigger']==exits[1]['trigger']=='THREE_POINT_PIVOT'
