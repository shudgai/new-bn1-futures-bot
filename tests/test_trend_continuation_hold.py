import pytest
from core.services.exits.peak_trailing_exit import evaluate_peak_trailing,STATE_KEY
from test_tiered_profit_gate import position

@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_missing_ma_cannot_authorize_profit_lock(side):
    p=position(side);sign=1 if side=='LONG' else -1
    evaluate_peak_trailing(p,100+sign*6,{'quote_ms':61000},fee=0,slippage=0)
    assert evaluate_peak_trailing(p,100+sign*4,{'quote_ms':62000},fee=0,slippage=0) is None
